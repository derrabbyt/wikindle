"""An in-memory Repository, so service behaviour is testable without Postgres."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone

from wikindle.models import (
    Conversion,
    ConversionStatus,
    DeliveryKind,
    DeliveryStatus,
    Edition,
    PoolArticle,
    Subscriber,
    SubscriberStatus,
)


@dataclass
class DeliveryRow:
    id: int
    subscriber_id: int
    conversion_id: int
    kind: DeliveryKind
    edition_date: date | None
    status: DeliveryStatus = DeliveryStatus.QUEUED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    provider_message_id: str | None = None
    error: str | None = None


class InMemoryRepository:
    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.subscribers: dict[int, Subscriber] = {}
        self.conversions: dict[int, Conversion] = {}
        self.editions: dict[date, Edition] = {}
        self.pool: dict[str, PoolArticle] = {}
        self.deliveries: list[DeliveryRow] = []

    # -- subscribers ------------------------------------------------------

    def create_subscriber(self, kindle_address, timezone_name=None) -> Subscriber:
        existing = self.subscriber_by_kindle_address(kindle_address)
        if existing:
            updated = replace(
                existing,
                status=SubscriberStatus.ACTIVE,
                timezone=timezone_name or existing.timezone,
            )
            self.subscribers[existing.id] = updated
            return updated

        subscriber = Subscriber(
            id=next(self._ids),
            kindle_address=kindle_address,
            status=SubscriberStatus.ACTIVE,
            timezone=timezone_name,
        )
        self.subscribers[subscriber.id] = subscriber
        return subscriber

    def subscriber_by_kindle_address(self, kindle_address):
        return next(
            (s for s in self.subscribers.values() if s.kindle_address == kindle_address),
            None,
        )

    def unsubscribe(self, subscriber_id):
        current = self.subscribers[subscriber_id]
        self.subscribers[subscriber_id] = replace(
            current, status=SubscriberStatus.UNSUBSCRIBED
        )

    def delete_subscriber(self, subscriber_id):
        self.subscribers.pop(subscriber_id, None)
        self.deliveries = [d for d in self.deliveries if d.subscriber_id != subscriber_id]

    def active_subscribers(self):
        return [
            s
            for s in sorted(self.subscribers.values(), key=lambda s: s.id)
            if s.status is SubscriberStatus.ACTIVE
        ]

    # -- conversions ------------------------------------------------------

    def built_conversion(self, source_url, version):
        return next(
            (
                c
                for c in self.conversions.values()
                if c.source_url == source_url
                and c.converter_version == version
                and c.status is ConversionStatus.BUILT
            ),
            None,
        )

    def start_conversion(self, source_url, version):
        existing = next(
            (
                c
                for c in self.conversions.values()
                if c.source_url == source_url and c.converter_version == version
            ),
            None,
        )
        if existing:
            updated = replace(existing, status=ConversionStatus.BUILDING, error=None)
            self.conversions[existing.id] = updated
            return updated

        conversion = Conversion(
            id=next(self._ids),
            source_url=source_url,
            converter_version=version,
            status=ConversionStatus.BUILDING,
        )
        self.conversions[conversion.id] = conversion
        return conversion

    def finish_conversion(self, conversion_id, **fields):
        self.conversions[conversion_id] = replace(
            self.conversions[conversion_id],
            status=ConversionStatus.BUILT,
            error=None,
            **fields,
        )

    def fail_conversion(self, conversion_id, error):
        self.conversions[conversion_id] = replace(
            self.conversions[conversion_id], status=ConversionStatus.FAILED, error=error
        )

    def conversion(self, conversion_id):
        return self.conversions.get(conversion_id)

    # -- editions ---------------------------------------------------------

    def edition_on(self, on):
        return self.editions.get(on)

    def create_edition(self, on, conversion_id):
        edition = Edition(on, conversion_id)
        self.editions.setdefault(on, edition)
        return self.editions[on]

    # -- article pool -----------------------------------------------------

    def replace_pool(self, language, articles):
        for article in articles:
            self.pool[article.source_url] = article
        return len(articles)

    def unsent_article(self, language, exclude=None):
        used = {
            self.conversions[e.conversion_id].source_url
            for e in self.editions.values()
            if e.conversion_id in self.conversions
        } | set(exclude or ())
        candidates = [
            a
            for a in self.pool.values()
            if a.language == language and a.source_url not in used
        ]
        return candidates[0] if candidates else None

    def pool_size(self, language):
        return sum(1 for a in self.pool.values() if a.language == language)

    # -- deliveries -------------------------------------------------------

    def delivery_exists(self, subscriber_id, edition_date):
        return any(
            d.subscriber_id == subscriber_id and d.edition_date == edition_date
            for d in self.deliveries
        )

    def record_delivery(self, subscriber_id, conversion_id, kind, edition_date=None):
        row = DeliveryRow(
            id=next(self._ids),
            subscriber_id=subscriber_id,
            conversion_id=conversion_id,
            kind=kind,
            edition_date=edition_date,
        )
        self.deliveries.append(row)
        return row.id

    def mark_delivery(self, delivery_id, status, provider_message_id=None, error=None):
        for row in self.deliveries:
            if row.id == delivery_id:
                row.status = status
                row.provider_message_id = provider_message_id
                row.error = error

    def on_demand_count_since(self, subscriber_id, since):
        return sum(
            1
            for d in self.deliveries
            if d.subscriber_id == subscriber_id
            and d.kind is DeliveryKind.ON_DEMAND
            and d.created_at >= since
        )
