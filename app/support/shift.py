"""Support shifts: start/end, presence heartbeat and the automatic end.

Shifts track presence and hours, but ending a shift never releases assigned
support threads (dialogs in Tybee live for days or months).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Staff, StaffShift, SupportThread
from app.support.common import now

_settings = get_settings()
OFFLINE_AFTER = timedelta(minutes=_settings.support_shift_offline_minutes)
IDLE_AFTER = timedelta(minutes=_settings.support_shift_idle_minutes)
NO_REPLY_AFTER = timedelta(minutes=_settings.support_shift_no_reply_minutes)

MANUAL = "manual"
LOGOUT = "logout"
OFFLINE = "offline"
IDLE = "idle"
NO_REPLY = "no_reply"
DEACTIVATED = "deactivated"
AUTO_REASONS = (OFFLINE, IDLE, NO_REPLY)

REASON_LABELS = {
    MANUAL: "завершена вручную",
    LOGOUT: "выход из панели",
    OFFLINE: "панель закрыта",
    IDLE: f"нет активности {int(IDLE_AFTER.total_seconds() // 60)} мин",
    NO_REPLY: f"клиент ждал ответа {int(NO_REPLY_AFTER.total_seconds() // 60)} мин",
    DEACTIVATED: "сотрудник отключён",
}


async def open_shift(session: AsyncSession, staff_id: int) -> StaffShift | None:
    return await session.scalar(
        select(StaffShift)
        .where(StaffShift.staff_id == staff_id, StaffShift.ended_at.is_(None))
        .order_by(StaffShift.started_at.desc())
        .limit(1)
    )


async def last_shift(session: AsyncSession, staff_id: int) -> StaffShift | None:
    return await session.scalar(
        select(StaffShift).where(StaffShift.staff_id == staff_id).order_by(StaffShift.id.desc()).limit(1)
    )


def touch(staff: Staff, active: bool, at: datetime | None = None) -> None:
    at = at or now()
    staff.last_seen_at = at
    if active:
        staff.last_active_at = at


async def start_shift(session: AsyncSession, staff: Staff) -> bool:
    """False when the shift is already running."""
    staff = await session.scalar(select(Staff).where(Staff.telegram_id == staff.telegram_id).with_for_update().execution_options(populate_existing=True))
    if staff is None:
        return False
    if await open_shift(session, staff.telegram_id) is not None:
        return False
    at = now()
    staff.on_shift = True
    touch(staff, True, at)
    session.add(StaffShift(staff_id=staff.telegram_id, started_at=at))
    await session.flush()
    return True


async def end_shift(session: AsyncSession, staff: Staff, reason: str) -> bool:
    """False when the employee was not on shift. Assigned threads stay with the employee."""
    staff = await session.scalar(select(Staff).where(Staff.telegram_id == staff.telegram_id).with_for_update().execution_options(populate_existing=True))
    if staff is None:
        return False
    rows = list(await session.scalars(select(StaffShift).where(StaffShift.staff_id == staff.telegram_id, StaffShift.ended_at.is_(None))))
    if not staff.on_shift and not rows:
        return False
    staff.on_shift = False
    for row in rows:
        row.ended_at = now()
        row.end_reason = reason
    if reason == DEACTIVATED:
        await release_waiting(session, staff.telegram_id)
    await session.flush()
    return True


async def release_waiting(session: AsyncSession, staff_id: int) -> int:
    """Return unanswered threads and unfinished next steps to the queue (used on deactivation)."""
    result = await session.execute(
        update(SupportThread)
        .where(SupportThread.assigned_to == staff_id, SupportThread.status == "open", or_(SupportThread.needs_reply.is_(True), SupportThread.next_step.is_not(None)))
        .values(assigned_to=None, assigned_at=None)
    )
    return result.rowcount or 0


@dataclass
class AutoEnd:
    staff: Staff
    reason: str
    released: int


async def sweep(session: AsyncSession, at: datetime | None = None) -> list[AutoEnd]:
    """End shifts of employees who are not really there without unassigning their threads."""
    at = at or now()
    on_shift = Staff.on_shift.is_(True)
    found: dict[int, tuple[Staff, str]] = {}

    offline = await session.scalars(
        select(Staff).where(on_shift, or_(Staff.last_seen_at.is_(None), Staff.last_seen_at < at - OFFLINE_AFTER))
    )
    for s in offline:
        found.setdefault(s.telegram_id, (s, OFFLINE))

    idle = await session.scalars(
        select(Staff).where(on_shift, or_(Staff.last_active_at.is_(None), Staff.last_active_at < at - IDLE_AFTER))
    )
    for s in idle:
        found.setdefault(s.telegram_id, (s, IDLE))

    ended: list[AutoEnd] = []
    for staff, reason in found.values():
        await end_shift(session, staff, reason)
        ended.append(AutoEnd(staff, reason, 0))
    return ended
