"""Migrations, against a real PostgreSQL where one is available.

The database tests are skipped unless ``WIKINDLE_TEST_DATABASE_URL`` is set —
see test_repository_db.py for how to start a throwaway one.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg
import pytest

from wikindle.migrate import apply_migrations, migration_scripts
from wikindle.repository import PostgresRepository

DATABASE_URL = os.environ.get("WIKINDLE_TEST_DATABASE_URL")
SCHEMA = Path(__file__).resolve().parents[3] / "db" / "schema.sql"


def needs_db(test):
    skip = pytest.mark.skipif(
        not DATABASE_URL, reason="WIKINDLE_TEST_DATABASE_URL is not set"
    )
    return pytest.mark.db(skip(test))


def test_scripts_are_found_in_order():
    names = [name for name, _sql in migration_scripts()]
    assert names[:2] == [
        "0001-drop-confirmation.sql",
        "0002-subscriber-interval-and-pool.sql",
    ]
    assert names == sorted(names)


def test_scripts_leave_the_transaction_to_the_runner():
    for name, sql in migration_scripts():
        assert not re.search(r"^\s*(BEGIN|COMMIT)\b", sql, re.I | re.M), name


@pytest.fixture
def fresh_database():
    """A database exactly as schema.sql leaves it, with no migration history."""
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            """
            DROP TABLE IF EXISTS deliveries, editions, conversions, subscribers,
                                 pool_articles, schema_migrations CASCADE
            """
        )
        connection.execute(SCHEMA.read_text())
        yield connection
        connection.execute("DROP TABLE IF EXISTS schema_migrations")


@needs_db
def test_every_migration_is_harmless_on_a_fresh_schema_and_runs_once(fresh_database):
    expected = [name for name, _sql in migration_scripts()]

    assert apply_migrations(DATABASE_URL) == expected
    assert apply_migrations(DATABASE_URL) == []


@needs_db
def test_a_database_behind_the_code_is_brought_up_to_date(fresh_database):
    # Production before 0002: the code writes two columns that are not there.
    fresh_database.execute(
        "ALTER TABLE subscribers DROP COLUMN interval_days, DROP COLUMN article_pool"
    )

    apply_migrations(DATABASE_URL)

    with psycopg.connect(DATABASE_URL) as connection:
        subscriber = PostgresRepository(connection).create_subscriber(
            "me@kindle.com", None, 7, "good"
        )
        connection.rollback()
    assert (subscriber.interval_days, subscriber.article_pool) == (7, "good")


@needs_db
def test_a_failing_migration_leaves_no_trace(fresh_database, monkeypatch):
    broken = [("0001-broken.sql", "CREATE TABLE half_done (id INT); SELECT 1/0;")]
    monkeypatch.setattr("wikindle.migrate.migration_scripts", lambda: broken)

    with pytest.raises(psycopg.Error):
        apply_migrations(DATABASE_URL)

    assert fresh_database.execute("SELECT to_regclass('half_done')").fetchone() == (None,)
    assert fresh_database.execute("SELECT count(*) FROM schema_migrations").fetchone() == (0,)
