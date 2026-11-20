from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RobokassaSettings, Staff
from app.services.robokassa import HASH_ALGORITHMS, empty_robokassa_config
from app.services.secret_box import SecretBoxError, decrypt_secret, encrypt_secret, key_configured
from panel.audit import write_audit
from panel.auth import csrf_guard, require_perm
from panel.deps import get_session
from panel.permissions import Perm

router = APIRouter(prefix="/api/robokassa", tags=["robokassa"], dependencies=[Depends(csrf_guard)])
manage = require_perm(Perm.ROBOKASSA_MANAGE)


def _response(row: RobokassaSettings | None) -> dict:
    defaults = empty_robokassa_config()
    password1_set = bool(row.password1_enc) if row is not None else bool(defaults.password1)
    password2_set = bool(row.password2_enc) if row is not None else bool(defaults.password2)
    shop_id = row.shop_id if row is not None else defaults.shop_id
    return {
        "shop_id": shop_id,
        "hash_algorithm": row.hash_algorithm if row is not None else defaults.hash_algorithm,
        "test": row.is_test if row is not None else defaults.test,
        "password1_set": password1_set,
        "password2_set": password2_set,
        "configured": bool(shop_id.strip() and password1_set and password2_set),
        "encryption_configured": key_configured(),
        "source": "database" if row is not None else "unset",
        "updated_at": row.updated_at.isoformat() if row is not None and row.updated_at else None,
    }


@router.get("")
async def get_robokassa_settings(
    _: Staff = Depends(manage), session: AsyncSession = Depends(get_session)
):
    return _response(await session.get(RobokassaSettings, 1))


class SettingsBody(BaseModel):
    shop_id: str = Field(min_length=1, max_length=64)
    password1: str = Field(default="", max_length=256)
    password2: str = Field(default="", max_length=256)
    hash_algorithm: str = Field(default="MD5", max_length=16)
    test: bool = False


@router.put("")
async def set_robokassa_settings(
    body: SettingsBody,
    me: Staff = Depends(manage),
    session: AsyncSession = Depends(get_session),
):
    shop_id = body.shop_id.strip()
    algorithm = body.hash_algorithm.upper().replace("-", "").strip()
    if not shop_id:
        raise HTTPException(422, "Укажите идентификатор магазина")
    if algorithm not in HASH_ALGORITHMS:
        raise HTTPException(422, "Поддерживаются алгоритмы MD5, SHA1, SHA256 и SHA512")
    if not key_configured():
        raise HTTPException(
            503,
            "WEB_SESSION_SECRET не задан. Укажите одинаковый секрет для процессов bot и site.",
        )

    row = await session.get(RobokassaSettings, 1, with_for_update=True)
    defaults = empty_robokassa_config()
    try:
        if body.password1.strip():
            password1 = body.password1.strip()
        elif row is not None:
            password1 = decrypt_secret(row.password1_enc) if row.password1_enc else ""
        else:
            password1 = defaults.password1
        if body.password2.strip():
            password2 = body.password2.strip()
        elif row is not None:
            password2 = decrypt_secret(row.password2_enc) if row.password2_enc else ""
        else:
            password2 = defaults.password2
    except SecretBoxError:
        raise HTTPException(
            409,
            "Сохранённые пароли нельзя расшифровать. Введите оба пароля заново.",
        ) from None
    if not password1 or not password2:
        raise HTTPException(422, "Введите оба пароля Robokassa")

    if row is None:
        row = RobokassaSettings(id=1)
        session.add(row)
    row.shop_id = shop_id
    row.password1_enc = encrypt_secret(password1)
    row.password2_enc = encrypt_secret(password2)
    row.hash_algorithm = algorithm
    row.is_test = body.test
    row.updated_by = me.telegram_id
    await write_audit(
        session,
        me,
        "robokassa.update",
        None,
        {"shop_id": shop_id, "hash_algorithm": algorithm, "test": body.test},
    )
    await session.commit()
    await session.refresh(row)
    return _response(row)
