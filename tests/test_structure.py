"""Milestone 3 / 6: regression tests for the extraction failures found on the real tender
(baseline: Docling output in codes/extraction_practice/debug_document.md) and coordinates."""
import pymupdf

from tendai import pdfparse

from .conftest import needs_samples


def _clause(nodes, ref, page):
    return next(n for n in nodes if n["kind"] == "clause" and n["ref"] == ref and n["page"] == page)


@needs_samples
def test_label_value_on_separate_blocks_is_paired(cable_tree):
    # Docling: "- 2.0 VOLTAGE GRADE Uo /U." and ": 19/33 KV" were separate elements
    n = _clause(cable_tree[2], "2.0", 57)
    assert n["meta"]["label"] == "VOLTAGE GRADE Uo /U" and n["meta"]["value"] == "19/33 KV"


@needs_samples
def test_wrapped_value_is_joined(cable_tree):
    # Docling: "2.3. RESISTANCE OF CONDUCTOR : Maximum of 0.0469 ohm per Km. at" / "20º C"
    n = _clause(cable_tree[2], "2.3", 57)
    assert n["meta"]["value"] == "Maximum of 0.0469 ohm per Km. at 20º C"


@needs_samples
def test_numbered_clauses_in_reading_order(cable_tree):
    # Docling emitted 3.1 / 3.2 before 2.0
    refs = [n["ref"] for n in cable_tree[2] if n["kind"] == "clause" and n["page"] == 57]
    assert refs.index("2.0") < refs.index("2.5") < refs.index("3.0") < refs.index("3.1")


@needs_samples
def test_wrapped_line_starting_with_a_number_is_not_a_clause(cable_tree):
    # "3 financial years should be uploaded" continues BQR clause 3, it is not clause "3"
    n = _clause(cable_tree[2], "3", 17)
    assert n["text"].startswith("3. The Annual Turnover") and "3 financial years should be uploaded" in n["text"]


@needs_samples
def test_page_numbers_are_furniture(cable_tree):
    pages = cable_tree[0]
    assert all(r["furniture"] for p in pages for r in p["rows"] if r["text"].replace(" ", "") == f":{p['page'] - 2}:")
    assert not any(n["text"].strip() == ": 15 :" for n in cable_tree[2])


@needs_samples
def test_superscript_kept(cable_tree):
    rows = [n for n in cable_tree[2] if n["kind"] == "row" and "27 degrees" in n["text"]]
    assert "1X10^14" in rows[0]["text"]


@needs_samples
def test_multipage_table_continues_without_repeated_header(cable_tree):
    gtp = next(n for i, n in enumerate(cable_tree[2]) if n["kind"] == "table" and "Bidder`s Data" in n["text"])
    idx = cable_tree[2].index(gtp)
    rows = [n for n in cable_tree[2] if n["kind"] == "row" and n["parent"] == idx]
    assert {r["page"] for r in rows} == {63, 64, 65} and len(gtp["regions"]) == 3
    assert not any(r["meta"]["cells"].get("Description") == "Description" for r in rows)
    assert any(r["ref"] == "25" and "0.0469" in r["text"] for r in rows)


@needs_samples
def test_scanned_pages_flagged_not_dropped(cable_tree):
    flagged = {n["page"] for n in cable_tree[2] if n["kind"] == "warning"}
    assert {20, 30, 66} <= flagged


def test_rotated_page_coordinates(tmp_path):
    """Stored boxes are in displayed (rotated) page space: mapping them back with the inverse
    rotation matrix must recover the text."""
    path = tmp_path / "rot.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "2.3. RESISTANCE OF CONDUCTOR : Maximum of 0.0469 ohm per Km", fontsize=10)
    page.set_rotation(90)
    doc.save(path)
    pages, tables = pdfparse.extract_pages(path)
    row = pages[0]["rows"][0]
    x0, y0, x1, y1 = row["bbox"]
    p = pymupdf.open(path)[0]
    assert x1 <= p.rect.width + 1 and y1 <= p.rect.height + 1  # inside the displayed (landscape) page
    text = p.get_textbox(pymupdf.Rect(row["bbox"]) * ~p.rotation_matrix)
    assert "0.0469" in text
    assert pdfparse.build_tree(pages, tables)[0]["meta"]["label"] == "RESISTANCE OF CONDUCTOR"
