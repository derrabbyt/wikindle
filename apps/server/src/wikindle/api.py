"""The HTTP surface.

Reached through a Cloudflare Tunnel, so the box itself accepts no inbound
connections. Handlers are ordinary ``def`` functions: everything they do is
blocking, and FastAPI runs them in a threadpool, which is the right shape for a
single box doing a handful of requests a minute.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Annotated, Iterator

import psycopg
from fastapi import BackgroundTasks, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from wikindle.config import Settings, settings as load_settings
from wikindle.mail import Mailer, RecordingMailer, ResendMailer
from wikindle.models import Subscriber, SubscriberStatus
from wikindle.repository import PostgresRepository, Repository
from wikindle.services.conversions import ConversionService, QualityGateFailed
from wikindle.services.ondemand import (
    NothingLeftToSend,
    OnDemandService,
    RateLimitExceeded,
)
from wikindle.services.subscriptions import AlreadyActive, SubscriptionService
from wikindle.sources.wikipedia import InvalidArticleUrl

log = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


# --------------------------------------------------------------- dependencies


def get_settings() -> Settings:
    return load_settings()


_pool = None


def get_connection(
    config: Annotated[Settings, Depends(get_settings)],
) -> Iterator[psycopg.Connection]:
    global _pool
    if _pool is None:
        from psycopg_pool import ConnectionPool

        _pool = ConnectionPool(config.database_url, min_size=1, max_size=8, open=True)
    with _pool.connection() as connection:
        yield connection


def get_repository(
    connection: Annotated[psycopg.Connection, Depends(get_connection)],
) -> Repository:
    return PostgresRepository(connection)


def get_mailer(config: Annotated[Settings, Depends(get_settings)]) -> Mailer:
    if not config.resend_api_key:
        log.warning("no Resend API key configured — mail will be recorded, not sent")
        return RecordingMailer()
    return ResendMailer(
        config.resend_api_key, f"{config.sender_name} <{config.sender_address}>"
    )


# ----------------------------------------------------------------- rate limits


class IpRateLimiter:
    """A crude per-IP window, kept in memory because there is only one box."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self._limit = limit
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True


_signup_limiter = IpRateLimiter(limit=10, window_seconds=3600)
_on_demand_limiter = IpRateLimiter(limit=20, window_seconds=3600)


# --------------------------------------------------------------------- schemas


class SubscribeRequest(BaseModel):
    kindle_address: str = Field(max_length=254)
    contact_email: str = Field(max_length=254)
    #: Captured from the browser so per-timezone delivery stays possible later
    #: without having to ask every existing Subscriber.
    timezone: str | None = Field(default=None, max_length=64)


class AddressRequest(BaseModel):
    kindle_address: str = Field(max_length=254)


class OnDemandRequest(BaseModel):
    kindle_address: str = Field(max_length=254)
    #: Absent means "surprise me".
    url: str | None = Field(default=None, max_length=2048)


# ------------------------------------------------------------------------ app


def create_app() -> FastAPI:
    app = FastAPI(title="wikindle", docs_url=None, redoc_url=None)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # no cookies, no credentials, no authenticated state
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
        max_age=86400,
    )

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "service": "wikindle"}

    @app.post("/api/subscribe", status_code=202)
    def subscribe(
        body: SubscribeRequest,
        request: Request,
        repository: Annotated[Repository, Depends(get_repository)],
        mailer: Annotated[Mailer, Depends(get_mailer)],
        config: Annotated[Settings, Depends(get_settings)],
    ):
        if not _signup_limiter.check(_client_ip(request)):
            return _error(429, "Too many signups from this address. Try later.")

        service = SubscriptionService(repository, config, mailer)
        try:
            service.register(body.kindle_address, body.contact_email, body.timezone)
        except AlreadyActive:
            return _error(409, "That Kindle address is already subscribed.")
        except ValueError as exc:
            return _error(400, str(exc))

        return {
            "status": "pending",
            "message": "Check your email inbox for a confirmation link.",
        }

    @app.get("/confirm", response_class=HTMLResponse)
    def confirm(
        token: str,
        repository: Annotated[Repository, Depends(get_repository)],
        mailer: Annotated[Mailer, Depends(get_mailer)],
        config: Annotated[Settings, Depends(get_settings)],
    ):
        service = SubscriptionService(repository, config, mailer)
        try:
            subscriber = service.confirm(token)
        except LookupError:
            return HTMLResponse(
                _page("Link not recognised", "That confirmation link is unknown or "
                      "has already been used."),
                status_code=404,
            )
        return HTMLResponse(_confirmed_page(subscriber, config))

    @app.post("/api/unsubscribe")
    def unsubscribe(
        body: AddressRequest,
        repository: Annotated[Repository, Depends(get_repository)],
        mailer: Annotated[Mailer, Depends(get_mailer)],
        config: Annotated[Settings, Depends(get_settings)],
    ):
        service = SubscriptionService(repository, config, mailer)
        try:
            service.unsubscribe(body.kindle_address)
        except LookupError:
            return _error(404, "That address is not subscribed.")
        except ValueError as exc:
            return _error(400, str(exc))
        return {"status": "unsubscribed"}

    @app.post("/api/forget-me")
    def forget_me(
        body: AddressRequest,
        repository: Annotated[Repository, Depends(get_repository)],
        mailer: Annotated[Mailer, Depends(get_mailer)],
        config: Annotated[Settings, Depends(get_settings)],
    ):
        service = SubscriptionService(repository, config, mailer)
        try:
            service.delete(body.kindle_address)
        except LookupError:
            return _error(404, "That address is not subscribed.")
        except ValueError as exc:
            return _error(400, str(exc))
        return {"status": "deleted"}

    @app.post("/api/on-demand", status_code=202)
    def on_demand(
        body: OnDemandRequest,
        request: Request,
        background: BackgroundTasks,
        repository: Annotated[Repository, Depends(get_repository)],
        mailer: Annotated[Mailer, Depends(get_mailer)],
        config: Annotated[Settings, Depends(get_settings)],
    ):
        """Accept the request and convert in the background.

        An image-heavy article takes over a minute to convert, and the reader is
        waiting for an email rather than for this response, so holding the
        connection open would buy nothing.
        """
        if not _on_demand_limiter.check(_client_ip(request)):
            return _error(429, "Too many requests from this address. Try later.")

        subscriber = repository.subscriber_by_kindle_address(
            body.kindle_address.strip().lower()
        )
        if subscriber is None or subscriber.status is not SubscriberStatus.ACTIVE:
            # Deliberately the same answer either way: this endpoint should not
            # confirm whether a given Kindle address is subscribed.
            return _error(404, "That address is not a confirmed subscriber.")

        service = OnDemandService(
            repository, config, ConversionService(repository, config)
        )
        try:
            service.check_can_send(subscriber)
            if body.url:
                from wikindle.sources.wikipedia import normalise_article_url

                normalise_article_url(body.url)
        except RateLimitExceeded as exc:
            return _error(429, str(exc))
        except InvalidArticleUrl as exc:
            return _error(400, f"Only Wikipedia article links are accepted ({exc}).")

        background.add_task(_deliver_on_demand, config, subscriber, body.url, mailer)
        return {"status": "building", "message": "It will arrive on your Kindle shortly."}

    return app


def _deliver_on_demand(
    config: Settings, subscriber: Subscriber, url: str | None, mailer: Mailer
) -> None:
    """Convert and send, on its own database connection.

    Since FastAPI 0.106 a dependency with ``yield`` is torn down *before*
    background tasks run, so the request's connection is already back in the
    pool by now. Reusing the request-scoped Repository here would work against a
    closed connection; a conversion takes up to a minute anyway, which is far
    too long to hold a pooled connection open for.
    """
    try:
        with psycopg.connect(config.database_url) as connection:
            repository = PostgresRepository(connection)
            service = OnDemandService(
                repository, config, ConversionService(repository, config)
            )
            if url:
                service.send_article(subscriber, url, mailer)
            else:
                service.send_random(subscriber, mailer)
            connection.commit()
    except (QualityGateFailed, NothingLeftToSend, RateLimitExceeded) as exc:
        log.warning("on-demand for %s failed: %s", subscriber.kindle_address, exc)
    except Exception:
        log.exception("on-demand for %s crashed", subscriber.kindle_address)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get(
        "x-forwarded-for", ""
    )
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status, headers=SECURITY_HEADERS)


def _page(heading: str, body: str) -> str:
    return (
        "<!doctype html><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{heading} — wikindle</title>"
        "<style>body{font:16px/1.6 system-ui,sans-serif;max-width:34rem;"
        "margin:4rem auto;padding:0 1.5rem;color:#18202a}"
        "h1{font-size:1.6rem;line-height:1.25}code{background:#eef1f5;"
        "padding:.15em .4em;border-radius:.3em}</style>"
        f"<h1>{heading}</h1>{body}"
    )


def _confirmed_page(subscriber: Subscriber, config: Settings) -> str:
    return _page(
        "You're subscribed",
        "<p><strong>One step left, and without it nothing will arrive.</strong></p>"
        "<p>Amazon only accepts documents from addresses you have approved. Open "
        "Amazon → Account &amp; Lists → Content and Devices → Preferences → "
        "Personal Document Settings, and add this address to your "
        "<em>Approved Personal Document E-mail List</em>:</p>"
        f"<p><code>{config.sender_address}</code></p>"
        f"<p>Until you do, mail to <code>{subscriber.kindle_address}</code> is "
        "discarded silently — Amazon sends no bounce, so we cannot tell that it "
        "happened.</p>",
    )


app = create_app()
