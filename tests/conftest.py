from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from alembic import command
from app.config import Settings, get_settings
from app.database import build_engine
from app.main import create_app
from app.models import CentreTest, DiagnosticCentre, DiagnosticTest, User
from app.security import create_access_token, hash_password

ROOT = Path(__file__).resolve().parents[1]
TEST_SECRET = "public-test-secret-used-only-in-isolated-tests-" + "a" * 32


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{(tmp_path / 'foundation.sqlite').as_posix()}",
        jwt_secret=TEST_SECRET,
        webhook_secret=TEST_SECRET,
        environment="test",
        rate_limit_enabled=False,
    )


def migration_config(connection) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.attributes["connection"] = connection
    return config


@pytest.fixture
def engine(settings) -> Generator[Engine, None, None]:
    engine = build_engine(settings.database_url)
    with engine.begin() as connection:
        command.upgrade(migration_config(connection), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine) -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


@pytest.fixture
def application(settings, engine):
    return create_app(settings)


@pytest.fixture
def client(application) -> Generator[TestClient, None, None]:
    with TestClient(application) as client:
        yield client


@pytest.fixture
def user_factory(session, settings):
    def create(email="patient@example.com"):
        user = User(email=email, password_hash=hash_password("test-password"))
        session.add(user)
        session.commit()
        token = create_access_token(user.id, settings)
        return user, {"Authorization": f"Bearer {token}"}

    return create


@pytest.fixture
def account(user_factory):
    return user_factory()


@pytest.fixture
def other_account(user_factory):
    return user_factory("other@example.com")


@pytest.fixture
def offering(session):
    offering = CentreTest(
        centre=DiagnosticCentre(name="EVE Centre", location="Delhi"),
        test=DiagnosticTest(name="Blood count"),
        price=Decimal("1250.50"),
    )
    session.add(offering)
    session.commit()
    return offering


@pytest.fixture
def booking(client, account, offering):
    response = client.post(
        "/bookings",
        headers=account[1],
        json={
            "centre_test_id": offering.id,
            "appointment_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def provider_headers(settings):
    return {"X-Webhook-Secret": settings.webhook_secret.get_secret_value()}
