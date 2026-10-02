from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from app.models import Booking, BookingStatus, Payment, PaymentStatus


@pytest.mark.parametrize("result,expected", [("SUCCESS", "CONFIRMED"), ("FAILED", "FAILED")])
def test_payment_outcome(client, account, booking, session, result, expected):
    response = client.post(
        "/payments",
        headers=account[1],
        json={
            "booking_id": booking["id"],
            "simulate_status": result,
        },
    )
    assert response.status_code == 201
    assert response.json()["amount"] == booking["amount"]
    payment = session.get(Payment, response.json()["id"])
    assert payment.amount == Decimal("1250.50")
    assert payment.status == PaymentStatus(result)
    assert session.get(Booking, booking["id"]).status == BookingStatus(expected)
    assert (
        client.post(
            "/payments",
            headers=account[1],
            json={
                "booking_id": booking["id"],
                "simulate_status": result,
            },
        ).status_code
        == 409
    )
    assert client.post(f"/bookings/{booking['id']}/cancel", headers=account[1]).status_code == 409
    assert session.scalar(select(func.count()).select_from(Payment)) == 1


def test_payment_ownership_and_missing_booking(client, account, other_account, booking, session):
    payload = {"booking_id": booking["id"], "simulate_status": "SUCCESS"}
    assert client.post("/payments", headers=other_account[1], json=payload).status_code == 404
    assert client.post("/payments", json=payload).status_code == 401
    payload["booking_id"] = 999
    assert client.post("/payments", headers=account[1], json=payload).status_code == 404
    assert session.scalar(select(func.count()).select_from(Payment)) == 0


def test_cancelled_booking_cannot_be_paid(client, account, booking, session):
    client.post(f"/bookings/{booking['id']}/cancel", headers=account[1])
    assert (
        client.post(
            "/payments",
            headers=account[1],
            json={
                "booking_id": booking["id"],
                "simulate_status": "SUCCESS",
            },
        ).status_code
        == 409
    )
    assert session.get(Booking, booking["id"]).status == BookingStatus.CANCELLED
    assert session.scalar(select(func.count()).select_from(Payment)) == 0


@pytest.mark.parametrize("extra", [{"simulate_status": "PENDING"}, {"amount": "0.01"}])
def test_invalid_payment_request(client, account, booking, extra):
    payload = {"booking_id": booking["id"], "simulate_status": "SUCCESS", **extra}
    assert client.post("/payments", headers=account[1], json=payload).status_code == 422


def test_payment_transaction_rolls_back(account, booking, application, session):
    def fail_flush(_session, _context):
        raise RuntimeError("injected failure after payment and booking writes")

    factory = application.state.session_factory
    event.listen(factory, "after_flush", fail_flush)
    try:
        with TestClient(application, raise_server_exceptions=False) as test_client:
            response = test_client.post(
                "/payments",
                headers=account[1],
                json={
                    "booking_id": booking["id"],
                    "simulate_status": "SUCCESS",
                },
            )
        assert response.status_code == 500
    finally:
        event.remove(factory, "after_flush", fail_flush)
    assert session.scalar(select(func.count()).select_from(Payment)) == 0
    assert session.get(Booking, booking["id"]).status == BookingStatus.PENDING
