"""Command line entry points for the scheduled work.

    python -m wikindle sync-pool     # weekly: refresh the quality article pool
    python -m wikindle build         # evening: build the Editions buffer
    python -m wikindle send          # 04:00 UTC: fan today's Edition out
    python -m wikindle scheduler     # all of the above, on a loop

    python -m wikindle send-test --to you@kindle.com   # smoke test, no database
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg
import requests

from wikindle.config import Settings, settings as load_settings
from wikindle.convert import convert_article
from wikindle.convert.pipeline import USER_AGENT
from wikindle.mail import Mailer, Message, RecordingMailer, ResendMailer
from wikindle.pool import sync_pool
from wikindle.repository import PostgresRepository
from wikindle.services.conversions import ConversionService
from wikindle.services.editions import EditionService, daily_body, epub_filename

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


#: Short, well-illustrated, and pleasant to find on a device you are testing.
DEFAULT_TEST_ARTICLE = "https://en.wikipedia.org/wiki/Kintsugi"


def command_send_test(config: Settings, to: str, url: str | None = None) -> int:
    """Convert one article and mail it, touching no database.

    The whole chain except the schedule: fetch, convert, attach, hand to Resend,
    Amazon, device. Deliberately dependency-free so it can be run from a laptop
    before any infrastructure exists, and again after every deploy.
    """
    url = url or DEFAULT_TEST_ARTICLE
    if not config.resend_api_key:
        log.error("no WIKINDLE_RESEND_API_KEY set — nothing would actually be sent")
        return 1

    mailer = _mailer(config)

    with tempfile.TemporaryDirectory(prefix="wikindle-test-") as scratch:
        result = convert_article(url, Path(scratch) / "test.epub", session=_session())
        log.info(
            "converted %r: %d KB, %d words, %d images (%d missing)",
            result.title, result.epub_bytes // 1024, result.word_count,
            result.images_kept, result.images_missing,
        )

        provider_id = mailer.send(
            Message(
                to=to,
                subject=result.title,
                text=daily_body(result.title, result.source_url),
                attachment=result.epub_path,
                attachment_name=epub_filename(result.title),
            )
        )

    log.info("handed to the provider as %s, from %s", provider_id, config.sender_address)
    log.info(
        "if nothing arrives, %s is almost certainly missing from the Approved "
        "Personal Document E-mail List on the Amazon account owning %s — "
        "Amazon discards it silently",
        config.sender_address, to,
    )
    return 0


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
        "command", choices=["sync-pool", "build", "send", "send-test", "scheduler"]
    )
    parser.add_argument("--date", help="YYYY-MM-DD, for send")
    parser.add_argument("--to", help="Kindle address, for send-test")
    parser.add_argument("--url", help="article to convert, for send-test")
    arguments = parser.parse_args(argv)

    config = load_settings()

    if arguments.command == "sync-pool":
        return command_sync_pool(config)
    if arguments.command == "build":
        return command_build(config)
    if arguments.command == "send":
        on = date.fromisoformat(arguments.date) if arguments.date else None
        return command_send(config, on)
    if arguments.command == "send-test":
        if not arguments.to:
            parser.error("send-test needs --to <your>@kindle.com")
        return command_send_test(config, arguments.to, arguments.url)
    return command_scheduler(config)


if __name__ == "__main__":
    sys.exit(main())
