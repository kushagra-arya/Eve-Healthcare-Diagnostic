import logging
from datetime import UTC, datetime, timedelta
from hmac import compare_digest
from typing import Annotated

import jwt
from fastapi import Depends, Request
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import DbSession
from app.exceptions import AppError
from app.models import User, normalize_email

password_hasher = PasswordHash.recommended()
logger = logging.getLogger("eve.security")
bearer = HTTPBearer(auto_error=False)
provider_secret_header = APIKeyHeader(name="X-Webhook-Secret", auto_error=False)
# Verify a real hash even for unknown accounts so login follows the same hashing path.
dummy_password_hash = password_hasher.hash("unused-dummy-login-password")


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password, password_hash)
    except (UnknownHashError, ValueError):
        return False


def create_access_token(user_id: int, settings: Settings) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
            "type": "access",
        },
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str, settings: Settings) -> int:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp", "iat", "type"]},
        )
        subject = payload["sub"]
        if payload["type"] != "access" or not subject.isascii() or not subject.isdecimal():
            raise ValueError("Invalid access token")
        user_id = int(subject)
        if not 1 <= user_id <= 2_147_483_647:
            raise ValueError("Invalid user ID")
        return user_id
    except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError) as exc:
        raise AppError(401, "unauthenticated", "Invalid or expired access token") from exc


def get_current_user(
    request: Request,
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    if credentials is None:
        raise AppError(401, "unauthenticated", "Authentication required")
    user_id = decode_access_token(credentials.credentials, request.app.state.settings)
    user = session.get(User, user_id)
    if user is None:
        raise AppError(401, "unauthenticated", "Invalid or expired access token")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def signup(session: Session, email: str, password: str) -> User:
    user = User(email=normalize_email(email), password_hash=hash_password(password))
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise AppError(409, "duplicate_email", "An account with this email already exists") from exc
    logger.info("user_registered", extra={"fields": {"user_id": user.id}})
    return user


def login(session: Session, email: str, password: str, settings: Settings) -> str:
    user = session.scalar(select(User).where(User.email == normalize_email(email)))
    valid = verify_password(password, user.password_hash if user else dummy_password_hash)
    if not valid or user is None:
        raise AppError(401, "invalid_credentials", "Invalid email or password")
    return create_access_token(user.id, settings)


def require_provider(
    request: Request,
    supplied_secret: Annotated[str | None, Depends(provider_secret_header)],
) -> None:
    expected = request.app.state.settings.webhook_secret.get_secret_value()
    if supplied_secret is None or not compare_digest(supplied_secret.encode(), expected.encode()):
        raise AppError(403, "forbidden", "Invalid webhook credentials")
