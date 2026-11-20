from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "site" / "backend"))

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import AvitoSettings, Staff
from panel.routes.avito import WebhookBody, list_profiles, set_avito_settings


class AvitoSettingsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.staff = Staff(telegram_id=2002, display_name="Admin", role="admin")
        async with self.factory() as session:
            session.add(self.staff)
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_webhook_base_is_saved_and_used_by_profiles(self) -> None:
        async with self.factory() as session:
            result = await set_avito_settings(
                WebhookBody(webhook_base="https://support.tybee.ru/"), self.staff, session
            )
            self.assertEqual(result["webhook_base"], "https://support.tybee.ru")
            row = await session.get(AvitoSettings, 1)
            self.assertEqual(row.webhook_base, "https://support.tybee.ru")
            self.assertEqual(row.updated_by, self.staff.telegram_id)
            profiles = await list_profiles(self.staff, session)
            self.assertEqual(profiles["webhook_base"], "https://support.tybee.ru")

    async def test_invalid_webhook_base_is_rejected(self) -> None:
        async with self.factory() as session:
            with self.assertRaises(HTTPException) as raised:
                await set_avito_settings(
                    WebhookBody(webhook_base="https://support.tybee.ru/avito/webhook"),
                    self.staff,
                    session,
                )
            self.assertEqual(raised.exception.status_code, 422)
            self.assertIsNone(await session.get(AvitoSettings, 1))


if __name__ == "__main__":
    unittest.main()
