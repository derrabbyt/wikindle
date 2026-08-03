"""Infobox flattening.

Nested tables are the worst case for the Kindle renderer, so infoboxes are turned
into linear label/value paragraphs. Doing that to a table that contains another
infobox used to crash the whole conversion.
"""
from bs4 import BeautifulSoup

from wikindle.convert.clean import flatten_infoboxes


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(f"<div id='root'>{html}</div>", "html.parser")


def test_flattens_a_simple_infobox_to_label_value_paragraphs():
    doc = soup_of(
        """
        <table class="infobox">
          <tr><th>Born</th><td>1907</td></tr>
          <tr><th>Died</th><td>1973</td></tr>
        </table>
        """
    )
    flatten_infoboxes(doc.find(id="root"))

    assert doc.find("table") is None
    text = doc.get_text(" ", strip=True)
    assert "Born : 1907" in " ".join(text.split())
    assert "Died : 1973" in " ".join(text.split())


def test_nested_infobox_does_not_crash():
    """Regression: ``{{Infobox}}`` child modules nest one infobox inside another.

    The outer flatten detaches the inner table, so by the time the loop reached
    the inner one it was no longer in the tree and ``replace_with`` raised
    ``ValueError: Cannot replace one element with another when the element to be
    replaced is not part of a tree``. Reproduced live against wikivoyage.
    """
    doc = soup_of(
        """
        <table class="infobox">
          <tr><th>Name</th><td>Vienna</td></tr>
          <tr><td>
            <table class="infobox">
              <tr><th>Population</th><td>1,900,000</td></tr>
            </table>
          </td></tr>
        </table>
        """
    )

    flatten_infoboxes(doc.find(id="root"))

    assert doc.find("table") is None
    text = " ".join(doc.get_text(" ", strip=True).split())
    assert "Vienna" in text
    assert "1,900,000" in text, "content of the nested infobox must survive"


def test_deeply_nested_infoboxes_are_all_flattened():
    doc = soup_of(
        """
        <table class="infobox">
          <tr><td>
            <table class="infobox">
              <tr><td>
                <table class="infobox"><tr><th>Depth</th><td>three</td></tr></table>
              </td></tr>
            </table>
          </td></tr>
        </table>
        """
    )

    flatten_infoboxes(doc.find(id="root"))

    assert doc.find("table") is None
    assert "three" in doc.get_text()


def test_label_markup_is_moved_not_stringified():
    """Labels can carry footnote anchors; a back-link with no target is a broken
    link in the finished EPUB."""
    doc = soup_of(
        """
        <table class="infobox">
          <tr><th>Area<sup id="cite_ref-1"><a id="cite_ref-1" href="#cite_note-1">[1]</a></sup></th>
              <td>414 km2</td></tr>
        </table>
        """
    )

    flatten_infoboxes(doc.find(id="root"))

    anchor = doc.find("a", href="#cite_note-1")
    assert anchor is not None, "the footnote anchor must survive flattening"


def test_leaves_other_tables_alone():
    doc = soup_of('<table class="wikitable"><tr><th>Year</th><td>1907</td></tr></table>')
    flatten_infoboxes(doc.find(id="root"))
    assert doc.find("table", class_="wikitable") is not None
