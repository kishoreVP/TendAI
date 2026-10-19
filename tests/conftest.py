import json
import os
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parents[1]
CABLE = ROOT / "data/samples/cable_m246"
PWD = ROOT / "data/samples/pwd_tiruchengode"
SOR = ROOT / "data/benchmarks/TN_PWD_SOR_2025-26_Vol1.pdf"
HIST = ROOT / "data/benchmarks/historical_awards_SYNTHETIC.csv"
GOLDEN = json.loads((ROOT / "tests/golden/expected_outcomes.json").read_text()) \
    if (ROOT / "tests/golden/expected_outcomes.json").exists() else {}

needs_samples = pytest.mark.skipif(not (CABLE / "M246Spec.pdf").exists() or not (CABLE / "bidders").exists(),
                                   reason="sample tenders / synthetic bidders not present (run scripts/make_synthetic_bids.py)")


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    from tendai import config
    monkeypatch.setattr(config, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(config, "DEFAULT_PROVIDER", "none")
    monkeypatch.setattr(config, "TASK_ROUTING", {})


@pytest.fixture
def session():
    from tendai.db import make_session_factory
    s = make_session_factory("sqlite://")()
    yield s
    s.close()


@pytest.fixture(scope="session")
def sor_extract(tmp_path_factory):
    """Pages 241-245 of the SOR (MCB section) - keeps tests fast; full SOR takes ~80 s."""
    out = tmp_path_factory.mktemp("sor") / "sor_extract.pdf"
    src = pymupdf.open(SOR)
    doc = pymupdf.open()
    doc.insert_pdf(src, from_page=240, to_page=244)
    doc.save(out)
    return out


@pytest.fixture(scope="session")
def cable_tree():
    from tendai import pdfparse
    pages, tables = pdfparse.extract_pages(CABLE / "M246Spec.pdf")
    return pages, tables, pdfparse.build_tree(pages, tables)
