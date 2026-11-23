"""Local storage and Telegram fetching for employee avatars."""
from __future__ import annotations

import io
import logging
import os
import tempfile
import time
from pathlib import Path

from aiogram import Bot

from app.support.attachments import IMAGE_EXT, data_root, validate_image

logger = logging.getLogger(__name__)
_EXT_TO_MIME = {ext: mime for mime, ext in IMAGE_EXT.items() if mime != "application/pdf"}


def _avatars_dir() -> Path:
    return data_root() / "avatars"


def save_staff_avatar(staff_id: int, data: bytes) -> str | None:
    mime = validate_image(data)
    if mime is None or mime not in IMAGE_EXT:
        return None
    ext = IMAGE_EXT[mime]
    directory = _avatars_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for old_ext in _EXT_TO_MIME:
        if old_ext != ext:
            (directory / f"staff_{staff_id}{old_ext}").unlink(missing_ok=True)
    target = directory / f"staff_{staff_id}{ext}"
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=f".staff_{staff_id}_", suffix=ext)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return f"/api/staff/{staff_id}/avatar?v={int(time.time())}"


def resolve_staff_avatar(staff_id: int) -> tuple[Path, str] | None:
    directory = _avatars_dir()
    for ext, mime in _EXT_TO_MIME.items():
        candidate = directory / f"staff_{staff_id}{ext}"
        if candidate.is_file():
            return candidate, mime
    return None


async def fetch_telegram_avatar(bot: Bot, staff_id: int) -> str | None:
    """Download the employee's Telegram profile photo into DATA_DIR/avatars and return its panel URL."""
    try:
        photos = await bot.get_user_profile_photos(staff_id, limit=1)
        if not photos.photos or not photos.photos[0]:
            return None
        sizes = photos.photos[0]
        # Prefer a crisp medium size (~320px) when available instead of the largest raw upload.
        picked = sizes[1] if len(sizes) >= 2 else sizes[0]
        info = await bot.get_file(picked.file_id)
        if not info.file_path:
            return None
        buf = io.BytesIO()
        await bot.download_file(info.file_path, destination=buf)
        return save_staff_avatar(staff_id, buf.getvalue())
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not fetch Telegram avatar for %s: %s", staff_id, exc)
        return None
