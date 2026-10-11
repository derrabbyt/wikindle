"""Joining and leaving.

There is no confirmation step. Confirming a second mailbox proved control of
that mailbox and never of the Kindle, so it prevented nobody from subscribing
somebody else's device. Amazon's Approved Sender List does that, and it is
enforced by Amazon rather than by us. See docs/adr/0008-no-confirmation-step.md.

A consequence worth holding in mind while reading this file: wikindle now has no
channel to a human at all. The only address we store is a Kindle Address, which
cannot receive a message without an attachment. Anything a Subscriber needs to
know has to be said on the website at the moment they sign up.
"""
from __future__ import annotations

import logging

from wikindle.addresses import normalise_kindle_address
from wikindle.config import Settings
from wikindle.models import Subscriber
from wikindle.repository import Repository

log = logging.getLogger(__name__)


class SubscriptionService:
    def __init__(self, repository: Repository, settings: Settings) -> None:
        self._repository = repository
        self._settings = settings

    def register(
        self,
        kindle_address: str,
        timezone_name: str | None = None,
        interval_days: int = 1,
        article_pool: str = "featured",
    ) -> Subscriber:
        """Subscribe a Kindle Address, effective immediately.

        Idempotent: an address that is already subscribed stays subscribed, and
        one that previously unsubscribed is reactivated. There is nothing to
        confirm, so there is no window in which a signup can be abandoned.
        """
        if interval_days < 1 or interval_days > 30:
            raise ValueError("interval_days must be between 1 and 30")
        if article_pool not in {"featured", "good", "unusual_articles", "did_you_know", "vital_articles", "common_misconceptions", "featured_articles"}:
            raise ValueError("unsupported article pool")

        address = normalise_kindle_address(kindle_address)
        subscriber = self._repository.create_subscriber(
            address,
            timezone_name,
            interval_days,
            article_pool,
        )
        log.info("subscribed %s (%s)", address, article_pool)
        return subscriber

    def unsubscribe(self, kindle_address: str) -> None:
        subscriber = self._repository.subscriber_by_kindle_address(
            normalise_kindle_address(kindle_address)
        )
        if subscriber is None:
            raise LookupError("not subscribed")
        self._repository.unsubscribe(subscriber.id)

    def delete(self, kindle_address: str) -> None:
        subscriber = self._repository.subscriber_by_kindle_address(
            normalise_kindle_address(kindle_address)
        )
        if subscriber is None:
            raise LookupError("not subscribed")
        self._repository.delete_subscriber(subscriber.id)
