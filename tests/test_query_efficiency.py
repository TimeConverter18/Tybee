from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "site" / "backend"))

from app.catalog.broadcast import count_recipients
from app.db.base import Base
from app.db.models import Cover, SupportThread, SupportThreadMessage, User, UserCover
from panel.routes.support import _thread_rows


class QueryEfficiencyTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.factory() as session:
            session.add_all(
                [
                    Cover(id=1, slug="default", path="/covers/default"),
                    Cover(id=2, slug="second", path="/covers/second"),
                    User(id=1001, full_name="First"),
                    User(id=1002, full_name="Second"),
                    User(id=1003, full_name="Banned", is_banned=True),
                    UserCover(user_id=1001, cover_id=1),
                    UserCover(user_id=1001, cover_id=2),
                    UserCover(user_id=1002, cover_id=1),
                    UserCover(user_id=1003, cover_id=1),
                    SupportThread(id=1, user_id=1001, channel="telegram", last_cover_id=1),
                    SupportThread(id=2, user_id=1002, channel="telegram", last_cover_id=1),
                    SupportThreadMessage(id=1, thread_id=1, direction="client", body="old", cover_id=1),
                    SupportThreadMessage(id=2, thread_id=1, direction="client", body="new", cover_id=1),
                    SupportThreadMessage(id=3, thread_id=2, direction="client", body="[default] hello", cover_id=1),
                ]
            )
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_latest_messages_use_one_query_for_whole_page(self) -> None:
        async with self.factory() as session:
            threads = list(await session.scalars(select(SupportThread).order_by(SupportThread.id)))
            queries = []

            def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
                if "support_thread_messages" in statement:
                    queries.append(statement)

            event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
            try:
                rows = await _thread_rows(session, threads)
            finally:
                event.remove(self.engine.sync_engine, "before_cursor_execute", capture)

            self.assertEqual(len(queries), 1)
            self.assertEqual([row["last_message"] for row in rows], ["new", "hello"])

    async def test_broadcast_count_stays_in_database(self) -> None:
        async with self.factory() as session:
            with patch(
                "app.catalog.broadcast.recipients",
                new=AsyncMock(side_effect=AssertionError("recipients must not be materialized")),
            ):
                count = await count_recipients(session, [1, 2])
            self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
