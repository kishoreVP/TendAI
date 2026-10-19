# GammaPrime.ai — tender evaluation, evidence-first

Reads a government tender, extracts its requirements into a reviewable template, finds each
bidder's evidence, evaluates explicit rules deterministically, compares prices against benchmarks
and shows every finding on the exact PDF region / spreadsheet cell it came from.

It is **decision support**: automatic statuses are proposals. A result becomes final only when a
named reviewer records a decision with a reason.

## Quick start

```bash
pip install -r requirements.txt
python scripts/make_synthetic_bids.py      # synthetic bidders + golden outcomes (needs data/samples)
python -m gammaprime demo --fresh          # ingest both sample cases, extract, approve (as demo-script), evaluate
python -m gammaprime serve                 # http://127.0.0.1:8000
python -m pytest                           # 78 tests, ~90 s
python scripts/retrieval_eval.py           # tree vs chunk retrieval metrics
```

`data/` is git-ignored (procurement documents never go in the repo). Expected layout:

```
data/samples/<case>/<tender files>             tender PDF(s) + BOQ (.xls/.xlsx)
data/samples/<case>/bidders/<name>/<files>     one folder per bidder
data/benchmarks/                               SOR PDF, historical-price CSV
```

## Workflow

```
upload ─▶ ingest (validate, hash, parse to a located document tree)
        ─▶ requirements (draft) ─▶ reviewer approves/edits/rejects ─▶ frozen template vN
        ─▶ benchmarks (SOR / CSV records)
        ─▶ run: per bidder × requirement: find claims ─▶ deterministic rule ─▶ proposed status
                per BOQ item: quoted rate vs comparable benchmark (normalised, every step recorded)
        ─▶ report (web, Excel, PDF, CSV, JSON) ─▶ reviewer decisions (audited)
```

Each run stores the template version, document hashes, benchmark ids, rule-engine version and
inference config, so it can be reproduced. Re-running never overwrites earlier runs or decisions.

## Modules (≈3,100 lines)

| File | Purpose |
|---|---|
| `pdfparse.py` | PDF → visual rows → tree (sections, clauses, tables). Structure recovery: label/value on separate blocks, wrapped values, headers/footers, superscripts, multi-page tables, SOR item codes. Coordinates in displayed-page space. |
| `ingest.py` | Upload validation (type, magic bytes, size, filename sanitising), SHA-256 content-addressed storage, XLS/XLSX/CSV rows with cell addresses, DOCX paragraphs/tables. |
| `requirements.py` | Rules for requirement tables (GTP), EMD, turnover, experience, completion period, contractor class, documents; duplicate/conflict/corroboration flags; optional grounded LLM extraction. |
| `bidders.py` | Finds bidder claims: requirement-table rows, retrieval + unit-aware values, document headings, exemption claims; optional grounded LLM fallback. |
| `evaluate.py` | Deterministic rules → `MEETS / DOES_NOT_MEET / INSUFFICIENT_EVIDENCE / REQUIRES_HUMAN_REVIEW / NOT_APPLICABLE`. |
| `benchmarks.py` | SOR table parsing, attribute-based matching (voltage, size, poles, amps, device…), exact unit normalisation. |
| `retrieval.py` | BM25 over tree nodes with section-path context; fixed-chunk baseline. |
| `llm.py` | `GroqProvider` (GPT-OSS, strict JSON schema), `LocalProvider` (llama.cpp on Jetson), per-task routing, cloud policy. |
| `units.py`, `schemas.py`, `db.py` | Quantities/units, versioned Pydantic contracts, SQLAlchemy schema (SQLite; Postgres via URL). |
| `pipeline.py`, `api.py`, `exports.py`, `web/` | Orchestration, FastAPI, exports, single-page UI with pdf.js viewer. |

### Main decisions

* **PyMuPDF instead of Docling as the extraction base.** The sample PDFs have text layers. PyMuPDF
  gives exact line/span coordinates, which highlighting needs, and runs in ~3 s for 101 pages. The
  earlier Docling run (`codes/extraction_practice`) split §2.0's label and value, broke §2.3's
  wrapped value, reordered §3.x and lost superscripts. All four are now regression tests
  (`tests/test_structure.py`). Docling (or another OCR engine) is the natural addition for scanned pages.
* **Rules first, LLM optional.** Everything in the sample tenders is handled deterministically. The LLM
  is used only for clauses/passages the rules don't understand. Its output must quote the source
  verbatim (ungrounded items are dropped) and is marked `ai_proposed` / `ai_extracted`. AI-extracted
  values can never produce a final status on their own.
* **No vector DB / queue / graph DB.** Retrieval evaluation shows no need yet (see below).
* **Plain HTML + JS UI**: no build step.

## Sample data

| Case | Source | Content |
|---|---|---|
| `cable_m246` | real | TNPDCL spec M.246/2026-27 (101 pp), NIC BOQ .xls |
| `pwd_tiruchengode` | real | TN PWD electrification tender (30 pp), 53-item BOQ .xls |
| SOR | real | TN PWD Schedule of Rates 2025-26 Vol I (393 pp) → 3,496 rate records |
| bidders | **SYNTHETIC** | 3 cable bidders, 2 PWD bidders, made by `scripts/make_synthetic_bids.py` |
| historical prices | **SYNTHETIC** | `historical_awards_SYNTHETIC.csv`, 4 records to test matching |

The synthetic bidders each exercise specific paths:

| Bidder | Designed behaviour |
|---|---|
| Coromandel | compliant; GTP value and type-test value differ slightly but both meet |
| Meridian | DC resistance 0.0480 > 0.0469; turnover ₹18.2 Cr < ₹21.8 Cr; insulation min 7.5 < 7.8 mm |
| Vajra | DC resistance "will be furnished" → insufficient; strands 91 in GTP vs 61 in type test → contradiction; AC resistance in ohm/m → converted; cable code A2XWaY ≠ A2XWa2Y → review; claims MSE EMD exemption; turnover only self-declared; **contains a prompt-injection sentence** |
| Murugan (PWD) | compliant |
| Kaveri (PWD) | EMD ₹43,000 < ₹43,500; Class-V; 120 > 90 days; no indemnity bond; turnover certificate without UDIN |

## Test results (actually executed, 2026-10-19, Windows 11, Python 3.12)

`python -m pytest` → **78 passed** (≈90 s).

| Milestone | Tests | What is verified |
|---|---|---|
| 1 Ingestion | `test_ingest.py` | rejects unsupported/empty/mis-typed/corrupted files (failed docs are recorded), filename sanitising, hashing, idempotent re-ingest, BOQ continuation rows |
| 2 Requirements | `test_requirements.py` | schema validity; 8 manually verified values with page numbers; unstated values stay `None`; duplicate / corroboration / conditional flags |
| 3 Structure | `test_structure.py` | the 4 Docling failure cases, page-number removal, superscripts, 3-page GTP table, scanned pages flagged, rotated pages |
| 4 Benchmarks | `test_rules.py` | SOR records (M-1765 = ₹485 higher end…), attribute conflicts, MCB/enclosure and pole parsing regressions, exact unit conversion |
| 5 Evaluation | `test_rules.py`, `test_e2e.py` | brief's 0.0469/0.0450/0.0480 example, missing ≠ failing, contradictions, self-declared vs documentary, approx without tolerance, AI-extracted never final |
| 6 Grounding | `test_e2e.py` | for every numeric claim (≥20), the text under the stored PDF box contains the claimed value; all requirement boxes lie inside their page |
| 7 Inference | `test_llm.py` | **mocked HTTP only**: routing, missing key, cloud block for bidder data, strict schema request, 429 retry, invalid output retry, timeout, unavailable model, grounding filter |
| 8 End-to-end | `test_e2e.py`, `test_api.py` | golden outcomes for all 5 bidders (27 expected outcomes), reproducible re-run, human decision preserved, injection without effect, commercial comparison, all 4 exports, full HTTP workflow |

Manual browser check (this session): opening Meridian's GTP-6 finding highlights row 6 of the bidder's
GTP table (p.2) and row 6 of Annexure-B in the real tender (p.63/101). Price links jump to the BOQ row
and the rate cell.

### Retrieval evaluation (`scripts/retrieval_eval.py`, 13 manually verified questions on M.246, k=3)

| | page recall@3 | page precision@3 | table-row hit@3 |
|---|---|---|---|
| tree (nodes + section context) | 1.00 | 0.51 | 4/5 |
| fixed 800-char chunks | 1.00 | 0.46 | n/a |

On this small set both find the right pages. The tree's advantage is returning the exact row/clause
with coordinates. No evidence yet that embeddings or fine-tuning are needed.

### Measured timings

| Step | Time |
|---|---|
| M.246 (101 pp) parse | ~3 s |
| SOR (393 pp) parse | ~80 s, dominated by PyMuPDF table detection; once per file (content-addressed) |
| Cable case end-to-end (3 bidders) | ~10 s |
| PWD case end-to-end incl. SOR | ~128 s |

## Inference configuration

Off by default (`GP_DEFAULT_PROVIDER=none`). See `.env.example`.

* **Groq**: set `GROQ_API_KEY`, `GP_DEFAULT_PROVIDER=groq` (or per task via `GP_TASK_ROUTING`). The default
  model `openai/gpt-oss-120b` and strict `json_schema` support were checked against Groq's docs on
  2026-10-19. **Not run against the live API in this session (no key available). Only mocked tests.**
* **Jetson / local Qwen**: see [docs/JETSON.md](docs/JETSON.md). **Not tested on hardware.**
  `scripts/bench_llm.py` measures latency / validity once a server is reachable.
* Bidder documents are never sent to a cloud provider unless `GP_ALLOW_CLOUD_FOR_BIDDER_DOCS=true`.

## Known limitations

* **OCR not implemented.** Image-only pages are flagged per page (14 in M.246), never guessed.
* Requirement rules cover the patterns in these two tenders (common in TN/Indian tenders). Other clause
  types need new rules or the LLM path. The cable spec yields 57 requirements but many GTP rows are
  descriptive. Synthetic bidders fill only 16 GTP rows, so many results are honestly `INSUFFICIENT_EVIDENCE`.
* "Approx / nominal / average" values without a stated tolerance always go to human review.
* Document checks verify presence and named attributes (e.g. UDIN) only, not authenticity or validity dates.
* Conditional requirements (Udyam certificate, partnership deed "if a firm") go to review rather than being
  auto-decided.
* Benchmark matching: SOR material rates vs supply-and-fix BOQ items are only ever *partial*. The device
  keyword list is small ("MCB isolators" still count as MCBs). No escalation, tax or location adjustment
  is applied because no documented basis is loaded.
* Clause headings are segmented heuristically. Unusual layouts (true two-column pages) are untested.
* Access control is a placeholder (`X-User` header). Real authentication and per-case authorisation are
  needed before multi-user use.
* PyMuPDF is AGPL-licensed. Check this before distributing.

## Next steps (priority order)

1. OCR for scanned pages (Docling or Tesseract via PyMuPDF), with confidence carried into nodes.
2. Run `bench_llm.py` against Groq with a real key and on the Jetson; fill the JETSON.md table.
3. More requirement rules from additional real tenders (warranty, delivery schedules, standards
   clauses), plus a larger golden set.
4. Authentication, per-case permissions, retention/deletion controls.
5. Postgres deployment + background worker for large SOR ingestion.
