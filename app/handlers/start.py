from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Cover, SupportThread, User, UserCover
from app.services.screens import show_menu
from app.services.support_transport import queue_support_message

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(
    message: Message, bot: Bot, session: AsyncSession, user: User, uc: UserCover
) -> None:
    existing_thread = await session.scalar(
        select(SupportThread.id).where(SupportThread.user_id == user.id)
    )
    if existing_thread is None:
        cover = await session.get(Cover, uc.cover_id) if uc.cover_id else None
        await queue_support_message(
            session,
            user,
            "Новый потенциальный клиент запустил бота (/start)",
            kind="new_client",
            external_id=f"start:{user.id}",
            cover=cover,
        )
    await show_menu(bot, session, user, uc, force_new=True)


@router.callback_query(F.data == "menu:open")
async def open_menu(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, uc: UserCover
) -> None:
    await call.answer()  # stop the button spinner before the slow screen edit
    await show_menu(bot, session, user, uc)


@router.callback_query(F.data == "back:menu")
async def back_menu(
    call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, uc: UserCover
) -> None:
    await call.answer()  # stop the button spinner before the slow screen edit
    """«Назад» to the main menu (previous step for the order-choice screen)."""
    await show_menu(bot, session, user, uc)
