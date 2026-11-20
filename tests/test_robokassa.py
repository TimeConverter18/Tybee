from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "site" / "backend"))

from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.avito_webhook import create_web_application
from app.db.base import Base
from app.db.models import Order, RobokassaPayment, RobokassaSettings, Staff, SupportInbox, SupportOutbox, User
from app.robokassa_webhook import process_robokassa_result
from app.services.robokassa import RobokassaConfig, build_payment_link, create_unban_payment, load_robokassa_config, payment_link
from app.services.secret_box import decrypt_secret, encrypt_secret
from panel.routes.robokassa import SettingsBody, set_robokassa_settings


def _config(*, test: bool = False) -> RobokassaConfig:
    return RobokassaConfig(
        shop_id="shop",
        password1="pass1",
        password2="pass2",
        hash_algorithm="MD5",
        test=test,
    )


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
        signature = hashlib.md5(b"100.000000:150000000:pass2").hexdigest().upper()
        settings = _config()
        with patch("app.robokassa_webhook.session_factory", self.factory):
            with patch("app.robokassa_webhook.load_robokassa_config", return_value=settings):
                response = await process_robokassa_result(
                    {"OutSum": "100.000000", "InvId": "150000000", "SignatureValue": signature}
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
        settings = _config()
        with patch("app.robokassa_webhook.session_factory", self.factory):
            with patch("app.robokassa_webhook.load_robokassa_config", return_value=settings):
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

    async def test_payment_callback_and_return_routes(self) -> None:
        app = create_web_application()
        async with TestClient(TestServer(app)) as client:
            for path, expected_text in (
                ("/robokassa/success", "Оплата выполнена"),
                ("/robokassa/fail", "Оплата не завершена"),
            ):
                response = await client.get(path)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.content_type, "text/html")
                self.assertIn(expected_text, await response.text())
            response = await client.get("/robokassa/result")
            self.assertEqual(response.status, 400)

    def test_payment_link_uses_documented_signature(self) -> None:
        link = build_payment_link(150_000_000, 100, "Order", config=_config())
        test_link = build_payment_link(150_000_001, 100, "Order", config=_config(test=True))

        query = parse_qs(urlparse(link).query)
        expected = hashlib.md5(b"shop:100.00:150000000:pass1").hexdigest().upper()
        self.assertEqual(query["MerchantLogin"], ["shop"])
        self.assertEqual(query["OutSum"], ["100.00"])
        self.assertEqual(query["InvId"], ["150000000"])
        self.assertEqual(query["SignatureValue"], [expected])
        self.assertNotIn("IsTest", query)
        self.assertEqual(parse_qs(urlparse(test_link).query)["IsTest"], ["1"])

    async def test_database_settings_are_used_for_links(self) -> None:
        with patch("app.services.secret_box.get_settings", return_value=SimpleNamespace(web_session_secret="panel-secret")):
            async with self.factory() as session:
                session.add(
                    RobokassaSettings(
                        id=1,
                        shop_id="db-shop",
                        password1_enc=encrypt_secret("db-pass1"),
                        password2_enc=encrypt_secret("db-pass2"),
                        hash_algorithm="SHA256",
                        is_test=True,
                    )
                )
                await session.commit()
                config = await load_robokassa_config(session)
                link = await payment_link(session, await session.get(Order, 150_000_000))
                stored = await session.get(RobokassaSettings, 1)

        self.assertEqual(config.shop_id, "db-shop")
        self.assertEqual(config.password1, "db-pass1")
        self.assertTrue(config.test)
        self.assertNotIn("db-pass1", stored.password1_enc)
        query = parse_qs(urlparse(link).query)
        expected = hashlib.sha256(b"db-shop:100.00:150000000:db-pass1").hexdigest().upper()
        self.assertEqual(query["SignatureValue"], [expected])
        self.assertEqual(query["IsTest"], ["1"])

    async def test_admin_settings_hide_and_preserve_passwords(self) -> None:
        me = Staff(telegram_id=2002, display_name="Admin", role="admin")
        with patch("panel.routes.robokassa.key_configured", return_value=True), patch(
            "app.services.secret_box.get_settings",
            return_value=SimpleNamespace(web_session_secret="panel-secret"),
        ):
            async with self.factory() as session:
                session.add(me)
                await session.commit()
                result = await set_robokassa_settings(
                    SettingsBody(
                        shop_id="db-shop",
                        password1="db-pass1",
                        password2="db-pass2",
                        hash_algorithm="SHA256",
                        test=True,
                    ),
                    me,
                    session,
                )
                self.assertTrue(result["configured"])
                self.assertEqual(result["source"], "database")
                self.assertNotIn("password1", result)
                self.assertNotIn("password2", result)
                self.assertNotIn("db-pass1", str(result))
                updated = await set_robokassa_settings(
                    SettingsBody(shop_id="new-shop", hash_algorithm="MD5", test=False),
                    me,
                    session,
                )
                row = await session.get(RobokassaSettings, 1)
                self.assertEqual(updated["shop_id"], "new-shop")
                self.assertEqual(decrypt_secret(row.password1_enc), "db-pass1")
                self.assertEqual(decrypt_secret(row.password2_enc), "db-pass2")

    def test_payment_link_rejects_missing_or_nonpositive_price(self) -> None:
        self.assertIsNone(build_payment_link(150_000_000, None, "Order"))
        self.assertIsNone(build_payment_link(150_000_000, 0, "Order"))
        self.assertIsNone(build_payment_link(150_000_000, -1, "Order"))


if __name__ == "__main__":
    unittest.main()
