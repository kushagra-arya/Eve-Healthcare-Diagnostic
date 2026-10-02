from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.models import Booking, DiagnosticCentre


def test_catalogue_paging_is_ordered_and_bounded(client, session, application):
    session.add_all(DiagnosticCentre(name=f"Centre {i}", location="Delhi") for i in range(23))
    session.commit()
    selects = []

    def record_query(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement.upper())

    event.listen(application.state.engine, "before_cursor_execute", record_query)
    try:
        first = client.get("/centres").json()
    finally:
        event.remove(application.state.engine, "before_cursor_execute", record_query)
    assert first["page"] == 1 and first["page_size"] == 20 and first["total"] == 23
    assert len(first["items"]) == 20
    assert len(selects) <= 2
    assert any("LIMIT" in statement for statement in selects)
    second = client.get("/centres?page=2").json()
    assert [item["id"] for item in first["items"] + second["items"]] == list(range(1, 24))
    assert client.get("/centres?page=3").json() == {
        "items": [],
        "page": 3,
        "page_size": 20,
        "total": 23,
    }


def test_booking_pagination_filters_items_and_total(
    client, account, other_account, offering, session
):
    session.add_all(
        Booking(
            user_id=user.id,
            centre_test_id=offering.id,
            appointment_at=datetime(2099, 1, 1, tzinfo=UTC),
            amount=Decimal("1250.50"),
        )
        for user in [account[0], account[0], other_account[0]]
    )
    session.commit()
    first = client.get("/bookings?page_size=1", headers=account[1]).json()
    second = client.get("/bookings?page_size=1&page=2", headers=account[1]).json()
    assert first["total"] == second["total"] == 2
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert first["items"][0]["user_id"] == second["items"][0]["user_id"] == account[0].id
    assert client.get("/bookings", headers=other_account[1]).json()["total"] == 1


@pytest.mark.parametrize(
    "query",
    [
        "page=0",
        "page=-1",
        "page=1000001",
        "page=bad",
        "page_size=0",
        "page_size=101",
        "page_size=-1",
        "page_size=bad",
    ],
)
def test_pagination_boundaries_are_consistent(client, account, offering, query):
    for path in ["/centres", "/tests", "/bookings", f"/centres/{offering.centre_id}/tests"]:
        response = client.get(f"{path}?{query}", headers=account[1])
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


def test_maximum_page_size_is_accepted(client):
    assert client.get("/centres?page_size=100").json() == {
        "items": [],
        "page": 1,
        "page_size": 100,
        "total": 0,
    }
