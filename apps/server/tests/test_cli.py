"""The command line, focusing on the smoke test that needs no database."""
from __future__ import annotations

from pathlib import Path

import pytest

from wikindle import cli
from wikindle.config import Settings
from wikindle.convert.pipeline import ConversionResult
from wikindle.mail import RecordingMailer


def fake_conversion(tmp_path: Path) -> ConversionResult:
    epub = tmp_path / "made.epub"
    epub.write_bytes(b"EPUB")
    return ConversionResult(
        source_url="https://en.wikipedia.org/wiki/Kintsugi",
        title="Kintsugi",
        language="en",
        epub_path=epub,
        epub_bytes=4,
        word_count=1200,
        images_kept=5,
        images_missing=0,
        icons_dropped=0,
    )


@pytest.fixture
def configured(monkeypatch, tmp_path):
    config = Settings(
        database_url="postgresql://unused",
        storage_dir=tmp_path,
        resend_api_key="re_test",
        sender_address="read@wikindle.xyz",
    )
    mailer = RecordingMailer()
    monkeypatch.setattr(cli, "_mailer", lambda _config: mailer)
    monkeypatch.setattr(cli, "load_settings", lambda: config)
    return config, mailer


def test_send_test_converts_and_mails_without_touching_a_database(
    configured, monkeypatch, tmp_path
):
    config, mailer = configured
    converted: list[str] = []

    def convert(url, destination, **kwargs):
        converted.append(url)
        return fake_conversion(tmp_path)

    monkeypatch.setattr(cli, "convert_article", convert)

    assert cli.command_send_test(config, "me@kindle.com") == 0

    assert converted == [cli.DEFAULT_TEST_ARTICLE]
    assert mailer.sent[0].to == "me@kindle.com"
    assert mailer.sent[0].subject == "Kintsugi"
    assert mailer.sent[0].attachment_name == "Kintsugi.epub"


def test_send_test_honours_an_explicit_url(configured, monkeypatch, tmp_path):
    config, _ = configured
    converted: list[str] = []
    monkeypatch.setattr(
        cli, "convert_article",
        lambda url, destination, **kw: (converted.append(url), fake_conversion(tmp_path))[1],
    )

    cli.command_send_test(config, "me@kindle.com", "https://de.wikipedia.org/wiki/Wien")

    assert converted == ["https://de.wikipedia.org/wiki/Wien"]


def test_send_test_refuses_to_pretend_when_no_api_key_is_set(monkeypatch, tmp_path):
    """Silently 'succeeding' into a RecordingMailer would be the worst possible
    outcome for a command whose entire purpose is proving mail works."""
    config = Settings(
        database_url="postgresql://unused", storage_dir=tmp_path, resend_api_key=""
    )
    monkeypatch.setattr(cli, "_mailer", lambda _c: RecordingMailer())
    monkeypatch.setattr(
        cli, "convert_article",
        lambda *a, **k: pytest.fail("must not convert without somewhere to send"),
    )

    assert cli.command_send_test(config, "me@kindle.com") == 1


def test_send_test_requires_a_recipient(configured):
    with pytest.raises(SystemExit):
        cli.main(["send-test"])
