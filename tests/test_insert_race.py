from __future__ import annotations

import unittest

from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import repositories as repo
from app.db.base import Base
from app.db.models import Cover, User, UserCover

RACER_ID = 42
COVER_ID = 1


class RacingSession(AsyncSession):
    miss_entity = None
    miss_ident = None

    async def get(self, entity, ident, **kwargs):
        if self.miss_entity is entity and self.miss_ident == ident:
            self.miss_entity = None
            self.miss_ident = None
            return None
        return await super().get(entity, ident, **kwargs)


class InsertRaceTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        self.factory = async_sessionmaker(self.engine, class_=RacingSession, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.factory() as session:
            session.add(Cover(id=COVER_ID, slug="default", path="/covers/default", enabled=True))
            session.add(User(id=RACER_ID, username="racer", full_name="Racer"))
            session.add(UserCover(user_id=RACER_ID, cover_id=COVER_ID, state="menu"))
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_lost_user_race_keeps_loaded_cover_usable(self) -> None:
        async with self.factory() as session:
            cover = await repo.get_cover(session, COVER_ID)
            self.assertIsNotNone(cover)
            session.miss_entity = User
            session.miss_ident = RACER_ID

            user = await repo.get_or_create_user(
                session, TgUser(id=RACER_ID, is_bot=False, first_name="Racer")
            )

            self.assertEqual(user.id, RACER_ID)
            self.assertEqual(cover.id, COVER_ID)
            self.assertEqual(cover.slug, "default")

    async def test_lost_user_cover_race_keeps_loaded_cover_usable(self) -> None:
        async with self.factory() as session:
            cover = await repo.get_cover(session, COVER_ID)
            user = await repo.get_user(session, RACER_ID)
            self.assertIsNotNone(user)
            session.miss_entity = UserCover
            session.miss_ident = (RACER_ID, COVER_ID)

            uc = await repo.get_or_create_user_cover(session, user, COVER_ID)

            self.assertEqual(uc.user_id, RACER_ID)
            self.assertEqual(uc.cover_id, COVER_ID)
            self.assertEqual(cover.id, COVER_ID)
            self.assertEqual(cover.slug, "default")


if __name__ == "__main__":
    unittest.main()
