from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app import payments
from app.database import DbSession
from app.exceptions import ErrorResponse
from app.models import BookingStatus, PaymentStatus
from app.security import CurrentUser, require_provider

router = APIRouter(
    prefix="/payments",
    tags=["payments"],
    responses={
        404: {"model": ErrorResponse, "description": "Booking missing/not owned"},
        409: {
            "model": ErrorResponse,
            "description": "Conflicting event payload or finalized booking",
        },
        429: {"model": ErrorResponse, "description": "Rate limit exceeded; see Retry-After"},
    },
)
Result = Literal["SUCCESS", "FAILED"]


class PaymentInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"booking_id": 1, "simulate_status": "SUCCESS"}]},
    )
    booking_id: int = Field(gt=0, le=2_147_483_647, strict=True)
    simulate_status: Result


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    booking_id: int
    provider_payment_id: str
    amount: Decimal
    status: PaymentStatus


class WebhookInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"event_id": "evt_123", "booking_id": 1, "status": "SUCCESS"}]
        },
    )
    event_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    booking_id: int = Field(gt=0, le=2_147_483_647, strict=True)
    status: Result


class WebhookResponse(BaseModel):
    event_id: str
    booking_id: int
    payment_id: int
    payment_status: PaymentStatus
    booking_status: BookingStatus


@router.post(
    "",
    response_model=PaymentResponse,
    status_code=201,
    summary="Settle your pending booking with a simulated result",
    responses={401: {"model": ErrorResponse, "description": "Missing or invalid bearer token"}},
)
def create_payment(payload: PaymentInput, session: DbSession, user: CurrentUser):
    return payments.create_payment(
        session,
        payload.booking_id,
        user.id,
        PaymentStatus(payload.simulate_status),
    )


@router.post(
    "/webhook",
    response_model=WebhookResponse,
    dependencies=[Depends(require_provider)],
    summary="Process an idempotent simulated provider event",
    responses={403: {"model": ErrorResponse, "description": "Invalid provider secret"}},
)
def process_webhook(payload: WebhookInput, session: DbSession):
    payment, booking = payments.process_webhook(
        session,
        payload.event_id,
        payload.booking_id,
        PaymentStatus(payload.status),
    )
    return WebhookResponse(
        event_id=payload.event_id,
        booking_id=booking.id,
        payment_id=payment.id,
        payment_status=payment.status,
        booking_status=booking.status,
    )
