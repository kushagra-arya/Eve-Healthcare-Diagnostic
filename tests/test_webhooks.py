from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

import app.payments as payments
from app.main import create_app
from app.models import Booking, BookingStatus, Payment, WebhookEvent


@pytest.mark.parametrize("status,expected", [("SUCCESS", "CONFIRMED"), ("FAILED", "FAILED")])
def test_webhook_and_repeated_delivery(
    client, booking, provider_headers, session, status, expected
):
    payload = {"event_id": "evt_123", "booking_id": booking["id"], "status": status}
    response = client.post("/payments/webhook", headers=provider_headers, json=payload)
    assert response.status_code == 200
    assert response.json()["booking_status"] == expected
    for _ in range(10):
        duplicate = client.post("/payments/webhook", headers=provider_headers, json=payload)
        assert duplicate.status_code == 200
        assert duplicate.json() == response.json()
    assert session.scalar(select(func.count()).select_from(Payment)) == 1
    assert session.scalar(select(func.count()).select_from(WebhookEvent)) == 1
    assert session.scalar(select(func.count()).select_from(Booking)) == 1
    assert session.get(Booking, booking["id"]).status == BookingStatus(expected)


def test_reused_event_id_is_rejected(client, booking, provider_headers, session):
    payload = {"event_id": "evt_123", "booking_id": booking["id"], "status": "SUCCESS"}
    assert (
        client.post("/payments/webhook", headers=provider_headers, json=payload).status_code == 200
    )
    payload["status"] = "FAILED"
    assert (
        client.post("/payments/webhook", headers=provider_headers, json=payload).status_code == 409
    )
    assert session.get(Booking, booking["id"]).status == BookingStatus.CONFIRMED
    assert session.scalar(select(func.count()).select_from(Payment)) == 1
    assert session.scalar(select(func.count()).select_from(WebhookEvent)) == 1


def test_matching_notification_after_user_payment(
    client, account, booking, provider_headers, session
):
    paid = client.post(
        "/payments",
        headers=account[1],
        json={
            "booking_id": booking["id"],
            "simulate_status": "SUCCESS",
        },
    ).json()
    for event_id in ["evt_1", "evt_2"]:
        response = client.post(
            "/payments/webhook",
            headers=provider_headers,
            json={
                "event_id": event_id,
                "booking_id": booking["id"],
                "status": "SUCCESS",
            },
        )
        assert response.status_code == 200
        assert response.json()["payment_id"] == paid["id"]
    assert session.scalar(select(func.count()).select_from(Payment)) == 1
    assert session.scalar(select(func.count()).select_from(WebhookEvent)) == 2
    response = client.post(
        "/payments/webhook",
        headers=provider_headers,
        json={
            "event_id": "evt_conflict",
            "booking_id": booking["id"],
            "status": "FAILED",
        },
    )
    assert response.status_code == 409
    assert session.scalar(select(func.count()).select_from(WebhookEvent)) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"booking_id": 999},
        {"booking_id": "bad"},
        {"status": "PENDING"},
        {"event_id": ""},
        {"event_id": "spaces not allowed"},
        {"event_id": "a" * 201},
    ],
)
def test_invalid_webhook(client, booking, provider_headers, session, change):
    payload = {"event_id": "evt_1", "booking_id": booking["id"], "status": "SUCCESS", **change}
    response = client.post("/payments/webhook", headers=provider_headers, json=payload)
    assert response.status_code == (404 if change.get("booking_id") == 999 else 422)
    assert session.scalar(select(func.count()).select_from(Payment)) == 0
    assert session.scalar(select(func.count()).select_from(WebhookEvent)) == 0


def test_webhook_requires_provider_secret(client, account, booking):
    payload = {"event_id": "evt_1", "booking_id": booking["id"], "status": "SUCCESS"}
    for headers in [{}, account[1], {"X-Webhook-Secret": "wrong"}]:
        assert client.post("/payments/webhook", headers=headers, json=payload).status_code == 403


def test_cancelled_booking_webhook_rejected(client, account, booking, provider_headers, session):
    client.post(f"/bookings/{booking['id']}/cancel", headers=account[1])
    response = client.post(
        "/payments/webhook",
        headers=provider_headers,
        json={
            "event_id": "evt_1",
            "booking_id": booking["id"],
            "status": "SUCCESS",
        },
    )
    assert response.status_code == 409
    assert session.scalar(select(func.count()).select_from(Payment)) == 0
    assert session.scalar(select(func.count()).select_from(WebhookEvent)) == 0


def test_webhook_transaction_rolls_back(client, booking, provider_headers, session, monkeypatch):
    def fail_recording(_table):
        raise RuntimeError("injected failure after payment flush")

    monkeypatch.setattr(payments, "sqlite_insert", fail_recording)
    with TestClient(
        create_app(client.app.state.settings), raise_server_exceptions=False
    ) as test_client:
        response = test_client.post(
            "/payments/webhook",
            headers=provider_headers,
            json={
                "event_id": "evt_1",
                "booking_id": booking["id"],
                "status": "SUCCESS",
            },
        )
    assert response.status_code == 500
    assert session.scalar(select(func.count()).select_from(Payment)) == 0
    assert session.scalar(select(func.count()).select_from(WebhookEvent)) == 0
    assert session.get(Booking, booking["id"]).status == BookingStatus.PENDING


def test_ten_concurrent_duplicate_deliveries(client, booking, provider_headers, session):
    barrier = Barrier(10)
    payload = {"event_id": "evt_parallel", "booking_id": booking["id"], "status": "SUCCESS"}

    def deliver(_index):
        barrier.wait(timeout=10)
        return client.post("/payments/webhook", headers=provider_headers, json=payload)

    with ThreadPoolExecutor(max_workers=10) as executor:
        responses = list(executor.map(deliver, range(10)))
    assert all(response.status_code == 200 for response in responses)
    assert all(response.json() == responses[0].json() for response in responses)
    assert session.scalar(select(func.count()).select_from(Payment)) == 1
    assert session.scalar(select(func.count()).select_from(WebhookEvent)) == 1


def test_event_id_cannot_be_reassigned_to_another_booking(
    client,
    account,
    booking,
    provider_headers,
    session,
):
    other = client.post(
        "/bookings",
        headers=account[1],
        json={
            "centre_test_id": booking["centre_test_id"],
            "appointment_at": booking["appointment_at"],
        },
    ).json()
    for booking_id, expected in [(booking["id"], 200), (other["id"], 409)]:
        response = client.post(
            "/payments/webhook",
            headers=provider_headers,
            json={
                "event_id": "evt_reused",
                "booking_id": booking_id,
                "status": "SUCCESS",
            },
        )
        assert response.status_code == expected
    assert session.get(Booking, other["id"]).status == BookingStatus.PENDING
    assert session.scalar(select(func.count()).select_from(Payment)) == 1
    assert session.scalar(select(func.count()).select_from(WebhookEvent)) == 1


def test_failure_after_event_recording_rolls_back_every_write(
    booking,
    provider_headers,
    application,
    session,
):
    def fail_commit(_session):
        raise RuntimeError("injected failure after event recording")

    factory = application.state.session_factory
    event.listen(factory, "before_commit", fail_commit)
    try:
        with TestClient(application, raise_server_exceptions=False) as client:
            response = client.post(
                "/payments/webhook",
                headers=provider_headers,
                json={
                    "event_id": "evt_rollback",
                    "booking_id": booking["id"],
                    "status": "SUCCESS",
                },
            )
        assert response.status_code == 500
    finally:
        event.remove(factory, "before_commit", fail_commit)
    assert session.scalar(select(func.count()).select_from(Payment)) == 0
    assert session.scalar(select(func.count()).select_from(WebhookEvent)) == 0
    assert session.get(Booking, booking["id"]).status == BookingStatus.PENDING
