import json
import logging
from io import StringIO
from typing import Annotated

from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy.exc import OperationalError

from app.models import User
from app.security import create_access_token, get_current_user, hash_password


def test_health_and_request_id(client):
    response = client.get("/health", headers={"X-Request-ID": "test-request-123"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"] == "test-request-123"


def test_404_error_envelope(client):
    response = client.get("/missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_404"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_validation_does_not_echo_password(application, client):
    class Payload(BaseModel):
        password: str = Field(min_length=20)

    @application.post("/_test/validation")
    def validate(payload: Payload):
        return {"ok": True}

    response = client.post("/_test/validation", json={"password": "private-password"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "private-password" not in response.text


def test_database_errors_are_sanitized(application, client):
    @application.get("/_test/database-error")
    def fail():
        raise OperationalError(
            "sensitive SQL", {"password": "private"}, Exception("database detail")
        )

    response = client.get("/_test/database-error")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "sensitive" not in response.text
    assert "private" not in response.text


def test_unexpected_errors_are_sanitized_and_correlated(application):
    @application.get("/_test/unexpected-error")
    def fail():
        raise RuntimeError("private internal detail")

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/_test/unexpected-error", headers={"X-Request-ID": "error-check"})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert response.json()["request_id"] == "error-check"
    assert response.headers["X-Request-ID"] == "error-check"
    assert "private" not in response.text


def test_request_log_excludes_query_and_authorization(monkeypatch, client):
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("eve")
    handler.setFormatter(logger.handlers[0].formatter)
    monkeypatch.setattr(logger, "handlers", [handler])
    response = client.get(
        "/health?password=sensitive-input",
        headers={"Authorization": "Bearer sensitive-token", "X-Request-ID": "logging-check"},
    )
    assert response.status_code == 200
    log_output = stream.getvalue()
    entry = json.loads(log_output)
    assert entry["event"] == "request_completed"
    assert entry["request_id"] == "logging-check"
    assert entry["route"] == "/health"
    assert "sensitive" not in log_output


def test_current_user_dependency(application, client, session, settings):
    user = User(email=" Patient@Example.com ", password_hash=hash_password("test-password"))
    session.add(user)
    session.commit()

    @application.get("/_test/me")
    def current_user(user: Annotated[User, Depends(get_current_user)]):
        return {"id": user.id}

    assert client.get("/_test/me").status_code == 401
    token = create_access_token(user.id, settings)
    response = client.get("/_test/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {"id": user.id}
    missing_user_token = create_access_token(user.id + 1, settings)
    assert (
        client.get(
            "/_test/me", headers={"Authorization": f"Bearer {missing_user_token}"}
        ).status_code
        == 401
    )
