"""Bring an existing database up to the schema this code expects.

``db/schema.sql`` only runs when PostgreSQL initialises an empty data directory,
so a database that already exists has to be changed by a migration. They ship
inside the package and are applied on startup, so that code which needs a schema
change can never be running without it — see
docs/adr/0010-migrations-apply-on-startup.md.

A migration is a plain ``.sql`` file in ``wikindle/migrations``, applied in
filename order and exactly once. Two rules:

* No ``BEGIN``/``COMMIT`` — each file already runs in one transaction, together
  with the row that records it.
* It must be harmless on a database that ``schema.sql`` has just built, because
  a fresh deployment gets the current schema *and then* every migration. In
  practice: ``IF EXISTS`` / ``IF NOT EXISTS`` throughout.
"""
from __future__ import annotations

import logging
from importlib import resources

import psycopg

log = logging.getLogger(__name__)

#: The api and the scheduler start together and both migrate; whoever loses the
#: race waits here and then finds nothing left to do. The number is arbitrary.
LOCK_KEY = 7_945_120_001


def migration_scripts() -> list[tuple[str, str]]:
    """Every migration as ``(filename, sql)``, in the order they apply."""
    directory = resources.files("wikindle") / "migrations"
    return sorted(
        (entry.name, entry.read_text(encoding="utf-8"))
        for entry in directory.iterdir()
        if entry.name.endswith(".sql")
    )


def apply_migrations(database_url: str) -> list[str]:
    """Apply whatever has not been applied yet; return the filenames that ran."""
    ran: list[str] = []

    with psycopg.connect(database_url, autocommit=True) as connection:
        # Session-level, so it is released when the connection closes.
        connection.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name       TEXT        PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        applied = {
            name for (name,) in connection.execute("SELECT name FROM schema_migrations")
        }

        for name, sql in migration_scripts():
            if name in applied:
                continue
            with connection.transaction():
                connection.execute(sql)
                connection.execute(
                    "INSERT INTO schema_migrations (name) VALUES (%s)", (name,)
                )
            log.info("applied migration %s", name)
            ran.append(name)

    return ran
