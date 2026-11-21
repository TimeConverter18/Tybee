from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Cover, SupportAttachment, SupportThread, SupportThreadMessage, User
from scripts.recover_telegram_support import import_history, load_history


def _message(message_id: int, sender: str, body: str, *, media: str = "") -> str:
    return f'''
<div class="message default clearfix" id="message{message_id}">
 <div class="body">
  <div class="pull_right date details" title="21.11.2026 12:00:0{message_id % 10} UTC+03:00">12:00</div>
  <div class="from_name">{sender}</div>
  <div class="reply_to details">In reply to <a href="#go_to_message100" onclick="return GoToMessage(100)">this message</a></div>
  <div class="text">{body}</div>
  {media}
 </div>
</div>'''


def _fixture(root: Path) -> tuple[Path, Path]:
    export = root / "export"
    export.mkdir()
    (export / "photos").mkdir()
    (export / "photos" / "test.jpg").write_bytes(b"image")
    (export / "messages.html").write_text(
        '''
<div class="message service" id="message100">
 <div class="body details">Tybee created topic &laquo;Client (@client) · 42&raquo;</div>
</div>
'''
        + _message(101, "Tybee", "🧵 <strong>Ветка клиента</strong>")
        + _message(102, "Tybee", "Client<br>question", media='<a href="photos/test.jpg">photo</a>')
        + _message(103, "Employee", "Answer"),
        encoding="utf-8",
    )
    dump = root / "old.sql"
    dump.write_text(
        "COPY public.users (id, username, full_name, is_banned, support_thread_id) FROM stdin;\n"
        "42\tclient\tClient\tf\t100\n\\.\n"
        "COPY public.relay_map (support_message_id, user_id) FROM stdin;\n"
        "102\t42\n\\.\n",
        encoding="utf-8",
    )
    return export, dump


class TelegramRecoveryTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.factory() as session:
            session.add(Cover(id=1, slug="default", path="/covers/default"))
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    def test_export_maps_directions_and_media(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            export, dump = _fixture(Path(directory))
            history, stats = load_history(export, dump)
        self.assertEqual([message.direction for message in history], ["system", "client", "staff"])
        self.assertEqual(stats["clients"], 1)
        self.assertEqual(stats["media"], 1)
        self.assertEqual(stats["media_missing"], 0)

    async def test_import_is_idempotent_and_keeps_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export, dump = _fixture(root)
            with patch(
                "app.support.attachments.get_settings",
                return_value=SimpleNamespace(data_dir=str(root / "data")),
            ):
                first = await import_history(export, dump, execute=True, factory=self.factory)
                second = await import_history(export, dump, execute=True, factory=self.factory)
                self.assertEqual(first["imported"], 3)
                self.assertEqual(first["attachments"], 1)
                self.assertEqual(second["imported"], 0)
                self.assertEqual(second["attachments"], 0)
            async with self.factory() as session:
                user = await session.get(User, 42)
                self.assertEqual(user.username, "client")
                thread = await session.scalar(select(SupportThread).where(SupportThread.user_id == 42))
                self.assertEqual(thread.status, "closed")
                messages = list(await session.scalars(select(SupportThreadMessage)))
                self.assertEqual(len(messages), 3)
                self.assertEqual(len({message.request_key for message in messages}), 3)
                attachment = await session.scalar(select(SupportAttachment))
                self.assertFalse(attachment.ephemeral)
                self.assertEqual(attachment.direction, "in")
                self.assertGreater(attachment.size, 0)
                self.assertEqual(await session.scalar(select(func.count()).select_from(SupportAttachment)), 1)


if __name__ == "__main__":
    unittest.main()
