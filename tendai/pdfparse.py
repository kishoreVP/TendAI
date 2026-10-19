"""PDF -> located rows -> document tree (sections, clauses, tables).

Structure recovery handled here (each has a regression test):
  * label and value printed as separate text blocks on one visual row ("2.0 VOLTAGE GRADE" | ": 19/33 KV")
  * values wrapped onto the next line ("... 0.0469 ohm per Km. at" / "20º C")
  * a clause number alone on its line ("1)" followed by the text)
  * repeated page headers/footers and page numbers (": 15 :", "Page 135")
  * superscripts ("1X10" + raised "14" -> "1X10^14")
  * tables, including tables continued on the next page
Coordinates: PDF points, top-left origin, page rotation applied (what a viewer displays).
"""
import re
from collections import Counter

import pymupdf
from rapidfuzz import fuzz

# "2.0 VOLTAGE", "2.1.PURPOSE", "6.1A tenderer", "4. The", "1)"; but not "3 financial years"
# (a wrapped line), "33 KV" or "1.5 sq.mm": a bare single number needs '.' or ')', and the
# text after the number must not start lower-case.
CLAUSE_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})([.)])?\s*(?=$|[^a-z0-9.])")


ITEM_CODE_RE = re.compile(r"^([A-Z]{1,2}-\d{3,5})\b")  # schedule-of-rates item codes, e.g. "M-1765"


def clause_number(text):
    m = CLAUSE_RE.match(text)
    if m and ("." in m.group(1) or m.group(2)):
        return m.group(1)
    m = ITEM_CODE_RE.match(text)
    return m.group(1) if m else None
SUBCLAUSE_RE = re.compile(r"^\(?([a-z]|[ivx]{1,5})\)\s*", re.I)
PAGE_NO_RE = re.compile(r"^(:\s*\d+\s*:|page\s*\d+|-\s*\d+\s*-|\d+)$", re.I)
FURNITURE_ZONE = 0.2  # top/bottom fraction of the page where headers/footers live


def _display_rect(page, bbox):
    r = (pymupdf.Rect(bbox) & page.cropbox) * page.rotation_matrix  # clip glyph overhang at page edges
    r.normalize()
    return [round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)]


def _line_text(line):
    """Join spans; mark raised, smaller spans as superscripts."""
    spans = [s for s in line["spans"] if s["text"].strip()]
    if not spans:
        return "", 0, False
    size = max(s["size"] for s in spans)
    base = max(s["origin"][1] for s in spans)
    out, prev_x1 = "", None
    for s in spans:
        t = s["text"]
        if s["size"] < 0.8 * size and s["origin"][1] < base - 1:
            t = "^" + t.strip()
        elif prev_x1 is not None and s["bbox"][0] - prev_x1 > 0.15 * size and not out.endswith(" "):
            t = " " + t  # visible gap but no space character in the PDF
        out += t
        prev_x1 = s["bbox"][2]
    bold = all(s["flags"] & 16 or "Bold" in s["font"] for s in spans)
    return re.sub(r"\s+", " ", out).strip(), size, bold


def _inside(b, area):
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return area[0] <= cx <= area[2] and area[1] <= cy <= area[3]


def extract_pages(path, find_tables=True):
    """Return (pages, tables). pages: [{w, h, rows:[...], has_text}] with rows already
    merged into visual rows. tables: [{page, bbox, header, rows:[{cells, bboxes, bbox}]}]."""
    doc = pymupdf.open(path)
    pages, tables = [], []
    for pno, page in enumerate(doc, start=1):
        w, h = page.rect.width, page.rect.height
        lines = []
        for block in page.get_text("dict", sort=True)["blocks"]:
            for line in block.get("lines", []):
                text, size, bold = _line_text(line)
                if text:
                    lines.append({"text": text, "bbox": _display_rect(page, line["bbox"]), "size": size, "bold": bold})
        page_tables = []
        if find_tables and page.get_drawings():  # ruled tables need drawn lines; skip otherwise
            for t in page.find_tables().tables:
                if t.row_count < 2 or t.col_count < 2:
                    continue
                rows = []
                for row in t.rows:
                    boxes = [_display_rect(page, c) if c else None for c in row.cells]
                    # cell text from our own line extraction (keeps superscripts and spacing)
                    cells = [" ".join(l["text"] for l in lines if b and _inside(l["bbox"], b)) for b in boxes]
                    rows.append({"cells": cells, "bboxes": boxes, "bbox": _display_rect(page, row.bbox)})
                page_tables.append({"page": pno, "bbox": _display_rect(page, t.bbox), "rows": rows})
        lines = [l for l in lines if not any(_inside(l["bbox"], t["bbox"]) for t in page_tables)]
        pages.append({"page": pno, "w": w, "h": h, "rows": _visual_rows(lines),
                      "has_text": len(page.get_text().strip()) >= 20 or not page.get_images()})
        tables += page_tables
    _mark_furniture(pages)
    return pages, _join_continued_tables(tables, pages)


def _visual_rows(lines):
    """Group line fragments that share a visual row. Fragments are merged only with
    structural support (clause label on the left, or a ':' separator); otherwise a
    wide gap between two long fragments is treated as separate columns."""
    rows = []
    for ln in sorted(lines, key=lambda l: (round(l["bbox"][1]), l["bbox"][0])):
        y0, y1 = ln["bbox"][1], ln["bbox"][3]
        target = None
        for r in rows[-3:]:
            ry0, ry1 = r["bbox"][1], r["bbox"][3]
            overlap = min(y1, ry1) - max(y0, ry0)
            if overlap >= 0.5 * min(y1 - y0, ry1 - ry0):
                target = r
                break
        if target is None:
            rows.append({**ln, "parts": [ln]})
            continue
        target["parts"].append(ln)
    out = []
    for r in rows:
        parts = sorted(r["parts"], key=lambda p: p["bbox"][0])
        groups = [[parts[0]]]
        for p in parts[1:]:
            left = groups[-1][-1]
            gap = p["bbox"][0] - left["bbox"][2]
            labelled = (left["text"].endswith(":") or p["text"].startswith(":")
                        or clause_number(groups[-1][0]["text"]) or len(left["text"]) < 40)
            if gap > 60 and not labelled and len(p["text"]) > 40:
                groups.append([p])  # looks like an independent column
            else:
                groups[-1].append(p)
        for g in groups:
            xs = [p["bbox"] for p in g]
            out.append({
                "text": " ".join(p["text"] for p in g),
                "bbox": [min(b[0] for b in xs), min(b[1] for b in xs), max(b[2] for b in xs), max(b[3] for b in xs)],
                "size": max(p["size"] for p in g), "bold": all(p["bold"] for p in g),
                "merged_from": len(g),
            })
    return sorted(out, key=lambda r: (round(r["bbox"][1]), r["bbox"][0]))


def _norm(t):
    return re.sub(r"\d+", "#", t.lower()).strip()


def _mark_furniture(pages):
    """Headers/footers: rows in the top/bottom zone repeated on many pages, or page numbers."""
    zone = lambda r, p: r["bbox"][3] < p["h"] * FURNITURE_ZONE or r["bbox"][1] > p["h"] * (1 - FURNITURE_ZONE)
    counts = Counter(_norm(r["text"]) for p in pages for r in p["rows"] if zone(r, p))
    min_repeats = max(3, int(0.3 * len(pages)))
    for p in pages:
        for r in p["rows"]:
            r["furniture"] = zone(r, p) and (counts[_norm(r["text"])] >= min_repeats
                                             or bool(PAGE_NO_RE.match(r["text"])))


def _join_continued_tables(tables, pages):
    """A table at the top of a page with the same column count as a table ending at the
    bottom of the previous page is a continuation; a repeated header row is dropped."""
    for prev, cur in zip(tables, tables[1:]):
        if cur["page"] != prev["page"] + 1 or len(cur["rows"][0]["cells"]) != len(prev["rows"][0]["cells"]):
            continue
        h_prev, h_cur = pages[prev["page"] - 1]["h"], pages[cur["page"] - 1]["h"]
        body_above = [r for r in pages[cur["page"] - 1]["rows"]
                      if not r["furniture"] and r["bbox"][3] < cur["bbox"][1]]
        if prev["bbox"][3] > 0.7 * h_prev and cur["bbox"][1] < 0.35 * h_cur and len(body_above) <= 1:
            cur["continues"] = True
            letters = lambda cells: re.sub(r"[^a-z]", "", "".join(cells).lower())
            if fuzz.ratio(letters(cur["rows"][0]["cells"]), letters(prev["rows"][0]["cells"])) >= 85:
                cur["repeated_header"] = True
    return tables


def is_heading(row):
    t = row["text"]
    letters = re.sub(r"[^A-Za-z]", "", t)
    if clause_number(t) or len(t) > 90 or len(letters) < 4 or re.search(r":\s*[\w(]", t):
        return False  # "UDIN: 2520..." is a labelled value, not a heading
    return letters.isupper() or (row["bold"] and len(t) < 70)


def build_tree(pages, tables):
    """Yield node dicts in reading order: {kind, ref, text, page, regions, meta, parent} where
    parent is the index of an earlier yielded node (or None)."""
    nodes = []

    def add(kind, text, page, regions, parent, ref=None, **meta):
        nodes.append({"kind": kind, "text": text, "page": page, "regions": regions,
                      "parent": parent, "ref": ref, "meta": meta})
        return len(nodes) - 1

    # Interleave rows and tables by position so tables land inside the right section.
    items = []
    for p in pages:
        if not p["has_text"]:
            items.append((p["page"], 0, "empty", p))
        for r in p["rows"]:
            items.append((p["page"], r["bbox"][1], "row", r))
    for t in tables:
        items.append((t["page"], t["bbox"][1], "table", t))
    items.sort(key=lambda x: (x[0], x[1]))

    section = clause = sub = None
    last_table = None
    last_bottom = (0, 0)
    for page, _, kind, it in items:
        if kind == "empty":
            add("warning", "Page has no text layer (scanned image?) - OCR not run", page,
                [{"page": page, "bbox": None}], None, flag="needs_ocr")
            continue
        if kind == "table":
            if it.get("continues") and last_table is not None:
                tnode = last_table
                nodes[tnode]["regions"].append({"page": page, "bbox": it["bbox"]})
            else:
                header = it["rows"][0]["cells"]
                tnode = add("table", " | ".join(header), page, [{"page": page, "bbox": it["bbox"]}],
                            section, header=header)
                last_table = tnode
            header = nodes[tnode]["meta"]["header"]
            body = it["rows"][1:] if (not it.get("continues") or it.get("repeated_header")) else it["rows"]
            label = None
            for r in body:
                cells = r["cells"]
                if cells[0]:
                    label = cells[0]
                add("row", " | ".join(c for c in cells if c), page, [{"page": page, "bbox": r["bbox"]}], tnode,
                    ref=cells[0] or label, cells=dict(zip(header, cells)), cell_bboxes=r["bboxes"])
            continue
        row = it
        if row["furniture"]:
            continue
        region = {"page": page, "bbox": row["bbox"]}
        text = row["text"]
        gap_ok = last_bottom[0] != page or row["bbox"][1] - last_bottom[1] < 2.2 * max(row["size"], 6)
        last_bottom = (page, row["bbox"][3])
        num = clause_number(text)
        if is_heading(row) and section is not None and section == len(nodes) - 1 and gap_ok \
                and nodes[section]["page"] == page:
            nodes[section]["text"] += " " + text  # heading wrapped onto a second line
            nodes[section]["ref"] = nodes[section]["text"][:80]
            b = nodes[section]["regions"][0]["bbox"]
            nodes[section]["regions"][0]["bbox"] = [min(b[0], row["bbox"][0]), b[1], max(b[2], row["bbox"][2]), row["bbox"][3]]
        elif is_heading(row):
            section = add("section", text, page, [region], None, ref=text[:80])
            clause = sub = None
            last_table = None
        elif num:
            clause = add("clause", text, page, [region], section, ref=num, method="clause_number",
                         merged_fragments=row["merged_from"])
            sub = None
        elif SUBCLAUSE_RE.match(text) and clause is not None:
            ref = (nodes[clause]["ref"] or "") + "(" + SUBCLAUSE_RE.match(text).group(1) + ")"
            sub = add("clause", text, page, [region], clause, ref=ref, method="subclause_marker")
        elif (sub if sub is not None else clause) is not None and gap_ok:
            target = sub if sub is not None else clause
            n = nodes[target]
            n["text"] = (n["text"] + " " + text).strip()
            n["meta"]["continued_lines"] = n["meta"].get("continued_lines", 0) + 1
            same = [g for g in n["regions"] if g["page"] == page]
            if same:  # grow this page's box to cover the wrapped line
                b = same[-1]["bbox"]
                same[-1]["bbox"] = [min(b[0], region["bbox"][0]), min(b[1], region["bbox"][1]),
                                    max(b[2], region["bbox"][2]), max(b[3], region["bbox"][3])]
            else:
                n["regions"].append(region)
        else:
            clause = add("paragraph", text, page, [region], section)
            sub = None
    # Clauses whose text is only a number ("1)") absorb nothing else; label/value split:
    for n in nodes:
        if n["kind"] == "clause":
            body = CLAUSE_RE.sub("", n["text"], count=1)
            if " : " in body or re.search(r"\S\s*:\s+\S", body):
                label, value = re.split(r"\s*:\s+", body, maxsplit=1)
                n["meta"].update(label=label.strip(" .:"), value=value.strip())
    return nodes
