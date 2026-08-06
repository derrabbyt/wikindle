"""The HTTP surface, against an in-memory Repository."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fakes import InMemoryRepository
from wikindle import api
from wikindle.config import Settings
from wikindle.mail import RecordingMailer

#: Amazon builds these with a random mixed-case suffix, and the local part is
#: case-sensitive. Using one throughout keeps that honest.
SELINA = "selina.liball16_nPgTrV@kindle.com"


@pytest.fixture
def context(tmp_path):
    repository = InMemoryRepository()
    mailer = RecordingMailer()
    config = Settings(
        database_url="postgresql://unused",
        storage_dir=tmp_path,
        sender_address="read@wikindle.test",
        public_url="https://wikindle.test",
    )

    app = api.create_app()
    app.dependency_overrides[api.get_repository] = lambda: repository
    app.dependency_overrides[api.get_mailer] = lambda: mailer
    app.dependency_overrides[api.get_settings] = lambda: config

    # The limiters are process-wide; a fresh pair per test keeps them independent.
    api._signup_limiter = api.IpRateLimiter(limit=10, window_seconds=3600)
    api._on_demand_limiter = api.IpRateLimiter(limit=20, window_seconds=3600)

    with TestClient(app) as client:
        yield client, repository, mailer


def subscribe(client, address=SELINA, **extra):
    return client.post("/api/subscribe", json={"kindle_address": address, **extra})


# ----------------------------------------------------------------- basics


def test_health(context):
    client, _, _ = context
    assert client.get("/api/health").json()["status"] == "ok"


def test_security_headers_are_set(context):
    client, _, _ = context
    headers = client.get("/api/health").headers
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["x-content-type-options"] == "nosniff"


# ------------------------------------------------------------- subscribing


def test_subscribing_takes_effect_immediately(context):
    """No confirmation step — it proved control of a mailbox, never of the
    Kindle. See docs/adr/0008-no-confirmation-step.md."""
    client, repository, _ = context

    response = subscribe(client, timezone="Europe/Vienna")

    assert response.status_code == 201
    assert repository.subscriber_by_kindle_address(SELINA).status == "active"
    assert repository.subscriber_by_kindle_address(SELINA).timezone == "Europe/Vienna"


def test_the_response_carries_the_approved_sender_instruction(context):
    """It is the only place we can say it: there is no email channel to a human."""
    client, _, _ = context

    payload = subscribe(client).json()

    assert payload["sender_address"] == "read@wikindle.test"
    assert "Approved" in payload["message"]
    assert "read@wikindle.test" in payload["message"]


def test_subscribing_sends_no_email(context):
    client, _, mailer = context
    subscribe(client)
    assert mailer.sent == []


def test_a_mixed_case_address_is_stored_verbatim(context):
    """Regression: lowercasing produced an address Amazon discards silently."""
    client, repository, _ = context

    subscribe(client)

    assert list(repository.subscribers.values())[0].kindle_address == SELINA


def test_subscribing_twice_is_idempotent(context):
    client, repository, _ = context

    assert subscribe(client).status_code == 201
    assert subscribe(client).status_code == 201
    assert len(repository.subscribers) == 1


def test_an_ordinary_mailbox_is_refused(context):
    client, _, _ = context
    response = subscribe(client, address="me@example.com")
    assert response.status_code == 400
    assert "kindle.com" in response.json()["error"]


def test_signup_is_rate_limited_by_ip(context):
    client, _, _ = context
    api._signup_limiter = api.IpRateLimiter(limit=2, window_seconds=3600)

    for index in range(2):
        subscribe(client, address=f"a{index}_Xy@kindle.com")

    assert subscribe(client, address="a9_Xy@kindle.com").status_code == 429


# ----------------------------------------------------------------- leaving


def test_unsubscribe(context):
    client, repository, _ = context
    subscribe(client)

    response = client.post("/api/unsubscribe", json={"kindle_address": SELINA})

    assert response.status_code == 200
    assert repository.subscriber_by_kindle_address(SELINA).status == "unsubscribed"


def test_forget_me_removes_the_row(context):
    client, repository, _ = context
    subscribe(client)

    assert client.post(
        "/api/forget-me", json={"kindle_address": SELINA}
    ).status_code == 200
    assert repository.subscriber_by_kindle_address(SELINA) is None


def test_unsubscribing_someone_who_is_not_subscribed(context):
    client, _, _ = context
    response = client.post("/api/unsubscribe", json={"kindle_address": SELINA})
    assert response.status_code == 404


# --------------------------------------------------------------- on demand


def test_on_demand_requires_a_subscriber(context):
    client, _, _ = context
    response = client.post("/api/on-demand", json={"kindle_address": SELINA})
    assert response.status_code == 404


def test_on_demand_accepts_a_wikipedia_url_and_returns_immediately(context, monkeypatch):
    """The reader is waiting for an email, not for this response."""
    client, _, _ = context
    subscribe(client)

    delivered: list[str] = []
    monkeypatch.setattr(
        api, "_deliver_on_demand",
        lambda config, subscriber, url, mailer: delivered.append(url),
    )

    response = client.post(
        "/api/on-demand",
        json={"kindle_address": SELINA, "url": "https://de.wikipedia.org/wiki/Wien"},
    )

    assert response.status_code == 202
    assert delivered == ["https://de.wikipedia.org/wiki/Wien"]


def test_on_demand_works_for_a_mixed_case_address(context, monkeypatch):
    client, _, _ = context
    subscribe(client)
    monkeypatch.setattr(api, "_deliver_on_demand", lambda *a: None)

    assert (
        client.post("/api/on-demand", json={"kindle_address": SELINA}).status_code == 202
    )


def test_on_demand_rejects_a_url_outside_the_allowlist(context):
    client, _, _ = context
    subscribe(client)

    response = client.post(
        "/api/on-demand",
        json={"kindle_address": SELINA, "url": "http://127.0.0.1:8000/admin"},
    )

    assert response.status_code == 400
    assert "Wikipedia" in response.json()["error"]


def test_on_demand_does_not_reveal_whether_an_address_is_subscribed(context):
    """An unsubscribed address and an unknown one must be indistinguishable."""
    client, _, _ = context
    subscribe(client)
    client.post("/api/unsubscribe", json={"kindle_address": SELINA})

    gone = client.post("/api/on-demand", json={"kindle_address": SELINA})
    unknown = client.post("/api/on-demand", json={"kindle_address": "who_Ab@kindle.com"})

    assert gone.status_code == unknown.status_code == 404
    assert gone.json() == unknown.json()


# -------------------------------------------------------------- resilience


def test_a_dead_database_gives_a_fast_503_not_a_hang(tmp_path):
    """A pool that cannot hand out a connection used to stall the request for
    the default timeout and then return an unexplained 500."""
    from psycopg_pool import PoolTimeout

    class ExhaustedPool:
        def connection(self):
            raise PoolTimeout("couldn't get a connection after 5.0 sec")

    config = Settings(database_url="postgresql://unused", storage_dir=tmp_path)
    app = api.create_app()
    app.dependency_overrides[api.get_settings] = lambda: config
    app.dependency_overrides[api.get_mailer] = lambda: RecordingMailer()
    api._pool = ExhaustedPool()

    try:
        with TestClient(app) as client:
            response = client.post("/api/unsubscribe", json={"kindle_address": SELINA})
        assert response.status_code == 503
        assert "unavailable" in response.json()["detail"].lower()
    finally:
        api._pool = None
