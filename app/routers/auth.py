from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator

from app import security
from app.database import DbSession
from app.exceptions import ErrorResponse
from app.models import normalize_email

router = APIRouter(
    prefix="/auth",
    tags=["authentication"],
    responses={
        429: {"model": ErrorResponse, "description": "Rate limit exceeded; see Retry-After"},
    },
)


class Credentials(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"email": "patient@example.com", "password": "example-password"}]
        },
    )
    email: EmailStr = Field(max_length=254)
    password: SecretStr = Field(min_length=8, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalized_email(cls, value):
        return normalize_email(value) if isinstance(value, str) else value

    @field_validator("password")
    @classmethod
    def nonblank_password(cls, value: SecretStr):
        if not value.get_secret_value().strip():
            raise ValueError("Password cannot be blank")
        return value


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post(
    "/signup",
    response_model=UserResponse,
    status_code=201,
    summary="Register a patient account",
    responses={409: {"model": ErrorResponse, "description": "Email already registered"}},
)
def signup(payload: Credentials, session: DbSession):
    return security.signup(session, str(payload.email), payload.password.get_secret_value())


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Get a bearer access token",
    responses={401: {"model": ErrorResponse, "description": "Invalid credentials"}},
)
def login(payload: Credentials, session: DbSession, request: Request):
    token = security.login(
        session,
        str(payload.email),
        payload.password.get_secret_value(),
        request.app.state.settings,
    )
    return TokenResponse(access_token=token)
