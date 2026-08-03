"""Pool syncing and the mailer."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from fakes import InMemoryRepository
from wikindle.mail import MailError, Message, RecordingMailer, ResendMailer
from wikindle.pool import UnsupportedLanguage, fetch_quality_articles, sync_pool


class FakeApiSession:
    """Replays paginated MediaWiki category listings."""

    def __init__(self, pages: dict[str, list[dict]]) -> None:
        self._pages = pages
        self.requests: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.requests.append(dict(params or {}))
        category = params["cmtitle"]
        remaining = self._pages.get(category, [])
        index = sum(1 for r in self.requests if r["cmtitle"] == category) - 1
        page = remaining[index] if index < len(remaining) else {"query": {"categorymembers": []}}
        return _FakeResponse(page)


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def member(title):
    return {"title": title, "ns": 0}


def test_fetches_both_quality_categories_and_builds_article_urls():
    session = FakeApiSession(
        {
            "Category:Featured articles": [
                {"query": {"categorymembers": [member("Cat")]}}
            ],
            "Category:Good articles": [
                {"query": {"categorymembers": [member("Dog breed")]}}
            ],
        }
    )

    articles = fetch_quality_articles("en", session)

    urls = {a.source_url for a in articles}
    assert urls == {
        "https://en.wikipedia.org/wiki/Cat",
        "https://en.wikipedia.org/wiki/Dog_breed",
    }
    assert {a.quality for a in articles} == {"featured", "good"}
    assert all(request["cmnamespace"] == "0" for request in session.requests)


def test_follows_continuation_until_the_listing_ends():
    session = FakeApiSession(
        {
            "Category:Featured articles": [
                {
                    "query": {"categorymembers": [member("A")]},
                    "continue": {"cmcontinue": "page|2"},
                },
                {"query": {"categorymembers": [member("B")]}},
            ],
            "Category:Good articles": [{"query": {"categorymembers": []}}],
        }
    )

    articles = fetch_quality_articles("en", session)

    assert len(articles) == 2
    featured_requests = [r for r in session.requests if "Featured" in r["cmtitle"]]
    assert featured_requests[1]["cmcontinue"] == "page|2"


def test_featured_wins_when_an_article_is_in_both_categories():
    session = FakeApiSession(
        {
            "Category:Featured articles": [{"query": {"categorymembers": [member("Cat")]}}],
            "Category:Good articles": [{"query": {"categorymembers": [member("Cat")]}}],
        }
    )

    articles = fetch_quality_articles("en", session)

    assert len(articles) == 1
    assert articles[0].quality == "featured"


def test_a_language_without_configured_categories_is_refused():
    with pytest.raises(UnsupportedLanguage):
        fetch_quality_articles("xx", FakeApiSession({}))


def test_sync_stores_articles_in_the_repository():
    repository = InMemoryRepository()
    session = FakeApiSession(
        {
            "Category:Featured articles": [{"query": {"categorymembers": [member("Cat")]}}],
            "Category:Good articles": [{"query": {"categorymembers": [member("Dog")]}}],
        }
    )

    report = sync_pool(repository, "en", session)

    assert report.stored == 2
    assert repository.pool_size("en") == 2


# ------------------------------------------------------------------- mailer


def test_recording_mailer_keeps_messages():
    mailer = RecordingMailer()
    mailer.send(Message(to="a@kindle.com", subject="s", text="t"))
    assert mailer.sent[0].to == "a@kindle.com"


def resend_with(handler) -> ResendMailer:
    transport = httpx.MockTransport(handler)
    return ResendMailer(
        "key", "wikindle <k@wikindle.test>", client=httpx.Client(transport=transport)
    )


def test_attaches_the_epub_as_base64(tmp_path):
    epub = tmp_path / "Cat.epub"
    epub.write_bytes(b"PK\x03\x04epub-bytes")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer key"
        return httpx.Response(200, json={"id": "msg_1"})

    identifier = resend_with(handler).send(
        Message(
            to="me@kindle.com", subject="Cat", text="body",
            attachment=epub, attachment_name="Cat.epub",
        )
    )

    assert identifier == "msg_1"
    assert captured["to"] == ["me@kindle.com"]
    attachment = captured["attachments"][0]
    assert attachment["filename"] == "Cat.epub"
    assert base64.b64decode(attachment["content"]) == b"PK\x03\x04epub-bytes"


def test_a_provider_error_is_raised_not_swallowed():
    def handler(request):
        return httpx.Response(422, text="validation failed")

    with pytest.raises(MailError, match="422"):
        resend_with(handler).send(Message(to="a@kindle.com", subject="s", text="t"))


def test_refuses_an_attachment_over_the_provider_limit(tmp_path, monkeypatch):
    from wikindle import mail

    monkeypatch.setattr(mail, "MAX_ATTACHMENT_BYTES", 16)
    epub = tmp_path / "big.epub"
    epub.write_bytes(b"x" * 1024)

    def handler(request):  # pragma: no cover - must never be reached
        raise AssertionError("an oversized attachment must not be sent")

    with pytest.raises(MailError, match="limit"):
        resend_with(handler).send(
            Message(to="a@kindle.com", subject="s", text="t", attachment=epub)
        )


def test_an_api_key_is_required():
    with pytest.raises(ValueError):
        ResendMailer("", "k@wikindle.test")
