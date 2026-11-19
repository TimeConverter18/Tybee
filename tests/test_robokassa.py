from __future__ import annotations

import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Order, RobokassaPayment, SupportInbox, SupportOutbox, User
from app.robokassa_webhook import process_robokassa_result
from app.services.robokassa import build_payment_link, create_unban_payment


class RobokassaTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.factory() as session:
            session.add(User(id=1001, full_name="Customer", is_banned=True))
            session.add(
                Order(
                    id=150_000_000,
                    user_id=1001,
                    type="personal",
                    status="awaiting_payment",
                    title="Order",
                    price=100,
                )
            )
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_valid_result_marks_order_paid_and_queues_notifications(self) -> None:
        signature = hashlib.md5(b"100.00:150000000:pass2").hexdigest().upper()
        settings = SimpleNamespace(robokassa_password2="pass2", robokassa_hash_algorithm="MD5")
        with patch("app.robokassa_webhook.session_factory", self.factory):
            with patch("app.services.robokassa.get_settings", return_value=settings):
                response = await process_robokassa_result(
                    {"OutSum": "100.00", "InvId": "150000000", "SignatureValue": signature}
                )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.text, "OK150000000")
        async with self.factory() as session:
            order = await session.get(Order, 150_000_000)
            self.assertEqual(order.status, "approved")
            self.assertEqual(order.paid_amount, 100)
            self.assertIsNotNone(order.paid_at)
            outboxes = list(await session.scalars(select(SupportOutbox)))
            self.assertEqual([event.kind for event in outboxes], ["approval"])
            inbox = await session.scalar(select(SupportInbox))
            self.assertEqual(inbox.external_id, "robokassa:150000000")

    async def test_invalid_result_does_not_change_order(self) -> None:
        settings = SimpleNamespace(robokassa_password2="pass2", robokassa_hash_algorithm="MD5")
        with patch("app.robokassa_webhook.session_factory", self.factory):
            with patch("app.services.robokassa.get_settings", return_value=settings):
                response = await process_robokassa_result(
                    {"OutSum": "100.00", "InvId": "150000000", "SignatureValue": "invalid"}
                )
        self.assertEqual(response.status, 403)
        async with self.factory() as session:
            order = await session.get(Order, 150_000_000)
            self.assertEqual(order.status, "awaiting_payment")
            self.assertIsNone(order.paid_at)

    async def test_unban_payment_locks_user_and_reuses_pending_invoice(self) -> None:
        async with self.factory() as session:
            user = await session.get(User, 1001)
            statements = []
            original_scalar = session.scalar

            async def traced_scalar(statement, *args, **kwargs):
                statements.append(statement)
                return await original_scalar(statement, *args, **kwargs)

            with patch.object(session, "scalar", new=traced_scalar):
                payment = await create_unban_payment(session, user, amount=50)
                await session.commit()
                self.assertIsNotNone(statements[0]._for_update_arg)
                repeated = await create_unban_payment(session, user, amount=50)
            self.assertEqual(repeated.id, payment.id)
            count = await session.scalar(select(func.count()).select_from(RobokassaPayment))
            self.assertEqual(count, 1)

    def test_payment_link_rejects_missing_or_nonpositive_price(self) -> None:
        self.assertIsNone(build_payment_link(150_000_000, None, "Order"))
        self.assertIsNone(build_payment_link(150_000_000, 0, "Order"))
        self.assertIsNone(build_payment_link(150_000_000, -1, "Order"))


if __name__ == "__main__":
    unittest.main()
