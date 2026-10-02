from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def normalize_email(email: str) -> str:
    return email.strip().lower()


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(trim(email))", name="email_normalized"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    bookings: Mapped[list["Booking"]] = relationship(back_populates="user")

    @validates("email")
    def validate_email(self, _key: str, value: str) -> str:
        return normalize_email(value)


class DiagnosticCentre(TimestampMixin, Base):
    __tablename__ = "diagnostic_centres"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    location: Mapped[str] = mapped_column(String(300))
    offerings: Mapped[list["CentreTest"]] = relationship(back_populates="centre")


class DiagnosticTest(TimestampMixin, Base):
    __tablename__ = "diagnostic_tests"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    offerings: Mapped[list["CentreTest"]] = relationship(back_populates="test")


class CentreTest(TimestampMixin, Base):
    __tablename__ = "centre_tests"
    __table_args__ = (
        UniqueConstraint("centre_id", "test_id"),
        CheckConstraint("price > 0", name="price_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    centre_id: Mapped[int] = mapped_column(ForeignKey("diagnostic_centres.id", ondelete="RESTRICT"))
    test_id: Mapped[int] = mapped_column(
        ForeignKey("diagnostic_tests.id", ondelete="RESTRICT"), index=True
    )
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    centre: Mapped["DiagnosticCentre"] = relationship(back_populates="offerings")
    test: Mapped["DiagnosticTest"] = relationship(back_populates="offerings")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="offering")


class BookingStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Booking(TimestampMixin, Base):
    __tablename__ = "bookings"
    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    centre_test_id: Mapped[int] = mapped_column(
        ForeignKey("centre_tests.id", ondelete="RESTRICT"), index=True
    )
    appointment_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Snapshot the authoritative offering price when the booking is created.
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus, native_enum=False, create_constraint=True, name="booking_status"),
        default=BookingStatus.PENDING,
        server_default="PENDING",
    )
    user: Mapped["User"] = relationship(back_populates="bookings")
    offering: Mapped["CentreTest"] = relationship(back_populates="bookings")
    payments: Mapped[list["Payment"]] = relationship(back_populates="booking")


class PaymentStatus(StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="RESTRICT"), index=True
    )
    provider_payment_id: Mapped[str] = mapped_column(String(200), unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, native_enum=False, create_constraint=True, name="payment_status"),
        default=PaymentStatus.PENDING,
        server_default="PENDING",
    )
    booking: Mapped["Booking"] = relationship(back_populates="payments")
    events: Mapped[list["WebhookEvent"]] = relationship(back_populates="payment")


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (CheckConstraint("status IN ('SUCCESS', 'FAILED')", name="result_final"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Insert this record and update payment/booking in the same transaction.
    event_id: Mapped[str] = mapped_column(String(200), unique=True)
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("payments.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, native_enum=False, create_constraint=True, name="event_status")
    )
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    payment: Mapped["Payment"] = relationship(back_populates="events")
