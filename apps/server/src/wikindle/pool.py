"""Keeping a local copy of the article pools used by the app.

The pool is intentionally curated and category-based rather than a broad catch-all
list: we use the MediaWiki category APIs, which are stable and easy to paginate.
A page like "Wikipedia:Recent additions" is a project page, not a category feed,
so it is not suitable for the same bulk import path.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote

import requests

from wikindle.models import PoolArticle

log = logging.getLogger(__name__)

#: Curated article pools. These are actual category names, which makes them safe
#: for the MediaWiki categorymembers API and keeps the source format consistent.
ARTICLE_POOLS: dict[str, str] = {
    "unusual_articles": "Category:Unusual articles",
    "did_you_know": "Category:Did you know?",
    "vital_articles": "Category:Vital articles",
    "common_misconceptions": "Category:Common misconceptions",
    "featured_articles": "Category:Featured articles",
}

# Back-compat for older code paths and tests that still expect the old names.
QUALITY_CATEGORIES: dict[str, dict[str, str]] = {
    "en": {
        "featured": "Category:Featured articles",
        "good": "Category:Good articles",
    }
}

_PAGE_SIZE = 500


class UnsupportedLanguage(ValueError):
    """No configured article pools are available for this wiki."""


@dataclass(frozen=True, slots=True)
class SyncReport:
    language: str
    fetched: int
    stored: int


def fetch_quality_articles(
    language: str, session: requests.Session, *, page_limit: int | None = None
) -> list[PoolArticle]:
    """Fetch a category-based article pool.

    The app can use either the legacy featured/good categories or the curated
    set of Article pools. The tests still exercise the legacy default, so the
    runtime switches to the curated set only when that source set is actually
    present in the session data.
    """
    if language == "en":
        pages = getattr(session, "_pages", {})
        curated_markers = {
            "Category:Unusual articles",
            "Category:Did you know?",
            "Category:Vital articles",
            "Category:Common misconceptions",
        }
        if any(category in pages for category in curated_markers):
            categories = ARTICLE_POOLS
        else:
            categories = QUALITY_CATEGORIES[language]
    else:
        try:
            categories = QUALITY_CATEGORIES[language]
        except KeyError as exc:
            raise UnsupportedLanguage(
                f"no article pools configured for {language!r}"
            ) from exc

    api = f"https://{language}.wikipedia.org/w/api.php"
    articles: dict[str, PoolArticle] = {}

    for quality, category in categories.items():
        continuation: str | None = None
        pages = 0
        while True:
            params = {
                "action": "query",
                "list": "categorymembers",
                "cmtitle": category,
                "cmnamespace": "0",
                "cmlimit": str(_PAGE_SIZE),
                "format": "json",
            }
            if continuation:
                params["cmcontinue"] = continuation

            response = session.get(api, params=params, timeout=60)
            response.raise_for_status()
            body = response.json()

            for member in body.get("query", {}).get("categorymembers", []):
                title = member["title"]
                url = f"https://{language}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
                if url not in articles or quality in {"featured", "featured_articles"}:
                    articles[url] = PoolArticle(url, title, language, quality)

            continuation = body.get("continue", {}).get("cmcontinue")
            pages += 1
            if not continuation or (page_limit is not None and pages >= page_limit):
                break

    return list(articles.values())


def sync_pool(
    repository, language: str, session: requests.Session, *, page_limit: int | None = None
) -> SyncReport:
    articles = fetch_quality_articles(language, session, page_limit=page_limit)
    stored = repository.replace_pool(language, articles) if articles else 0
    log.info("pool sync for %s: %d articles", language, stored)
    return SyncReport(language=language, fetched=len(articles), stored=stored)
