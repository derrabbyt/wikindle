"""Generating a cover.

A plain typographic cover is better than a wrong one, so the lead image is only
used when its filename shares a word with the title. Otherwise the "lead image"
is often a photograph of somebody merely quoted in the article, which makes a
confidently misleading cover.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFont

COVER_SIZE = (1600, 2560)
_TEXT_WIDTH = 1240

#: DejaVu lives somewhere different on every distribution.
FONT_DIRECTORIES = (
    "/usr/share/fonts/TTF",
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts",
    "/Library/Fonts",
)


def load_font(names: tuple[str, ...], size: int) -> ImageFont.ImageFont:
    for directory in FONT_DIRECTORIES:
        path = Path(directory)
        if not path.is_dir():
            continue
        for name in names:
            hit = next(path.rglob(name), None)
            if hit:
                return ImageFont.truetype(str(hit), size)
    return ImageFont.load_default(size)


def _fold(text: str) -> str:
    normalised = unicodedata.normalize("NFKD", text.lower())
    return re.sub(r"[^a-z0-9]", "", normalised.encode("ascii", "ignore").decode())


def choose_cover_art(title: str, candidates: list[Path]) -> Path | None:
    """The first image big enough to fill a cover whose name echoes the title."""
    tokens = [_fold(word) for word in re.split(r"[\s,–—-]+", title)]
    tokens = [token for token in tokens if len(token) >= 4]
    if not tokens:
        return None

    for candidate in candidates:
        try:
            with Image.open(candidate) as image:
                large_enough = max(image.size) >= 250
        except OSError:
            continue
        if large_enough and any(token in _fold(candidate.name) for token in tokens):
            return candidate
    return None


def render_cover(
    title: str, source_url: str, candidates: list[Path], destination: Path
) -> Path:
    canvas = Image.new("RGB", COVER_SIZE, "white")
    draw = ImageDraw.Draw(canvas)
    title_font = load_font(("DejaVuSerif-Bold.ttf", "DejaVuSerif.ttf"), 130)
    subtitle_font = load_font(("DejaVuSans.ttf",), 62)

    lines: list[str] = []
    current = ""
    for word in title.split():
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=title_font) > _TEXT_WIDTH and current:
            lines.append(current)
            current = word
        else:
            current = trial
    lines.append(current)

    y = 240
    for line in lines:
        x = (COVER_SIZE[0] - draw.textlength(line, font=title_font)) / 2
        draw.text((x, y), line, font=title_font, fill="#111111")
        y += 165
    draw.rectangle([500, y + 60, 1100, y + 68], fill="#111111")

    art = choose_cover_art(title, candidates)
    if art:
        with Image.open(art) as opened:
            image = opened.convert("RGB")
        # Small locator maps are worth upscaling a little, but not endlessly.
        scale = min(_TEXT_WIDTH / image.width, _TEXT_WIDTH / image.height, 2.5)
        image = image.resize(
            (int(image.width * scale), int(image.height * scale)), Image.LANCZOS
        )
        canvas.paste(image, ((COVER_SIZE[0] - image.width) // 2, y + 220))

    footer = ["Wikipedia — offline", urlsplit(source_url).netloc]
    for index, line in enumerate(footer):
        if not line:
            continue
        x = (COVER_SIZE[0] - draw.textlength(line, font=subtitle_font)) / 2
        draw.text((x, 2200 + index * 90), line, font=subtitle_font, fill="#444444")

    canvas.save(destination, "JPEG", quality=88)
    return destination
