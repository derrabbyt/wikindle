"""Wikipedia as a source of Articles.

The allowlist here is a security boundary, not a convenience check: Subscribers
paste URLs into a public form and this process fetches them, from a host that
also runs PostgreSQL and the API. Everything not provably a Wikipedia article is
refused. See docs/adr/0003-wikipedia-only-url-allowlist.md.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

#: ``en``, ``simple``, ``zh-yue`` — a language subdomain of wikipedia.org, and
#: nothing else. Anchored at both ends so ``en.wikipedia.org.evil.test`` and
#: ``enwikipedia.org`` both fail.
_HOST = re.compile(r"^[a-z]{2,}(?:-[a-z0-9]+)*\.wikipedia\.org$")

_ARTICLE_PATH = re.compile(r"^/wiki/(?P<title>[^/].*)$")

#: Titles prefixed with one of these are project pages, not Articles. English and
#: German are spelled out because those are the wikis we actually read; an
#: unlisted namespace in another language would convert a talk page, which is
#: harmless next to what the host check prevents.
_NAMESPACES = frozenset(
    n.lower()
    for n in (
        "Special", "Talk", "User", "User talk", "Wikipedia", "Wikipedia talk",
        "WP", "File", "File talk", "Image", "MediaWiki", "MediaWiki talk",
        "Template", "Template talk", "Help", "Help talk", "Category",
        "Category talk", "Portal", "Portal talk", "Draft", "Draft talk",
        "TimedText", "Module", "Module talk", "Gadget", "Book", "Course",
        "Spezial", "Diskussion", "Benutzer", "Benutzer Diskussion", "Datei",
        "Vorlage", "Hilfe", "Kategorie", "Portal Diskussion",
    )
)


class InvalidArticleUrl(ValueError):
    """The URL is not a Wikipedia article we are willing to fetch."""


def normalise_article_url(url: str) -> str:
    """Return the canonical form of *url*, or raise :class:`InvalidArticleUrl`.

    Canonical means: no fragment, so that two Subscribers pasting the same
    article with different anchors share one Conversion instead of building it
    twice.
    """
    if not url or not isinstance(url, str):
        raise InvalidArticleUrl("no URL given")

    try:
        parts = urlsplit(url.strip())
    except ValueError as exc:  # malformed IPv6 literals land here
        raise InvalidArticleUrl(f"unparseable URL: {exc}") from exc

    if parts.scheme != "https":
        raise InvalidArticleUrl("only https is accepted")

    # .hostname strips any userinfo, so https://en.wikipedia.org@evil.test
    # is correctly seen as evil.test.
    host = parts.hostname or ""
    if not _HOST.match(host):
        raise InvalidArticleUrl(f"{host!r} is not a Wikipedia language site")

    try:
        port = parts.port
    except ValueError as exc:
        raise InvalidArticleUrl("invalid port") from exc
    if port not in (None, 443):
        raise InvalidArticleUrl("only the default https port is accepted")

    if parts.query:
        raise InvalidArticleUrl("query strings are not accepted")

    match = _ARTICLE_PATH.match(parts.path)
    if not match:
        raise InvalidArticleUrl("not an /wiki/<title> article path")

    title = match.group("title")
    prefix, _, rest = title.partition(":")
    if rest and prefix.replace("_", " ").strip().lower() in _NAMESPACES:
        raise InvalidArticleUrl(f"{prefix!r} is a project namespace, not an article")

    return f"https://{host}{parts.path}"


def article_language(url: str) -> str:
    """The wiki's language code, taken from the host of an accepted URL."""
    host = urlsplit(url).hostname or ""
    return host.split(".", 1)[0]
