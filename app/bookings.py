import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exceptions import AppError
from app.models import Booking, BookingStatus, CentreTest

logger = logging.getLogger("eve.bookings")


def validate_transition(current: BookingStatus, target: BookingStatus) -> None:
    if current != BookingStatus.PENDING or target not in {
        BookingStatus.CONFIRMED,
        BookingStatus.FAILED,
        BookingStatus.CANCELLED,
    }:
        raise AppError(409, "invalid_state_transition", "Booking cannot enter the requested state")


def get_booking(
    session: Session, booking_id: int, user_id: int | None = None, *, lock=False
) -> Booking:
    query = select(Booking).where(Booking.id == booking_id)
    if user_id is not None:
        query = query.where(Booking.user_id == user_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    booking = session.scalar(query)
    if booking is None:
        raise AppError(404, "booking_not_found", "Booking not found")
    return booking


def create_booking(
    session: Session,
    user_id: int,
    centre_test_id: int,
    appointment_at: datetime,
) -> Booking:
    if appointment_at.utcoffset() is None or appointment_at <= datetime.now(UTC):
        raise AppError(
            422, "invalid_appointment", "Appointment must be a future timezone-aware time"
        )
    offering = session.get(CentreTest, centre_test_id)
    if offering is None:
        raise AppError(404, "offering_not_found", "Centre test offering not found")
    booking = Booking(
        user_id=user_id,
        centre_test_id=offering.id,
        appointment_at=appointment_at.astimezone(UTC),
        amount=offering.price,
        status=BookingStatus.PENDING,
    )
    session.add(booking)
    session.commit()
    logger.info("booking_created", extra={"fields": {"booking_id": booking.id}})
    return booking


def cancel_booking(session: Session, booking_id: int, user_id: int) -> Booking:
    booking = get_booking(session, booking_id, user_id, lock=True)
    if booking.status != BookingStatus.CANCELLED:
        validate_transition(booking.status, BookingStatus.CANCELLED)
        booking.status = BookingStatus.CANCELLED
        session.commit()
        logger.info("booking_cancelled", extra={"fields": {"booking_id": booking.id}})
    else:
        session.commit()
    return booking
