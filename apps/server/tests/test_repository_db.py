"""The real SQL, against a real PostgreSQL.

Skipped unless ``WIKINDLE_TEST_DATABASE_URL`` points at a throwaway database:

    docker run --rm -d -p 55432:5432 -e POSTGRES_PASSWORD=x --name wikindle-test postgres:17-alpine
    WIKINDLE_TEST_DATABASE_URL=postgresql://postgres:x@localhost:55432/postgres \\
        pytest tests/test_repository_db.py
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from wikindle.models import DeliveryKind, DeliveryStatus, PoolArticle, SubscriberStatus
from wikindle.repository import PostgresRepository

DATABASE_URL = os.environ.get("WIKINDLE_TEST_DATABASE_URL")
SCHEMA = Path(__file__).resolve().parents[3] / "db" / "schema.sql"

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not DATABASE_URL, reason="WIKINDLE_TEST_DATABASE_URL is not set"),
]

TODAY = date(2026, 8, 3)


@pytest.fixture
def repository():
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DROP TABLE IF EXISTS deliveries, editions, conversions,
                                     subscribers, pool_articles CASCADE
                """
            )
            cursor.execute(SCHEMA.read_text())
        connection.commit()
        yield PostgresRepository(connection)
        connection.rollback()


def test_the_schema_applies_cleanly(repository):
    assert repository.pool_size("en") == 0


def test_subscriber_lifecycle(repository):
    created = repository.create_subscriber("me_aBc@kindle.com", "Europe/Vienna")
    assert created.status is SubscriberStatus.ACTIVE
    assert [s.id for s in repository.active_subscribers()] == [created.id]

    repository.unsubscribe(created.id)
    assert repository.active_subscribers() == []


def test_the_database_preserves_the_case_of_a_kindle_address(repository):
    """Regression: lowercasing yields an address Amazon discards silently."""
    address = "selina.liball16_nPgTrV@kindle.com"

    repository.create_subscriber(address, None)

    assert repository.subscriber_by_kindle_address(address).kindle_address == address
    assert repository.subscriber_by_kindle_address(address.lower()) is None


def test_subscribing_again_reactivates_rather_than_erroring(repository):
    created = repository.create_subscriber("me_aBc@kindle.com", "Europe/Vienna")
    repository.unsubscribe(created.id)

    again = repository.create_subscriber("me_aBc@kindle.com", None)

    assert again.id == created.id
    assert again.status is SubscriberStatus.ACTIVE
    assert again.timezone == "Europe/Vienna", "an omitted timezone must not erase one"


def built_conversion(repository, url="https://en.wikipedia.org/wiki/Cat"):
    conversion = repository.start_conversion(url, "1")
    repository.finish_conversion(
        conversion.id,
        title="Cat",
        language="en",
        epub_path="/tmp/cat.epub",
        epub_bytes=1234,
        word_count=2500,
        images_kept=10,
        images_missing=0,
    )
    return repository.conversion(conversion.id)


def test_conversion_cache_key_is_url_plus_version(repository):
    url = "https://en.wikipedia.org/wiki/Cat"
    built_conversion(repository, url)

    assert repository.built_conversion(url, "1") is not None
    assert repository.built_conversion(url, "2") is None, "a version bump must rebuild"


def test_a_failed_conversion_is_not_served_from_cache(repository):
    conversion = repository.start_conversion("https://en.wikipedia.org/wiki/Cat", "1")
    repository.fail_conversion(conversion.id, "pandoc exploded")

    assert repository.built_conversion("https://en.wikipedia.org/wiki/Cat", "1") is None


def test_unsent_article_excludes_articles_already_used_as_editions(repository):
    repository.replace_pool(
        "en",
        [
            PoolArticle("https://en.wikipedia.org/wiki/Cat", "Cat", "en", "good"),
            PoolArticle("https://en.wikipedia.org/wiki/Dog", "Dog", "en", "good"),
        ],
    )
    conversion = built_conversion(repository, "https://en.wikipedia.org/wiki/Cat")
    repository.create_edition(TODAY, conversion.id)

    picked = repository.unsent_article("en")
    assert picked.source_url.endswith("/Dog")


def test_unsent_article_honours_the_exclude_set(repository):
    repository.replace_pool(
        "en",
        [
            PoolArticle("https://en.wikipedia.org/wiki/Cat", "Cat", "en", "good"),
            PoolArticle("https://en.wikipedia.org/wiki/Dog", "Dog", "en", "good"),
        ],
    )

    picked = repository.unsent_article(
        "en", exclude={"https://en.wikipedia.org/wiki/Cat"}
    )
    assert picked.source_url.endswith("/Dog")


def test_unsent_article_with_an_empty_exclude_set(repository):
    """An empty array parameter must not break the ANY() cast."""
    repository.replace_pool(
        "en", [PoolArticle("https://en.wikipedia.org/wiki/Cat", "Cat", "en", "good")]
    )
    assert repository.unsent_article("en", exclude=set()) is not None


def test_creating_the_same_edition_twice_is_idempotent(repository):
    """Regression: ON CONFLICT DO NOTHING returned no row, so a repeated build
    raised instead of returning the Edition that already existed."""
    first = built_conversion(repository, "https://en.wikipedia.org/wiki/Cat")
    second = built_conversion(repository, "https://en.wikipedia.org/wiki/Dog")

    repository.create_edition(TODAY, first.id)
    again = repository.create_edition(TODAY, second.id)

    assert again.conversion_id == first.id, "the first build for a date wins"
    assert repository.edition_on(TODAY).conversion_id == first.id


def test_pool_sync_is_repeatable(repository):
    articles = [PoolArticle("https://en.wikipedia.org/wiki/Cat", "Cat", "en", "good")]
    repository.replace_pool("en", articles)
    repository.replace_pool("en", articles)

    assert repository.pool_size("en") == 1


def test_one_delivery_per_subscriber_per_edition_is_enforced_by_the_database(repository):
    subscriber = repository.create_subscriber("me_aBc@kindle.com", None)
    conversion = built_conversion(repository)
    repository.create_edition(TODAY, conversion.id)

    repository.record_delivery(
        subscriber.id, conversion.id, DeliveryKind.DAILY, edition_date=TODAY
    )
    assert repository.delivery_exists(subscriber.id, TODAY)

    with pytest.raises(psycopg.errors.UniqueViolation):
        repository.record_delivery(
            subscriber.id, conversion.id, DeliveryKind.DAILY, edition_date=TODAY
        )


def test_on_demand_deliveries_are_not_covered_by_that_constraint(repository):
    """Several extra articles a day is the point; the unique index is partial."""
    subscriber = repository.create_subscriber("me_aBc@kindle.com", None)
    conversion = built_conversion(repository)

    for _ in range(3):
        repository.record_delivery(subscriber.id, conversion.id, DeliveryKind.ON_DEMAND)

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    assert repository.on_demand_count_since(subscriber.id, since) == 3


def test_marking_a_delivery_sent_records_the_provider_id(repository):
    subscriber = repository.create_subscriber("me_aBc@kindle.com", None)
    conversion = built_conversion(repository)
    delivery_id = repository.record_delivery(
        subscriber.id, conversion.id, DeliveryKind.ON_DEMAND
    )

    repository.mark_delivery(delivery_id, DeliveryStatus.SENT, "msg_123")

    with repository._connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, provider_message_id, sent_at FROM deliveries WHERE id = %s",
            (delivery_id,),
        )
        status, provider_id, sent_at = cursor.fetchone()
    assert (status, provider_id) == ("sent", "msg_123")
    assert sent_at is not None


def test_deleting_a_subscriber_removes_their_deliveries(repository):
    subscriber = repository.create_subscriber("me_aBc@kindle.com", None)
    conversion = built_conversion(repository)
    repository.record_delivery(subscriber.id, conversion.id, DeliveryKind.ON_DEMAND)

    repository.delete_subscriber(subscriber.id)

    with repository._connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM deliveries")
        assert cursor.fetchone()[0] == 0
