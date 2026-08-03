"""The domain, as data. Vocabulary is defined in CONTEXT.md."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class SubscriberStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    UNSUBSCRIBED = "unsubscribed"


class ConversionStatus(StrEnum):
    BUILDING = "building"
    BUILT = "built"
    FAILED = "failed"


class DeliveryKind(StrEnum):
    DAILY = "daily"
    WELCOME = "welcome"
    ON_DEMAND = "on_demand"


class DeliveryStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Subscriber:
    id: int
    kindle_address: str
    contact_email: str
    status: SubscriberStatus
    timezone: str | None = None
    confirm_token: str | None = None
    confirmed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Conversion:
    id: int
    source_url: str
    converter_version: str
    status: ConversionStatus
    title: str | None = None
    language: str | None = None
    epub_path: str | None = None
    epub_bytes: int | None = None
    word_count: int | None = None
    images_kept: int | None = None
    images_missing: int | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PoolArticle:
    source_url: str
    title: str
    language: str
    quality: str


@dataclass(frozen=True, slots=True)
class Edition:
    edition_date: date
    conversion_id: int
