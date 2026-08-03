"""Sending a Subscriber an extra article, now.

Not authenticated beyond the Kindle Address — see
docs/adr/0005-on-demand-sends-are-unauthenticated.md. The rate limit here exists
to protect the send quota and to be polite to Wikimedia, not as a security
control.
"""
from __future__ import annotations

import logging
from pathlib import Path

from wikindle.config import Settings
from wikindle.mail import Mailer, Message
from wikindle.models import DeliveryKind, DeliveryStatus, Subscriber
from wikindle.repository import Repository, start_of_utc_day
from wikindle.services.conversions import ConversionService
from wikindle.services.editions import daily_body, epub_filename
from wikindle.sources.wikipedia import normalise_article_url

log = logging.getLogger(__name__)


class RateLimitExceeded(RuntimeError):
    """This Subscriber has had their allowance of extra articles today."""


class NothingLeftToSend(RuntimeError):
    """Every Article in the pool has already been an Edition."""


class OnDemandService:
    def __init__(
        self,
        repository: Repository,
        settings: Settings,
        conversions: ConversionService,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._conversions = conversions

    def send_random(self, subscriber: Subscriber, mailer: Mailer) -> None:
        article = self._repository.unsent_article(self._settings.language)
        if article is None:
            raise NothingLeftToSend("the article pool is exhausted")
        self.send_article(subscriber, article.source_url, mailer)

    def send_article(self, subscriber: Subscriber, url: str, mailer: Mailer) -> None:
        url = normalise_article_url(url)
        self.check_can_send(subscriber)

        conversion = self._conversions.get_or_build(url)
        delivery_id = self._repository.record_delivery(
            subscriber.id, conversion.id, DeliveryKind.ON_DEMAND
        )

        message = Message(
            to=subscriber.kindle_address,
            subject=conversion.title or "Your Wikipedia article",
            text=daily_body(conversion.title, conversion.source_url),
            attachment=Path(conversion.epub_path),
            attachment_name=epub_filename(conversion.title),
        )
        try:
            provider_id = mailer.send(message)
        except Exception as exc:
            self._repository.mark_delivery(
                delivery_id, DeliveryStatus.FAILED, error=str(exc)[:2000]
            )
            raise

        self._repository.mark_delivery(delivery_id, DeliveryStatus.SENT, provider_id)

    def check_can_send(self, subscriber: Subscriber) -> None:
        """Raise if this Subscriber has used their allowance for today.

        Public so the API can answer a request synchronously before accepting it
        for background conversion.
        """
        used = self._repository.on_demand_count_since(subscriber.id, start_of_utc_day())
        if used >= self._settings.on_demand_daily_limit:
            raise RateLimitExceeded(
                f"{used} extra articles already sent today; the limit is "
                f"{self._settings.on_demand_daily_limit}"
            )
