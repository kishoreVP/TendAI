"""HTTP API + static web UI.   uvicorn tendai.api:app --reload

Access control is a placeholder: the reviewer name comes from the X-User header. A real
deployment needs authentication and per-case authorisation in front of this.
"""
import shutil
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import exports, pipeline
from .db import (AuditEvent, Benchmark, Bidder, Case, Document, Evidence, Node, PriceComparison, Requirement,
                 Result, Run, TemplateVersion, audit, make_session_factory)
from .ingest import IngestError, get_bidder, get_case, ingest

app = FastAPI(title="TendAI")
Session = make_session_factory()
WEB = Path(__file__).parent / "web"


def db():
    s = Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def user(x_user: str = Header(default="reviewer")):
    return x_user[:100]


def _get(s, model, id_):
    obj = s.get(model, id_)
    if obj is None:
        raise HTTPException(404, f"{model.__name__} {id_} not found")
    return obj


# ------------------------------------------------------------------ cases
@app.get("/api/cases")
def list_cases(s=Depends(db)):
    out = []
    for c in s.query(Case).order_by(Case.id):
        docs = s.query(Document).filter_by(case_id=c.id).all()
        out.append({"id": c.id, "name": c.name,
                    "documents": [{"id": d.id, "filename": d.filename, "role": d.role, "kind": d.kind,
                                   "bidder_id": d.bidder_id, "status": d.status, "sha256": d.sha256,
                                   "warnings": d.warnings, "meta": {k: v for k, v in d.meta.items() if k != "pages"}}
                                  for d in docs],
                    "bidders": [{"id": b.id, "name": b.name} for b in s.query(Bidder).filter_by(case_id=c.id)],
                    "templates": [{"id": t.id, "version": t.version, "approved_by": t.approved_by,
                                   "created": t.created.isoformat(), "count": len(t.requirements)}
                                  for t in s.query(TemplateVersion).filter_by(case_id=c.id)],
                    "runs": [{"id": r.id, "template_version_id": r.template_version_id, "created": r.created.isoformat()}
                             for r in s.query(Run).filter_by(case_id=c.id).order_by(Run.id)]})
    return out


@app.post("/api/cases/{name}/upload")
def upload(name: str, role: str = Form(...), bidder: str | None = Form(None), file: UploadFile = File(...),
           s=Depends(db), who=Depends(user)):
    if role not in ("tender", "benchmark", "bidder"):
        raise HTTPException(400, "role must be tender, benchmark or bidder")
    if role == "bidder" and not bidder:
        raise HTTPException(400, "bidder name required")
    case = get_case(s, name)
    b = get_bidder(s, case, bidder) if role == "bidder" else None
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "upload"
        with path.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        try:
            doc = ingest(s, case, path, role, b, filename=file.filename, actor=who)
        except IngestError as e:
            s.commit()  # keep the failed-document record and audit entry
            raise HTTPException(422, str(e))
    return {"id": doc.id, "status": doc.status, "warnings": doc.warnings}


@app.post("/api/cases/{case_id}/extract")
def extract(case_id: int, s=Depends(db), who=Depends(user)):
    case = _get(s, Case, case_id)
    reqs = pipeline.extract_requirements(s, case, actor=who)
    n = pipeline.extract_benchmarks(s, case, actor=who)
    return {"requirements": len(reqs), "benchmarks": n}


# ----------------------------------------------------------- requirements
@app.get("/api/cases/{case_id}/requirements")
def list_requirements(case_id: int, s=Depends(db)):
    return [{"id": r.id, "key": r.key, "review_status": r.review_status, "data": r.data, "history": r.history}
            for r in s.query(Requirement).filter_by(case_id=case_id).order_by(Requirement.id)]


class Review(BaseModel):
    status: str | None = None  # approved | rejected | draft
    changes: dict | None = None
    comment: str | None = None


@app.post("/api/requirements/{req_id}/review")
def review(req_id: int, body: Review, s=Depends(db), who=Depends(user)):
    if body.status not in (None, "approved", "rejected", "draft"):
        raise HTTPException(400, "invalid status")
    allowed = {"normalised_requirement", "evaluation_rule", "comparison_operator", "expected_value", "unit",
               "mandatory_status", "category", "title", "applicability_conditions", "required_evidence_types"}
    if body.changes and set(body.changes) - allowed:
        raise HTTPException(400, f"editable fields: {sorted(allowed)}")
    req = _get(s, Requirement, req_id)
    try:
        pipeline.review_requirement(s, req, who, body.status, body.changes, body.comment)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True, "review_status": req.review_status}


@app.post("/api/cases/{case_id}/templates")
def freeze_template(case_id: int, s=Depends(db), who=Depends(user)):
    try:
        tv = pipeline.approve_template(s, _get(s, Case, case_id), who)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": tv.id, "version": tv.version}


@app.get("/api/templates/{tv_id}")
def get_template(tv_id: int, s=Depends(db)):
    tv = _get(s, TemplateVersion, tv_id)
    return {"id": tv.id, "version": tv.version, "approved_by": tv.approved_by, "requirements": tv.requirements}


# ------------------------------------------------------------- benchmarks
@app.get("/api/cases/{case_id}/benchmarks")
def list_benchmarks(case_id: int, q: str = "", limit: int = 200, s=Depends(db)):
    rows = s.query(Benchmark).filter_by(case_id=case_id).all()
    if q:
        rows = [b for b in rows if q.lower() in (b.data["normalised_description"] + (b.data["code"] or "")).lower()]
    return {"total": len(rows), "items": [{"id": b.id, "doc_id": b.doc_id, "review_status": b.review_status, **b.data}
                                          for b in rows[:limit]]}


# ------------------------------------------------------------------- runs
@app.post("/api/templates/{tv_id}/runs")
def start_run(tv_id: int, s=Depends(db), who=Depends(user)):
    tv = _get(s, TemplateVersion, tv_id)
    run = pipeline.run_evaluation(s, _get(s, Case, tv.case_id), tv, actor=who)
    return {"id": run.id}


@app.get("/api/runs/{run_id}")
def get_run(run_id: int, s=Depends(db)):
    return exports.run_report(s, _get(s, Run, run_id))


class Decision(BaseModel):
    final_status: str
    reason: str


@app.post("/api/results/{result_id}/decision")
def decide(result_id: int, body: Decision, s=Depends(db), who=Depends(user)):
    valid = {"MEETS_REQUIREMENT", "DOES_NOT_MEET_REQUIREMENT", "INSUFFICIENT_EVIDENCE",
             "REQUIRES_HUMAN_REVIEW", "NOT_APPLICABLE"}
    if body.final_status not in valid or not body.reason.strip():
        raise HTTPException(400, "final_status must be a valid status and a reason is required")
    r = _get(s, Result, result_id)
    audit(s, who, "decision", "result", r.id, auto_status=r.auto_status, previous_final=r.final_status,
          final_status=body.final_status, reason=body.reason)
    r.final_status, r.reviewer, r.review_reason = body.final_status, who, body.reason
    return {"ok": True}


@app.get("/api/runs/{run_id}/export.{fmt}")
def export(run_id: int, fmt: str, s=Depends(db)):
    run = _get(s, Run, run_id)
    makers = {"json": exports.to_json, "csv": exports.to_csv, "xlsx": exports.to_xlsx, "pdf": exports.to_pdf}
    if fmt not in makers:
        raise HTTPException(400, "format: json, csv, xlsx or pdf")
    data, media = makers[fmt](s, run)
    return Response(data, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="tendai_run{run_id}.{fmt}"'})


@app.get("/api/runs/{run_id}/bidders/{bidder_id}/evidence-pack.pdf")
def bidder_evidence_pack(run_id: int, bidder_id: int, s=Depends(db)):
    data = exports.evidence_pack(s, _get(s, Run, run_id), bidder_id)
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="evidence_pack_run{run_id}_bidder{bidder_id}.pdf"'})


@app.get("/api/documents/{doc_id}/snippet.png")
def snippet(doc_id: int, page: int, bbox: str, s=Depends(db)):
    """Rendered, highlighted crop of a PDF region (visual proof shown next to each finding)."""
    d = _get(s, Document, doc_id)
    try:
        box = [float(x) for x in bbox.split(",")]
        assert d.kind == "pdf" and len(box) == 4 and page >= 1
        png = exports.snippet_png(d.path, page, box)
    except (AssertionError, ValueError, IndexError):
        raise HTTPException(400, "snippet needs a PDF document, a valid page and bbox=x0,y0,x1,y1")
    return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/benchmarks/{bench_id}")
def get_benchmark(bench_id: int, s=Depends(db)):
    b = _get(s, Benchmark, bench_id)
    return {"id": b.id, "doc_id": b.doc_id, "review_status": b.review_status, **b.data}


@app.get("/api/audit")
def audit_log(limit: int = 200, s=Depends(db)):
    return [{"ts": a.ts.isoformat(), "actor": a.actor, "action": a.action, "entity": a.entity,
             "entity_id": a.entity_id, "detail": a.detail}
            for a in s.query(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)]


# -------------------------------------------------------- source viewer
@app.get("/api/documents/{doc_id}/file")
def document_file(doc_id: int, s=Depends(db)):
    d = _get(s, Document, doc_id)
    return FileResponse(d.path, filename=d.filename)


@app.get("/api/documents/{doc_id}")
def document_info(doc_id: int, s=Depends(db)):
    d = _get(s, Document, doc_id)
    return {"id": d.id, "filename": d.filename, "kind": d.kind, "role": d.role, "meta": d.meta,
            "warnings": d.warnings, "sha256": d.sha256}


@app.get("/api/documents/{doc_id}/nodes")
def document_nodes(doc_id: int, q: str = "", kind: str = "", limit: int = 100, s=Depends(db)):
    """Search within extracted text (or list sheet rows for the spreadsheet viewer)."""
    query = s.query(Node).filter_by(doc_id=doc_id)
    if kind:
        query = query.filter(Node.kind == kind)
    if q:
        query = query.filter(Node.text.ilike(f"%{q}%"))
    return [_node(n) for n in query.order_by(Node.seq).limit(limit)]


@app.get("/api/nodes/{node_id}")
def get_node(node_id: int, s=Depends(db)):
    n = _get(s, Node, node_id)
    out = _node(n)
    out["requirements"] = [r.key for r in s.query(Requirement).all() if r.data.get("source_node_id") == n.id]
    out["evidence"] = [{"run_id": e.run_id, "bidder_id": e.bidder_id, "requirement_key": e.requirement_key}
                       for e in s.query(Evidence).filter_by(node_id=n.id)]
    return out


def _node(n):
    return {"id": n.id, "doc_id": n.doc_id, "kind": n.kind, "ref": n.ref, "text": n.text, "page": n.page,
            "regions": n.regions, "meta": n.meta}


@app.middleware("http")
async def revalidate_ui(request, call_next):
    """Browsers must re-check the UI files so an updated app.js is never served stale."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
