"""Building Editions ahead of time, and fanning them out.

Editions are built the evening before they are sent, so a failure has somewhere
to be noticed. A build that fails the quality gate costs a candidate Article,
never a delivery.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from wikindle.config import Settings
from wikindle.mail import Mailer, Message
from wikindle.models import DeliveryKind, DeliveryStatus, Edition
from wikindle.repository import Repository
from wikindle.services.conversions import ConversionService

log = logging.getLogger(__name__)


@dataclass
class SendReport:
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    missing_edition: bool = False


class EditionService:
    def __init__(
        self,
        repository: Repository,
        settings: Settings,
        conversions: ConversionService,
        *,
        max_attempts: int = 5,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._conversions = conversions
        self._max_attempts = max_attempts

    def ensure_editions(self, start: date, days: int) -> list[Edition]:
        """Top the buffer back up, from *start* inclusive."""
        built = []
        for offset in range(days):
            edition = self.ensure_edition(start + timedelta(days=offset))
            if edition is not None:
                built.append(edition)
        return built

    def ensure_edition(self, on: date) -> Edition | None:
        """The Edition for *on*, building one if it does not exist yet.

        Returns ``None`` when no candidate Article survived the quality gate,
        which is a situation for a human rather than an exception — the buffer
        absorbs it.
        """
        existing = self._repository.edition_on(on)
        if existing is not None:
            return existing

        tried: set[str] = set()
        for _ in range(self._max_attempts):
            article = self._repository.unsent_article(
                self._settings.language, exclude=tried
            )
            if article is None:
                log.warning("no unsent article left for %s", self._settings.language)
                return None

            tried.add(article.source_url)
            try:
                conversion = self._conversions.get_or_build(article.source_url)
            except Exception as exc:
                log.warning("skipping %s: %s", article.source_url, exc)
                continue

            return self._repository.create_edition(on, conversion.id)

        log.error("gave up building an edition for %s after %d attempts", on, self._max_attempts)
        return None

    def send_edition(self, on: date, mailer: Mailer) -> SendReport:
        """Send the Edition for *on* to every active Subscriber.

        Safe to re-run: whoever already has a Delivery for this date is skipped,
        so a partial fan-out can simply be run again.
        """
        edition = self._repository.edition_on(on)
        if edition is None:
            log.error("no edition built for %s", on)
            return SendReport(missing_edition=True)

        conversion = self._repository.conversion(edition.conversion_id)
        if conversion is None or not conversion.epub_path:
            # Checked before the loop: building the Message would raise, and an
            # exception here would abandon the whole fan-out rather than one
            # recipient.
            log.error("edition %s points at an unusable conversion", on)
            return SendReport(missing_edition=True)

        report = SendReport()

        for subscriber in self._repository.active_subscribers():
            if self._repository.delivery_exists(subscriber.id, on):
                report.skipped += 1
                continue

            delivery_id = self._repository.record_delivery(
                subscriber.id, conversion.id, DeliveryKind.DAILY, edition_date=on
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
                report.failed += 1
                log.warning("delivery to %s failed: %s", subscriber.kindle_address, exc)
                continue

            self._repository.mark_delivery(delivery_id, DeliveryStatus.SENT, provider_id)
            report.sent += 1

        return report


def epub_filename(title: str | None) -> str:
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in (title or "article"))
    return f"{safe.strip()[:80] or 'article'}.epub"


def daily_body(title: str | None, source_url: str) -> str:
    return (
        f"Today's article: {title}\n\n"
        f"{source_url}\n\n"
        "Text is available under CC BY-SA 4.0.\n"
    )
