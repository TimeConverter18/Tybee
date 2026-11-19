from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "site" / "backend"))

from app.db.base import Base
from app.db.models import Product, Staff
from panel.routes import catalog as catalog_routes


class CatalogCleanupTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_deleting_product_removes_photo_after_commit(self) -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with factory() as session:
                session.add(Staff(telegram_id=999, display_name="Admin", role="admin"))
                session.add(Product(id=1, article="123456", title="Product", price=100))
                await session.commit()

                with tempfile.TemporaryDirectory() as directory:
                    photo = Path(directory) / "1"
                    photo.write_bytes(b"image")
                    admin = await session.get(Staff, 999)
                    with patch.object(catalog_routes, "product_photo_path", return_value=photo):
                        result = await catalog_routes.delete_product(1, admin, session)
                    self.assertEqual(result, {"ok": True})
                    self.assertFalse(photo.exists())
                    self.assertIsNone(await session.get(Product, 1))
        finally:
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
