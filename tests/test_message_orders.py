from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "site" / "backend"))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Order, Staff, SupportThread, SupportThreadMessage, User
from panel.routes.support import get_messages


class MessageOrdersTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_message_contains_full_order_context(self) -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with factory() as session:
                session.add_all(
                    [
                        User(id=42, full_name="Client"),
                        Staff(telegram_id=100, display_name="Admin", role="admin"),
                        SupportThread(id=1, user_id=42),
                        Order(
                            id=9001,
                            user_id=42,
                            type="personal",
                            status="approved",
                            title="Product",
                            description="Selected model",
                            article="123456",
                            price=2500,
                        ),
                        SupportThreadMessage(
                            id=1,
                            thread_id=1,
                            direction="system",
                            kind="status",
                            body="Order updated",
                            order_id=9001,
                        ),
                    ]
                )
                await session.commit()
                staff = await session.get(Staff, 100)
                rows = await get_messages(1, q=None, me=staff, session=session)
                self.assertEqual(rows[0]["order_id"], 9001)
                self.assertEqual(rows[0]["order"]["title"], "Product")
                self.assertEqual(rows[0]["order"]["description"], "Selected model")
                self.assertEqual(rows[0]["order"]["article"], "123456")
                self.assertEqual(rows[0]["order"]["price"], 2500)
                self.assertEqual(rows[0]["order"]["type_label"], "Персональный")
                self.assertEqual(rows[0]["order"]["status_label"], "✅ Оплата подтверждена")
        finally:
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
