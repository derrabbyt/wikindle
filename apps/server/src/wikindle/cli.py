"""Command line entry points for the scheduled work.

    python -m wikindle sync-pool     # weekly: refresh the quality article pool
    python -m wikindle build         # evening: build the Editions buffer
    python -m wikindle send          # 04:00 UTC: fan today's Edition out
    python -m wikindle scheduler     # all of the above, on a loop
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone

import psycopg
import requests

from wikindle.config import Settings, settings as load_settings
from wikindle.convert.pipeline import USER_AGENT
from wikindle.mail import Mailer, RecordingMailer, ResendMailer
from wikindle.pool import sync_pool
from wikindle.repository import PostgresRepository
from wikindle.services.conversions import ConversionService
from wikindle.services.editions import EditionService

log = logging.getLogger("wikindle")

#: Editions go out at 04:00 UTC — 06:00 in Vienna, so it is on the device before
#: the reader is awake. The build runs the evening before so a failure has all
#: night to be noticed and retried.
SEND_HOUR_UTC = 4
BUILD_HOUR_UTC = 20
POOL_SYNC_WEEKDAY = 6  # Sunday


def _mailer(config: Settings) -> Mailer:
    if not config.resend_api_key:
        log.warning("no Resend API key — mail will be recorded, not sent")
        return RecordingMailer()
    return ResendMailer(
        config.resend_api_key, f"{config.sender_name} <{config.sender_address}>"
    )


def _session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def _editions(connection, config: Settings) -> EditionService:
    repository = PostgresRepository(connection)
    return EditionService(repository, config, ConversionService(repository, config))


def command_sync_pool(config: Settings) -> int:
    with psycopg.connect(config.database_url) as connection:
        report = sync_pool(
            PostgresRepository(connection), config.language, _session()
        )
        connection.commit()
    log.info("pool: %d articles for %s", report.stored, report.language)
    return 0


def command_build(config: Settings) -> int:
    """Top the Edition buffer back up, starting today."""
    today = datetime.now(timezone.utc).date()
    with psycopg.connect(config.database_url) as connection:
        built = _editions(connection, config).ensure_editions(
            today, config.build_ahead_days + 1
        )
        connection.commit()
    log.info("editions ready: %d", len(built))
    return 0 if built else 1


def command_send(config: Settings, on: date | None = None) -> int:
    on = on or datetime.now(timezone.utc).date()
    with psycopg.connect(config.database_url) as connection:
        report = _editions(connection, config).send_edition(on, _mailer(config))
        connection.commit()
    log.info(
        "edition %s: %d sent, %d failed, %d skipped", on, report.sent, report.failed,
        report.skipped,
    )
    return 1 if report.missing_edition else 0


def command_scheduler(config: Settings) -> int:
    """Run the jobs on a loop, checking once a minute.

    A loop rather than cron: it keeps the schedule in the same place as the code
    that implements it, and there is only one box to run it on.
    """
    last_run: dict[str, str] = {}

    while True:
        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y-%m-%d")

        def due(name: str, hour: int) -> bool:
            return now.hour == hour and last_run.get(name) != stamp

        try:
            if due("build", BUILD_HOUR_UTC):
                last_run["build"] = stamp
                command_build(config)
            if due("send", SEND_HOUR_UTC):
                last_run["send"] = stamp
                command_send(config)
            if now.weekday() == POOL_SYNC_WEEKDAY and due("pool", BUILD_HOUR_UTC - 1):
                last_run["pool"] = stamp
                command_sync_pool(config)
        except Exception:
            log.exception("scheduled job failed; continuing")

        time.sleep(60)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s %(message)s"
    )
    parser = argparse.ArgumentParser(prog="wikindle")
    parser.add_argument(
        "command", choices=["sync-pool", "build", "send", "scheduler"]
    )
    parser.add_argument("--date", help="YYYY-MM-DD, for send")
    arguments = parser.parse_args(argv)

    config = load_settings()

    if arguments.command == "sync-pool":
        return command_sync_pool(config)
    if arguments.command == "build":
        return command_build(config)
    if arguments.command == "send":
        on = date.fromisoformat(arguments.date) if arguments.date else None
        return command_send(config, on)
    return command_scheduler(config)


if __name__ == "__main__":
    sys.exit(main())
