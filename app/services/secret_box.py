"""Encryption of integration secrets at rest using WEB_SESSION_SECRET."""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class SecretBoxError(RuntimeError):
    """WEB_SESSION_SECRET is missing or a stored secret cannot be decrypted."""


def _fernet() -> Fernet:
    secret = get_settings().web_session_secret.strip()
    if not secret:
        raise SecretBoxError(
            "WEB_SESSION_SECRET не задан: секреты интеграций нельзя сохранять и использовать"
        )
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def key_configured() -> bool:
    try:
        _fernet()
    except SecretBoxError:
        return False
    return True


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise SecretBoxError(
            "не удалось расшифровать сохранённый секрет (сменился WEB_SESSION_SECRET?)"
        ) from exc


def webhook_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()
