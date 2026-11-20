from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Order, RobokassaPayment, RobokassaSettings, User
from app.services.secret_box import SecretBoxError, decrypt_secret

logger = logging.getLogger(__name__)

_PAYMENT_URL = "https://auth.robokassa.ru/Merchant/Index.aspx"
HASH_ALGORITHMS = {
    "MD5": "md5",
    "SHA1": "sha1",
    "SHA256": "sha256",
    "SHA512": "sha512",
}


@dataclass(frozen=True)
class RobokassaConfig:
    shop_id: str
    password1: str
    password2: str
    hash_algorithm: str
    test: bool

    @property
    def ready(self) -> bool:
        return bool(self.shop_id and self.password1 and self.password2)


def empty_robokassa_config() -> RobokassaConfig:
    return RobokassaConfig(
        shop_id="",
        password1="",
        password2="",
        hash_algorithm="MD5",
        test=False,
    )


async def load_robokassa_config(session: AsyncSession) -> RobokassaConfig:
    row = await session.get(RobokassaSettings, 1)
    if row is None:
        return empty_robokassa_config()
    try:
        password1 = decrypt_secret(row.password1_enc) if row.password1_enc else ""
        password2 = decrypt_secret(row.password2_enc) if row.password2_enc else ""
    except SecretBoxError as exc:
        logger.error("Could not decrypt Robokassa settings: %s", exc)
        password1 = password2 = ""
    return RobokassaConfig(
        shop_id=row.shop_id.strip(),
        password1=password1,
        password2=password2,
        hash_algorithm=row.hash_algorithm.strip(),
        test=row.is_test,
    )


def _hash_name(config: RobokassaConfig) -> str:
    normalized = config.hash_algorithm.upper().replace("-", "")
    try:
        return HASH_ALGORITHMS[normalized]
    except KeyError as exc:
        raise RuntimeError(f"Unsupported Robokassa hash algorithm: {normalized}") from exc


def _signature(value: str, config: RobokassaConfig) -> str:
    return hashlib.new(_hash_name(config), value.encode("utf-8")).hexdigest().upper()


def build_payment_link(
    invoice_id: int,
    amount: int | None,
    description: str,
    *,
    config: RobokassaConfig | None = None,
) -> str | None:
    config = config or empty_robokassa_config()
    if amount is None or amount <= 0 or not config.ready:
        return None
    out_sum = f"{amount:.2f}"
    invoice = str(invoice_id)
    signature = _signature(
        f"{config.shop_id}:{out_sum}:{invoice}:{config.password1}", config
    )
    query = {
        "MerchantLogin": config.shop_id,
        "OutSum": out_sum,
        "InvId": invoice,
        "Description": description[:100],
        "SignatureValue": signature,
        "Culture": "ru",
    }
    if config.test:
        query["IsTest"] = "1"
    return f"{_PAYMENT_URL}?{urlencode(query)}"


async def payment_link(session: AsyncSession, order: Order) -> str | None:
    config = await load_robokassa_config(session)
    return build_payment_link(
        order.id, order.price, order.title or f"Заказ #{order.id}", config=config
    )


def valid_result_signature(
    out_sum: str,
    inv_id: str,
    signature: str,
    *,
    config: RobokassaConfig,
) -> bool:
    expected = _signature(f"{out_sum}:{inv_id}:{config.password2}", config)
    return hmac.compare_digest(expected, signature.upper())


async def create_unban_payment(
    session: AsyncSession, user: User, *, cover_id: int | None = None, amount: int | None = None
) -> RobokassaPayment:
    await session.scalar(select(User).where(User.id == user.id).with_for_update())
    existing = await session.scalar(
        select(RobokassaPayment)
        .where(
            RobokassaPayment.user_id == user.id,
            RobokassaPayment.status == "pending",
        )
        .order_by(RobokassaPayment.id.desc())
        .limit(1)
    )
    if existing is not None:
        if cover_id is not None:
            existing.cover_id = cover_id
        return existing
    while True:
        invoice_id = secrets.randbelow(100_000_000) + 2_000_000_001
        if await session.get(RobokassaPayment, invoice_id) is not None:
            continue
        if await session.get(Order, invoice_id) is None:
            break
    payment = RobokassaPayment(
        id=invoice_id,
        user_id=user.id,
        amount=amount if amount is not None else get_settings().unban_fine_rub,
        cover_id=cover_id,
    )
    session.add(payment)
    await session.flush()
    return payment
