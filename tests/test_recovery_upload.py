from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import UploadFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "site" / "backend"))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import AuditLog, Staff
from panel.routes import recovery


class RecoveryUploadTestCase(unittest.IsolatedAsyncioTestCase):
    def test_zip_is_extracted_and_html_root_is_found(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "export.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("ChatExport/messages.html", "<html></html>")
                bundle.writestr("ChatExport/photos/test.jpg", b"image")
                bundle.writestr("ChatExport/css/style.css", "ignored")
            export = recovery._extract_export(archive, root / "unpacked")
            self.assertEqual(export.name, "ChatExport")
            self.assertTrue((export / "messages.html").is_file())
            self.assertTrue((export / "photos" / "test.jpg").is_file())
            self.assertFalse((export / "css" / "style.css").exists())

    def test_zip_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../messages.html", "bad")
            with self.assertRaisesRegex(ValueError, "Небезопасный путь"):
                recovery._extract_export(archive, Path(directory) / "unpacked")

    def test_zip_unpacked_size_limit_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "large.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("messages.html", "too large")
            with patch("panel.routes.recovery.UNPACK_LIMIT", 2):
                with self.assertRaisesRegex(ValueError, "слишком большой"):
                    recovery._extract_export(archive, Path(directory) / "unpacked")

    async def test_prepare_route_uploads_and_reports_without_database_write(self) -> None:
        sql = (
            "COPY public.users (id, support_thread_id) FROM stdin;\n"
            "42\t100\n\\.\n"
            "COPY public.relay_map (support_message_id, user_id) FROM stdin;\n"
            "\\.\n"
        ).encode()
        html = (
            '<div class="message service" id="message100"><div class="body details">'
            "Tybee created topic Client · 42</div></div>"
            '<div class="message default" id="message101"><div class="body">'
            '<div class="pull_right date details" title="21.11.2026 12:00:00 UTC+03:00"></div>'
            '<div class="from_name">Employee</div>'
            '<div class="reply_to details"><a onclick="return GoToMessage(100)">reply</a></div>'
            '<div class="text">Recovered answer</div></div></div>'
        )
        archive_data = io.BytesIO()
        with zipfile.ZipFile(archive_data, "w") as bundle:
            bundle.writestr("ChatExport/messages.html", html)
        archive_data.seek(0)
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "panel.routes.recovery.get_settings",
                return_value=SimpleNamespace(data_dir=directory),
            ):
                job = await recovery.prepare_recovery(
                    UploadFile(filename="old.sql", file=io.BytesIO(sql)),
                    UploadFile(filename="export.zip", file=archive_data),
                    None,
                )
            try:
                self.assertEqual(job["status"], "ready")
                self.assertEqual(job["stats"]["messages"], 1)
                self.assertEqual(job["stats"]["clients"], 1)
                self.assertNotIn("imported", job["stats"])
            finally:
                recovery._jobs.pop(job["id"], None)
                shutil.rmtree(Path(directory) / "recovery" / job["id"], ignore_errors=True)

    async def test_successful_job_writes_audit_and_removes_sources(self) -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with factory() as session:
                session.add(Staff(telegram_id=42, display_name="Admin", role="admin"))
                await session.commit()
            with tempfile.TemporaryDirectory() as directory:
                job_dir = Path(directory) / "job"
                job_dir.mkdir()
                (job_dir / "old.sql").write_text("source", encoding="utf-8")
                recovery._jobs["job"] = {
                    "status": "running",
                    "stats": {},
                    "dump": job_dir / "old.sql",
                    "export_dir": job_dir / "export",
                    "directory": job_dir,
                }
                stats = {"imported": 10, "attachments": 2}
                with patch(
                    "panel.routes.recovery.import_history",
                    new=AsyncMock(return_value=stats),
                ), patch("panel.routes.recovery.session_factory", factory):
                    await recovery._run_import("job", 42)
                self.assertEqual(recovery._jobs["job"]["status"], "done")
                self.assertEqual(recovery._jobs["job"]["stats"], stats)
                self.assertFalse(job_dir.exists())
            async with factory() as session:
                audit = await session.scalar(select(AuditLog))
                self.assertEqual(audit.action, "support.recovery.execute")
                self.assertEqual(audit.details["messages"], 10)
        finally:
            recovery._jobs.pop("job", None)
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
