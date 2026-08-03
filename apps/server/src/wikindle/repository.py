"""Persistence.

The services talk to this interface rather than to SQL, so their behaviour —
idempotent fan-out, rate limits, the quality gate — is testable without a
database. :class:`PostgresRepository` is the real one.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Protocol

import psycopg
from psycopg.rows import class_row, dict_row

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


class Repository(Protocol):
    # -- subscribers ------------------------------------------------------
    def create_pending_subscriber(
        self, kindle_address: str, contact_email: str, timezone_name: str | None,
        confirm_token: str,
    ) -> Subscriber: ...
    def subscriber_by_token(self, token: str) -> Subscriber | None: ...
    def subscriber_by_kindle_address(self, kindle_address: str) -> Subscriber | None: ...
    def activate_subscriber(self, subscriber_id: int) -> None: ...
    def unsubscribe(self, subscriber_id: int) -> None: ...
    def delete_subscriber(self, subscriber_id: int) -> None: ...
    def active_subscribers(self) -> list[Subscriber]: ...

    # -- conversions ------------------------------------------------------
    def built_conversion(self, source_url: str, version: str) -> Conversion | None: ...
    def start_conversion(self, source_url: str, version: str) -> Conversion: ...
    def finish_conversion(self, conversion_id: int, **fields) -> None: ...
    def fail_conversion(self, conversion_id: int, error: str) -> None: ...
    def conversion(self, conversion_id: int) -> Conversion | None: ...

    # -- editions ---------------------------------------------------------
    def edition_on(self, on: date) -> Edition | None: ...
    def create_edition(self, on: date, conversion_id: int) -> Edition: ...

    # -- article pool -----------------------------------------------------
    def replace_pool(self, language: str, articles: list[PoolArticle]) -> int: ...
    def unsent_article(
        self, language: str, exclude: set[str] | None = None
    ) -> PoolArticle | None: ...
    def pool_size(self, language: str) -> int: ...

    # -- deliveries -------------------------------------------------------
    def delivery_exists(self, subscriber_id: int, edition_date: date) -> bool: ...
    def record_delivery(
        self, subscriber_id: int, conversion_id: int, kind: DeliveryKind,
        edition_date: date | None = None,
    ) -> int: ...
    def mark_delivery(
        self, delivery_id: int, status: DeliveryStatus,
        provider_message_id: str | None = None, error: str | None = None,
    ) -> None: ...
    def on_demand_count_since(self, subscriber_id: int, since: datetime) -> int: ...


class PostgresRepository:
    """psycopg3 over the schema in ``db/schema.sql``."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self._connection = connection

    # -- subscribers ------------------------------------------------------

    def create_pending_subscriber(
        self, kindle_address: str, contact_email: str, timezone_name: str | None,
        confirm_token: str,
    ) -> Subscriber:
        row = self._one(
            """
            INSERT INTO subscribers (kindle_address, contact_email, timezone,
                                     confirm_token, status)
            VALUES (%s, %s, %s, %s, 'pending')
            ON CONFLICT (kindle_address) DO UPDATE
                SET contact_email = EXCLUDED.contact_email,
                    timezone      = EXCLUDED.timezone,
                    confirm_token = EXCLUDED.confirm_token,
                    status        = CASE WHEN subscribers.status = 'active'
                                         THEN 'active' ELSE 'pending' END
            RETURNING *
            """,
            (kindle_address, contact_email, timezone_name, confirm_token),
        )
        return _subscriber(row)

    def subscriber_by_token(self, token: str) -> Subscriber | None:
        row = self._maybe_one(
            "SELECT * FROM subscribers WHERE confirm_token = %s", (token,)
        )
        return _subscriber(row) if row else None

    def subscriber_by_kindle_address(self, kindle_address: str) -> Subscriber | None:
        row = self._maybe_one(
            "SELECT * FROM subscribers WHERE kindle_address = %s", (kindle_address,)
        )
        return _subscriber(row) if row else None

    def activate_subscriber(self, subscriber_id: int) -> None:
        self._execute(
            """
            UPDATE subscribers
               SET status = 'active', confirmed_at = now(), confirm_token = NULL
             WHERE id = %s
            """,
            (subscriber_id,),
        )

    def unsubscribe(self, subscriber_id: int) -> None:
        self._execute(
            """
            UPDATE subscribers
               SET status = 'unsubscribed', unsubscribed_at = now()
             WHERE id = %s
            """,
            (subscriber_id,),
        )

    def delete_subscriber(self, subscriber_id: int) -> None:
        self._execute("DELETE FROM subscribers WHERE id = %s", (subscriber_id,))

    def active_subscribers(self) -> list[Subscriber]:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT * FROM subscribers WHERE status = 'active' ORDER BY id"
            )
            return [_subscriber(row) for row in cursor.fetchall()]

    # -- conversions ------------------------------------------------------

    def built_conversion(self, source_url: str, version: str) -> Conversion | None:
        row = self._maybe_one(
            """
            SELECT * FROM conversions
             WHERE source_url = %s AND converter_version = %s AND status = 'built'
            """,
            (source_url, version),
        )
        return _conversion(row) if row else None

    def start_conversion(self, source_url: str, version: str) -> Conversion:
        row = self._one(
            """
            INSERT INTO conversions (source_url, converter_version, status)
            VALUES (%s, %s, 'building')
            ON CONFLICT (source_url, converter_version) DO UPDATE
                SET status = 'building', error = NULL
            RETURNING *
            """,
            (source_url, version),
        )
        return _conversion(row)

    def finish_conversion(self, conversion_id: int, **fields) -> None:
        self._execute(
            """
            UPDATE conversions
               SET status = 'built', built_at = now(), error = NULL,
                   title = %(title)s, language = %(language)s,
                   epub_path = %(epub_path)s, epub_bytes = %(epub_bytes)s,
                   word_count = %(word_count)s, images_kept = %(images_kept)s,
                   images_missing = %(images_missing)s
             WHERE id = %(id)s
            """,
            {"id": conversion_id, **fields},
        )

    def fail_conversion(self, conversion_id: int, error: str) -> None:
        self._execute(
            "UPDATE conversions SET status = 'failed', error = %s WHERE id = %s",
            (error[:2000], conversion_id),
        )

    def conversion(self, conversion_id: int) -> Conversion | None:
        row = self._maybe_one("SELECT * FROM conversions WHERE id = %s", (conversion_id,))
        return _conversion(row) if row else None

    # -- editions ---------------------------------------------------------

    def edition_on(self, on: date) -> Edition | None:
        row = self._maybe_one(
            "SELECT * FROM editions WHERE edition_date = %s", (on,)
        )
        return Edition(row["edition_date"], row["conversion_id"]) if row else None

    def create_edition(self, on: date, conversion_id: int) -> Edition:
        # DO NOTHING would return no row when the Edition already exists, so a
        # repeated build would raise instead of being idempotent. Keeping the
        # existing conversion_id makes the first build for a date the winner.
        row = self._one(
            """
            INSERT INTO editions (edition_date, conversion_id) VALUES (%s, %s)
            ON CONFLICT (edition_date) DO UPDATE
                SET conversion_id = editions.conversion_id
            RETURNING *
            """,
            (on, conversion_id),
        )
        return Edition(row["edition_date"], row["conversion_id"])

    # -- article pool -----------------------------------------------------

    def replace_pool(self, language: str, articles: list[PoolArticle]) -> int:
        with self._connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO pool_articles (source_url, title, language, quality)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (source_url) DO UPDATE
                    SET title = EXCLUDED.title, quality = EXCLUDED.quality,
                        synced_at = now()
                """,
                [(a.source_url, a.title, a.language, a.quality) for a in articles],
            )
        return len(articles)

    def unsent_article(
        self, language: str, exclude: set[str] | None = None
    ) -> PoolArticle | None:
        """A random Article of the right language that has never been an Edition.

        ``exclude`` lets a caller skip candidates it has already tried and
        rejected within one run, which is otherwise indistinguishable from an
        Article that has never been picked.
        """
        row = self._maybe_one(
            """
            SELECT p.source_url, p.title, p.language, p.quality
              FROM pool_articles p
             WHERE p.language = %s
               AND NOT (p.source_url = ANY(%s::text[]))
               AND NOT EXISTS (
                     SELECT 1
                       FROM editions e
                       JOIN conversions c ON c.id = e.conversion_id
                      WHERE c.source_url = p.source_url
                   )
             ORDER BY random()
             LIMIT 1
            """,
            (language, list(exclude or ())),
        )
        return PoolArticle(**row) if row else None

    def pool_size(self, language: str) -> int:
        row = self._one(
            "SELECT count(*) AS n FROM pool_articles WHERE language = %s", (language,)
        )
        return row["n"]

    # -- deliveries -------------------------------------------------------

    def delivery_exists(self, subscriber_id: int, edition_date: date) -> bool:
        row = self._maybe_one(
            """
            SELECT 1 AS hit FROM deliveries
             WHERE subscriber_id = %s AND edition_date = %s
            """,
            (subscriber_id, edition_date),
        )
        return row is not None

    def record_delivery(
        self, subscriber_id: int, conversion_id: int, kind: DeliveryKind,
        edition_date: date | None = None,
    ) -> int:
        row = self._one(
            """
            INSERT INTO deliveries (subscriber_id, conversion_id, kind,
                                    edition_date, status)
            VALUES (%s, %s, %s, %s, 'queued')
            RETURNING id
            """,
            (subscriber_id, conversion_id, str(kind), edition_date),
        )
        return row["id"]

    def mark_delivery(
        self, delivery_id: int, status: DeliveryStatus,
        provider_message_id: str | None = None, error: str | None = None,
    ) -> None:
        self._execute(
            """
            UPDATE deliveries
               SET status = %s,
                   provider_message_id = %s,
                   error = %s,
                   sent_at = CASE WHEN %s = 'sent' THEN now() ELSE sent_at END
             WHERE id = %s
            """,
            (str(status), provider_message_id, error, str(status), delivery_id),
        )

    def on_demand_count_since(self, subscriber_id: int, since: datetime) -> int:
        row = self._one(
            """
            SELECT count(*) AS n FROM deliveries
             WHERE subscriber_id = %s AND kind = 'on_demand' AND created_at >= %s
            """,
            (subscriber_id, since),
        )
        return row["n"]

    # -- plumbing ---------------------------------------------------------

    def _execute(self, sql: str, params) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(sql, params)

    def _one(self, sql: str, params) -> dict:
        row = self._maybe_one(sql, params)
        if row is None:
            raise LookupError("expected exactly one row")
        return row

    def _maybe_one(self, sql: str, params) -> dict | None:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()


def _subscriber(row: dict) -> Subscriber:
    return Subscriber(
        id=row["id"],
        kindle_address=row["kindle_address"],
        contact_email=row["contact_email"],
        status=SubscriberStatus(row["status"]),
        timezone=row.get("timezone"),
        confirm_token=row.get("confirm_token"),
        confirmed_at=row.get("confirmed_at"),
    )


def _conversion(row: dict) -> Conversion:
    return Conversion(
        id=row["id"],
        source_url=row["source_url"],
        converter_version=row["converter_version"],
        status=ConversionStatus(row["status"]),
        title=row.get("title"),
        language=row.get("language"),
        epub_path=row.get("epub_path"),
        epub_bytes=row.get("epub_bytes"),
        word_count=row.get("word_count"),
        images_kept=row.get("images_kept"),
        images_missing=row.get("images_missing"),
        error=row.get("error"),
    )


def start_of_utc_day(moment: datetime | None = None) -> datetime:
    moment = moment or datetime.now(timezone.utc)
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)
