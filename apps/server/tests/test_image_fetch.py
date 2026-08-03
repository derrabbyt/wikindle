"""Image fetching.

Wikimedia rate-limits image requests hard. Converting two articles back to back
from a residential IP was enough to earn a burst of 429s, and the converter's
response was to drop nine images and report success.
"""
import pytest
import requests

from wikindle.convert.images import ImageFetcher, RateLimited


class FakeResponse:
    def __init__(self, status_code, content=b"IMG", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


class FakeSession:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return self._responses.pop(0) if self._responses else FakeResponse(200)


def fetcher(session, **kw):
    slept = []
    kw.setdefault("politeness_delay", 0.0)
    return ImageFetcher(session, sleep=slept.append, **kw), slept


def test_fetches_an_image():
    session = FakeSession(FakeResponse(200, b"JPEGDATA"))
    f, _ = fetcher(session)
    assert f.fetch("https://upload.wikimedia.org/a.jpg") == b"JPEGDATA"


def test_retries_after_a_429_and_succeeds():
    session = FakeSession(
        FakeResponse(429), FakeResponse(429), FakeResponse(200, b"EVENTUALLY")
    )
    f, slept = fetcher(session, max_attempts=4)

    assert f.fetch("https://upload.wikimedia.org/a.jpg") == b"EVENTUALLY"
    assert len(session.calls) == 3
    assert slept, "a rate-limited retry must back off rather than hammer"
    assert slept == sorted(slept), "backoff must not shrink between attempts"


def test_honours_retry_after_header():
    session = FakeSession(
        FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(200)
    )
    f, slept = fetcher(session, max_attempts=2)

    f.fetch("https://upload.wikimedia.org/a.jpg")
    assert 7 in slept


def test_persistent_rate_limiting_raises_rather_than_silently_dropping():
    """The old behaviour printed a warning and exited 0, shipping a degraded
    EPUB that the delivery log recorded as a success."""
    session = FakeSession(*[FakeResponse(429) for _ in range(5)])
    f, _ = fetcher(session, max_attempts=3)

    with pytest.raises(RateLimited):
        f.fetch("https://upload.wikimedia.org/a.jpg")
    assert len(session.calls) == 3


def test_retries_server_errors_too():
    session = FakeSession(FakeResponse(503), FakeResponse(200, b"OK"))
    f, _ = fetcher(session, max_attempts=3)
    assert f.fetch("https://upload.wikimedia.org/a.jpg") == b"OK"


def test_does_not_retry_a_404():
    session = FakeSession(FakeResponse(404))
    f, _ = fetcher(session, max_attempts=4)

    assert f.fetch("https://upload.wikimedia.org/gone.jpg") is None
    assert len(session.calls) == 1, "a missing image is not worth retrying"


def test_waits_between_consecutive_fetches():
    """The 429 burst came from fetching ~50 images as fast as the network
    allowed. Spacing them out is the actual fix; retrying is the safety net."""
    session = FakeSession(FakeResponse(200), FakeResponse(200))
    f, slept = fetcher(session, politeness_delay=0.25)

    f.fetch("https://upload.wikimedia.org/a.jpg")
    f.fetch("https://upload.wikimedia.org/b.jpg")

    assert slept.count(0.25) == 1, "delay applies between fetches, not before the first"


def test_caches_by_url_so_repeated_images_cost_nothing():
    session = FakeSession(FakeResponse(200, b"ONCE"))
    f, _ = fetcher(session)

    assert f.fetch("https://upload.wikimedia.org/a.jpg") == b"ONCE"
    assert f.fetch("https://upload.wikimedia.org/a.jpg") == b"ONCE"
    assert len(session.calls) == 1


def test_refuses_non_http_urls():
    """Articles contain data: URIs; the old code handed them to requests and
    logged a confusing 'No connection adapters' failure."""
    session = FakeSession()
    f, _ = fetcher(session)

    assert f.fetch("data:image/gif;base64,R0lGODlhAQABAIAB") is None
    assert session.calls == []
