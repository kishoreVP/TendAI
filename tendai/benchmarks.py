"""Benchmark extraction (SOR PDFs, historical-award CSVs), matching and price normalisation.

Matching is attribute-first: two items are compared only if none of their parsed technical
attributes conflict (voltage, size, cores, conductor, insulation, amps, poles, device type,
grade). Lexical similarity ranks candidates but never makes an incompatible item comparable.
Normalisation is deterministic and every step is recorded; no tax, inflation or regional
adjustment is applied without a documented basis.
"""
import csv
import re
import statistics

from rapidfuzz import fuzz

from .db import Node
from .schemas import BenchmarkRecord

CODE_RE = re.compile(r"^([A-Z]{1,2}-\d{3,5})\b\s*(.*)")

UNIT_NORMAL = {"km": ("m", 1000), "kms": ("m", 1000), "metre": ("m", 1), "meter": ("m", 1), "mtrs": ("m", 1),
               "mtr": ("m", 1), "rm": ("m", 1), "rmt": ("m", 1), "m": ("m", 1),
               "each": ("no", 1), "no": ("no", 1), "nos": ("no", 1), "no.": ("no", 1), "nos.": ("no", 1),
               "set": ("set", 1), "job": ("job", 1), "pts": ("point", 1), "pts.": ("point", 1), "point": ("point", 1)}

POLES = {"single pole & neutral": "SPN", "single pole": "1P", "double pole": "2P", "triple pole & neutral": "TPN",
         "triple pole": "3P", "four pole": "4P", "1 pole": "1P", "2 pole": "2P", "3 pole": "3P", "4 pole": "4P",
         "2p": "2P", "dp": "2P", "4p": "4P", "fp": "4P", "tpn": "TPN", "tp": "3P", "sp": "1P"}
DEVICES = {"mcb": r"\bmcb|miniature circuit breaker", "rccb": r"\brccb\b|\belcb\b", "enclosure": r"\benclosure",
           "cable": r"\bcable\b", "led": r"\bled\b", "fan": r"\bfan\b", "switch": r"\bswitch\b", "socket": r"\bsocket\b"}


def _first(t, patterns):
    """The pattern matching earliest in the text wins: the head noun of 'MCB in sheet steel
    enclosure' is the MCB, of 'ENCLOSURE FOR MCB's' the enclosure."""
    hits = [(m.start(), -len(m.group(0)), v) for k, v in patterns for m in [re.search(k, t)] if m]
    return min(hits)[2] if hits else None


def attributes(text):
    """Parse comparable technical attributes from an item description."""
    t = text.lower().replace("sq mm", "sq.mm").replace("sqmm", "sq.mm")
    a = {}
    if m := re.search(r"(\d+(?:\.\d+)?)\s*kv\b", t):
        a["kv"] = float(m.group(1))
    # "3 1/2 x 25 sq.mm" or "12 core 2.5 sq.mm"
    if m := re.search(r"(\d+(?:\s*1/2)?)\s*(?:[x×]|cores?)\s*(\d+(?:\.\d+)?)\s*sq", t):
        a["cores"], a["size_sqmm"] = m.group(1).replace(" ", ""), float(m.group(2))
    for k in ("aluminium", "copper"):
        if k in t:
            a["conductor"] = k
    for k in ("xlpe", "pvc"):
        if k in t:
            a["insulation"] = k
            break
    amps = re.findall(r"(\d+)\s*a\b", t)
    if m := re.search(r"(\d+)\s*a\s*to\s*(\d+)\s*a", t):
        a["amps"] = (float(m.group(1)), float(m.group(2)))
    elif amps:
        a["amps"] = (float(amps[0]), float(amps[0]))
    if poles := _first(t, [(r"\b" + re.escape(k) + r"\b", v) for k, v in POLES.items()]):
        a["poles"] = poles
    if device := _first(t, [(pat, k) for k, pat in DEVICES.items()]):
        a["device"] = device
    if "higher end" in t:
        a["grade"] = "higher end"
    elif "lower end" in t:
        a["grade"] = "lower end"
    return a


def compatibility(item, ref):
    """-> (conflicts, matched_keys). Amps match when the item's rating is inside the ref range."""
    conflicts, matched = [], []
    for k in set(item) & set(ref):
        if k == "amps":
            ok = ref[k][0] <= item[k][0] <= ref[k][1]
        else:
            ok = item[k] == ref[k]
        (matched if ok else conflicts).append(k if ok else f"{k}: {item[k]} vs {ref[k]}")
    return conflicts, matched


# ------------------------------------------------------------ extraction
def _sor_columns(header):
    find = lambda pat: next((h for h in header if re.search(pat, h, re.I)), None)
    return find(r"code"), find(r"descr|detail"), find(r"^unit|^per$"), find(r"rate")


def from_sor_nodes(nodes, issuer, effective_date, tax_basis=None):
    """SOR table rows 'M-1765 | Double Pole 6A to 32A, 10KA | Each | 485.00'. Rows without an
    item code are headings ('3 | MINIATURE CIRCUIT BREAKERS ... - HIGHER END') and give
    context to the records that follow."""
    records, heading, last_was_heading = [], "", False
    tables = {n.id: n for n in nodes if n.kind == "table"}
    for n in nodes:
        if n.kind != "row" or n.parent_id not in tables:
            continue
        code_col, desc_col, unit_col, rate_col = _sor_columns(tables[n.parent_id].meta.get("header", []))
        if not (code_col and desc_col and unit_col and rate_col):
            continue
        cells = n.meta.get("cells", {})
        code, desc = cells.get(code_col, "").strip(), cells.get(desc_col, "").strip()
        rate = re.fullmatch(r"[\d,]+(\.\d+)?", cells.get(rate_col, "").strip())
        if CODE_RE.match(code) and rate:
            context = heading if len(desc) < 80 else ""  # long descriptions are self-contained
            records.append(BenchmarkRecord(
                code=code, original_description=desc, normalised_description=f"{context} :: {desc}".strip(" :"),
                attributes=attributes(f"{context} {desc}"), unit=cells.get(unit_col, "").strip(),
                rate=float(rate.group(0).replace(",", "")), tax_basis=tax_basis,
                effective_date=effective_date, geography="Tamil Nadu", issuer=issuer, source_type="official_sor",
                source_page=n.page, source_regions=n.regions, extraction_confidence=0.9))
            last_was_heading = False
        elif desc and not code:
            heading = f"{heading} {desc}" if last_was_heading else desc
            last_was_heading = True
        elif desc and re.fullmatch(r"\d+", code):  # numbered group heading
            heading, last_was_heading = desc, True
    return records


def from_csv(path):
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            out.append(BenchmarkRecord(
                code=row["code"], original_description=row["description"], normalised_description=row["description"],
                attributes=attributes(row["description"]), unit=row["unit"], rate=float(row["rate"]),
                currency=row.get("currency", "INR"), tax_basis=row.get("tax_basis"), effective_date=row.get("date"),
                issuer=row.get("issuer"), source_type=row.get("source_type", "historical_award"),
                source_regions=[{"sheet": "csv", "row": i}], extraction_confidence=1.0))
    return out


# ----------------------------------------------------------- comparison
def normalise_rate(rate, from_unit, to_unit):
    """Exact unit conversion of a per-unit price. -> (rate, steps) or (None, reason)."""
    f = UNIT_NORMAL.get(from_unit.strip().lower())
    t = UNIT_NORMAL.get(to_unit.strip().lower())
    if not f or not t or f[0] != t[0]:
        return None, f"units not comparable: {from_unit} vs {to_unit}"
    # price per from_unit -> price per to_unit
    factor = t[1] / f[1]
    steps = [] if factor == 1 else [f"unit conversion: {rate:,.2f} per {from_unit} x {factor:g} = {rate * factor:,.2f} per {to_unit}"]
    return rate * factor, steps


# installation work in an item ("clarnping" is a typo in the real PWD BOQ)
WORK_RE = re.compile(r"\b(fixing|laying|erection|installation|run of|clamping|clarnping|wiring|testing|commissioning)\b", re.I)


def match_item(desc, unit, benchmarks):
    """-> {comparable: [...], partial: [...], excluded: [...]} with reasons and normalised rates."""
    item = attributes(desc)
    result = {"item_attributes": item, "comparable": [], "partial": [], "excluded": []}
    for b in benchmarks:
        rec = b.data
        conflicts, matched = compatibility(item, rec["attributes"])
        lexical = fuzz.token_set_ratio(desc.lower(), rec["normalised_description"].lower())
        entry = {"benchmark_id": b.id, "code": rec["code"], "description": rec["normalised_description"],
                 "source_type": rec["source_type"], "issuer": rec["issuer"], "date": rec["effective_date"],
                 "page": rec["source_page"], "lexical": lexical, "matched_attributes": matched}
        if conflicts:
            if matched and len(matched) >= 2:
                result["excluded"].append({**entry, "reason": "attribute conflict: " + "; ".join(conflicts)})
            continue
        if "device" not in matched and len(matched) < 3:
            continue  # not enough technical overlap to even consider
        rate, steps = normalise_rate(rec["rate"], rec["unit"], unit)
        if rate is None:
            result["excluded"].append({**entry, "reason": steps})
            continue
        entry.update(reference_rate=rec["rate"], reference_unit=rec["unit"], normalised_rate=round(rate, 2),
                     steps=steps + ["no escalation / tax / location adjustment applied (no documented basis)"])
        notes = []
        if WORK_RE.search(desc) and not WORK_RE.search(rec["original_description"]):
            notes.append("BOQ item includes installation work; reference is a material-only rate")
        missing = [k for k in item if k not in rec["attributes"]]
        if missing:
            notes.append(f"attributes not stated in reference: {', '.join(missing)}")
        unstated = [k for k in rec["attributes"] if k not in item and k != "grade"]
        if unstated:  # e.g. BOQ does not say aluminium or copper: prices can differ several-fold
            notes.append(f"BOQ item does not specify: {', '.join(unstated)}")
        entry["notes"] = notes
        (result["partial"] if notes else result["comparable"]).append(entry)
    for k in ("comparable", "partial"):  # more matched technical attributes first, then wording
        result[k].sort(key=lambda e: (-len(e["matched_attributes"]), -e["lexical"]))
    rates = [e["normalised_rate"] for e in result["comparable"]]
    if len(rates) >= 2:
        result["summary"] = {"basis": "comparable references only", "n": len(rates), "min": min(rates),
                             "median": statistics.median(rates), "max": max(rates)}
    elif len(rates) == 1:
        result["summary"] = {"basis": "single comparable reference", "n": 1, "median": rates[0]}
    return result


def _is_item_row(r):
    """A priced BOQ line: integer Sl.No and a textual description (not the '1 | 2 | 3' column-number row)."""
    return (not r.meta.get("is_header") and re.fullmatch(r"\d+", r.meta.get("sl", ""))
            and re.search(r"[A-Za-z]{3}", r.meta.get("cells", {}).get("B", "")))


def boq_items(s, doc):
    """Tender BOQ rows that carry a quantity: [{sl, description, qty, unit, node}]."""
    rows = s.query(Node).filter_by(doc_id=doc.id, kind="row").order_by(Node.seq).all()
    children = {}
    for r in rows:
        children.setdefault(r.parent_id, []).append(r)
    items = []
    for r in rows:
        c = r.meta.get("cells", {})
        if not _is_item_row(r):
            continue
        try:
            qty = float(c.get("D", ""))
        except ValueError:
            continue
        desc = c.get("B", "")
        desc += " " + " ".join(ch.meta["cells"].get("B", "") for ch in children.get(r.id, []))
        items.append({"sl": r.meta["sl"], "description": " ".join(desc.split()), "qty": qty,
                      "unit": c.get("E", ""), "node": r})
    return items


def bidder_prices(s, doc):
    """{sl: (rate, node, column)} from a bidder's priced BOQ (column whose header asks for
    the bidder's rate/price 'in figures')."""
    sheet = s.query(Node).filter_by(doc_id=doc.id, kind="sheet").first()
    header = (sheet.meta or {}).get("header", {}) if sheet else {}
    col = next((k for k, v in header.items() if re.search(r"(rate|price).*figures|figures.*(rate|price)", v, re.I)), None)
    out = {}
    if not col:
        return out, None
    for r in s.query(Node).filter_by(doc_id=doc.id, kind="row").all():
        v = r.meta.get("cells", {}).get(col)
        if v and _is_item_row(r):
            try:
                out[r.meta["sl"]] = (float(v), r, col)
            except ValueError:
                pass
    return out, col
