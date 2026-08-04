"""The HTTP surface, against an in-memory Repository."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fakes import InMemoryRepository
from wikindle import api
from wikindle.config import Settings
from wikindle.mail import RecordingMailer
from wikindle.models import PoolArticle


@pytest.fixture
def context(tmp_path):
    repository = InMemoryRepository()
    mailer = RecordingMailer()
    config = Settings(
        database_url="postgresql://unused",
        storage_dir=tmp_path,
        sender_address="kindle@wikindle.test",
        public_url="https://wikindle.test",
    )

    app = api.create_app()
    app.dependency_overrides[api.get_repository] = lambda: repository
    app.dependency_overrides[api.get_mailer] = lambda: mailer
    app.dependency_overrides[api.get_settings] = lambda: config

    # The limiters are process-wide; a fresh one per test keeps them independent.
    api._signup_limiter = api.IpRateLimiter(limit=10, window_seconds=3600)
    api._on_demand_limiter = api.IpRateLimiter(limit=20, window_seconds=3600)

    with TestClient(app) as client:
        yield client, repository, mailer


def test_health(context):
    client, _, _ = context
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_security_headers_are_set(context):
    client, _, _ = context
    headers = client.get("/api/health").headers
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["x-content-type-options"] == "nosniff"


def test_subscribe_then_confirm(context):
    client, repository, mailer = context

    response = client.post(
        "/api/subscribe",
        json={
            "kindle_address": "me@kindle.com",
            "contact_email": "me@example.com",
            "timezone": "Europe/Vienna",
        },
    )
    assert response.status_code == 202
    assert response.json()["status"] == "pending"

    subscriber = repository.subscriber_by_kindle_address("me@kindle.com")
    assert subscriber.timezone == "Europe/Vienna"
    assert mailer.sent[0].to == "me@example.com"

    confirmed = client.get(f"/confirm?token={subscriber.confirm_token}")
    assert confirmed.status_code == 200
    assert "Approved Personal Document" in confirmed.text
    assert "kindle@wikindle.test" in confirmed.text
    assert repository.subscriber_by_kindle_address("me@kindle.com").status == "active"


def test_subscribe_rejects_a_non_kindle_address(context):
    client, _, _ = context
    response = client.post(
        "/api/subscribe",
        json={"kindle_address": "me@example.com", "contact_email": "me@example.com"},
    )
    assert response.status_code == 400
    assert "kindle.com" in response.json()["error"]


def test_subscribing_twice_conflicts_once_active(context):
    client, repository, _ = context
    body = {"kindle_address": "me@kindle.com", "contact_email": "me@example.com"}
    client.post("/api/subscribe", json=body)
    token = repository.subscriber_by_kindle_address("me@kindle.com").confirm_token
    client.get(f"/confirm?token={token}")

    assert client.post("/api/subscribe", json=body).status_code == 409


def test_an_unknown_confirmation_token_is_not_found(context):
    client, _, _ = context
    assert client.get("/confirm?token=nonsense").status_code == 404


def test_signup_is_rate_limited_by_ip(context):
    client, _, _ = context
    api._signup_limiter = api.IpRateLimiter(limit=2, window_seconds=3600)

    for index in range(2):
        client.post(
            "/api/subscribe",
            json={
                "kindle_address": f"a{index}@kindle.com",
                "contact_email": f"a{index}@example.com",
            },
        )
    blocked = client.post(
        "/api/subscribe",
        json={"kindle_address": "a9@kindle.com", "contact_email": "a9@example.com"},
    )
    assert blocked.status_code == 429


def subscribe_and_confirm(client, repository, address="me@kindle.com"):
    client.post(
        "/api/subscribe",
        json={"kindle_address": address, "contact_email": "me@example.com"},
    )
    token = repository.subscriber_by_kindle_address(address).confirm_token
    client.get(f"/confirm?token={token}")
    return repository.subscriber_by_kindle_address(address)


def test_unsubscribe(context):
    client, repository, _ = context
    subscribe_and_confirm(client, repository)

    response = client.post(
        "/api/unsubscribe", json={"kindle_address": "me@kindle.com"}
    )
    assert response.status_code == 200
    assert repository.subscriber_by_kindle_address("me@kindle.com").status == "unsubscribed"


def test_forget_me_removes_the_row(context):
    client, repository, _ = context
    subscribe_and_confirm(client, repository)

    assert client.post("/api/forget-me", json={"kindle_address": "me@kindle.com"}).status_code == 200
    assert repository.subscriber_by_kindle_address("me@kindle.com") is None


def test_on_demand_requires_a_confirmed_subscriber(context):
    client, _, _ = context
    response = client.post(
        "/api/on-demand", json={"kindle_address": "stranger@kindle.com"}
    )
    assert response.status_code == 404


def test_on_demand_does_not_reveal_whether_an_address_is_pending(context):
    """A pending address and an unknown one must be indistinguishable."""
    client, repository, _ = context
    client.post(
        "/api/subscribe",
        json={"kindle_address": "me@kindle.com", "contact_email": "me@example.com"},
    )

    pending = client.post("/api/on-demand", json={"kindle_address": "me@kindle.com"})
    unknown = client.post("/api/on-demand", json={"kindle_address": "who@kindle.com"})

    assert pending.status_code == unknown.status_code == 404
    assert pending.json() == unknown.json()


def test_on_demand_rejects_a_url_outside_the_allowlist(context):
    client, repository, _ = context
    subscribe_and_confirm(client, repository)

    response = client.post(
        "/api/on-demand",
        json={"kindle_address": "me@kindle.com", "url": "http://127.0.0.1:8000/admin"},
    )
    assert response.status_code == 400
    assert "Wikipedia" in response.json()["error"]


def test_on_demand_accepts_a_wikipedia_url_and_returns_immediately(context, monkeypatch):
    """The reader is waiting for an email, not for this response."""
    client, repository, _ = context
    subscribe_and_confirm(client, repository)

    delivered: list[str] = []
    monkeypatch.setattr(
        api, "_deliver_on_demand",
        lambda config, subscriber, url, mailer: delivered.append(url),
    )

    response = client.post(
        "/api/on-demand",
        json={
            "kindle_address": "me@kindle.com",
            "url": "https://de.wikipedia.org/wiki/Wien",
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "building"
    assert delivered == ["https://de.wikipedia.org/wiki/Wien"]


def test_the_confirmation_page_offers_the_address_as_a_copy(context):
    """A retyped address fails silently and permanently — Amazon never accepts
    our mail and neither side gets an error. See ADR 0007."""
    client, repository, _ = context
    client.post(
        "/api/subscribe",
        json={"kindle_address": "me@kindle.com", "contact_email": "me@example.com"},
    )
    token = repository.subscriber_by_kindle_address("me@kindle.com").confirm_token

    page = client.get(f"/confirm?token={token}").text

    assert 'id="sender"' in page and 'id="copy"' in page
    assert "clipboard" in page
