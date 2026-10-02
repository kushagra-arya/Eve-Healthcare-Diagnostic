from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import Booking, BookingStatus


def test_booking_snapshot_and_utc(client, account, offering, session):
    response = client.post(
        "/bookings",
        headers=account[1],
        json={
            "centre_test_id": offering.id,
            "appointment_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        },
    )
    assert response.status_code == 201
    booking = response.json()
    assert booking["status"] == "PENDING"
    assert booking["amount"] == "1250.50"
    assert booking["user_id"] == account[0].id
    assert datetime.fromisoformat(booking["appointment_at"]).utcoffset() == timedelta(0)
    client.patch(
        f"/centres/{offering.centre_id}/tests/{offering.test_id}",
        headers=account[1],
        json={"price": "1500.00"},
    )
    assert (
        client.get(f"/bookings/{booking['id']}", headers=account[1]).json()["amount"] == "1250.50"
    )
    assert session.get(Booking, booking["id"]).amount == Decimal("1250.50")


@pytest.mark.parametrize(
    "appointment",
    [
        "not-a-date",
        "2099-01-01T12:00:00",
        "2020-01-01T12:00:00Z",
    ],
)
def test_invalid_appointments(client, account, offering, session, appointment):
    response = client.post(
        "/bookings",
        headers=account[1],
        json={
            "centre_test_id": offering.id,
            "appointment_at": appointment,
        },
    )
    assert response.status_code == 422
    assert session.scalar(select(func.count()).select_from(Booking)) == 0


def test_invalid_offering_and_client_amount(client, account, offering):
    payload = {
        "centre_test_id": 999,
        "appointment_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
    }
    assert client.post("/bookings", headers=account[1], json=payload).status_code == 404
    payload["centre_test_id"] = offering.id
    payload["amount"] = "0.01"
    assert client.post("/bookings", headers=account[1], json=payload).status_code == 422
    payload.pop("amount")
    payload["user_id"] = 999
    assert client.post("/bookings", headers=account[1], json=payload).status_code == 422


def test_ownership_and_idempotent_cancel(client, account, other_account, booking, session):
    booking_id = booking["id"]
    assert client.get("/bookings", headers=account[1]).json()["items"] == [booking]
    assert client.get("/bookings", headers=other_account[1]).json()["items"] == []
    assert client.get(f"/bookings/{booking_id}", headers=other_account[1]).status_code == 404
    assert (
        client.post(f"/bookings/{booking_id}/cancel", headers=other_account[1]).status_code == 404
    )
    first = client.post(f"/bookings/{booking_id}/cancel", headers=account[1])
    repeated = client.post(f"/bookings/{booking_id}/cancel", headers=account[1])
    assert first.status_code == repeated.status_code == 200
    assert first.json() == repeated.json()
    assert session.get(Booking, booking_id).status == BookingStatus.CANCELLED


@pytest.mark.parametrize("path", ["bad", "0", "-1", "999999999999999999999"])
def test_bad_booking_ids(client, account, path):
    assert client.get(f"/bookings/{path}", headers=account[1]).status_code == 422
    assert client.post(f"/bookings/{path}/cancel", headers=account[1]).status_code == 422


def test_protected_booking_operations(client, booking):
    assert client.get(f"/bookings/{booking['id']}").status_code == 401
    assert client.post(f"/bookings/{booking['id']}/cancel").status_code == 401
    assert (
        client.post(
            "/bookings",
            json={
                "centre_test_id": booking["centre_test_id"],
                "appointment_at": "2099-01-01T12:00:00Z",
            },
        ).status_code
        == 401
    )


def test_appointment_with_offset_is_stored_as_utc(client, account, offering, session):
    response = client.post(
        "/bookings",
        headers=account[1],
        json={
            "centre_test_id": offering.id,
            "appointment_at": "2099-01-01T12:00:00+05:30",
        },
    )
    assert response.status_code == 201
    actual = datetime.fromisoformat(response.json()["appointment_at"])
    assert actual == datetime(2099, 1, 1, 6, 30, tzinfo=UTC)
    stored = session.get(Booking, response.json()["id"]).appointment_at
    assert stored.replace(tzinfo=UTC) == actual
