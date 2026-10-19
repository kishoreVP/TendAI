"""Milestones 4 and 5 (unit level): units, rules, benchmark matching. No documents needed."""
import pytest

from tendai import benchmarks as bm
from tendai import evaluate as ev
from tendai import units
from tendai.schemas import Claim, Requirement, Rule


@pytest.mark.parametrize("text,value,unit", [
    ("Rs.21.80Cr.", 218_000_000, "INR"), ("Rs. 70,00,000/-", 7_000_000, "INR"), ("Rs.43,500/-", 43_500, "INR"),
    ("Rs. 48.6 Lakhs", 4_860_000, "INR"), ("0.0469 ohm per Km", 0.0469, "ohm/km"), ("0.000062 ohm/m", 0.000062, "ohm/m"),
    ("90 Days", 90, "day"), ("ONE YEAR", 1, "year"), ("50 KM", 50, "km"),
])
def test_parse_quantities(text, value, unit):
    v, u, _ = units.parse_quantities(text)[0]
    assert (round(v, 9), u) == (value, unit)


def test_header_unit_and_exact_conversion_only():
    assert units.parse_quantities("26.40", default_unit=units.unit_in_header("Turnover (Rs. Crores)"))[0][0] == 264_000_000
    assert units.convert(0.000062, "ohm/m", "ohm/km") == pytest.approx(0.062)
    assert units.convert(3, "month", "day") is None  # not exact -> left for review


def _req(op, value=None, unit=None, **rule):
    return Requirement(requirement_id="R", tender_id="t", category="technical", title="t", original_clause_text="x",
                       normalised_requirement="x", requirement_type="numeric", mandatory_status="mandatory",
                       comparison_operator=op, evaluation_rule=Rule(op=op, value=value, unit=unit, **rule),
                       source_document_id=1, extraction_method="test", extraction_confidence=1,
                       required_evidence_types=rule.pop("required", []) if False else [])


def _claim(value, etype="bidder_declaration", raw=None, **kw):
    return Claim(bidder="b", requirement_id="R", claim_text="x", value=value, raw_value=raw or str(value),
                 evidence_type=etype, source_document_id=1, extraction_method="test", extraction_confidence=1, **kw)


def test_max_threshold_examples_from_brief():
    req = _req("<=", 0.0469, "ohm/km")
    assert ev.evaluate(req, [_claim(0.0450)])[0] == ev.MEETS
    assert ev.evaluate(req, [_claim(0.0480)])[0] == ev.FAILS
    assert ev.evaluate(req, [])[0] == ev.INSUFFICIENT  # missing is not failing
    assert ev.evaluate(req, [_claim("Will be furnished later")])[0] == ev.INSUFFICIENT


def test_contradiction_only_when_outcomes_differ():
    req = _req("<=", 0.0469, "ohm/km")
    assert ev.evaluate(req, [_claim(0.0450), _claim(0.0452, "third_party_document")])[0] == ev.MEETS
    assert ev.evaluate(req, [_claim(0.0450), _claim(0.0472, "third_party_document")])[0] == ev.REVIEW


def test_aggregate_max_and_third_party_requirement():
    req = _req(">", 218_000_000, "INR", aggregate="max")
    req.required_evidence_types = ["third_party_document"]
    assert ev.evaluate(req, [_claim(19.75e7, "third_party_document"), _claim(26.4e7, "third_party_document")])[0] == ev.MEETS
    assert ev.evaluate(req, [_claim(22.5e7)])[0] == ev.REVIEW  # meets, but only self-declared
    assert ev.evaluate(req, [_claim(18.2e7)])[0] == ev.FAILS   # own declaration of failing counts


def test_approx_needs_review_when_not_equal():
    req = _req("approx", 8.8, "mm")
    assert ev.evaluate(req, [_claim(8.8)])[0] == ev.MEETS
    status, why, _ = ev.evaluate(req, [_claim(8.5)])
    assert status == ev.REVIEW and "no tolerance" in why


def test_ai_extracted_values_are_never_final():
    req = _req("<=", 0.0469, "ohm/km")
    c = _claim(0.0450, "ai_inferred", verification_status="ai_extracted")
    assert ev.evaluate(req, [c])[0] == ev.REVIEW


def test_documents_and_exemption():
    doc = _req("document", must_mention=["UDIN"])
    assert ev.evaluate(doc, [])[0] == ev.INSUFFICIENT
    assert ev.evaluate(doc, [_claim("present", raw="present; missing: UDIN")])[0] == ev.REVIEW
    assert ev.evaluate(doc, [_claim("present", raw="present")])[0] == ev.MEETS
    emd = _req(">=", 7e6, "INR", exemption_keywords=["udyam"])
    assert ev.evaluate(emd, [_claim("exemption_claimed")])[0] == ev.REVIEW


@pytest.mark.parametrize("required,offered,ok", [
    ("H2/H4 Grade Aluminium", "H4 Grade Aluminium", True), ("A2XWa2Y", "A2XWaY", False),
    ("IS 7098 (Part 2) 2011 / IEC 60502 (2)", "IS 7098 (Part 2) 2011", True),
    ("Shall be Gas cured with CCV process", "Dry gas cured on CCV line", True), ("XLPE", "PVC", False),
])
def test_text_matching(required, offered, ok):
    assert ev.text_matches(required, offered) is ok


def test_benchmark_attribute_compatibility():
    item = bm.attributes("Supply of 33 KV 1x630 sq mm XLPE Aluminium Power Cable")
    assert bm.compatibility(item, bm.attributes("33 KV 1x630 sq.mm XLPE Aluminium cable"))[0] == []
    assert bm.compatibility(item, bm.attributes("33 KV 1x400 sq.mm XLPE Aluminium cable"))[0]  # size conflict
    assert bm.compatibility(item, bm.attributes("11 KV 3x300 sq.mm XLPE Aluminium cable"))[0]
    mcb = bm.attributes("Supply and fixing of 32A 2P MCB in sheet steel enclosure")
    assert bm.compatibility(mcb, bm.attributes("MINIATURE CIRCUIT BREAKERS :: Double Pole 6A to 32A, 10KA"))[0] == []
    assert bm.compatibility(mcb, bm.attributes("MINIATURE CIRCUIT BREAKERS :: Double Pole 40A, 10KA"))[0]


@pytest.mark.parametrize("text,device,poles", [
    # regressions found on the PWD BOQ vs SOR: head noun decides the device, first pole token wins
    ("Supply and fixing of 32A 2P MCB in sheet steel enclosure box", "mcb", "2P"),
    ("SHEET STEEL ENCLOSURE FOR MCB's - LOWER END :: 4 pole", "enclosure", "4P"),
    ("Supply and fixing of 63A FP MCB in Metal box DP enclosure", "mcb", "4P"),
    ("MINIATURE CIRCUIT BREAKERS :: Triple Pole & Neutral 6A to 32A", "mcb", "TPN"),
])
def test_device_and_poles(text, device, poles):
    a = bm.attributes(text)
    assert (a["device"], a["poles"]) == (device, poles)


def test_price_normalisation_is_exact_and_recorded():
    rate, steps = bm.normalise_rate(2150, "Metre", "KM")
    assert rate == 2_150_000 and "unit conversion" in steps[0]
    assert bm.normalise_rate(100, "Each", "KM")[0] is None


def test_sor_extract_records(session, sor_extract):
    from tendai import ingest
    from tendai.db import Node
    doc = ingest.ingest(session, ingest.get_case(session, "c"), sor_extract, "benchmark")
    nodes = session.query(Node).filter_by(doc_id=doc.id).order_by(Node.seq).all()
    recs = {r.code: r for r in bm.from_sor_nodes(nodes, "TN PWD", "2025-07-22")}
    r = recs["M-1765"]
    assert (r.rate, r.unit, r.attributes["grade"], r.attributes["poles"]) == (485.0, "Each", "higher end", "2P")
    assert recs["M-1777"].rate == 402.0 and recs["M-1777"].attributes["grade"] == "lower end"
    assert recs["M-1785"].normalised_description.startswith("MCB ::")
    assert r.source_page and r.source_regions[0].bbox
