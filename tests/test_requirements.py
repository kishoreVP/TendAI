"""Milestone 2: requirement extraction against manually verified values from the tenders."""
import pytest

from tendai import ingest, requirements
from tendai.schemas import Requirement

from .conftest import CABLE, PWD, needs_samples

pytestmark = needs_samples


@pytest.fixture(scope="module")
def reqs(tmp_path_factory):
    from tendai import config
    from tendai.db import make_session_factory
    config.STORAGE_DIR = tmp_path_factory.mktemp("s")
    s = make_session_factory("sqlite://")()
    out = {}
    for name, pdf in [("cable", CABLE / "M246Spec.pdf"), ("pwd", PWD / "Ten_Con_TgodeCTOElectrical.pdf")]:
        case = ingest.get_case(s, name)
        doc = ingest.ingest(s, case, pdf, "tender")
        out[name] = {r.requirement_id: r for r in requirements.extract_requirements(s, case, [doc], use_llm=False)}
    return out


def test_all_records_validate_against_schema(reqs):
    for case in reqs.values():
        for r in case.values():
            Requirement.model_validate(r.model_dump())
            assert r.source_regions and r.original_clause_text


@pytest.mark.parametrize("case,key,op,value,unit,page", [
    ("cable", "TURNOVER", ">", 218_000_000, "INR", 17),        # "more than Rs.21.80Cr."
    ("cable", "EXPERIENCE-QTY", ">=", 50, "km", 17),           # "minimum of 50 KM"
    ("cable", "EMD", ">=", 7_000_000, "INR", 7),                # "Rs. 70,00,000/-"
    ("cable", "GTP-6", "<=", 0.0469, "ohm/km", 63),            # Maximum DC resistance
    ("cable", "GTP-26", "<=", 0.065, "ohm/km", 65),
    ("cable", "GTP-13-3", ">=", 7.8, "mm", 63),                # insulation minimum thickness
    ("pwd", "EMD", ">=", 43_500, "INR", 1),
    ("pwd", "COMPLETION", "<=", 90, "day", 3),
])
def test_known_values(reqs, case, key, op, value, unit, page):
    r = reqs[case][key]
    assert (r.comparison_operator, r.evaluation_rule.value, r.unit, r.source_page) == (op, value, unit, page)


def test_text_and_categorical_requirements(reqs):
    assert reqs["cable"]["GTP-4"].comparison_operator == "matches"  # "19/33 kV" is not a single number
    assert reqs["cable"]["GTP-12"].evaluation_rule.value.startswith("1X10^14")
    assert reqs["pwd"]["CONTRACTOR-CLASS"].permitted_values == ["Class-I", "Class-II", "Class-III", "Class-IV"]


def test_unstated_values_stay_unknown(reqs):
    r = reqs["cable"]["GTP-8.1"]  # "To be furnished by the bidder"
    assert r.comparison_operator == "provided" and r.evaluation_rule.value is None and r.unit is None
    assert all(d.evaluation_rule.value is None for k, d in reqs["pwd"].items() if k.startswith("DOC-"))


def test_duplicates_conflicts_and_corroboration_flagged(reqs):
    assert "possible duplicate of GTP-6" in reqs["cable"]["GTP-25"].flags
    assert any("corroborated by clause 2.3" in f for f in reqs["cable"]["GTP-6"].flags)
    assert any("also stated" in f for f in reqs["pwd"]["EMD"].flags)


def test_conditional_requirements(reqs):
    emd = reqs["cable"]["EMD"]
    assert emd.mandatory_status == "conditional" and emd.evaluation_rule.exemption_keywords == ["udyam"]
    assert reqs["cable"]["DOC-UDYAM-REGISTRATION-CERTIFICATE"].mandatory_status == "conditional"
    assert reqs["pwd"]["DOC-AVERAGE-ANNUAL-TURNOVER-CERTIFICATE"].evaluation_rule.must_mention == ["UDIN"]


def test_original_text_is_verbatim(reqs):
    assert "21.80" in reqs["cable"]["TURNOVER"].original_clause_text
    assert "90 Days" in reqs["pwd"]["COMPLETION"].original_clause_text
