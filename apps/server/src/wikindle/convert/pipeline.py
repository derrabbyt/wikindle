"""Article in, Conversion out.

This is the whole conversion, callable in process: the API converts on the same
box that serves it, so there is no queue and no subprocess boundary to marshal
results across.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from wikindle.convert import clean
from wikindle.convert.cover import render_cover
from wikindle.convert.images import ImageFetcher, to_kindle_image
from wikindle.sources.wikipedia import article_language, normalise_article_url

USER_AGENT = (
    "wikindle/0.1 (one Wikipedia article a day to a Kindle; "
    "https://github.com/derrabbyt/wikindle)"
)

#: Wikimedia thumbnail URLs accept an arbitrary width; the ones embedded in an
#: article are sized for a browser column and look soft on a 300ppi screen.
_PREFERRED_THUMB_WIDTH = 800

EPUB_CSS = """\
body { font-family: serif; line-height: 1.4; margin: 0; padding: 0; text-align: left; }
h1, h2, h3, h4 { font-family: sans-serif; page-break-after: avoid; line-height: 1.2; }
h1 { font-size: 1.5em; margin: 1em 0 0.6em; }
h2 { font-size: 1.25em; margin: 1.2em 0 0.5em; }
h3 { font-size: 1.1em; margin: 1em 0 0.4em; }
h4 { font-size: 1em; font-style: italic; }
p { margin: 0 0 0.6em; text-indent: 0; widows: 2; orphans: 2; }
img { max-width: 100%; height: auto; }
figure { margin: 1em 0; text-align: center; page-break-inside: avoid; }
figcaption { font-size: 0.8em; font-style: italic; text-align: center; margin-top: 0.3em; }
table { width: 100%; font-size: 0.75em; border-collapse: collapse; margin: 1em 0; }
th, td { border: 1px solid #999; padding: 0.25em 0.4em; text-align: left; vertical-align: top; }
th { background: #eee; }
ol, ul { margin: 0 0 0.6em 1.2em; padding: 0; }
sup { font-size: 0.7em; vertical-align: super; line-height: 0; }
blockquote { margin: 0.8em 1em; font-style: italic; }
a { text-decoration: none; color: inherit; word-wrap: break-word; overflow-wrap: break-word; }
section.footnotes, .reflist { font-size: 0.8em; }
hr { border: 0; border-top: 1px solid #ccc; }
"""


class ConversionFailed(RuntimeError):
    """The Article could not be turned into an EPUB."""


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """What came out, and how well it went.

    ``images_missing`` is what the quality gate reads: a Conversion that lost a
    large share of its pictures is not fit to become an Edition.
    """

    source_url: str
    title: str
    language: str
    epub_path: Path
    epub_bytes: int
    word_count: int
    images_kept: int
    images_missing: int
    icons_dropped: int

    @property
    def images_expected(self) -> int:
        return self.images_kept + self.images_missing

    @property
    def missing_image_ratio(self) -> float:
        return self.images_missing / self.images_expected if self.images_expected else 0.0


def pandoc_binary() -> str:
    found = shutil.which("pandoc")
    if found:
        return found
    fallback = Path.home() / "bin" / "pandoc"
    if fallback.exists():
        return str(fallback)
    raise ConversionFailed("pandoc is not installed")


def _widest_from_srcset(img) -> str | None:
    """Wikipedia ships 1.5x and 2x variants; take the sharpest offered."""
    candidates = []
    for part in (img.get("srcset") or "").split(","):
        bits = part.strip().split()
        if len(bits) == 2 and bits[1].endswith("x"):
            try:
                candidates.append((float(bits[1][:-1]), bits[0]))
            except ValueError:
                continue
    return max(candidates)[1] if candidates else None


def _upscale_thumb(url: str) -> str:
    match = re.search(r"/(\d+)px-", url)
    if not match or int(match.group(1)) >= _PREFERRED_THUMB_WIDTH:
        return url
    return re.sub(r"/\d+px-", f"/{_PREFERRED_THUMB_WIDTH}px-", url, count=1)


def _absolute(url: str, base_url: str) -> str:
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith(("http://", "https://")):
        return url
    return urljoin(base_url, url)


def _extract_title(soup: BeautifulSoup) -> str:
    heading = soup.select_one("#firstHeading, .mw-page-title-main")
    if heading:
        title = heading.get_text(" ", strip=True)
    elif soup.title:
        title = soup.title.get_text(strip=True)
    else:
        title = "Article"
    return re.sub(r"\s*[–-]\s*Wikipedia\s*$", "", title).strip()


def convert_article(
    source_url: str,
    output_path: Path,
    *,
    session: requests.Session | None = None,
    build_dir: Path | None = None,
    fetcher: ImageFetcher | None = None,
) -> ConversionResult:
    """Fetch *source_url* and write a Kindle-readable EPUB to *output_path*.

    Raises :class:`ConversionFailed` if pandoc fails, and
    :class:`~wikindle.convert.images.RateLimited` if Wikimedia refuses images
    persistently — both of which the caller must treat as a failed build rather
    than a degraded one.
    """
    source_url = normalise_article_url(source_url)

    owns_session = session is None
    session = session or requests.Session()
    if owns_session:
        session.headers["User-Agent"] = USER_AGENT

    with tempfile.TemporaryDirectory(prefix="wikindle-") as temporary:
        build = build_dir or Path(temporary)
        images_dir = build / "img"
        images_dir.mkdir(parents=True, exist_ok=True)

        response = session.get(source_url, timeout=60)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        content = (
            soup.select_one("#mw-content-text .mw-parser-output")
            or soup.select_one("#mw-content-text")
            or soup.body
        )
        if content is None:
            raise ConversionFailed(f"no article content found at {source_url}")

        title = _extract_title(soup)
        language = (soup.html.get("lang") if soup.html else None) or article_language(
            source_url
        )

        clean.strip_cruft(content)
        icons_dropped = clean.drop_icon_images(content)

        fetcher = fetcher or ImageFetcher(session)
        kept, missing, local_images = _localise_images(
            content, source_url, build, images_dir, fetcher
        )

        clean.drop_empty_figures(content)
        _absolutise_links(content, source_url)
        clean.flatten_sections(content)
        clean.fix_footnote_anchors(content)
        clean.strip_identifiers(content)
        clean.clean_styles(content)
        clean.flatten_infoboxes(content)

        word_count = len(content.get_text(" ", strip=True).split())

        # Inner HTML only: an enclosing wrapper would nest every heading in a
        # Div and defeat both chapter splitting and the table of contents.
        (build / "body.html").write_text(
            f'<h1 class="unnumbered">{title}</h1>\n{content.decode_contents()}',
            encoding="utf-8",
        )
        (build / "style.css").write_text(EPUB_CSS, encoding="utf-8")
        _write_metadata(build / "meta.yaml", title, language, source_url)
        cover = render_cover(title, source_url, local_images, build / "cover.jpg")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _run_pandoc(build, cover, output_path)

    return ConversionResult(
        source_url=source_url,
        title=title,
        language=language,
        epub_path=output_path,
        epub_bytes=output_path.stat().st_size,
        word_count=word_count,
        images_kept=kept,
        images_missing=missing,
        icons_dropped=icons_dropped,
    )


def _localise_images(
    content, source_url: str, build: Path, images_dir: Path, fetcher: ImageFetcher
) -> tuple[int, int, list[Path]]:
    """Download, transcode and rewrite every ``<img>``; drop the ones that fail.

    Returns the counts and the local files in document order, the first of which
    is the article's lead image and the only cover candidate worth considering.
    """
    kept = 0
    missing = 0
    by_url: dict[str, str] = {}
    order: list[Path] = []

    for img in content.find_all("img"):
        source = _widest_from_srcset(img) or img.get("src") or ""
        if not source:
            img.decompose()
            continue

        url = _upscale_thumb(_absolute(source, source_url))
        if url in by_url:
            img["src"] = by_url[url]
            continue

        payload = fetcher.fetch(url)
        if payload is None and url != _absolute(source, source_url):
            url = _absolute(source, source_url)  # the upscaled width did not exist
            payload = fetcher.fetch(url)
        if payload is None:
            img.decompose()
            missing += 1
            continue

        name = Path(unquote(urlsplit(url).path)).name
        name = re.sub(r"^\d+px-", "", re.sub(r"[^A-Za-z0-9_.-]", "_", name))[:70]
        raw = build / f"{hashlib.sha1(url.encode()).hexdigest()[:8]}_{name}"
        raw.write_bytes(payload)

        transcoded = to_kindle_image(raw, images_dir)
        if transcoded is None:
            img.decompose()
            missing += 1
            continue

        relative = f"img/{transcoded.name}"
        by_url[url] = relative
        order.append(transcoded)
        kept += 1

        for attribute in (
            "srcset", "data-file-width", "data-file-height", "style", "class",
            "decoding", "loading", "width", "height",
        ):
            img.attrs.pop(attribute, None)
        img["src"] = relative

    return kept, missing, order


def _absolutise_links(content, source_url: str) -> None:
    """Keep anchors and real external links; make wiki-internal links absolute.

    A relative link in an EPUB points at a file that is not there, so anything
    that cannot be resolved loses its anchor and keeps its text.
    """
    for anchor in content.find_all("a", href=True):
        href = anchor["href"]
        if href.startswith("#") or href.startswith(("http://", "https://")):
            continue
        if href.startswith("/"):
            anchor["href"] = urljoin(source_url, href)
        else:
            anchor.unwrap()


def _write_metadata(path: Path, title: str, language: str, source_url: str) -> None:
    escaped = title.replace('"', '\\"')
    path.write_text(
        f'title: "{escaped}"\n'
        f'author: "Wikipedia"\n'
        f"lang: {language}\n"
        f'rights: "CC BY-SA 4.0 — {source_url}"\n'
        f'description: "Offline reading copy of the Wikipedia article."\n',
        encoding="utf-8",
    )


def _run_pandoc(build: Path, cover: Path, output_path: Path) -> None:
    command = [
        pandoc_binary(),
        str(build / "body.html"),
        "--metadata-file", str(build / "meta.yaml"),
        "-f", "html", "-t", "epub3",
        "--standalone", "--toc", "--toc-depth=3", "--split-level=2",
        "--css", str(build / "style.css"),
        "--resource-path", str(build),
        "--epub-title-page=true",
        "--epub-cover-image", str(cover),
        "-o", str(output_path),
    ]
    finished = subprocess.run(command, capture_output=True, text=True, cwd=build)
    if finished.returncode != 0 or not output_path.exists():
        raise ConversionFailed(f"pandoc failed: {finished.stderr[-2000:]}")
