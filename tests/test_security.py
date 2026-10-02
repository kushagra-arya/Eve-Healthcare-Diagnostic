from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.exceptions import AppError
from app.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_argon2_hash_and_verification():
    password_hash = hash_password("test-password")
    assert password_hash.startswith("$argon2id$")
    assert password_hash != "test-password"
    assert verify_password("test-password", password_hash)
    assert not verify_password("wrong-password", password_hash)
    assert not verify_password("test-password", "corrupted-hash")


def test_access_token_round_trip(settings):
    token = create_access_token(42, settings)
    assert decode_access_token(token, settings) == 42


@pytest.mark.parametrize(
    "case", ["expired", "wrong_secret", "wrong_algorithm", "missing_exp", "bad_sub"]
)
def test_invalid_tokens_rejected(settings, case):
    now = datetime.now(UTC)
    payload = {"sub": "42", "iat": now, "exp": now + timedelta(minutes=5), "type": "access"}
    secret = settings.jwt_secret.get_secret_value()
    algorithm = "HS256"
    if case == "expired":
        payload["exp"] = now - timedelta(seconds=1)
    elif case == "wrong_secret":
        secret = "different-secret-with-at-least-32-characters"
    elif case == "wrong_algorithm":
        algorithm = "HS512"
    elif case == "missing_exp":
        payload.pop("exp")
    elif case == "bad_sub":
        payload["sub"] = "-1"
    token = jwt.encode(payload, secret, algorithm=algorithm)
    with pytest.raises(AppError) as exc:
        decode_access_token(token, settings)
    assert exc.value.status_code == 401
