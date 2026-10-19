"""Milestone 1: upload validation, storage, hashing, conversion."""
import pytest

from gammaprime import ingest
from gammaprime.db import Document, Node

from .conftest import CABLE, needs_samples


def test_rejects_unsupported_type(session, tmp_path):
    f = tmp_path / "evil.exe"
    f.write_bytes(b"MZ....")
    with pytest.raises(ingest.IngestError, match="Unsupported"):
        ingest.ingest(session, ingest.get_case(session, "c"), f, "tender")


def test_rejects_empty_file(session, tmp_path):
    f = tmp_path / "empty.pdf"
    f.write_bytes(b"")
    with pytest.raises(ingest.IngestError, match="empty"):
        ingest.ingest(session, ingest.get_case(session, "c"), f, "tender")


def test_rejects_wrong_content_for_extension(session, tmp_path):
    f = tmp_path / "fake.pdf"
    f.write_bytes(b"this is not a pdf")
    with pytest.raises(ingest.IngestError, match="does not look like"):
        ingest.ingest(session, ingest.get_case(session, "c"), f, "tender")


def test_corrupted_pdf_is_recorded_as_failed(session, tmp_path):
    f = tmp_path / "broken.pdf"
    f.write_bytes(b"%PDF-1.7\n garbage garbage")
    with pytest.raises(ingest.IngestError, match="Could not parse"):
        ingest.ingest(session, ingest.get_case(session, "c"), f, "tender")
    doc = session.query(Document).one()
    assert doc.status == "failed" and doc.warnings


def test_filename_is_sanitised():
    assert ingest.safe_name("../../etc/passwd") == "passwd"
    assert ingest.safe_name("..\\..\\win.ini") == "win.ini"
    assert "/" not in ingest.safe_name("a/b<c>.pdf")


def test_csv_ingest_cells(session, tmp_path):
    f = tmp_path / "rates.csv"
    f.write_text("code,description,unit,rate\nX-1,Cable,KM,100\n", encoding="utf-8")
    doc = ingest.ingest(session, ingest.get_case(session, "c"), f, "benchmark")
    rows = session.query(Node).filter_by(doc_id=doc.id, kind="row").all()
    assert rows[1].meta["cells"] == {"A": "X-1", "B": "Cable", "C": "KM", "D": "100"}
    assert rows[1].regions == [{"sheet": "csv", "row": 2}]


@needs_samples
def test_pdf_hash_storage_and_idempotent_reingest(session):
    case = ingest.get_case(session, "c")
    d1 = ingest.ingest(session, case, CABLE / "M246Spec.pdf", "tender")
    d2 = ingest.ingest(session, case, CABLE / "M246Spec.pdf", "tender")
    assert d1.id == d2.id and d1.status == "parsed"
    assert d1.sha256 == ingest.sha256(CABLE / "M246Spec.pdf")
    assert d1.path.endswith(f"{d1.sha256}.pdf")
    assert session.query(Node).filter_by(doc_id=d1.id).count() > 500
    assert len(d1.meta["pages"]) == 101


@needs_samples
def test_xls_boq_rows_and_continuations(session):
    from .conftest import PWD
    doc = ingest.ingest(session, ingest.get_case(session, "c"), PWD / "BOQ_851660.xls", "tender")
    rows = {r.meta["sl"]: r for r in session.query(Node).filter_by(doc_id=doc.id, kind="row")}
    item49, cont = rows["49"], rows["49.01"]
    assert cont.parent_id == item49.id  # multi-row description kept with its item
    assert rows["16"].meta["cells"]["D"] == "2" and "32A 2P MCB" in rows["16"].meta["cells"]["B"]
