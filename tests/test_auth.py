from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import func, select

from app.models import User
from app.security import verify_password


def test_signup_login_and_normalization(client, session):
    payload = {"email": " Patient@Example.COM ", "password": "test-password"}
    response = client.post("/auth/signup", json=payload)
    assert response.status_code == 201
    assert response.json() == {"id": 1, "email": "patient@example.com"}
    user = session.scalar(select(User))
    assert verify_password("test-password", user.password_hash)
    assert "password" not in response.text
    duplicate = client.post("/auth/signup", json=payload)
    assert duplicate.status_code == 409
    assert session.scalar(select(func.count()).select_from(User)) == 1
    login = client.post("/auth/login", json=payload)
    assert login.status_code == 200
    assert login.json()["token_type"] == "bearer"
    token = login.json()["access_token"]
    assert client.get("/bookings", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_bad_credentials_are_generic(client, account):
    wrong = client.post(
        "/auth/login",
        json={
            "email": account[0].email,
            "password": "wrong-password",
        },
    )
    missing = client.post(
        "/auth/login",
        json={
            "email": "missing@example.com",
            "password": "wrong-password",
        },
    )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json()["error"] == missing.json()["error"]


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "bad-address", "password": "test-password"},
        {"email": "patient@example.com", "password": "short"},
        {"email": "patient@example.com", "password": "a" * 129},
        {"email": "patient@example.com", "password": "test-password", "id": 42},
    ],
)
def test_invalid_signup(client, session, payload):
    assert client.post("/auth/signup", json=payload).status_code == 422
    assert session.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.parametrize("authorization", [None, "Bearer malformed", "Basic password"])
def test_missing_or_malformed_auth(client, authorization):
    headers = {"Authorization": authorization} if authorization else {}
    assert client.get("/bookings", headers=headers).status_code == 401


def test_expired_token(client, account, settings):
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(account[0].id),
            "iat": now - timedelta(hours=1),
            "exp": now - timedelta(seconds=1),
            "type": "access",
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    assert client.get("/bookings", headers={"Authorization": f"Bearer {token}"}).status_code == 401
