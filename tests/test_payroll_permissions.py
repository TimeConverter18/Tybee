from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "site" / "backend"))

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import PayrollAccrual, Staff, SupportReview
from panel.routes.payroll import (
    AccrueBody,
    CancelBody,
    ReviewBody,
    accrue_payroll,
    add_review,
    cancel_payroll,
    list_reviews,
    payroll_report,
    remove_review,
)


class PayrollPermissionsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.factory() as session:
            session.add_all(
                [
                    Staff(telegram_id=100, display_name="Employee", role="client_manager_1", base_salary_kop=100_000),
                    Staff(telegram_id=500, display_name="Lead", role="support_lead", base_salary_kop=60_000),
                    Staff(telegram_id=999, display_name="Admin", role="admin", base_salary_kop=90_000),
                ]
            )
            session.add_all(
                [
                    SupportReview(staff_id=100, source="avito", rating=5),
                    SupportReview(staff_id=999, source="avito", rating=5),
                ]
            )
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_lead_manages_only_subordinate_payroll_and_reviews(self) -> None:
        async with self.factory() as session:
            lead = await session.get(Staff, 500)
            with patch("panel.routes.payroll.fnotify.send", new=AsyncMock()):
                result = await accrue_payroll(AccrueBody(period="2026-10", staff_id=None), lead, session)
            self.assertEqual([row["staff_id"] for row in result["created"]], [100])
            accruals = list(await session.scalars(select(PayrollAccrual)))
            self.assertEqual([accrual.staff_id for accrual in accruals], [100])

            report = await payroll_report("2026-10", lead, session)
            self.assertEqual({row["staff_id"] for row in report["rows"]}, {100})
            self.assertEqual({member["telegram_id"] for member in report["members"]}, {100})

            reviews = await list_reviews(None, None, lead, session)
            self.assertEqual({review["staff_id"] for review in reviews}, {100})

            with self.assertRaises(HTTPException) as cancel_error:
                await cancel_payroll(999, CancelBody(period="2026-10"), lead, session)
            self.assertEqual(cancel_error.exception.status_code, 403)

            with self.assertRaises(HTTPException) as add_error:
                await add_review(ReviewBody(staff_id=999), lead, session)
            self.assertEqual(add_error.exception.status_code, 403)

            admin_review = await session.scalar(select(SupportReview).where(SupportReview.staff_id == 999))
            with self.assertRaises(HTTPException) as delete_error:
                await remove_review(admin_review.id, lead, session)
            self.assertEqual(delete_error.exception.status_code, 403)
            self.assertIsNotNone(await session.get(SupportReview, admin_review.id))


if __name__ == "__main__":
    unittest.main()
