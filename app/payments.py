import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.bookings import get_booking, validate_transition
from app.exceptions import AppError
from app.models import Booking, BookingStatus, Payment, PaymentStatus, WebhookEvent

logger = logging.getLogger("eve.payments")


def apply_payment_result(
    session: Session,
    booking: Booking,
    result: PaymentStatus,
    *,
    allow_matching_result=False,
) -> Payment:
    target = BookingStatus.CONFIRMED if result == PaymentStatus.SUCCESS else BookingStatus.FAILED
    if result not in {PaymentStatus.SUCCESS, PaymentStatus.FAILED}:
        raise AppError(422, "invalid_payment_status", "Payment result must be SUCCESS or FAILED")
    if allow_matching_result and booking.status == target:
        payment = session.scalar(
            select(Payment)
            .where(
                Payment.booking_id == booking.id,
                Payment.status == result,
            )
            .order_by(Payment.id.desc())
            .limit(1)
        )
        if payment is not None:
            return payment
    validate_transition(booking.status, target)
    payment = session.scalar(
        select(Payment)
        .where(
            Payment.booking_id == booking.id,
            Payment.status == PaymentStatus.PENDING,
        )
        .order_by(Payment.id)
        .limit(1)
    )
    if payment is None:
        payment = Payment(booking_id=booking.id, provider_payment_id=f"sim_{uuid4().hex}")
        session.add(payment)
    payment.amount = booking.amount
    payment.status = result
    booking.status = target
    return payment


def create_payment(
    session: Session,
    booking_id: int,
    user_id: int,
    result: PaymentStatus,
) -> Payment:
    # Payment, cancellation, and webhook operations lock the same booking first.
    booking = get_booking(session, booking_id, user_id, lock=True)
    payment = apply_payment_result(session, booking, result)
    session.commit()
    event = "payment_processed" if result == PaymentStatus.SUCCESS else "payment_failed"
    logger.info(
        event,
        extra={"fields": {"booking_id": booking.id, "payment_id": payment.id, "status": result}},
    )
    return payment


def verify_event_payload(event: WebhookEvent, booking_id: int, result: PaymentStatus) -> None:
    if event.payment.booking_id != booking_id or event.status != result:
        logger.warning(
            "webhook_conflict",
            extra={
                "fields": {
                    "event_id": event.event_id,
                    "booking_id": booking_id,
                    "payment_id": event.payment_id,
                    "status": result,
                }
            },
        )
        raise AppError(409, "event_conflict", "Event ID was already used for a different payload")


def process_webhook(
    session: Session,
    event_id: str,
    booking_id: int,
    result: PaymentStatus,
) -> tuple[Payment, Booking]:
    logger.info(
        "webhook_received",
        extra={"fields": {"event_id": event_id, "booking_id": booking_id, "status": result}},
    )
    booking = get_booking(session, booking_id, lock=True)
    existing = session.scalar(select(WebhookEvent).where(WebhookEvent.event_id == event_id))
    if existing is not None:
        verify_event_payload(existing, booking_id, result)
        payment = existing.payment
        session.commit()
        logger.info(
            "duplicate_webhook_ignored",
            extra={
                "fields": {
                    "event_id": event_id,
                    "booking_id": booking.id,
                    "payment_id": payment.id,
                    "status": result,
                }
            },
        )
        return payment, booking

    try:
        payment = apply_payment_result(session, booking, result, allow_matching_result=True)
    except AppError as exc:
        if exc.status_code == 409:
            logger.warning(
                "webhook_conflict",
                extra={
                    "fields": {
                        "event_id": event_id,
                        "booking_id": booking.id,
                        "status": result,
                        "booking_status": booking.status,
                    }
                },
            )
        raise
    session.flush()
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    recorded_id = session.scalar(
        insert(WebhookEvent)
        .values(
            event_id=event_id,
            payment_id=payment.id,
            status=result,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(WebhookEvent.id)
    )
    if recorded_id is None:
        # Discard all speculative writes before returning the winning event.
        session.rollback()
        existing = session.scalar(select(WebhookEvent).where(WebhookEvent.event_id == event_id))
        verify_event_payload(existing, booking_id, result)
        payment = existing.payment
        booking = get_booking(session, booking_id)
    session.commit()
    event = "webhook_processed" if recorded_id is not None else "duplicate_webhook_ignored"
    logger.info(
        event,
        extra={
            "fields": {
                "event_id": event_id,
                "booking_id": booking.id,
                "payment_id": payment.id,
                "status": result,
            }
        },
    )
    return payment, booking
