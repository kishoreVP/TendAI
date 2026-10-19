"""HTTP API workflow: upload -> extract -> review -> freeze -> run -> decide -> export."""
import importlib

import pytest
from fastapi.testclient import TestClient

from .conftest import CABLE, HIST, needs_samples


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TENDAI_DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    from tendai import api, config
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    importlib.reload(api)
    return TestClient(api.app, headers={"X-User": "alice"})


def _upload(client, case, path, role, bidder=None):
    data = {"role": role} | ({"bidder": bidder} if bidder else {})
    with open(path, "rb") as f:
        return client.post(f"/api/cases/{case}/upload", data=data, files={"file": (path.name, f)})


def test_rejects_bad_upload(client, tmp_path):
    f = tmp_path / "x.pdf"
    f.write_bytes(b"not a pdf")
    r = _upload(client, "c", f, "tender")
    assert r.status_code == 422 and "does not look like" in r.json()["detail"]
    assert client.post("/api/cases/c/upload", data={"role": "bidder"},
                       files={"file": ("a.csv", b"a,b\n")}).status_code == 400  # bidder name required


@needs_samples
def test_full_workflow(client):
    assert _upload(client, "cable", CABLE / "M246Spec.pdf", "tender").status_code == 200
    _upload(client, "cable", CABLE / "BOQ_838535.xls", "tender")
    _upload(client, "cable", HIST, "benchmark")
    for f in (CABLE / "bidders/meridian").iterdir():
        assert _upload(client, "cable", f, "bidder", "meridian").status_code == 200
    case = client.get("/api/cases").json()[0]
    assert client.post(f"/api/cases/{case['id']}/extract").json()["benchmarks"] == 4
    reqs = client.get(f"/api/cases/{case['id']}/requirements").json()
    turnover = next(r for r in reqs if r["key"] == "TURNOVER")
    bad = client.post(f"/api/requirements/{turnover['id']}/review", json={"changes": {"original_clause_text": "x"}})
    assert bad.status_code == 400  # source text is not editable
    for r in reqs:
        if r["key"] in ("TURNOVER", "GTP-6", "EMD"):
            assert client.post(f"/api/requirements/{r['id']}/review", json={"status": "approved"}).status_code == 200
    tv = client.post(f"/api/cases/{case['id']}/templates").json()
    run = client.post(f"/api/templates/{tv['id']}/runs").json()
    rep = client.get(f"/api/runs/{run['id']}").json()
    assert {r["requirement_key"]: r["auto_status"] for r in rep["results"]} == {
        "TURNOVER": "DOES_NOT_MEET_REQUIREMENT", "GTP-6": "DOES_NOT_MEET_REQUIREMENT",
        "EMD": "INSUFFICIENT_EVIDENCE"}  # Meridian's EMD receipt is a scan without a text layer
    res = rep["results"][0]
    assert client.post(f"/api/results/{res['id']}/decision", json={"final_status": "MEETS_REQUIREMENT", "reason": ""}).status_code == 400
    assert client.post(f"/api/results/{res['id']}/decision",
                       json={"final_status": "DOES_NOT_MEET_REQUIREMENT", "reason": "confirmed against CA certificate"}).status_code == 200
    rep = client.get(f"/api/runs/{run['id']}").json()
    decided = next(r for r in rep["results"] if r["id"] == res["id"])
    assert decided["reviewer"] == "alice" and decided["auto_status"] == res["auto_status"]
    assert any(a["action"] == "decision" and a["actor"] == "alice" for a in client.get("/api/audit").json())
    ev = decided["evidence"][0]
    node = client.get(f"/api/nodes/{ev['source_node_id']}").json()
    assert node["regions"] and node["page"] == ev["source_page"]
    assert client.get(f"/api/documents/{ev['source_document_id']}/file").content.startswith(b"%PDF")
    for fmt in ("xlsx", "pdf", "csv", "json"):
        assert client.get(f"/api/runs/{run['id']}/export.{fmt}").status_code == 200
