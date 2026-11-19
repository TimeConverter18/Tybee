from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import FinanceOperation, PayrollAccrual, Staff
from app.finance import ledger, payroll
from app.finance.common import FinanceError


class FinanceTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.factory() as session:
            session.add_all(
                [
                    Staff(telegram_id=100, display_name="Employee", role="support_lead", base_salary_kop=100_000),
                    Staff(telegram_id=999, display_name="Finance", role="admin"),
                ]
            )
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_reaccrual_preserves_previous_cycles_and_balance(self) -> None:
        async with self.factory() as session:
            first_result = await payroll.accrue(session, 999, "2026-10", [100])
            await session.commit()
            first_id = first_result["created"][0]["accrual_id"]
            first = await session.get(PayrollAccrual, first_id)
            self.assertIsNotNone(first)
            first_op_id = first.salary_op_id
            first_details = dict(first.details or {})
            self.assertEqual(first.cycle, 1)
            self.assertEqual(await ledger.balance(session, 100), 100_000)

            await payroll.cancel(session, 999, 100, "2026-10")
            await session.commit()
            self.assertTrue(first.reversed)
            self.assertIsNotNone(first.reversed_at)
            self.assertEqual(await ledger.balance(session, 100), 0)

            second_result = await payroll.accrue(session, 999, "2026-10", [100])
            await session.commit()
            second_id = second_result["created"][0]["accrual_id"]
            second = await session.get(PayrollAccrual, second_id)
            self.assertIsNotNone(second)
            self.assertNotEqual(second.id, first.id)
            self.assertEqual(second.cycle, 2)
            self.assertFalse(second.reversed)
            self.assertEqual(await ledger.balance(session, 100), 100_000)

            await session.refresh(first)
            self.assertTrue(first.reversed)
            self.assertEqual(first.salary_op_id, first_op_id)
            self.assertEqual(first.details, first_details)

            repeated = await payroll.accrue(session, 999, "2026-10", [100])
            self.assertEqual(repeated["created"], [])
            self.assertEqual(repeated["skipped"][0]["reason"], "уже начислено")

            current = await payroll.compute(session, await session.get(Staff, 100), "2026-10")
            self.assertEqual(current["accrual"]["id"], second.id)

            await payroll.cancel(session, 999, 100, "2026-10")
            await session.commit()
            self.assertEqual(await ledger.balance(session, 100), 0)
            operations = list(await session.scalars(select(FinanceOperation).order_by(FinanceOperation.id)))
            self.assertEqual(len(operations), 4)
            self.assertEqual(
                {operation.reversal_of for operation in operations if operation.kind == "reversal"},
                {first.salary_op_id, second.salary_op_id},
            )

            third_result = await payroll.accrue(session, 999, "2026-10", [100])
            await session.commit()
            third = await session.get(PayrollAccrual, third_result["created"][0]["accrual_id"])
            self.assertEqual(third.cycle, 3)

    async def test_reverse_does_not_mask_database_failure(self) -> None:
        async with self.factory() as session:
            operation = await ledger.add_operation(
                session,
                staff_id=100,
                kind="bonus",
                amount_kop=5_000,
                actor_id=999,
            )
            await session.commit()
            failure = OperationalError("INSERT", {}, Exception("database unavailable"))
            with patch.object(session, "flush", new=AsyncMock(side_effect=failure)):
                with self.assertRaises(OperationalError):
                    await ledger.reverse(session, operation.id, 999, "Correction")
            await session.rollback()

    async def test_reverse_does_not_mask_unrelated_integrity_failure(self) -> None:
        async with self.factory() as session:
            operation = await ledger.add_operation(
                session,
                staff_id=100,
                kind="bonus",
                amount_kop=5_000,
                actor_id=999,
            )
            await session.commit()
            failure = IntegrityError("INSERT", {}, Exception("not-null violation"))
            with patch.object(session, "flush", new=AsyncMock(side_effect=failure)):
                with self.assertRaises(IntegrityError):
                    await ledger.reverse(session, operation.id, 999, "Correction")

    async def test_reverse_translates_only_reversal_unique_conflict(self) -> None:
        class Diag:
            constraint_name = "finance_operations_reversal_of_key"

        class DatabaseError(Exception):
            diag = Diag()

        async with self.factory() as session:
            operation = await ledger.add_operation(
                session,
                staff_id=100,
                kind="bonus",
                amount_kop=5_000,
                actor_id=999,
            )
            await session.commit()
            failure = IntegrityError("INSERT", {}, DatabaseError("duplicate"))
            with patch.object(session, "flush", new=AsyncMock(side_effect=failure)):
                with self.assertRaises(FinanceError) as raised:
                    await ledger.reverse(session, operation.id, 999, "Correction")
            self.assertEqual(raised.exception.status, 409)


if __name__ == "__main__":
    unittest.main()
