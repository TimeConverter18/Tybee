from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Used ONLY to create covers/default/config.yaml on first start; afterwards
    # every cover (including "default") takes its token from its config.yaml.
    bot_token: str = Field(
        default="", validation_alias=AliasChoices("TYBEE_BOT_TOKEN", "TAIBI_BOT_TOKEN", "BOT_TOKEN")
    )

    unban_fine_rub: int = 50
    profile_orders_limit: int = Field(default=10, ge=1)
    support_shift_offline_minutes: int = Field(default=3, ge=1)
    support_shift_idle_minutes: int = Field(default=15, ge=1)
    support_shift_no_reply_minutes: int = Field(default=10, ge=1)

    # Multi-cover setup: every cover is a folder under covers_root with its own
    # config.yaml (token, texts, buttons, statuses) and banners/.
    covers_root: str = "./covers"
    # Cover whose bot is used for service work shared by all covers
    # (e.g. downloading legacy product photos).
    support_cover_slug: str = "default"
    covers_reload_sec: int = 10
    # Runtime data (product photos) kept on disk, mounted as a volume.
    data_dir: str = "./data"

    # Web panel (site/). The login bot is a separate bot used only for the
    # Telegram Login Widget; it does not need polling.
    web_port: int = 8000
    web_admin_ids_raw: str = Field(default="", validation_alias="WEB_ADMIN_IDS")
    web_login_bot_token: str = ""
    web_login_bot_username: str = ""
    web_session_secret: str = ""
    web_public_url: str = ""
    web_dev_login: bool = Field(default=False, validation_alias=AliasChoices("WEB_DEV_LOGIN", "DEV"))
    web_cookie_secure: bool = False
    web_forwarded_allow_ips: str = Field(default="127.0.0.1,::1", validation_alias="FORWARDED_ALLOW_IPS")

    avito_webhook_port: int = 8080
    webhook_trusted_proxy_ips: str = ""
    # Integration secrets are managed on the site and encrypted with WEB_SESSION_SECRET.

    postgres_host: str = "db"
    postgres_port: int = 5432
    postgres_db: str = "taibi"
    postgres_user: str = "taibi"
    postgres_password: str = "taibi"

    @field_validator("web_dev_login", "web_cookie_secure", mode="before")
    @classmethod
    def _parse_optional_bool(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return False
        return value

    @property
    def web_admin_ids(self) -> list[int]:
        return [
            int(part)
            for part in self.web_admin_ids_raw.replace(";", ",").split(",")
            if part.strip()
        ]

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
