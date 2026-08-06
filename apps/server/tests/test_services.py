"""Service behaviour: caching, the quality gate, idempotent fan-out, rate limits."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from fakes import InMemoryRepository
from wikindle.config import Settings
from wikindle.convert.pipeline import ConversionFailed, ConversionResult
from wikindle.mail import RecordingMailer
from wikindle.models import ConversionStatus, DeliveryKind, DeliveryStatus, PoolArticle
from wikindle.services.conversions import ConversionService, QualityGateFailed
from wikindle.services.editions import EditionService
from wikindle.services.ondemand import OnDemandService, RateLimitExceeded
from wikindle.services.subscriptions import SubscriptionService

TODAY = date(2026, 8, 3)


def settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        database_url="postgresql://unused",
        storage_dir=tmp_path,
        resend_api_key="unused",
        sender_address="kindle@wikindle.test",
        **overrides,
    )


def result_for(url: str, tmp_path: Path, *, kept=10, missing=0) -> ConversionResult:
    epub = tmp_path / f"{abs(hash(url))}.epub"
    epub.write_bytes(b"EPUB")
    return ConversionResult(
        source_url=url,
        title=url.rsplit("/", 1)[-1].replace("_", " "),
        language="en",
        epub_path=epub,
        epub_bytes=4,
        word_count=2500,
        images_kept=kept,
        images_missing=missing,
        icons_dropped=0,
    )


class SpyConverter:
    """Stands in for the real conversion, which needs pandoc and the network."""

    def __init__(self, tmp_path: Path, *, missing_for: dict[str, int] | None = None,
                 raises_for: set[str] | None = None) -> None:
        self._tmp = tmp_path
        self._missing_for = missing_for or {}
        self._raises_for = raises_for or set()
        self.calls: list[str] = []

    def __call__(self, source_url: str, output_path: Path, **kwargs) -> ConversionResult:
        self.calls.append(source_url)
        if source_url in self._raises_for:
            raise ConversionFailed(f"pandoc hated {source_url}")
        missing = self._missing_for.get(source_url, 0)
        return result_for(source_url, self._tmp, kept=10 - missing, missing=missing)


def article(name: str) -> PoolArticle:
    return PoolArticle(
        source_url=f"https://en.wikipedia.org/wiki/{name}",
        title=name.replace("_", " "),
        language="en",
        quality="good",
    )


# ---------------------------------------------------------------- conversions


def test_a_built_conversion_is_reused_rather_than_rebuilt(tmp_path):
    """Two Subscribers pasting the same link must not convert it twice."""
    repository = InMemoryRepository()
    converter = SpyConverter(tmp_path)
    service = ConversionService(repository, settings(tmp_path), convert=converter)

    url = "https://en.wikipedia.org/wiki/Cat"
    first = service.get_or_build(url)
    second = service.get_or_build(url)

    assert first.id == second.id
    assert converter.calls == [url], "the second request must hit the cache"


def test_a_new_converter_version_rebuilds(tmp_path):
    repository = InMemoryRepository()
    converter = SpyConverter(tmp_path)
    url = "https://en.wikipedia.org/wiki/Cat"

    ConversionService(
        repository, settings(tmp_path, converter_version="1"), convert=converter
    ).get_or_build(url)
    ConversionService(
        repository, settings(tmp_path, converter_version="2"), convert=converter
    ).get_or_build(url)

    assert converter.calls == [url, url]


def test_quality_gate_rejects_a_conversion_that_lost_its_images(tmp_path):
    """A build that silently lost a third of its pictures used to be recorded as
    a success and mailed to everyone."""
    repository = InMemoryRepository()
    url = "https://en.wikipedia.org/wiki/Cat"
    converter = SpyConverter(tmp_path, missing_for={url: 5})
    service = ConversionService(
        repository, settings(tmp_path, max_missing_image_ratio=0.25), convert=converter
    )

    with pytest.raises(QualityGateFailed):
        service.get_or_build(url)

    stored = next(iter(repository.conversions.values()))
    assert stored.status is ConversionStatus.FAILED
    assert "image" in (stored.error or "")


def test_a_conversion_failure_is_recorded_not_swallowed(tmp_path):
    repository = InMemoryRepository()
    url = "https://en.wikipedia.org/wiki/Cat"
    converter = SpyConverter(tmp_path, raises_for={url})
    service = ConversionService(repository, settings(tmp_path), convert=converter)

    with pytest.raises(ConversionFailed):
        service.get_or_build(url)

    assert next(iter(repository.conversions.values())).status is ConversionStatus.FAILED


def test_rejects_a_url_outside_the_allowlist_before_fetching_anything(tmp_path):
    repository = InMemoryRepository()
    converter = SpyConverter(tmp_path)
    service = ConversionService(repository, settings(tmp_path), convert=converter)

    with pytest.raises(ValueError):
        service.get_or_build("http://127.0.0.1:8000/internal")

    assert converter.calls == [], "nothing may be fetched for a rejected URL"


# ------------------------------------------------------------------ editions


def test_building_ahead_creates_an_edition_from_an_unsent_article(tmp_path):
    repository = InMemoryRepository()
    repository.replace_pool("en", [article("Cat")])
    converter = SpyConverter(tmp_path)
    service = EditionService(
        repository,
        settings(tmp_path),
        ConversionService(repository, settings(tmp_path), convert=converter),
    )

    edition = service.ensure_edition(TODAY)

    assert edition is not None
    assert repository.edition_on(TODAY) is not None
    assert converter.calls == ["https://en.wikipedia.org/wiki/Cat"]


def test_building_ahead_is_idempotent(tmp_path):
    repository = InMemoryRepository()
    repository.replace_pool("en", [article("Cat"), article("Dog")])
    converter = SpyConverter(tmp_path)
    service = EditionService(
        repository,
        settings(tmp_path),
        ConversionService(repository, settings(tmp_path), convert=converter),
    )

    service.ensure_edition(TODAY)
    service.ensure_edition(TODAY)

    assert len(converter.calls) == 1, "a second run must not rebuild the Edition"


def test_an_article_that_fails_the_gate_is_replaced_by_another(tmp_path):
    """The buffer exists so a bad build eats a candidate, never a delivery."""
    repository = InMemoryRepository()
    repository.replace_pool("en", [article("Cat"), article("Dog")])
    converter = SpyConverter(
        tmp_path, missing_for={"https://en.wikipedia.org/wiki/Cat": 9}
    )
    config = settings(tmp_path, max_missing_image_ratio=0.25)
    service = EditionService(
        repository, config, ConversionService(repository, config, convert=converter)
    )

    edition = service.ensure_edition(TODAY)

    assert edition is not None
    assert len(converter.calls) == 2
    built = repository.conversion(edition.conversion_id)
    assert built.source_url.endswith("/Dog")


def test_gives_up_after_a_bounded_number_of_bad_articles(tmp_path):
    repository = InMemoryRepository()
    repository.replace_pool("en", [article(f"A{n}") for n in range(10)])
    converter = SpyConverter(
        tmp_path, missing_for={f"https://en.wikipedia.org/wiki/A{n}": 9 for n in range(10)}
    )
    config = settings(tmp_path)
    service = EditionService(
        repository, config, ConversionService(repository, config, convert=converter),
        max_attempts=3,
    )

    assert service.ensure_edition(TODAY) is None
    assert len(converter.calls) == 3


def test_never_repeats_an_article_across_editions(tmp_path):
    repository = InMemoryRepository()
    repository.replace_pool("en", [article("Cat"), article("Dog")])
    config = settings(tmp_path)
    converter = SpyConverter(tmp_path)
    service = EditionService(
        repository, config, ConversionService(repository, config, convert=converter)
    )

    service.ensure_edition(TODAY)
    service.ensure_edition(TODAY + timedelta(days=1))

    assert len(set(converter.calls)) == 2


# ------------------------------------------------------------------ delivery


def confirmed_subscriber(repository, address="a@kindle.com", email=None):
    return repository.create_subscriber(address, "Europe/Vienna")


def edition_service(repository, tmp_path, converter=None):
    config = settings(tmp_path)
    converter = converter or SpyConverter(tmp_path)
    return EditionService(
        repository, config, ConversionService(repository, config, convert=converter)
    )


def test_daily_send_reaches_every_active_subscriber(tmp_path):
    repository = InMemoryRepository()
    repository.replace_pool("en", [article("Cat")])
    confirmed_subscriber(repository, "a@kindle.com")
    confirmed_subscriber(repository, "b@kindle.com")
    mailer = RecordingMailer()
    service = edition_service(repository, tmp_path)
    service.ensure_edition(TODAY)

    report = service.send_edition(TODAY, mailer)

    assert report.sent == 2
    assert {m.to for m in mailer.sent} == {"a@kindle.com", "b@kindle.com"}
    assert all(m.attachment is not None for m in mailer.sent)


def test_daily_send_is_idempotent(tmp_path):
    """A re-run after a partial failure must not send the same Edition twice."""
    repository = InMemoryRepository()
    repository.replace_pool("en", [article("Cat")])
    confirmed_subscriber(repository)
    mailer = RecordingMailer()
    service = edition_service(repository, tmp_path)
    service.ensure_edition(TODAY)

    service.send_edition(TODAY, mailer)
    second = service.send_edition(TODAY, mailer)

    assert len(mailer.sent) == 1
    assert second.sent == 0
    assert second.skipped == 1


def test_one_failed_recipient_does_not_stop_the_others(tmp_path):
    repository = InMemoryRepository()
    repository.replace_pool("en", [article("Cat")])
    confirmed_subscriber(repository, "broken@kindle.com")
    confirmed_subscriber(repository, "fine@kindle.com")
    mailer = RecordingMailer(fail_for={"broken@kindle.com"})
    service = edition_service(repository, tmp_path)
    service.ensure_edition(TODAY)

    report = service.send_edition(TODAY, mailer)

    assert report.sent == 1
    assert report.failed == 1
    failures = [d for d in repository.deliveries if d.status is DeliveryStatus.FAILED]
    assert len(failures) == 1 and failures[0].error


def test_unsubscribed_readers_are_not_sent_to(tmp_path):
    repository = InMemoryRepository()
    repository.replace_pool("en", [article("Cat")])
    gone = confirmed_subscriber(repository, "gone@kindle.com")
    repository.unsubscribe(gone.id)
    mailer = RecordingMailer()
    service = edition_service(repository, tmp_path)
    service.ensure_edition(TODAY)

    assert service.send_edition(TODAY, mailer).sent == 0


def test_sending_an_edition_that_was_never_built_reports_rather_than_crashes(tmp_path):
    repository = InMemoryRepository()
    service = edition_service(repository, tmp_path)

    report = service.send_edition(TODAY, RecordingMailer())

    assert report.sent == 0
    assert report.missing_edition is True


# --------------------------------------------------------------- subscriptions
# There is no confirmation step: it proved control of a mailbox, never of the
# Kindle, so it stopped nobody from subscribing somebody else's device. See
# docs/adr/0008-no-confirmation-step.md.


def test_registering_subscribes_immediately(tmp_path):
    repository = InMemoryRepository()
    service = SubscriptionService(repository, settings(tmp_path))

    service.register("me@kindle.com", "Europe/Vienna")

    subscriber = repository.subscriber_by_kindle_address("me@kindle.com")
    assert subscriber.status.value == "active"
    assert subscriber.timezone == "Europe/Vienna"


def test_registering_sends_nothing(tmp_path):
    """We hold no address a human reads, so there is nothing to send them."""
    repository = InMemoryRepository()
    mailer = RecordingMailer()
    SubscriptionService(repository, settings(tmp_path)).register("me@kindle.com")

    assert mailer.sent == []


def test_registering_twice_is_harmless(tmp_path):
    repository = InMemoryRepository()
    service = SubscriptionService(repository, settings(tmp_path))

    service.register("me@kindle.com")
    service.register("me@kindle.com")

    assert len(repository.subscribers) == 1
    assert repository.subscriber_by_kindle_address("me@kindle.com").status.value == "active"


def test_registering_again_reactivates_someone_who_left(tmp_path):
    repository = InMemoryRepository()
    service = SubscriptionService(repository, settings(tmp_path))
    service.register("me@kindle.com")
    service.unsubscribe("me@kindle.com")

    service.register("me@kindle.com")

    assert repository.subscriber_by_kindle_address("me@kindle.com").status.value == "active"


def test_a_mixed_case_address_is_stored_verbatim(tmp_path):
    repository = InMemoryRepository()
    address = "selina.liball16_nPgTrV@kindle.com"

    SubscriptionService(repository, settings(tmp_path)).register(address)

    assert repository.subscriber_by_kindle_address(address) is not None


def test_a_kindle_address_must_look_like_one(tmp_path):
    service = SubscriptionService(InMemoryRepository(), settings(tmp_path))
    with pytest.raises(ValueError):
        service.register("not-an-address")


def test_an_ordinary_mailbox_is_refused(tmp_path):
    service = SubscriptionService(InMemoryRepository(), settings(tmp_path))
    with pytest.raises(ValueError):
        service.register("me@example.com")


def test_unsubscribing_and_forgetting(tmp_path):
    repository = InMemoryRepository()
    service = SubscriptionService(repository, settings(tmp_path))
    service.register("me@kindle.com")

    service.unsubscribe("me@kindle.com")
    assert repository.subscriber_by_kindle_address("me@kindle.com").status.value == (
        "unsubscribed"
    )

    service.delete("me@kindle.com")
    assert repository.subscriber_by_kindle_address("me@kindle.com") is None


# ------------------------------------------------------------------ on demand


def on_demand(repository, tmp_path, converter=None, **overrides):
    config = settings(tmp_path, **overrides)
    converter = converter or SpyConverter(tmp_path)
    return OnDemandService(
        repository, config, ConversionService(repository, config, convert=converter)
    ), converter


def test_on_demand_sends_a_pasted_article(tmp_path):
    repository = InMemoryRepository()
    subscriber = confirmed_subscriber(repository)
    mailer = RecordingMailer()
    service, converter = on_demand(repository, tmp_path)

    service.send_article(subscriber, "https://de.wikipedia.org/wiki/Wien", mailer)

    assert converter.calls == ["https://de.wikipedia.org/wiki/Wien"]
    assert mailer.sent[0].to == subscriber.kindle_address


def test_on_demand_picks_a_random_unsent_article_when_no_url_is_given(tmp_path):
    repository = InMemoryRepository()
    repository.replace_pool("en", [article("Cat")])
    subscriber = confirmed_subscriber(repository)
    service, converter = on_demand(repository, tmp_path)

    service.send_random(subscriber, RecordingMailer())

    assert converter.calls == ["https://en.wikipedia.org/wiki/Cat"]


def test_on_demand_is_rate_limited_per_subscriber(tmp_path):
    repository = InMemoryRepository()
    subscriber = confirmed_subscriber(repository)
    mailer = RecordingMailer()
    service, _ = on_demand(repository, tmp_path, on_demand_daily_limit=2)

    for index in range(2):
        service.send_article(
            subscriber, f"https://en.wikipedia.org/wiki/A{index}", mailer
        )

    with pytest.raises(RateLimitExceeded):
        service.send_article(subscriber, "https://en.wikipedia.org/wiki/A3", mailer)

    assert len(mailer.sent) == 2


def test_on_demand_refuses_a_url_outside_wikipedia(tmp_path):
    repository = InMemoryRepository()
    subscriber = confirmed_subscriber(repository)
    service, converter = on_demand(repository, tmp_path)

    with pytest.raises(ValueError):
        service.send_article(subscriber, "https://evil.test/wiki/Cat", RecordingMailer())

    assert converter.calls == []
