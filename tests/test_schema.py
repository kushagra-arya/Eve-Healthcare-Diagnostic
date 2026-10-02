from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.database import Base
from app.models import (
    Booking,
    CentreTest,
    DiagnosticCentre,
    DiagnosticTest,
    Payment,
    User,
    WebhookEvent,
)
from tests.conftest import migration_config


def test_migration_matches_models_and_is_reversible(engine):
    with engine.begin() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []
        command.downgrade(migration_config(connection), "base")
        assert set(inspect(connection).get_table_names()) <= {"alembic_version"}
        command.upgrade(migration_config(connection), "head")
        assert compare_metadata(context, Base.metadata) == []


def test_email_normalized_and_unique(session):
    session.add(User(email=" Patient@Example.com ", password_hash="hash-for-schema-test"))
    session.commit()
    assert session.query(User).one().email == "patient@example.com"
    session.add(User(email="PATIENT@example.com", password_hash="another-hash"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_foreign_keys_and_status_constraints(session, booking):
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO payments (booking_id, provider_payment_id, amount, status) "
                "VALUES (999, 'missing-booking', 100, 'PENDING')"
            )
        )
    session.rollback()
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO payments (booking_id, provider_payment_id, amount, status) "
                "VALUES (:booking_id, 'invalid-status', 100, 'UNKNOWN')"
            ),
            {"booking_id": booking["id"]},
        )
    session.rollback()
    with pytest.raises(IntegrityError):
        session.execute(
            text("UPDATE bookings SET status = 'UNKNOWN' WHERE id = :id"), {"id": booking["id"]}
        )
    session.rollback()
    assert session.get(Booking, booking["id"]).status == "PENDING"


def test_database_rejects_nonpositive_money(session, booking):
    for statement, parameters in [
        ("UPDATE bookings SET amount = 0 WHERE id = :id", {"id": booking["id"]}),
        ("UPDATE centre_tests SET price = -1 WHERE id = :id", {"id": booking["centre_test_id"]}),
        (
            "INSERT INTO payments (booking_id, provider_payment_id, amount, status) "
            "VALUES (:id, 'bad-amount', 0, 'PENDING')",
            {"id": booking["id"]},
        ),
    ]:
        with pytest.raises(IntegrityError):
            session.execute(text(statement), parameters)
        session.rollback()
    assert session.get(Booking, booking["id"]).amount == Decimal("1250.50")


def test_domain_relationships_money_and_event_uniqueness(session):
    user = User(email="patient@example.com", password_hash="hash-for-schema-test")
    centre = DiagnosticCentre(name="EVE Centre", location="Delhi")
    test = DiagnosticTest(name="Blood count")
    offering = CentreTest(centre=centre, test=test, price=Decimal("1250.50"))
    booking = Booking(
        user=user,
        offering=offering,
        amount=offering.price,
        appointment_at=datetime.now(UTC) + timedelta(days=1),
    )
    payment = Payment(booking=booking, provider_payment_id="attempt-1", amount=booking.amount)
    session.add(payment)
    session.commit()
    offering.price = Decimal("1500.00")
    session.commit()
    session.expire_all()
    assert booking.amount == Decimal("1250.50")
    assert isinstance(booking.amount, Decimal)
    assert booking.offering.centre.name == "EVE Centre"
    session.add(WebhookEvent(event_id="event-1", payment=payment, status="SUCCESS"))
    session.commit()
    session.add(WebhookEvent(event_id="event-1", payment=payment, status="SUCCESS"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    assert session.query(WebhookEvent).count() == 1
