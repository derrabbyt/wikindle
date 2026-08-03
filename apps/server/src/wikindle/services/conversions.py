"""Getting a Conversion, building one only if we have to."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from wikindle.config import Settings
from wikindle.convert import convert_article
from wikindle.models import Conversion
from wikindle.repository import Repository
from wikindle.sources.wikipedia import normalise_article_url

log = logging.getLogger(__name__)


class QualityGateFailed(RuntimeError):
    """The EPUB was built but is not good enough to send."""


class ConversionService:
    def __init__(
        self, repository: Repository, settings: Settings, *, convert=convert_article
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._convert = convert

    def get_or_build(self, source_url: str) -> Conversion:
        """Return a built Conversion for *source_url*, building it if needed.

        Raises :class:`~wikindle.sources.wikipedia.InvalidArticleUrl` before any
        network access, :class:`QualityGateFailed` if the result lost too many
        images, and whatever the converter raised otherwise. Every failure is
        recorded against the Conversion rather than swallowed.
        """
        url = normalise_article_url(source_url)
        version = self._settings.converter_version

        cached = self._repository.built_conversion(url, version)
        if cached is not None:
            log.debug("serving cached conversion for %s", url)
            return cached

        conversion = self._repository.start_conversion(url, version)
        destination = self._epub_path(url, version)

        try:
            result = self._convert(url, destination)
        except Exception as exc:
            self._repository.fail_conversion(conversion.id, f"{type(exc).__name__}: {exc}")
            raise

        if result.missing_image_ratio > self._settings.max_missing_image_ratio:
            reason = (
                f"{result.images_missing} of {result.images_expected} images missing "
                f"({result.missing_image_ratio:.0%}), over the "
                f"{self._settings.max_missing_image_ratio:.0%} limit"
            )
            self._repository.fail_conversion(conversion.id, reason)
            raise QualityGateFailed(reason)

        self._repository.finish_conversion(
            conversion.id,
            title=result.title,
            language=result.language,
            epub_path=str(result.epub_path),
            epub_bytes=result.epub_bytes,
            word_count=result.word_count,
            images_kept=result.images_kept,
            images_missing=result.images_missing,
        )
        return self._repository.conversion(conversion.id)

    def _epub_path(self, url: str, version: str) -> Path:
        digest = hashlib.sha256(f"{url}@{version}".encode()).hexdigest()[:16]
        slug = url.rsplit("/", 1)[-1][:60] or "article"
        directory = Path(self._settings.storage_dir)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{digest}-{slug}.epub"
