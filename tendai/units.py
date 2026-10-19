"""Quantity parsing and *exact* unit conversion. No approximate conversions
(e.g. months -> days) are performed: those return None and go to human review."""
import re

# canonical unit -> (dimension, factor to the dimension's base unit)
UNITS = {
    "INR": ("money", 1.0), "lakh": ("money", 1e5), "crore": ("money", 1e7),
    "ohm/km": ("resistance_per_length", 1.0), "ohm/m": ("resistance_per_length", 1000.0),
    "mm": ("length", 0.001), "m": ("length", 1.0), "km": ("length", 1000.0),
    "day": ("days", 1.0), "week": ("days", 7.0),
    "month": ("months", 1.0), "year": ("months", 12.0),
    "kV": ("voltage", 1.0),
}

ALIASES = [  # (regex, canonical); checked in order, longest first
    (r"ohms?\s*(?:/|per)\s*k\s*m", "ohm/km"), (r"ohms?\s*(?:/|per)\s*m(?:etre|eter)?\b", "ohm/m"),
    (r"crores?|cr\b\.?", "crore"), (r"lakhs?|lacs?", "lakh"),
    (r"k\s*m\b|kms?\b|kilomet(?:er|re)s?", "km"), (r"mm\b", "mm"), (r"met(?:er|re)s?\b|mtrs?\b|rm\b|m\b", "m"),
    (r"days?\b", "day"), (r"weeks?\b", "week"), (r"months?\b", "month"), (r"years?\b", "year"),
    (r"kv\b", "kV"),
]
WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "ten": 10, "twelve": 12}

NUM = r"(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
MONEY_RE = re.compile(r"(?:Rs\.?|INR|₹)\s*" + NUM + r"\s*(?:/-)?\s*(crores?|cr\b\.?|lakhs?|lacs?)?", re.I)
QTY_RE = re.compile(NUM + r"\s*(" + "|".join(a for a, _ in ALIASES) + r")", re.I)
WORD_QTY_RE = re.compile(r"\b(" + "|".join(WORD_NUMBERS) + r")\s+(days?|weeks?|months?|years?)\b", re.I)


def canonical_unit(text):
    for pattern, unit in ALIASES:
        if re.fullmatch(pattern, text.strip(), re.I):
            return unit
    return None


def _num(s):
    return float(s.replace(",", ""))


def parse_quantities(text, default_unit=None):
    """Return [(value, unit, matched_text)] found in text. Money is normalised to INR."""
    out, taken = [], []
    for m in MONEY_RE.finditer(text):
        mult = UNITS[canonical_unit(m.group(2))][1] if m.group(2) else 1.0
        out.append((_num(m.group(1)) * mult, "INR", m.group(0).strip()))
        taken.append(m.span())
    for m in QTY_RE.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        unit = canonical_unit(m.group(2))
        if unit in ("crore", "lakh"):  # "21.80 Cr" without "Rs."
            out.append((_num(m.group(1)) * UNITS[unit][1], "INR", m.group(0)))
        elif unit:
            out.append((_num(m.group(1)), unit, m.group(0)))
        taken.append(m.span())
    for m in WORD_QTY_RE.finditer(text):
        out.append((float(WORD_NUMBERS[m.group(1).lower()]), canonical_unit(m.group(2)), m.group(0)))
    if not out and default_unit:  # e.g. table cell "26.40" under a "(Rs. Crores)" header
        m = re.fullmatch(r"\s*" + NUM + r"\s*", text)
        if m:
            dim_unit = default_unit
            value = _num(m.group(1))
            if default_unit in ("crore", "lakh"):
                value, dim_unit = value * UNITS[default_unit][1], "INR"
            out.append((value, dim_unit, text.strip()))
    return out


def unit_in_header(header):
    """'Annual turnover (Rs. Crores)' -> 'crore'"""
    for pattern, unit in ALIASES:
        if re.search(pattern, header, re.I):
            return unit
    return None


def convert(value, from_unit, to_unit):
    """Exact conversion or None when the units are not commensurable."""
    if from_unit == to_unit:
        return value
    a, b = UNITS.get(from_unit), UNITS.get(to_unit)
    if not a or not b or a[0] != b[0]:
        return None
    return value * a[1] / b[1]


def dimension(unit):
    return UNITS.get(unit, (None,))[0]


def inexact_pair(a, b):
    """Units of the same quantity whose conversion is not exact (months vs days)."""
    return {dimension(a), dimension(b)} == {"days", "months"}
