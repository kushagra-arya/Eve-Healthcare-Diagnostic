import pytest
from sqlalchemy import func, select

from app.models import CentreTest, DiagnosticCentre, DiagnosticTest


def test_catalogue_management(client, account, session):
    headers = account[1]
    centre = client.post("/centres", headers=headers, json={"name": " EVE ", "location": "Delhi"})
    assert centre.status_code == 201
    centre_id = centre.json()["id"]
    assert centre.json()["name"] == "EVE"
    test = client.post("/tests", headers=headers, json={"name": "Blood count"})
    assert test.status_code == 201
    test_id = test.json()["id"]
    assert client.get("/centres").json()["items"] == [centre.json()]
    assert client.get(f"/centres/{centre_id}").json() == centre.json()
    assert client.get("/tests").json()["items"] == [test.json()]
    update = client.patch(f"/centres/{centre_id}", headers=headers, json={"location": "Mumbai"})
    assert update.status_code == 200
    assert update.json()["location"] == "Mumbai"
    offering = client.post(
        f"/centres/{centre_id}/tests",
        headers=headers,
        json={
            "test_id": test_id,
            "price": "1250.50",
        },
    )
    assert offering.status_code == 201
    assert client.get(f"/centres/{centre_id}/tests").json()["items"] == [offering.json()]
    assert (
        client.post(
            f"/centres/{centre_id}/tests",
            headers=headers,
            json={
                "test_id": test_id,
                "price": "1250.50",
            },
        ).status_code
        == 409
    )
    updated = client.patch(
        f"/centres/{centre_id}/tests/{test_id}", headers=headers, json={"price": "1400.00"}
    )
    assert updated.status_code == 200
    assert updated.json()["price"] == "1400.00"
    session.expire_all()
    assert session.get(DiagnosticCentre, centre_id).location == "Mumbai"
    assert session.get(DiagnosticTest, test_id).name == "Blood count"
    assert session.scalar(select(func.count()).select_from(CentreTest)) == 1


@pytest.mark.parametrize("price", ["0", "-1", "1.001", "10000000000.00", "NaN", "Infinity"])
def test_bad_prices(client, account, offering, price):
    response = client.patch(
        f"/centres/{offering.centre_id}/tests/{offering.test_id}",
        headers=account[1],
        json={"price": price},
    )
    assert response.status_code == 422


def test_missing_catalogue_resources(client, account, offering):
    headers = account[1]
    assert client.get("/centres/999").status_code == 404
    assert client.get("/centres/999/tests").status_code == 404
    assert (
        client.post(
            "/centres/999/tests",
            headers=headers,
            json={"test_id": offering.test_id, "price": "1.00"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/centres/{offering.centre_id}/tests",
            headers=headers,
            json={"test_id": 999, "price": "1.00"},
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/centres/{offering.centre_id}/tests/999", headers=headers, json={"price": "1.00"}
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("post", "/centres", {"name": "EVE", "location": "Delhi"}),
        ("patch", "/centres/1", {"name": "changed"}),
        ("post", "/tests", {"name": "Blood count"}),
        ("post", "/centres/1/tests", {"test_id": 1, "price": "1.00"}),
        ("patch", "/centres/1/tests/1", {"price": "1.00"}),
    ],
)
def test_catalogue_writes_need_auth(client, method, path, payload):
    assert getattr(client, method)(path, json=payload).status_code == 401


@pytest.mark.parametrize("payload", [{}, {"name": None}, {"location": "   "}, {"status": "closed"}])
def test_invalid_centre_updates(client, account, offering, payload):
    assert (
        client.patch(f"/centres/{offering.centre_id}", headers=account[1], json=payload).status_code
        == 422
    )
