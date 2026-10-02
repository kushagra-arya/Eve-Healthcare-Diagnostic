from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.mark.parametrize("path", ["/auth/login", "/auth/signup", "/payments", "/payments/webhook"])
def test_high_value_endpoints_are_limited(application, client, path):
    limiter = application.state.rate_limiter
    limiter.enabled = True
    limiter.limits[path] = 2
    now = [10.0]
    limiter.clock = lambda: now[0]
    assert client.post(path, json={}).status_code != 429
    assert client.post(path, json={}).status_code != 429
    response = client.post(path, json={}, headers={"X-Request-ID": "rate-test"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limit_exceeded"
    assert response.headers["Retry-After"] == "60"
    assert response.headers["X-Request-ID"] == response.json()["request_id"] == "rate-test"
    # Unrelated reads remain available, and recovery uses a deterministic clock.
    assert client.get("/health").status_code == 200
    now[0] += 60
    assert client.post(path, json={}).status_code != 429


def test_forwarded_header_cannot_bypass_limit(application, client):
    limiter = application.state.rate_limiter
    limiter.enabled = True
    limiter.limits["/auth/login"] = 1
    assert (
        client.post("/auth/login", json={}, headers={"X-Forwarded-For": "1.1.1.1"}).status_code
        == 422
    )
    assert (
        client.post("/auth/login", json={}, headers={"X-Forwarded-For": "2.2.2.2"}).status_code
        == 429
    )
    assert client.post("/auth/signup", json={}).status_code == 422


def test_counters_are_atomic_and_memory_bounded(application):
    limiter = application.state.rate_limiter
    limiter.enabled = True
    limiter.max_keys = 2
    now = [10.0]
    limiter.clock = lambda: now[0]
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(lambda _: limiter.check("/auth/login", "client-a"), range(20)))
    assert results.count(None) == 5
    assert limiter.check("/auth/login", "client-b") is None
    assert limiter.check("/auth/login", "client-c") == 60
    assert len(limiter.counters) == 2
    now[0] += 60
    assert limiter.check("/auth/login", "client-c") is None
    assert len(limiter.counters) == 1
