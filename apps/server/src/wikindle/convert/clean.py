"""Reducing Wikipedia's markup to something an e-reader can lay out.

Everything here mutates the parsed article in place. It is Wikipedia-specific by
design; a second source would bring its own cleaning rather than adding branches
to this one.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

#: Navigation, editing affordances, maintenance banners and metadata: all of it
#: is furniture for a browser and noise on a Kindle.
CRUFT_SELECTORS = (
    "script", "style", "link", "noscript",
    ".mw-editsection", ".mw-jump-link", ".navigation-not-searchable",
    ".navbox", ".vertical-navbox", ".metadata", ".ambox", ".sistersitebox",
    ".mw-empty-elt", ".mw-indicators", ".printfooter", ".catlinks",
    "#toc", ".toc", ".toccolours", ".mw-hidden-catlinks",
    ".mw-kartographer-maplink", ".mw-collapsible-toggle", ".hide-when-compact",
    ".shortdescription", ".side-box", ".mbox-small", ".mw-authority-control",
    ".navigation-only", ".hatnote", ".dablink", ".normdaten", "#normdaten",
    ".noprint", ".reference-accessdate",
)

#: Class names worth keeping; everything else is presentational noise that only
#: makes the EPUB bigger.
KEPT_CLASSES = frozenset({"wikitable", "infobox", "reflist", "thumbcaption"})

#: Sizing and floating are decided by the reader's device, not by us.
_LAYOUT_STYLE = re.compile(
    r"(float|width|max-width|min-width|margin|padding|position)\s*:[^;]*;?", re.I
)

_ICON_MAX_EDGE = 40

_tag_factory = BeautifulSoup("", "html.parser")


def strip_cruft(content: Tag) -> None:
    for selector in CRUFT_SELECTORS:
        for element in content.select(selector):
            element.decompose()

    # Hidden sort keys are invisible in a browser but pandoc renders them,
    # doubling every country name in a table.
    for element in content.select('[style*="display:none"], [style*="display: none"]'):
        element.decompose()


def drop_icon_images(content: Tag) -> int:
    """Remove flag and rating sprites, which clutter Kindle text badly."""
    dropped = 0
    for img in content.find_all("img"):
        try:
            width = int(img.get("width") or 0)
            height = int(img.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if 0 < width <= _ICON_MAX_EDGE and 0 < height <= _ICON_MAX_EDGE:
            img.decompose()
            dropped += 1
    return dropped


def drop_empty_figures(content: Tag) -> None:
    """A figure whose media was dropped leaves a caption describing nothing."""
    for figure in content.find_all("figure"):
        if not figure.find("img"):
            figure.decompose()
    for anchor in content.find_all("a"):
        if not anchor.get_text(strip=True) and not anchor.find("img"):
            anchor.decompose()


def flatten_sections(content: Tag) -> None:
    """Unwrap the ``<section>``/``<div>`` nesting around every heading.

    pandoc splits chapters and builds its table of contents from *top-level*
    headings only, so a heading buried in a wrapper produces neither.
    """
    for meta in content.find_all("meta"):
        meta.decompose()
    for element in content.find_all(["section", "div"]):
        element.unwrap()


def fix_footnote_anchors(content: Tag) -> None:
    """Move ``cite_ref`` ids from the ``<sup>`` onto the ``<a>`` inside it.

    pandoc renders a ``<sup>`` as a bare Superscript and drops its attributes,
    which would break every footnote back-link once chapters are split.
    """
    for sup in content.find_all("sup", id=True):
        anchor = sup.find("a")
        if anchor is not None and sup["id"].startswith("cite_ref"):
            anchor["id"] = sup["id"]


def strip_identifiers(content: Tag) -> None:
    """Drop parser-generated ids and attributes, keeping citation anchors."""
    for element in content.find_all(id=True):
        keeps_anchor = element["id"].startswith(("cite_note", "cite_ref"))
        if not keeps_anchor and element.name not in ("h1", "h2", "h3", "h4", "h5", "h6"):
            del element["id"]

    for anchor in content.find_all("a", title=True):
        del anchor["title"]

    for element in content.find_all(True):
        for attribute in list(element.attrs):
            if attribute.startswith("data-") or attribute in (
                "about", "typeof", "rel", "role", "aria-labelledby", "aria-hidden"
            ):
                del element[attribute]


def clean_styles(content: Tag) -> None:
    for element in content.find_all(True):
        style = element.get("style")
        if style:
            style = _LAYOUT_STYLE.sub("", style)
            if style.strip():
                element["style"] = style
            else:
                del element["style"]

        classes = element.get("class")
        if classes:
            kept = [name for name in classes if name in KEPT_CLASSES]
            if kept:
                element["class"] = kept
            else:
                del element["class"]


def flatten_infoboxes(content: Tag) -> None:
    """Replace every infobox table with linear label/value paragraphs.

    Nested tables are the worst case for the Kindle renderer, so the whole box
    becomes text that reflows at any screen size.

    Infoboxes nest — ``{{Infobox}}`` child modules put one inside another — and
    flattening the outer box detaches the inner one. Collecting every table up
    front and iterating that list therefore hands ``replace_with`` an element
    that is no longer in the tree, which raises. Re-querying each time avoids it:
    each pass removes exactly one table, so the loop always terminates.
    """
    guard = len(content.find_all("table", class_="infobox")) + 1
    while guard >= 0:
        guard -= 1
        box = content.find("table", class_="infobox")
        if box is None:
            return
        _flatten_one(box)


def _flatten_one(table: Tag) -> None:
    replacement = _tag_factory.new_tag("div")

    def emit_table(current: Tag) -> None:
        for row in current.find_all("tr"):
            if row.find_parent("table") is current:
                emit_row(row)

    def emit_row(row: Tag) -> None:
        cells = row.find_all(["th", "td"], recursive=False)
        if not cells:
            return

        # Inner tables are emitted before the row that contained them, then
        # removed, so their content survives in reading order.
        for cell in cells:
            for inner in cell.find_all("table", recursive=False):
                emit_table(inner)
                inner.decompose()

        cells = [c for c in cells if c.get_text(strip=True) or c.find("img")]
        if not cells:
            return

        paragraph = _tag_factory.new_tag("p")
        if cells[0].name == "th":
            # Move the label's markup rather than its text: labels carry
            # footnote anchors, and a back-link with no target is a broken link.
            label = _tag_factory.new_tag("strong")
            for child in list(cells[0].contents):
                label.append(child.extract())
            paragraph.append(label)
            if len(cells) >= 2:
                paragraph.append(": ")
                for cell in cells[1:]:
                    for child in list(cell.contents):
                        paragraph.append(child.extract())
        else:
            for cell in cells:
                for child in list(cell.contents):
                    paragraph.append(child.extract())

        if paragraph.get_text(strip=True) or paragraph.find("img"):
            replacement.append(paragraph)

    emit_table(table)
    table.replace_with(replacement)
    replacement.unwrap()
