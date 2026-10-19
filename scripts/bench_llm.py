"""Benchmark a configured LLM provider on requirement extraction from real tender clauses.

    GP_DEFAULT_PROVIDER=local GP_LOCAL_LLM_URL=http://jetson.local:8080/v1 python scripts/bench_llm.py
    GP_DEFAULT_PROVIDER=groq  GROQ_API_KEY=...                              python scripts/bench_llm.py

Reports load/first-call latency, per-clause latency, structured-output validity, grounding
rate (quotes found verbatim) and whether the expected threshold was recovered. Memory must be
measured on the device itself (e.g. `tegrastats` on Jetson) while this runs.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gammaprime import llm, requirements  # noqa: E402
from gammaprime.db import Node  # noqa: E402

CLAUSES = [  # verbatim from the sample tenders, with the value a correct extraction must contain
    ("3. The Annual Turnover of the Tenderer shall be more than Rs.21.80Cr. ( Rupees Twenty one core and eighty "
     "Lakhs only) during any one of the last three years ( (i.e.) 2022-23, 2023-24 & 2024-25 ).", "21.8"),
    ("2. The bidders should have manufactured and supplied satisfactorily a minimum of 50 KM of 33 KV 1 x 630 sq.mm "
     "or higher voltage rating / higher size of UG XLPE Power Cables put together during the preceding ten years.", "50"),
    ("3. Period of completion for 90 Days", "90"),
    ("5. The bidder should furnish a copy of BIS license for the manufacture of the tendered cable valid on the day "
     "of tender opening.", "BIS"),
]


class Case:
    name = "bench"


def main():
    provider = llm.get_provider("requirement_extraction", contains_bidder_data=False)
    if provider is None:
        sys.exit("Set GP_DEFAULT_PROVIDER=local or groq (see .env.example)")
    print(f"provider={provider.name} model={provider.model}")
    stats = {"ok": 0, "grounded": 0, "found": 0, "errors": 0, "latency": []}
    for i, (text, expect) in enumerate(CLAUSES):
        node = Node(id=i, doc_id=0, kind="clause", ref=str(i), page=1, regions=[], text=text)
        t0 = time.perf_counter()
        try:
            reqs = requirements.extract_with_llm(Case(), node, provider)
            stats["ok"] += 1
            stats["grounded"] += bool(reqs)
            stats["found"] += any(expect.lower() in (str(r.evaluation_rule.value) + r.normalised_requirement).lower()
                                  for r in reqs)
        except llm.LLMError as e:
            stats["errors"] += 1
            print("  error:", e)
        stats["latency"].append(time.perf_counter() - t0)
        print(f"  clause {i}: {stats['latency'][-1]:.2f}s")
    n, lat = len(CLAUSES), stats["latency"]
    print(f"first call {lat[0]:.2f}s (includes warm-up), mean of rest {sum(lat[1:]) / max(len(lat) - 1, 1):.2f}s")
    print(f"valid structured output {stats['ok']}/{n}, grounded {stats['grounded']}/{n}, "
          f"expected value recovered {stats['found']}/{n}, errors {stats['errors']}")


if __name__ == "__main__":
    main()
