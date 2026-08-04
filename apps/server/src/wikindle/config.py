"""Runtime configuration.

Language is a setting rather than a constant so the whole deployment can be
pointed at another Wikipedia without touching code.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WIKINDLE_", env_file=".env")

    database_url: str = "postgresql://wikindle@localhost/wikindle"

    #: Which Wikipedia the daily Edition is drawn from. Pasted links may be any
    #: language; this only governs the shared daily article.
    language: str = "en"

    #: Where built EPUBs live on the box.
    storage_dir: Path = Path("/var/lib/wikindle/epub")

    #: Bumping this invalidates the Conversion cache, so a converter fix is not
    #: hidden behind EPUBs built by the code it replaced.
    converter_version: str = "1"

    #: A Conversion that lost more than this share of its images is not fit to
    #: become an Edition.
    max_missing_image_ratio: float = 0.25

    #: How far ahead Editions are built. One means "the evening before".
    build_ahead_days: int = 1

    #: Per-Subscriber cap on on-demand conversions per day. Protects the send
    #: quota and Wikimedia, not the Subscriber — see ADR 0005.
    on_demand_daily_limit: int = 5

    #: Signs one-click unsubscribe links. Changing it invalidates every link
    #: already sitting in somebody's inbox, so set it once and leave it.
    secret_key: str = "insecure-development-secret"

    #: Deep link to Amazon's Personal Document Settings. Defaults to the US site
    #: because signup is open; a mostly German-speaking readership would be
    #: better served by pointing this at amazon.de. Deliberately carries no
    #: ``:~:text=`` highlight — that is Chromium-only and would not match on a
    #: page rendered in another language anyway.
    amazon_settings_url: str = "https://www.amazon.com/hz/mycd/myx#/home/settings/payment"

    resend_api_key: str = ""
    #: Typed into Amazon Approved Sender Lists by hand, so it can never change
    #: without silently cutting off every existing Subscriber. See
    #: docs/adr/0007-sending-domain.md.
    sender_address: str = "read@wikindle.xyz"
    sender_name: str = "wikindle"
    public_url: str = "https://api.wikindle.xyz"

    request_timeout_seconds: float = 30.0


_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
