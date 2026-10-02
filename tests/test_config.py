import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_are_environment_driven(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://eve:eve@localhost/eve_test")
    monkeypatch.setenv("JWT_SECRET", "a-generated-secret-longer-than-32-characters")
    monkeypatch.setenv("WEBHOOK_SECRET", "a-different-secret-longer-than-32-characters")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15")
    settings = Settings(_env_file=None)
    assert settings.access_token_expire_minutes == 15
    assert settings.database_url.startswith("postgresql+psycopg:")
    assert "a-generated-secret" not in repr(settings)
    monkeypatch.setenv("AUTH_RATE_LIMIT", "3")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    settings = Settings(_env_file=None)
    assert settings.auth_rate_limit == 3
    assert settings.rate_limit_enabled is False


@pytest.mark.parametrize(
    "secret", ["short", "replace-with-a-random-secret-of-at-least-32-characters"]
)
def test_unsafe_secrets_rejected(secret):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None, database_url="sqlite://", jwt_secret=secret, webhook_secret="b" * 48
        )


def test_test_settings_ignore_private_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DATABASE_URL=invalid\nJWT_SECRET=private\n")
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        jwt_secret="a" * 48,
        webhook_secret="b" * 48,
        environment="test",
    )
    assert settings.database_url == "sqlite://"


@pytest.mark.parametrize("expiry", [0, 1441])
def test_expiry_is_bounded(expiry):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="sqlite://",
            jwt_secret="a" * 48,
            webhook_secret="b" * 48,
            access_token_expire_minutes=expiry,
        )


def test_malformed_database_url_is_a_validation_error():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None, database_url="invalid", jwt_secret="a" * 48, webhook_secret="b" * 48
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("auth_rate_limit", 0),
        ("payment_rate_limit", -1),
        ("webhook_rate_limit", 10001),
        ("rate_limit_window_seconds", 0),
        ("rate_limit_window_seconds", 3601),
    ],
)
def test_rate_limit_settings_are_bounded(field, value):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="sqlite://",
            jwt_secret="a" * 48,
            webhook_secret="b" * 48,
            **{field: value},
        )
