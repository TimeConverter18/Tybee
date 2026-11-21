from __future__ import annotations

import asyncio
import re
import secrets
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.config import get_settings
from app.db.base import session_factory
from app.db.models import Staff
from panel.audit import write_audit
from panel.auth import csrf_guard, require_perm
from panel.permissions import Perm
from app.support.recovery import import_history, load_history

router = APIRouter(prefix="/api/recovery", tags=["recovery"], dependencies=[Depends(csrf_guard)])
manage = require_perm(Perm.RECOVERY_IMPORT)

SQL_LIMIT = 80 * 1024 * 1024
ZIP_LIMIT = 200 * 1024 * 1024
UNPACK_LIMIT = 400 * 1024 * 1024
MAX_ENTRIES = 2000
MEDIA_DIRS = {"photos", "video_files", "voice_messages", "files", "stickers"}
JOB_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_jobs: dict[str, dict] = {}
_tasks: set[asyncio.Task] = set()


def _root() -> Path:
    path = Path(get_settings().data_dir).resolve() / "recovery"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _job_path(job_id: str) -> Path:
    if not JOB_ID.fullmatch(job_id):
        raise HTTPException(404, "Задание не найдено")
    return _root() / job_id


def _public(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Задание не найдено")
    return {
        "id": job_id,
        "status": job["status"],
        "stats": job["stats"],
        "error": job.get("error"),
    }


async def _save_upload(upload: UploadFile, target: Path, limit: int) -> None:
    size = 0
    try:
        with target.open("wb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(422, f"Файл {upload.filename or ''} слишком большой")
                output.write(chunk)
    finally:
        await upload.close()


def _extract_export(archive: Path, target: Path) -> Path:
    total = 0
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > MAX_ENTRIES:
            raise ValueError("В ZIP слишком много файлов")
        for info in entries:
            if info.is_dir():
                continue
            name = PurePosixPath(info.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in info.filename:
                raise ValueError("Небезопасный путь в ZIP")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("Символические ссылки в ZIP не поддерживаются")
            is_html = name.name == "messages.html"
            is_media = any(part in MEDIA_DIRS for part in name.parts)
            if not is_html and not is_media:
                continue
            if info.file_size > UNPACK_LIMIT:
                raise ValueError("Распакованный экспорт слишком большой")
            destination = target.joinpath(*name.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, destination.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > UNPACK_LIMIT:
                        raise ValueError("Распакованный экспорт слишком большой")
                    output.write(chunk)
    roots = list(target.rglob("messages.html"))
    if len(roots) != 1:
        raise ValueError("В ZIP должен быть ровно один messages.html")
    return roots[0].parent


@router.post("")
async def prepare_recovery(
    dump: UploadFile,
    export: UploadFile,
    _: Staff = Depends(manage),
):
    job_id = secrets.token_urlsafe(18)
    directory = _job_path(job_id)
    directory.mkdir(mode=0o700)
    dump_path = directory / "old.sql"
    archive_path = directory / "export.zip"
    try:
        await _save_upload(dump, dump_path, SQL_LIMIT)
        await _save_upload(export, archive_path, ZIP_LIMIT)
        export_dir = await asyncio.to_thread(_extract_export, archive_path, directory / "export")
        _history, stats = await asyncio.to_thread(load_history, export_dir, dump_path)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(422, str(exc)) from None
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    _jobs[job_id] = {
        "status": "ready",
        "stats": stats,
        "dump": dump_path,
        "export_dir": export_dir,
        "directory": directory,
    }
    return _public(job_id)


@router.get("/{job_id}")
async def recovery_status(job_id: str, _: Staff = Depends(manage)):
    _job_path(job_id)
    return _public(job_id)


async def _run_import(job_id: str, actor_id: int) -> None:
    job = _jobs[job_id]
    try:
        stats = await import_history(job["export_dir"], job["dump"], execute=True)
        job["stats"] = stats
        job["status"] = "done"
        async with session_factory() as session:
            actor = await session.get(Staff, actor_id)
            if actor is not None:
                await write_audit(
                    session,
                    actor,
                    "support.recovery.execute",
                    None,
                    {"messages": stats.get("imported", 0), "attachments": stats.get("attachments", 0)},
                )
                await session.commit()
        shutil.rmtree(job["directory"], ignore_errors=True)
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)[:500]


@router.post("/{job_id}/execute")
async def execute_recovery(
    job_id: str,
    me: Staff = Depends(manage),
):
    _job_path(job_id)
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Задание не найдено")
    if job["status"] == "running" or any(
        other["status"] == "running" for other in _jobs.values()
    ):
        raise HTTPException(409, "Другой импорт уже выполняется")
    if job["status"] == "done":
        raise HTTPException(409, "Импорт уже завершён")
    if not job["stats"].get("messages"):
        raise HTTPException(422, "В экспорте нет сообщений для импорта")
    if job["stats"].get("media_missing"):
        raise HTTPException(422, "В экспорте отсутствуют вложения. Загрузите полный ZIP")
    job["status"] = "running"
    job["error"] = None
    task = asyncio.create_task(_run_import(job_id, me.telegram_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return _public(job_id)
