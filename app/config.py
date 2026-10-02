from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    database_url: str
    jwt_secret: SecretStr
    webhook_secret: SecretStr
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_expire_minutes: int = 30
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    auth_rate_limit: int = Field(default=5, ge=1, le=10000)
    payment_rate_limit: int = Field(default=20, ge=1, le=10000)
    webhook_rate_limit: int = Field(default=120, ge=1, le=10000)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        try:
            url = make_url(value)
        except ArgumentError as exc:
            raise ValueError("DATABASE_URL must be a valid SQLAlchemy URL") from exc
        if url.drivername not in {"postgresql+psycopg", "sqlite"}:
            raise ValueError("Use postgresql+psycopg (or SQLite for isolated tests)")
        return value

    @field_validator("jwt_secret", "webhook_secret")
    @classmethod
    def validate_jwt_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret) < 32 or secret.startswith("replace-"):
            raise ValueError("Secrets must be generated and contain at least 32 characters")
        return value

    @field_validator("access_token_expire_minutes")
    @classmethod
    def validate_token_expiry(cls, value: int) -> int:
        if not 1 <= value <= 1440:
            raise ValueError("Token expiry must be between 1 and 1440 minutes")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
