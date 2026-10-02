import json
import logging
from io import StringIO

import pytest
from psycopg import OperationalError as DriverOperationalError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.models import Booking, CentreTest, Payment, User, WebhookEvent
from app.security import create_access_token


def test_deleted_user_and_bad_signature_are_rejected(client, account, session, settings):
    token = create_access_token(account[0].id, settings)
    bad_settings = settings.model_copy(update={"jwt_secret": type(settings.jwt_secret)("b" * 64)})
    bad_token = create_access_token(account[0].id, bad_settings)
    assert (
        client.get("/bookings", headers={"Authorization": f"Bearer {bad_token}"}).status_code == 401
    )
    session.execute(delete(User).where(User.id == account[0].id))
    session.commit()
    assert client.get("/bookings", headers={"Authorization": f"Bearer {token}"}).status_code == 401


@pytest.mark.parametrize("price", ["not-money", "1.2.3", True, {}])
def test_malformed_prices(client, account, offering, price):
    assert (
        client.patch(
            f"/centres/{offering.centre_id}/tests/{offering.test_id}",
            headers=account[1],
            json={"price": price},
        ).status_code
        == 422
    )


def test_request_cannot_supply_redundant_centre_or_test(client, account, offering):
    response = client.post(
        "/bookings",
        headers=account[1],
        json={
            "centre_test_id": offering.id,
            "centre_id": offering.centre_id + 1,
            "test_id": offering.test_id,
            "appointment_at": "2099-01-01T00:00:00Z",
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "path,body",
    [
        ("/bookings", {"centre_test_id": True, "appointment_at": "2099-01-01T00:00:00Z"}),
        ("/bookings", {"centre_test_id": 1, "appointment_at": 4070908800}),
        ("/bookings", {"centre_test_id": 1, "appointment_at": "4070908800"}),
        ("/payments", {"booking_id": True, "simulate_status": "SUCCESS"}),
        ("/payments/webhook", {"booking_id": True, "event_id": "evt_bool", "status": "SUCCESS"}),
    ],
)
def test_ambiguous_input_is_rejected(client, account, provider_headers, path, body):
    assert client.post(path, headers=account[1] | provider_headers, json=body).status_code == 422


def test_blank_password_is_rejected(client):
    response = client.post(
        "/auth/signup", json={"email": "patient@example.com", "password": " " * 8}
    )
    assert response.status_code == 422


def test_history_cannot_be_deleted_at_database_level(session, booking):
    # Foreign keys enforce RESTRICT even for writes outside API code.
    with pytest.raises(IntegrityError):
        session.execute(delete(CentreTest).where(CentreTest.id == booking["centre_test_id"]))
    session.rollback()
    assert session.get(Booking, booking["id"]) is not None


def test_duplicate_provider_payment_id_is_database_protected(session, booking):
    first = Payment(
        booking_id=booking["id"],
        provider_payment_id="sim_unique",
        amount="1250.50",
        status="FAILED",
    )
    session.add(first)
    session.commit()
    duplicate = Payment(
        booking_id=booking["id"],
        provider_payment_id="sim_unique",
        amount="1250.50",
        status="SUCCESS",
    )
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    assert session.scalar(select(func.count()).select_from(Payment)) == 1


@pytest.mark.parametrize("sqlstate", ["40001", "40P01", "08006"])
def test_transient_database_errors_are_retryable(application, client, sqlstate):
    class TransientError(Exception):
        pass

    @application.get("/_test/transient")
    def fail():
        original = TransientError("private database credentials")
        original.sqlstate = sqlstate
        raise OperationalError("sensitive SQL", {}, original)

    response = client.get("/_test/transient")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert response.json()["error"]["code"] == "database_unavailable"
    assert "private" not in response.text and "sensitive" not in response.text


@pytest.mark.parametrize(
    "error",
    [
        PoolTimeoutError("private pool state"),
        OperationalError("sensitive SQL", {}, DriverOperationalError("private connection failure")),
    ],
)
def test_pool_and_connection_failures_are_retryable(application, client, error):
    @application.get("/_test/unavailable")
    def fail():
        raise error

    response = client.get("/_test/unavailable")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert "private" not in response.text and "sensitive" not in response.text


@pytest.mark.parametrize("request_id", ["contains spaces", "a" * 65, "bad/request", ""])
def test_unsafe_request_ids_are_replaced(client, request_id):
    response = client.get("/health", headers={"X-Request-ID": request_id})
    generated = response.headers["X-Request-ID"]
    assert len(generated) == 32 and generated.isalnum()
    assert generated != request_id


def test_webhook_logs_are_correlated_without_credentials(
    monkeypatch, client, booking, provider_headers, settings
):
    stream = StringIO()
    logger = logging.getLogger("eve")
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logger.handlers[0].formatter)
    monkeypatch.setattr(logger, "handlers", [handler])
    payload = {"event_id": "evt_logged", "booking_id": booking["id"], "status": "SUCCESS"}
    headers = provider_headers | {
        "X-Request-ID": "webhook-log-test",
        "Authorization": "Bearer private-token",
    }
    assert client.post("/payments/webhook", headers=headers, json=payload).status_code == 200
    assert client.post("/payments/webhook", headers=headers, json=payload).status_code == 200
    assert (
        client.post(
            "/payments/webhook", headers=headers, json=payload | {"status": "FAILED"}
        ).status_code
        == 409
    )
    entries = [json.loads(line) for line in stream.getvalue().splitlines()]
    events = {entry["event"] for entry in entries}
    assert {
        "webhook_received",
        "webhook_processed",
        "duplicate_webhook_ignored",
        "webhook_conflict",
    } <= events
    assert all(entry["request_id"] == "webhook-log-test" for entry in entries)
    assert all(
        entry["event_id"] == "evt_logged"
        for entry in entries
        if entry["event"].startswith("webhook")
    )
    assert "private-token" not in stream.getvalue()
    assert settings.webhook_secret.get_secret_value() not in stream.getvalue()


@pytest.mark.parametrize("result,expected", [("SUCCESS", "CONFIRMED"), ("FAILED", "FAILED")])
def test_full_assignment_flow(client, session, result, expected):
    credentials = {"email": "patient@example.com", "password": "test-password"}
    assert client.post("/auth/signup", json=credentials).status_code == 201
    token = client.post("/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    centre = client.post(
        "/centres", headers=headers, json={"name": "EVE", "location": "Delhi"}
    ).json()
    test = client.post("/tests", headers=headers, json={"name": "Blood count"}).json()
    offering = client.post(
        f"/centres/{centre['id']}/tests",
        headers=headers,
        json={"test_id": test["id"], "price": "1250.50"},
    ).json()
    assert client.get(f"/centres/{centre['id']}/tests").json()["items"] == [offering]
    booking = client.post(
        "/bookings",
        headers=headers,
        json={
            "centre_test_id": offering["id"],
            "appointment_at": "2099-01-01T00:00:00Z",
        },
    ).json()
    payment = client.post(
        "/payments", headers=headers, json={"booking_id": booking["id"], "simulate_status": result}
    )
    assert payment.status_code == 201
    assert payment.json()["amount"] == booking["amount"] == "1250.50"
    assert client.get(f"/bookings/{booking['id']}", headers=headers).json()["status"] == expected
    assert session.get(Booking, booking["id"]).status == expected
    assert session.get(Payment, payment.json()["id"]).status == result
    assert session.scalar(select(func.count()).select_from(Payment)) == 1
    assert session.scalar(select(func.count()).select_from(WebhookEvent)) == 0


def test_openapi_documents_security_examples_and_errors(client):
    schema = client.get("/openapi.json").json()
    assert client.get("/docs").status_code == 200
    booking = schema["paths"]["/bookings"]["get"]
    assert booking["security"] == [{"HTTPBearer": []}]
    assert {parameter["name"] for parameter in booking["parameters"]} == {"page", "page_size"}
    assert schema["paths"]["/payments/webhook"]["post"]["security"] == [{"APIKeyHeader": []}]
    assert "429" in schema["paths"]["/auth/login"]["post"]["responses"]
    assert schema["components"]["schemas"]["WebhookInput"]["examples"][0]["event_id"] == "evt_123"
    assert schema["paths"]["/bookings"]["post"]["responses"]["422"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/ErrorResponse"}
    assert "409" not in schema["paths"]["/auth/login"]["post"]["responses"]
    assert "401" not in schema["paths"]["/payments/webhook"]["post"]["responses"]
    assert "403" not in schema["paths"]["/payments"]["post"]["responses"]
    assert "401" not in schema["paths"]["/centres"]["get"]["responses"]
    assert schema["paths"]["/auth/login"]["post"]["responses"]["401"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}
