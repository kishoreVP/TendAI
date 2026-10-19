"""Command line.

  python -m tendai demo [--fresh]   load both sample cases, extract, approve (as 'demo-script'), run
  python -m tendai serve            start the web UI on http://127.0.0.1:8000
"""
import sys
import time

from . import config, pipeline
from .db import Requirement, make_session_factory

SOR = config.ROOT / "data/benchmarks/TN_PWD_SOR_2025-26_Vol1.pdf"
SOR_META = {"issuer": "Government of Tamil Nadu, Public Works Department", "effective_date": "2025-07-22",
            "tax_basis": "Excludes all taxes including GST (SOR general note (xi), p.10; title page, p.4)"}
SAMPLES = [
    ("cable_m246", config.ROOT / "data/samples/cable_m246",
     [config.ROOT / "data/benchmarks/historical_awards_SYNTHETIC.csv"]),
    ("pwd_tiruchengode", config.ROOT / "data/samples/pwd_tiruchengode", [SOR]),
]


def demo(fresh=False):
    if fresh:
        (config.STORAGE_DIR / "tendai.db").unlink(missing_ok=True)
    s = make_session_factory()()
    for name, folder, bench in SAMPLES:
        t0 = time.perf_counter()
        case = pipeline.load_sample_case(s, folder, name, [b for b in bench if b.exists()], SOR_META)
        pipeline.extract_requirements(s, case)
        n_bench = pipeline.extract_benchmarks(s, case)
        # DEMO ONLY: a real reviewer approves requirements in the UI. Duplicates and AI-proposed
        # requirements stay drafts so a human has to look at them.
        reqs = s.query(Requirement).filter_by(case_id=case.id).all()
        for r in reqs:
            ai = r.data["extraction_method"].startswith("llm")
            if r.review_status == "draft" and not ai and not any("duplicate" in f for f in r.data["flags"]):
                pipeline.review_requirement(s, r, "demo-script", "approved", comment="auto-approved by demo script")
        tv = pipeline.approve_template(s, case, "demo-script")
        run = pipeline.run_evaluation(s, case, tv, actor="demo-script")
        s.commit()
        print(f"{name}: {len(reqs)} requirements, {n_bench} benchmarks, template v{tv.version}, run {run.id} "
              f"({time.perf_counter() - t0:.1f}s)")
    s.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "demo"
    if cmd == "demo":
        demo(fresh="--fresh" in sys.argv)
    elif cmd == "serve":
        import uvicorn
        uvicorn.run("tendai.api:app", host="127.0.0.1", port=8000)
    else:
        print(__doc__)
