"""Workflow steps. Each step is idempotent and audited:

  ingest files -> extract requirements (drafts) -> review/approve -> freeze template version
  -> extract benchmarks -> run evaluation (evidence + rule results + price comparison)

Runs never overwrite each other: each run stores the template version, document hashes,
benchmark ids, rule-engine version and inference configuration it used.
"""
from pathlib import Path

from . import benchmarks as bm
from . import config, evaluate, requirements
from .bidders import BidderContext, collect_claims
from .db import (Benchmark, Bidder, Document, Evidence, PriceComparison, Requirement, Result, Run,
                 TemplateVersion, audit)
from .schemas import Requirement as ReqSchema


def extract_requirements(s, case, actor="system", use_llm=True):
    """Create draft requirements. Requirements already reviewed by a human are kept as they are."""
    docs = s.query(Document).filter_by(case_id=case.id, role="tender", status="parsed").all()
    drafts = requirements.extract_requirements(s, case, docs, use_llm=use_llm)
    existing = {r.key: r for r in s.query(Requirement).filter_by(case_id=case.id)}
    for d in drafts:
        cur = existing.get(d.requirement_id)
        if cur is None:
            s.add(Requirement(case_id=case.id, key=d.requirement_id, data=d.model_dump(), history=[]))
        elif cur.review_status == "draft":
            cur.data = d.model_dump()
    audit(s, actor, "extract_requirements", "case", case.id, count=len(drafts))
    s.flush()
    return drafts


def review_requirement(s, req, actor, status=None, changes=None, comment=None):
    """Edit and/or approve/reject one requirement; every change is kept in its history."""
    entry = {"actor": actor, "status": status, "changes": changes, "comment": comment,
             "previous": {"status": req.review_status, "data": {k: req.data.get(k) for k in (changes or {})}}}
    if changes:
        data = {**req.data, **changes, "version": req.data.get("version", 1) + 1}
        ReqSchema.model_validate(data)  # reject edits that break the schema
        req.data = data
    if status:
        req.review_status = status
    req.history = [*req.history, entry]
    audit(s, actor, "review_requirement", "requirement", req.key, status=status, comment=comment,
          changes=changes)
    s.flush()


def approve_template(s, case, actor):
    """Freeze all approved requirements into a new immutable template version."""
    reqs = s.query(Requirement).filter_by(case_id=case.id, review_status="approved").order_by(Requirement.id).all()
    if not reqs:
        raise ValueError("no approved requirements to freeze")
    last = s.query(TemplateVersion).filter_by(case_id=case.id).order_by(TemplateVersion.version.desc()).first()
    tv = TemplateVersion(case_id=case.id, version=(last.version + 1 if last else 1),
                         requirements=[r.data for r in reqs], approved_by=actor)
    s.add(tv)
    s.flush()
    audit(s, actor, "approve_template", "template_version", tv.id, version=tv.version, count=len(reqs))
    return tv


def extract_benchmarks(s, case, actor="system"):
    """(Re)build benchmark records from the case's benchmark documents."""
    s.query(Benchmark).filter_by(case_id=case.id, review_status="draft").delete()
    count = 0
    for doc in s.query(Document).filter_by(case_id=case.id, role="benchmark", status="parsed"):
        if doc.kind == "pdf":
            from .db import Node
            nodes = s.query(Node).filter_by(doc_id=doc.id).order_by(Node.seq).all()
            meta = doc.meta.get("benchmark", {})
            records = bm.from_sor_nodes(nodes, meta.get("issuer"), meta.get("effective_date"), meta.get("tax_basis"))
        elif doc.kind == "csv":
            records = bm.from_csv(doc.path)
        else:
            continue
        for r in records:
            s.add(Benchmark(case_id=case.id, doc_id=doc.id, data=r.model_dump()))
        count += len(records)
    audit(s, actor, "extract_benchmarks", "case", case.id, count=count)
    s.flush()
    return count


def run_evaluation(s, case, template_version, actor="system", use_llm=True):
    reqs = [ReqSchema.model_validate(r) for r in template_version.requirements]
    docs = s.query(Document).filter_by(case_id=case.id, status="parsed").all()
    bench = s.query(Benchmark).filter_by(case_id=case.id).all()
    run = Run(case_id=case.id, template_version_id=template_version.id, config={
        "rule_engine_version": evaluate.RULE_ENGINE_VERSION,
        "documents": {d.id: {"filename": d.filename, "sha256": d.sha256, "role": d.role} for d in docs},
        "benchmark_ids": [b.id for b in bench],
        "inference": {"default": config.DEFAULT_PROVIDER, "routing": config.TASK_ROUTING,
                      "groq_model": config.GROQ_MODEL, "local_model": config.LOCAL_LLM_MODEL,
                      "cloud_for_bidder_docs": config.ALLOW_CLOUD_FOR_BIDDER_DOCS, "llm_enabled": use_llm},
    })
    s.add(run)
    s.flush()
    for bidder in s.query(Bidder).filter_by(case_id=case.id).order_by(Bidder.id):
        ctx = BidderContext(s, bidder)
        for req in reqs:
            claims = collect_claims(ctx, req, use_llm=use_llm)
            ev_ids = []
            for c in claims:
                e = Evidence(run_id=run.id, bidder_id=bidder.id, requirement_key=req.requirement_id,
                             node_id=c.source_node_id, data=c.model_dump())
                s.add(e)
                s.flush()
                ev_ids.append(e.id)
            status, why, calc = evaluate.evaluate(req, claims)
            s.add(Result(run_id=run.id, bidder_id=bidder.id, requirement_key=req.requirement_id,
                         auto_status=status, explanation=why, calc=calc, evidence_ids=ev_ids))
        compare_prices(s, case, run, bidder, bench)
    audit(s, actor, "run_evaluation", "run", run.id, template_version=template_version.version)
    s.flush()
    return run


def compare_prices(s, case, run, bidder, bench):
    tender_boqs = [d for d in s.query(Document).filter_by(case_id=case.id, role="tender", status="parsed")
                   if d.kind in ("xls", "xlsx", "csv")]
    bidder_boqs = [d for d in s.query(Document).filter_by(bidder_id=bidder.id, status="parsed")
                   if d.kind in ("xls", "xlsx", "csv")]
    if not tender_boqs or not bidder_boqs:
        return
    prices, col = {}, None
    for d in bidder_boqs:
        p, c = bm.bidder_prices(s, d)
        prices.update(p)
        col = col or c
    for item in bm.boq_items(s, tender_boqs[0]):
        quoted = prices.get(item["sl"])
        match = bm.match_item(item["description"], item["unit"], bench)
        data = {"sl": item["sl"], "description": item["description"], "qty": item["qty"], "unit": item["unit"],
                "tender_node_id": item["node"].id, "quoted_rate": quoted[0] if quoted else None,
                "quoted_column": col, "amount": round(quoted[0] * item["qty"], 2) if quoted else None,
                "benchmark": match}
        ref = (match.get("summary") or {}).get("median")
        if quoted and ref:
            data["ratio_to_benchmark"] = round(quoted[0] / ref, 3)
        s.add(PriceComparison(run_id=run.id, bidder_id=bidder.id, node_id=quoted[1].id if quoted else None,
                              data=data))


def load_sample_case(s, folder, case_name, benchmark_files=(), benchmark_meta=None):
    """Ingest a sample folder: tender files at the top level, bidders/<name>/* for bidders."""
    from .ingest import get_bidder, get_case, ingest
    folder = Path(folder)
    case = get_case(s, case_name)
    for f in sorted(folder.iterdir()):
        if f.is_file():
            ingest(s, case, f, "tender")
    for b in sorted((folder / "bidders").iterdir()) if (folder / "bidders").exists() else []:
        bidder = get_bidder(s, case, b.name)
        for f in sorted(b.iterdir()):
            ingest(s, case, f, "bidder", bidder)
    for f in benchmark_files:
        doc = ingest(s, case, f, "benchmark")
        if benchmark_meta and doc.kind == "pdf":
            doc.meta = {**doc.meta, "benchmark": benchmark_meta}
    s.flush()
    return case
