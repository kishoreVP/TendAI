# TendAI — evidence-grounded tender evaluation

TendAI reads a government tender, extracts its requirements into a reviewable template, finds each
bidder's evidence, evaluates explicit rules deterministically, compares prices against benchmarks
and shows **every finding next to a highlighted snapshot of the tender clause and the bidder's
document** it came from.

It is **decision support**: automatic statuses are proposals. A result becomes final only when a
named reviewer records a decision with a reason.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env                       # add GROQ_API_KEY (optional - rules work without any LLM)
python scripts/make_synthetic_bids.py      # synthetic bidders + golden outcomes (needs data/samples)
python -m tendai demo --fresh              # ingest both cases, extract, approve (as demo-script), evaluate
python -m tendai serve                     # http://127.0.0.1:8000
python -m pytest                           # 80 tests, ~105 s
python scripts/retrieval_eval.py           # tree vs chunk retrieval metrics
python scripts/bench_llm.py                # live LLM check (Groq or local Jetson server)
```

`data/` and `.env` are git-ignored (procurement documents and keys never go in the repo). Layout:

```
data/samples/<case>/<tender files>             tender PDF(s) + BOQ (.xls/.xlsx)
data/samples/<case>/bidders/<name>/<files>     one folder per bidder
data/benchmarks/                               SOR PDF, historical-price CSV
```

## Using the app

* **Bidders** (start page): pick a bidder on the left (cards show price rank L1/L2/…, quoted total
  and a compliance bar). The bidder page shows a verdict donut, key facts and three tabs:
  * **Findings**: one card per requirement. *Tender requires* vs *Bidder submitted*, each with a
    highlighted snapshot cropped from the real PDF. Click a snapshot to open the full page with the
    region highlighted. Filters: open issues / fails / review / missing / meets, category, search.
    Reviewers record decisions (with a reason) on the card.
  * **Commercial**: each BOQ item, the quoted rate (click → the exact cell in the bidder's Excel),
    the benchmark reference, and a bar showing the quote against it. Totals and ranking across bidders.
  * **Documents**: the bidder's files, hashes and extraction notes (e.g. scanned pages).
  * **⬇ Evidence pack (PDF)**: every failed / review / missing finding with tender and bidder crops
    side by side. A visual-grounding report you can hand in or put on slides.
* **Compare**: requirement × bidder matrix. Click a cell to jump to that bidder's finding.
* **Tender requirements**: review the extracted template (approve / reject / edit, each with
  history), see each requirement's source snapshot, freeze a new template version and re-run.
  Requirements proposed by the LLM are tagged *AI-proposed* and stay drafts until a human approves them.
* **Benchmarks**, **Audit**: SOR / historical records with source links; every action with actor and time.

## Workflow

```
upload ─▶ ingest (validate, hash, parse to a located document tree)
        ─▶ requirements (rules + optional grounded LLM, draft) ─▶ reviewer approves ─▶ frozen template vN
        ─▶ benchmarks (SOR / CSV records)
        ─▶ run: per bidder × requirement: find claims ─▶ deterministic rule ─▶ proposed status
                per BOQ item: quoted rate vs comparable benchmark (normalised, every step recorded)
        ─▶ report (web, evidence pack, Excel, PDF, CSV, JSON) ─▶ reviewer decisions (audited)
```

Each run stores the template version, document hashes, benchmark ids, rule-engine version and
inference config, so it can be reproduced. Re-running never overwrites earlier runs or decisions.

## Modules (`tendai/`)

| File | Purpose |
|---|---|
| `pdfparse.py` | PDF → visual rows → tree (sections, clauses, tables). Structure recovery: label/value on separate blocks, wrapped values and headings, letterheads/footers, superscripts, multi-page tables, SOR item codes. Coordinates in displayed-page space. |
| `ingest.py` | Upload validation (type, magic bytes, size, filename sanitising), SHA-256 content-addressed storage, XLS/XLSX/CSV rows with cell addresses, DOCX paragraphs/tables. |
| `requirements.py` | Rules for requirement tables (GTP), EMD, turnover, experience, completion period, contractor class, documents (incl. conditional ones); duplicate/conflict/corroboration flags; grounded LLM extraction. |
| `bidders.py` | Finds bidder claims: the bidder's copy of the requirement table, retrieval + unit-aware values, whole document sections, exemption claims; grounded LLM fallback. |
| `evaluate.py` | Deterministic rules → `MEETS / DOES_NOT_MEET / INSUFFICIENT_EVIDENCE / REQUIRES_HUMAN_REVIEW / NOT_APPLICABLE`. |
| `benchmarks.py` | SOR table parsing, attribute-based matching (voltage, size, poles, amps, device…), exact unit normalisation. |
| `retrieval.py` | BM25 over tree nodes with section-path context; fixed-chunk baseline. |
| `llm.py` | `GroqProvider` (GPT-OSS, strict JSON schema), `LocalProvider` (llama.cpp on Jetson), per-task routing, cloud policy. |
| `exports.py` | Run report, Excel/PDF/CSV/JSON, highlighted snapshots, per-bidder evidence pack. |
| `units.py`, `schemas.py`, `db.py`, `pipeline.py`, `api.py`, `web/` | Units, versioned contracts, SQLite/Postgres schema, orchestration, FastAPI, single-page UI (pdf.js). |

### Main decisions

* **PyMuPDF instead of Docling as the extraction base.** The sample PDFs have text layers. PyMuPDF
  gives exact coordinates for highlighting and parses 101 pages in ~3 s. The earlier Docling run
  split §2.0's label and value, broke §2.3's wrapped value, reordered §3.x and lost superscripts.
  All four are regression tests. OCR for scanned pages is the main missing piece (see limitations).
* **Rules decide, the LLM proposes.** Statuses come only from deterministic rules. The LLM proposes
  requirements for clauses the rules don't understand, and values for passages they can't parse.
  Its output must quote the source verbatim (ungrounded items are dropped), and it is tagged in the
  UI. AI-extracted values never produce a final status on their own.
* **No vector DB / queue / graph DB / React build.** Retrieval evaluation shows no need yet.

## Sample data

| Case | Source | Content |
|---|---|---|
| `cable_m246` | real | TNPDCL spec M.246/2026-27 (101 pp), NIC BOQ .xls |
| `pwd_tiruchengode` | real | TN PWD electrification tender (30 pp), 53-item BOQ .xls |
| SOR | real | TN PWD Schedule of Rates 2025-26 Vol I (393 pp) → 3,496 rate records |
| bidders | **SYNTHETIC** | 6 fictional bidders, 35 documents, `scripts/make_synthetic_bids.py` |
| historical prices | **SYNTHETIC** | `historical_awards_SYNTHETIC.csv`, 4 records to test matching |

Each synthetic bidder uploads separate documents like a real e-tender: a covering letter on
letterhead (reference no., signatory), the full 56-row GTP copied from the tender, a supply
statement, declarations, a CA turnover certificate (with UDIN), a lab type-test report, a BIS
licence, an EMD e-receipt or exemption claim, statutory documents and a priced NIC BOQ.

| Bidder | Designed behaviour |
|---|---|
| Coromandel | compliant; type-test values differ slightly from the GTP but still meet the limits |
| Meridian | DC resistance 0.0480 > 0.0469; turnover ₹18.2 Cr < ₹21.8 Cr; insulation min 7.5 < 7.8 mm; **EMD receipt is a scanned image** (no text layer → flagged, not guessed) |
| Vajra | DC resistance "will be furnished" → missing; strands 91 (GTP) vs 61 (type test) → contradiction; AC resistance in ohm/m → converted; cable code A2XWaY ≠ A2XWa2Y; claims MSE EMD exemption; turnover only self-declared; **prompt-injection sentence in the covering letter** |
| Murugan (PWD) | compliant |
| Kaveri (PWD) | EMD ₹43,000 < ₹43,500; Class-V; 120 > 90 days; no indemnity bond; turnover certificate without UDIN |
| Vinayaga (PWD) | lowest price; completion "3 months" vs limit in days → review (no inexact unit conversion) |

## Test results (actually executed, 2026-10-19, Windows 11, Python 3.12)

`python -m pytest` → **80 passed**.

| Milestone | Tests | What is verified |
|---|---|---|
| 1 Ingestion | `test_ingest.py` | rejects unsupported/empty/mis-typed/corrupted files (failed docs recorded), filename sanitising, hashing, idempotent re-ingest, BOQ continuation rows |
| 2 Requirements | `test_requirements.py` | schema validity; 8 manually verified values with page numbers; unstated values stay `None`; duplicate / corroboration / conditional flags |
| 3 Structure | `test_structure.py` | the 4 Docling failure cases, page-number removal, superscripts, 3-page GTP table, scanned pages flagged, rotated pages |
| 4 Benchmarks | `test_rules.py` | SOR records, attribute conflicts, MCB/enclosure and pole parsing regressions, exact unit conversion |
| 5 Evaluation | `test_rules.py`, `test_e2e.py` | the brief's 0.0469/0.0450/0.0480 example, missing ≠ failing, contradictions, self-declared vs documentary, approx without tolerance, AI-extracted never final |
| 6 Grounding | `test_e2e.py` | the text under every stored numeric-evidence box contains the claimed value; requirement boxes lie inside their pages; snapshots are PNG crops; evidence pack embeds the crops; document evidence covers the whole certificate |
| 7 Inference | `test_llm.py` | mocked HTTP: routing, missing key, cloud block for bidder data, strict schema request, 429 retry, invalid output retry, timeout, unavailable model, grounding filter |
| 8 End-to-end | `test_e2e.py`, `test_api.py` | golden outcomes for all 6 bidders, reproducible re-run, human decision preserved, injection without effect, commercial comparison, all exports, full HTTP workflow |

**Live Groq check** (`scripts/bench_llm.py`, `openai/gpt-oss-120b`, 4 real tender clauses):
4/4 valid structured outputs, 4/4 grounded quotes, 4/4 thresholds recovered, ~2.1 s per clause.
In the demo, Groq proposes about 25 extra requirements per tender (the count varies slightly run to run)
from clauses the rules did not cover. They are AI-proposed drafts awaiting review.

### Retrieval evaluation (13 manually verified questions on M.246, k=3)

| | page recall@3 | page precision@3 | table-row hit@3 |
|---|---|---|---|
| tree (nodes + section context) | 1.00 | 0.51 | 4/5 |
| fixed 800-char chunks | 1.00 | 0.46 | n/a |

Both find the right pages. The tree's advantage is returning the exact row/clause with coordinates.

### Timings

| Step | Time |
|---|---|
| M.246 (101 pp) parse | ~3 s |
| SOR (393 pp) parse | ~80 s (PyMuPDF table detection), once per file |
| Requirement extraction with Groq | +~1 min per tender (~30 clauses × ~2 s) |

## Inference configuration (`.env`)

* `TENDAI_DEFAULT_PROVIDER=none|groq|local`, per task via `TENDAI_TASK_ROUTING`
  (e.g. `{"requirement_extraction": "groq", "evidence_mapping": "local"}`).
* Groq: `GROQ_API_KEY`, `TENDAI_GROQ_MODEL` (default `openai/gpt-oss-120b`).
* Jetson / local Qwen: see [docs/JETSON.md](docs/JETSON.md). **Not tested on hardware.**
* Bidder documents are never sent to a cloud provider unless `TENDAI_ALLOW_CLOUD_FOR_BIDDER_DOCS=true`.

## Known limitations

* **No OCR.** Image-only pages are flagged per page (14 in M.246, Meridian's EMD receipt), never guessed.
* Requirement rules cover the patterns in these tenders. Others come from the LLM as drafts.
* "Approx / nominal / average" values without a stated tolerance always go to human review.
* Document checks verify presence and named attributes (e.g. UDIN), not authenticity or validity dates.
* Benchmark matching: SOR material rates vs supply-and-fix BOQ items are only *partial*. The device
  keyword list is small. No escalation/tax/location adjustment without a documented basis.
* Access control is a placeholder (`X-User` header).
* PyMuPDF is AGPL-licensed. Check this before distributing.

## Next steps

1. OCR for scanned pages, with confidence carried into nodes.
2. Run `bench_llm.py` on the Jetson; fill the JETSON.md table.
3. More requirement rules and a larger golden set from additional real tenders.
4. Authentication, per-case permissions, retention/deletion controls.
