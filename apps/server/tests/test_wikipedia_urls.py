"""The allowlist is the only thing standing between a public form and SSRF.

See docs/adr/0003-wikipedia-only-url-allowlist.md.
"""
import pytest

from wikindle.sources.wikipedia import InvalidArticleUrl, normalise_article_url

ACCEPTED = [
    "https://en.wikipedia.org/wiki/Richard_Titmuss",
    "https://de.wikipedia.org/wiki/Wien",
    "https://simple.wikipedia.org/wiki/Cat",
    "https://zh-yue.wikipedia.org/wiki/Hong_Kong",
    # percent-encoded and fragment-bearing links are what people actually paste
    "https://de.wikipedia.org/wiki/Russischer_%C3%9Cberfall",
    "https://en.wikipedia.org/wiki/Cat#Behaviour",
]

REJECTED = [
    # --- the reason this function exists -------------------------------
    "http://127.0.0.1:8000/internal/admin",
    "http://localhost:5432/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]:8000/",
    "file:///etc/passwd",
    "gopher://evil.test/",
    # --- hosts that merely look like Wikipedia --------------------------
    "https://en.wikipedia.org.evil.test/wiki/Cat",
    "https://evil.test/en.wikipedia.org/wiki/Cat",
    "https://en.wikipedia.org@evil.test/wiki/Cat",
    "https://enwikipedia.org/wiki/Cat",
    # --- real Wikimedia, still not Wikipedia ----------------------------
    "https://en.wikivoyage.org/wiki/Vienna",
    "https://commons.wikimedia.org/wiki/Main_Page",
    # --- right host, wrong shape ----------------------------------------
    "http://en.wikipedia.org/wiki/Cat",  # plaintext
    "https://en.wikipedia.org/w/index.php?title=Cat&action=edit",
    "https://en.wikipedia.org/wiki/Special:Random",
    "https://en.wikipedia.org/wiki/Talk:Cat",
    "https://en.wikipedia.org/wiki/",
    "https://en.wikipedia.org/",
    "",
    "not a url at all",
]


@pytest.mark.parametrize("url", ACCEPTED)
def test_accepts_wikipedia_articles(url):
    assert normalise_article_url(url) == url.split("#")[0]


@pytest.mark.parametrize("url", REJECTED)
def test_rejects_everything_else(url):
    with pytest.raises(InvalidArticleUrl):
        normalise_article_url(url)


def test_strips_fragment_so_cache_keys_match():
    """Two people pasting the same article with different anchors must hit
    the same Conversion rather than converting it twice."""
    a = normalise_article_url("https://en.wikipedia.org/wiki/Cat#Behaviour")
    b = normalise_article_url("https://en.wikipedia.org/wiki/Cat#Diet")
    assert a == b == "https://en.wikipedia.org/wiki/Cat"


def test_language_is_extracted_from_the_host():
    from wikindle.sources.wikipedia import article_language

    assert article_language("https://de.wikipedia.org/wiki/Wien") == "de"
    assert article_language("https://en.wikipedia.org/wiki/Cat") == "en"
