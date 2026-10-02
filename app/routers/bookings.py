from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Path
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app import bookings
from app.database import DbSession
from app.exceptions import ErrorResponse
from app.models import Booking, BookingStatus
from app.pagination import Page, PageQuery, paginate
from app.security import CurrentUser

router = APIRouter(
    prefix="/bookings",
    tags=["bookings"],
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid bearer token"},
    },
)
BookingId = Annotated[int, Path(gt=0, le=2_147_483_647)]


class BookingInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"centre_test_id": 1, "appointment_at": "2099-10-05T11:30:00+05:30"}]
        },
    )
    centre_test_id: int = Field(gt=0, le=2_147_483_647, strict=True)
    appointment_at: AwareDatetime

    @field_validator("appointment_at", mode="before")
    @classmethod
    def iso_timestamp(cls, value):
        if not isinstance(value, str):
            raise ValueError("Use an ISO 8601 timestamp with timezone")
        return datetime.fromisoformat(value)


class BookingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    centre_test_id: int
    appointment_at: datetime
    amount: Decimal
    status: BookingStatus

    @field_validator("appointment_at")
    @classmethod
    def utc_appointment(cls, value: datetime) -> datetime:
        # SQLite test storage drops offsets; the application only writes UTC.
        return value.replace(tzinfo=UTC) if value.utcoffset() is None else value.astimezone(UTC)


@router.post(
    "",
    response_model=BookingResponse,
    status_code=201,
    summary="Book a centre's test at its current price",
    responses={404: {"model": ErrorResponse, "description": "Centre test offering not found"}},
)
def create_booking(payload: BookingInput, session: DbSession, user: CurrentUser):
    return bookings.create_booking(
        session,
        user.id,
        payload.centre_test_id,
        payload.appointment_at,
    )


@router.get("", response_model=Page[BookingResponse], summary="List your bookings")
def list_bookings(session: DbSession, user: CurrentUser, pagination: PageQuery):
    return paginate(
        session,
        select(Booking).where(Booking.user_id == user.id).order_by(Booking.id),
        pagination,
    )


@router.get(
    "/{booking_id}",
    response_model=BookingResponse,
    responses={404: {"model": ErrorResponse, "description": "Booking missing/not owned"}},
)
def get_booking(booking_id: BookingId, session: DbSession, user: CurrentUser):
    return bookings.get_booking(session, booking_id, user.id)


@router.post(
    "/{booking_id}/cancel",
    response_model=BookingResponse,
    summary="Cancel a pending booking; repeated cancellation is safe",
    responses={
        404: {"model": ErrorResponse, "description": "Booking missing/not owned"},
        409: {"model": ErrorResponse, "description": "Only pending bookings can be cancelled"},
    },
)
def cancel_booking(booking_id: BookingId, session: DbSession, user: CurrentUser):
    return bookings.cancel_booking(session, booking_id, user.id)
