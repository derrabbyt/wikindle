"""Keeping a local copy of the wiki's quality articles.

Uniform random Wikipedia is mostly stubs, so the daily Article is drawn from the
featured and good article categories instead — 51,535 titles on the English
wiki, about 141 years of daily Editions.

Syncing them locally also means the daily run does not depend on the MediaWiki
API being reachable at four in the morning.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote

import requests

from wikindle.models import PoolArticle

log = logging.getLogger(__name__)

#: Category names are per-wiki, so a new language needs an entry here rather
#: than a code change.
QUALITY_CATEGORIES: dict[str, dict[str, str]] = {
    "en": {"featured": "Category:Featured articles", "good": "Category:Good articles"},
    "de": {"featured": "Kategorie:Wikipedia:Exzellent", "good": "Kategorie:Wikipedia:Lesenswert"},
    "simple": {"featured": "Category:Very good articles", "good": "Category:Good articles"},
}

_PAGE_SIZE = 500


class UnsupportedLanguage(ValueError):
    """No quality categories are configured for this wiki."""


@dataclass(frozen=True, slots=True)
class SyncReport:
    language: str
    fetched: int
    stored: int


def fetch_quality_articles(
    language: str, session: requests.Session, *, page_limit: int | None = None
) -> list[PoolArticle]:
    """Page through the wiki's quality categories, newest listing first.

    ``page_limit`` exists so tests and smoke runs can take a slice instead of
    103 requests.
    """
    try:
        categories = QUALITY_CATEGORIES[language]
    except KeyError as exc:
        raise UnsupportedLanguage(
            f"no quality categories configured for {language!r}"
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
                # Featured wins over good when an article is in both.
                if url not in articles or quality == "featured":
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
