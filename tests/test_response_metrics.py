from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Staff, SupportThread, SupportThreadMessage, User
from app.finance import payroll
from app.support import leaderboard


class ResponseMetricsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.factory() as session:
            session.add(User(id=1001, full_name="Customer"))
            session.add(Staff(telegram_id=100, display_name="Employee", role="client_manager_1"))
            session.add(SupportThread(id=1, user_id=1001, channel="telegram"))
            session.add_all(
                [
                    SupportThreadMessage(
                        id=1, thread_id=1, direction="client", kind="message", body="Question",
                        created_at=datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc),
                    ),
                    SupportThreadMessage(
                        id=2, thread_id=1, direction="staff", kind="message", staff_id=100, body="Answer",
                        created_at=datetime(2026, 10, 1, 10, 2, tzinfo=timezone.utc),
                    ),
                    SupportThreadMessage(
                        id=3, thread_id=1, direction="client", kind="message", body="Thanks",
                        created_at=datetime(2026, 10, 1, 10, 10, tzinfo=timezone.utc),
                    ),
                ]
            )
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_first_answer_uses_client_message_before_answer(self) -> None:
        lo = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
        hi = datetime(2026, 10, 2, 0, 0, tzinfo=timezone.utc)
        async with self.factory() as session:
            count, wait = await payroll._accepted_clients(session, 100, lo, hi)
            self.assertEqual(count, 1)
            self.assertEqual(wait, 120.0)

            first, waits = await leaderboard._first_replies(session, [100], lo, hi)
            self.assertEqual(first[100], 1)
            self.assertEqual(waits[100], 120.0)

            member = await session.get(Staff, 100)
            board = await leaderboard.build(
                session, [member], "day", datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
            )
            self.assertEqual(board["rows"][0]["answer_sec"], 120.0)


if __name__ == "__main__":
    unittest.main()
