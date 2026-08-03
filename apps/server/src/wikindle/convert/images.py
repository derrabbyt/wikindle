"""Fetching and transcoding the images in an Article.

Wikimedia rate-limits image requests per client, and an article's worth of
images fetched as fast as the network allows is enough to trigger it. Spacing
requests out is the fix; backing off on a 429 is the safety net. Neither existed
before, and the result was EPUBs that silently lost a third of their pictures.
"""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests
from PIL import Image

#: Kindle screens are small and the device is slow; larger costs size for nothing.
MAX_DIMENSIONS = (1200, 1600)
JPEG_QUALITY = 85

_RETRYABLE = frozenset({429, 500, 502, 503, 504})


class RateLimited(RuntimeError):
    """Wikimedia kept refusing after every retry."""


class ImageFetcher:
    """Downloads images politely, with caching and backoff.

    A single instance should serve a whole conversion so that the politeness
    delay and the URL cache actually apply across an article's images.
    """

    def __init__(
        self,
        session,
        *,
        sleep=time.sleep,
        max_attempts: int = 4,
        politeness_delay: float = 0.25,
        backoff_base: float = 1.0,
    ) -> None:
        self._session = session
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._politeness_delay = politeness_delay
        self._backoff_base = backoff_base
        self._cache: dict[str, bytes | None] = {}
        self._fetched_anything = False

    def fetch(self, url: str) -> bytes | None:
        """Return the bytes at *url*, or ``None`` if it is absent or unusable.

        Raises :class:`RateLimited` when the server is still refusing after
        ``max_attempts``, so that a degraded conversion fails loudly instead of
        being recorded as a success.
        """
        if url in self._cache:
            return self._cache[url]

        if urlsplit(url).scheme not in ("http", "https"):
            self._cache[url] = None
            return None

        if self._fetched_anything and self._politeness_delay > 0:
            self._sleep(self._politeness_delay)
        self._fetched_anything = True

        result = self._get_with_retries(url)
        self._cache[url] = result
        return result

    def _get_with_retries(self, url: str) -> bytes | None:
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._session.get(url, timeout=60)
            except requests.RequestException:
                if attempt == self._max_attempts:
                    return None
                self._back_off(attempt, None)
                continue

            status = getattr(response, "status_code", 200)
            if status < 400:
                return response.content
            if status not in _RETRYABLE:
                return None  # 404 and friends: the image is simply not there
            if attempt == self._max_attempts:
                if status == 429:
                    raise RateLimited(f"{url} still rate-limited after {attempt} tries")
                return None

            self._back_off(attempt, response)
        return None

    def _back_off(self, attempt: int, response) -> None:
        delay = self._backoff_base * (2 ** (attempt - 1))
        retry_after = (getattr(response, "headers", None) or {}).get("Retry-After")
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass  # HTTP-date form; the exponential delay will do
        self._sleep(delay)


def to_kindle_image(source: Path, destination_dir: Path) -> Path | None:
    """Transcode *source* into something a Kindle renders: a right-sized JPEG.

    SVG goes through ImageMagick because Pillow cannot rasterise it. Returns
    ``None`` if the file cannot be read at all, which is treated as a missing
    image rather than a failure.
    """
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", source.stem)[:60] or "img"

    try:
        if source.suffix.lower() == ".svg":
            destination = destination_dir / f"{stem}.png"
            subprocess.run(
                [
                    "magick", "-background", "none", "-density", "144", str(source),
                    "-resize", "1000x1000>", "-flatten", str(destination),
                ],
                check=True,
                capture_output=True,
            )
            return destination

        with Image.open(source) as opened:
            opened.thumbnail(MAX_DIMENSIONS, Image.LANCZOS)
            if opened.mode in ("RGBA", "LA", "P"):
                flattened = Image.new("RGB", opened.size, "white")
                opened = opened.convert("RGBA")
                flattened.paste(opened, mask=opened.split()[-1])
                opened = flattened
            else:
                opened = opened.convert("RGB")

            destination = destination_dir / f"{stem}.jpg"
            opened.save(destination, "JPEG", quality=JPEG_QUALITY, optimize=True)
            return destination
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None
