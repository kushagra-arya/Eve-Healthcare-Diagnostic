import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

import app.payments as payments
from alembic import command
from app.config import Settings
from app.database import build_engine
from app.main import create_app
from app.models import Booking, BookingStatus, Payment, WebhookEvent
from tests.conftest import TEST_SECRET, migration_config


@pytest.fixture
def postgres_app():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL for real PostgreSQL concurrency tests")
    url = make_url(database_url)
    if url.drivername != "postgresql+psycopg" or not (url.database or "").endswith("_test"):
        pytest.fail("Use a dedicated PostgreSQL database ending in _test")
    admin_engine = build_engine(database_url)
    schema = f"eve_core_test_{uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    application = None
    try:
        scoped_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
        settings = Settings(
            _env_file=None,
            database_url=scoped_url.render_as_string(hide_password=False),
            jwt_secret=TEST_SECRET,
            webhook_secret=TEST_SECRET,
            environment="test",
            rate_limit_enabled=False,
        )
        application = create_app(settings)
        with application.state.engine.begin() as connection:
            command.upgrade(migration_config(connection), "head")
        yield application
    finally:
        if application is not None:
            application.state.engine.dispose()
        with admin_engine.begin() as connection:
            # Only this fixture's uniquely named schema is removed.
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture
def postgres_client(postgres_app):
    with TestClient(postgres_app) as client:
        yield client


@pytest.fixture
def scenario(postgres_client):
    client = postgres_client
    credentials = {"email": "patient@example.com", "password": "test-password"}
    assert client.post("/auth/signup", json=credentials).status_code == 201
    token = client.post("/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    centre = client.post("/centres", headers=headers, json={"name": "EVE", "location": "Delhi"})
    test = client.post("/tests", headers=headers, json={"name": "Blood count"})
    assert centre.status_code == test.status_code == 201
    offering = client.post(
        f"/centres/{centre.json()['id']}/tests",
        headers=headers,
        json={
            "test_id": test.json()["id"],
            "price": "1250.50",
        },
    )
    assert offering.status_code == 201
    booking_payload = {
        "centre_test_id": offering.json()["id"],
        "appointment_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
    }
    booking = client.post("/bookings", headers=headers, json=booking_payload)
    assert booking.status_code == 201
    return booking.json()["id"], headers, booking_payload


def run_concurrently(functions):
    barrier = Barrier(len(functions))

    def run(function):
        barrier.wait(timeout=10)
        return function()

    with ThreadPoolExecutor(max_workers=len(functions)) as executor:
        return list(executor.map(run, functions))


def test_ten_simultaneous_duplicate_webhooks(postgres_client, postgres_app, scenario):
    booking_id, _, _ = scenario
    headers = {"X-Webhook-Secret": TEST_SECRET}
    payload = {"event_id": "evt_concurrent", "booking_id": booking_id, "status": "SUCCESS"}
    responses = run_concurrently(
        [
            lambda: postgres_client.post("/payments/webhook", headers=headers, json=payload)
            for _ in range(10)
        ]
    )
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["payment_id"] for response in responses}) == 1
    with Session(postgres_app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
        assert session.scalar(select(func.count()).select_from(WebhookEvent)) == 1
        assert session.scalar(select(func.count()).select_from(Booking)) == 1
        assert session.get(Booking, booking_id).status == BookingStatus.CONFIRMED


def test_colliding_event_ids_roll_back_losing_booking(
    postgres_client,
    postgres_app,
    scenario,
    monkeypatch,
):
    first_id, user_headers, booking_payload = scenario
    second_id = postgres_client.post(
        "/bookings", headers=user_headers, json=booking_payload
    ).json()["id"]
    headers = {"X-Webhook-Secret": TEST_SECRET}
    both_results_applied = Barrier(2)
    apply_result = payments.apply_payment_result

    def apply_before_either_event_is_recorded(*args, **kwargs):
        payment = apply_result(*args, **kwargs)
        both_results_applied.wait(timeout=10)
        return payment

    monkeypatch.setattr(payments, "apply_payment_result", apply_before_either_event_is_recorded)
    responses = run_concurrently(
        [
            lambda booking_id=booking_id: postgres_client.post(
                "/payments/webhook",
                headers=headers,
                json={
                    "event_id": "evt_collision",
                    "booking_id": booking_id,
                    "status": "SUCCESS",
                },
            )
            for booking_id in [first_id, second_id]
        ]
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    with Session(postgres_app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
        assert session.scalar(select(func.count()).select_from(WebhookEvent)) == 1
        assert sorted(session.scalars(select(Booking.status)).all()) == [
            BookingStatus.CONFIRMED,
            BookingStatus.PENDING,
        ]


def test_concurrent_user_payments_do_not_duplicate(postgres_client, postgres_app, scenario):
    booking_id, headers, _ = scenario
    responses = run_concurrently(
        [
            lambda: postgres_client.post(
                "/payments",
                headers=headers,
                json={
                    "booking_id": booking_id,
                    "simulate_status": "SUCCESS",
                },
            )
            for _ in range(2)
        ]
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    with Session(postgres_app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
        assert session.get(Booking, booking_id).status == BookingStatus.CONFIRMED


def test_payment_cancellation_race(postgres_client, postgres_app, scenario):
    booking_id, headers, _ = scenario
    payment, cancellation = run_concurrently(
        [
            lambda: postgres_client.post(
                "/payments",
                headers=headers,
                json={
                    "booking_id": booking_id,
                    "simulate_status": "SUCCESS",
                },
            ),
            lambda: postgres_client.post(f"/bookings/{booking_id}/cancel", headers=headers),
        ]
    )
    assert (payment.status_code, cancellation.status_code) in {(201, 409), (409, 200)}
    with Session(postgres_app.state.engine) as session:
        booking = session.get(Booking, booking_id)
        payment_count = session.scalar(select(func.count()).select_from(Payment))
        assert (booking.status, payment_count) in {
            (BookingStatus.CONFIRMED, 1),
            (BookingStatus.CANCELLED, 0),
        }


def test_conflicting_webhook_results(postgres_client, postgres_app, scenario):
    booking_id, _, _ = scenario
    headers = {"X-Webhook-Secret": TEST_SECRET}
    responses = run_concurrently(
        [
            lambda result=result: postgres_client.post(
                "/payments/webhook",
                headers=headers,
                json={
                    "event_id": f"evt_{result}",
                    "booking_id": booking_id,
                    "status": result,
                },
            )
            for result in ["SUCCESS", "FAILED"]
        ]
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    with Session(postgres_app.state.engine) as session:
        payment = session.scalar(select(Payment))
        expected = BookingStatus.CONFIRMED if payment.status == "SUCCESS" else BookingStatus.FAILED
        assert session.get(Booking, booking_id).status == expected
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
        assert session.scalar(select(func.count()).select_from(WebhookEvent)) == 1
