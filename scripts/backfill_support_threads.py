"""One-off idempotent backfill: create SupportThread for every existing client/order.

Run inside the backend/bot container:
    python3 -m scripts.backfill_support_threads
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, select

from app.db.base import init_models, session_factory
from app.db.models import Order, SupportConversation, SupportThread, User, UserCover
from app.support.common import now

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


async def backfill() -> tuple[int, int]:
    await init_models()
    created_threads = 0
    created_convs = 0
    async with session_factory() as session:
        existing_user_ids = set(await session.scalars(select(SupportThread.user_id)))
        users = list(await session.scalars(select(User).order_by(User.id)))

        # Latest cover per user
        cover_rows = (
            await session.execute(
                select(UserCover.user_id, UserCover.cover_id, UserCover.last_seen_at)
                .order_by(UserCover.user_id, UserCover.last_seen_at.desc().nulls_last())
            )
        ).all()
        latest_cover: dict[int, tuple[int | None, object]] = {}
        for uid, cid, seen in cover_rows:
            if uid not in latest_cover:
                latest_cover[uid] = (cid, seen)

        # Latest order timestamp and cover per user
        order_rows = (
            await session.execute(
                select(
                    Order.user_id,
                    func.max(Order.created_at).label("last_order_at"),
                    func.max(Order.cover_id).label("order_cover_id"),
                ).group_by(Order.user_id)
            )
        ).all()
        order_meta = {int(uid): (last_at, cid) for uid, last_at, cid in order_rows}

        existing_conv_users = set(
            await session.scalars(
                select(SupportConversation.user_id).where(SupportConversation.status == "open")
            )
        )

        current = now()
        for user in users:
            if user.id in existing_user_ids:
                continue
            cov_id, seen_at = latest_cover.get(user.id, (None, None))
            ord_at, ord_cov = order_meta.get(user.id, (None, None))
            last_at = ord_at or seen_at or user.created_at or current
            thread = SupportThread(
                user_id=user.id,
                channel=user.source or "telegram",
                status="open",
                assigned_to=None,
                last_cover_id=cov_id or ord_cov,
                last_message_at=last_at,
                unread=0,
                needs_reply=False,
                created_at=user.created_at or current,
            )
            session.add(thread)
            created_threads += 1
            if user.id not in existing_conv_users:
                session.add(SupportConversation(user_id=user.id, channel=user.source or "telegram", status="open"))
                existing_conv_users.add(user.id)
                created_convs += 1

        await session.commit()
    logger.info(
        "Backfill complete: created %d SupportThread rows and %d SupportConversation rows (total users checked: %d)",
        created_threads,
        created_convs,
        len(users),
    )
    return created_threads, created_convs


if __name__ == "__main__":
    asyncio.run(backfill())
