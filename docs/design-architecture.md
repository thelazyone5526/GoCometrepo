# Design and Architecture: Trade Document Pipeline (Part 1)

Status: draft v0.3 · September 2026 · Lives in `GoCometrepo/docs/` since Phase 1. Phase 1 findings are folded into §9 and §10.1; Phase 2 ticked item 13 and added the samples layout (§9) and two open items (§10.1).

This is the engineering blueprint: what we build, how the parts fit, and what "done" means. The "what and why" for GoComet lives in `Ansh/PRD-part1-draft.md`. The plain-language teaching version, with every rejected alternative, lives in `Ansh/00-architecture-overview.md`.

## 1. Build checklist

Tags: **B** = build and test in code · **D** = describe only (PRD, write-up, README) · **P2** = later, Part 2.

### 1.1 Pipeline (brief A–E, the minimum bar)

| # | Item | Tag | Done when |
|---|---|---|---|
| 1 | Accept one PDF, PNG or JPG (max 10 MB, 5 pages) plus a customer ID | B | All three submission samples (and the sample JPG) upload and start a run |
| 2 | Page preparation: render pages to images; get text lines with positions from the PDF text layer, or from image clean-up + OCR for scans | B | Every page yields a list of text spans (lines), each with position and confidence |
| 3 | Extractor: Gemini returns document type and all 8 fields, each with value (or null), exact source text and a self-rating | B | Output passes its schema on all three submission samples |
| 4 | Grounding and confidence in code: find each source text among the page words, check the value follows from it, check formats, score confidence | B | Unit tests: invented value → uncertain; "ACME" read from a page saying "ACEM" → uncertain with both readings; bad format → uncertain |
| 5 | Validator: check each field against ACME's YAML rules; Gemini only for the goods description and unsettled consignee names | B | One unit test per rule; no "match" unless grounded and above the threshold |
| 6 | Router (Option B): code works out the allowed outcomes, Gemini picks one and writes the reasoning and amendment draft, code checks both | B | Unit test: no path auto-approves a document with an uncertain or mismatched field; every mismatch appears in the draft; overrides are logged |
| 7 | LangGraph graph with a SQLite checkpointer and a step limit | B | A run killed after extraction resumes without repeating the extraction call |
| 8 | LLM wrapper: timeout, 2 retries with backoff, 6 calls per document, every call logged | B | A forced Gemini failure ends in human review with the reason; the call log shows tokens and time |
| 9 | Storage: every run stored (not just approved ones), duplicate uploads caught by file hash | B | Flagged and amendment runs can be queried |
| 10 | Plain-English query: Gemini writes SQL, code allows read-only SELECTs on approved views only, answer shown with its SQL | B | 6 sample questions match hand-written SQL; a DELETE attempt is refused |
| 11 | API (FastAPI), bound to localhost only | B | All endpoints in section 6 work from the UI |
| 12 | UI: upload, live pipeline progress, decision first, field table with both confidences, evidence on the page image, reasoning, draft, query box | B | Shows real state from a real run on all three submission samples |

### 1.2 Trust, evals and evidence

| # | Item | Tag | Done when |
|---|---|---|---|
| 13 | Sample generator: ACME commercial invoices with known correct values; clean and degraded versions; planted errors | B | Makes three submission samples (clean and correct, clean with errors, messy scan) plus the eval grid, each with an answer file. **Done (Phase 2):** `python -m samples.generate` writes 28 grid PDFs, 3 submission samples and 1 JPG, each with a schema-checked answer file; `tests/test_samples.py` |
| 14 | Offline eval: 28 documents (4 image conditions × 7 versions), one command | B | Report shows field accuracy by field and condition, invented values, verdict and outcome accuracy; any wrong auto-approval fails the run |
| 15 | Unit tests (pytest) for everything deterministic | B | Grounding, confidence, formats, rules, guardrails and the SQL gate pass |
| 16 | Failure log kept during testing | B | At least 3 real failures written down, with cause and fix, for the write-up |

### 1.3 Submission

| # | Item | Tag | Done when |
|---|---|---|---|
| 17 | README: setup and run on a fresh laptop in under 10 minutes | B | Followed from scratch on a clean clone |
| 18 | Sample queries file with the questions, SQL and results | B | Produced from a real database |
| 19 | Finalise the PRD: Section 1 in Ansh's own words; replace the five placeholder numbers (see 10.1) | D | No "(assumption)" left without a stated source |
| 20 | Technical write-up: diagram, 3 worst failures from item 16, observability for 50 customers, cost, latency, week vs day | D | 1–2 pages, numbers taken from the call log and eval |
| 21 | 2–3 minute demo video on one document | D | Recorded from a real run |

### 1.4 Deliberately not built

| Item | Tag | Note |
|---|---|---|
| Pilot plan, operator override rate, correct touchless rate in production | D | In the PRD. The `runs` table keeps empty operator-review columns so the metric can be measured later without a schema change |
| LiteLLM gateway, Langfuse tracing, ClickHouse, multi-tenancy, login | D | Production path, named in PRD and write-up |
| Email trigger, several documents per shipment, cross-document checks, editable draft and send flow | P2 | Designed for (sections 3.4, 5), not built |

## 2. System overview

```
                      Browser (React + Vite, localhost:5173)
        Upload  |  Run view (polls every second)  |  Ask box
           |                   ^                       |
           v                   |                       v
  ------------------- FastAPI (127.0.0.1:8000) -------------------
  POST /api/runs       GET /api/runs/{id}          POST /api/query
      |                                                 |
      | background task                                 v
      v                                          Query service
  LangGraph pipeline                        Gemini -> SQL -> gate
  prepare -> extract -> validate -> route        -> read-only run
      |          |          |         |                 |
      |       Gemini     Gemini*   Gemini               |
      |      (vision)   (fuzzy)  (decide, draft)        |
      v                                                 v
  data/uploads/  (original files + rendered pages, named by file hash)
  data/app.db          shipments, documents, runs, field_results,
                       llm_calls, and query views          <-----
  data/checkpoints.db  LangGraph state after every step
  rules/acme.yaml      customer rules (read at "prepare")

  * only when code can't settle a field
```

Where state lives:
- During a run: in the LangGraph state, checkpointed to `checkpoints.db` after every node.
- After a run: the final result in `app.db`. The UI and the query layer read only `app.db`.
- Files: `data/uploads/`. Rules: YAML under version control.
- Everything under `data/` is git-ignored and can be deleted to reset.

## 3. The pipeline

### 3.1 Graph

```
START -> prepare -> extract -> validate -> route -> persist -> END
            |          |          |          |
            +----------+----------+----------+--> escalate -> persist
                     (any node that sets state.error)
```

Each node has one conditional edge: normal path, or `escalate` if it recorded an error or hit a limit. `escalate` sets the outcome to human review with the reason, so nothing fails silently. The graph has no cycles. The recursion limit is set to 10 as a backstop.

| Node | Agent? | LLM | Job |
|---|---|---|---|
| prepare | No, deterministic setup | None | File hash, file type, text layer check, render pages, clean images, build the page word list, load customer rules. Nova stages 1–2: scope resolution and context compilation |
| extract | Agent 1 (executor) | 1 call, +1 on retry | Gemini reads the page images; code grounds, checks and scores each field; one higher-resolution retry for fields that fail grounding |
| validate | Agent 2 (verifier) | 1 call | Rule checks in code; Gemini for the description rule and unsettled consignee names |
| route | Agent 3 (decider) | 1 call | Allowed outcomes in code; Gemini decides and writes; code checks |
| persist | No | None | Write the final result to `app.db` and mark the run completed |
| escalate | No | None | Human review with the reason |

### 3.2 Shared state (what each node reads and writes)

| Key | Written by | Contents |
|---|---|---|
| `run_id`, `customer_id`, `file_path`, `file_hash` | API | Inputs |
| `pages` | prepare | Per page: image path, source (`text_layer` or `ocr`), text spans (lines) with box and confidence, quality stats |
| `rules` | prepare | The customer's parsed YAML |
| `extraction` | extract | Document type, and per field: value, source text, page, box, model rating, grounding result, OCR reading, format result, confidence |
| `validation` | validate | Per field: verdict, found, expected, rule ID, reason, verdict confidence |
| `decision` | route | Allowed outcomes, outcome, reasoning, cited fields, amendment draft, decision source, override reason |
| `llm_calls_used`, `current_step`, `error` | any | Budget counter, progress for the UI, escalation reason |

Each section is stored as plain data, so checkpoints save cleanly, and is validated through its Pydantic model when written. A node only writes its own section.

### 3.3 Extractor detail

1. **Prepare the pages** (in `prepare`):
   - Text PDF: words come from the text layer (PyMuPDF) and are grouped into lines, confidence 1.0.
   - Scan or image: clean the image with OpenCV (straighten, remove noise, raise contrast), then RapidOCR gives text lines, boxes and a confidence per line.
   - OCR returns lines rather than single words, so both sources produce line-level text spans. Boxes are in pixels of the stored page image (the cleaned image for scans), so UI highlights line up.
   - Every page is rendered to an image at 200 DPI. That's what Gemini sees, so text and scan documents take the same path.
2. **Gemini call**: page images plus a prompt asking for the document type and, for each of the 8 fields, the value, the exact text as printed, the page number and a 0–1 self-rating. "Not on this document" means null. Temperature 0. The output must follow a JSON schema.
3. **Grounding** (code): normalise the text (case, spaces, punctuation), then look for the source text in that page's text spans, within one line or across two adjacent lines.
   - `exact`: found.
   - `near`: something at least 90% similar is found, but not identical (RapidFuzz). The page's actual text is stored as the OCR reading, so both show in the UI.
   - `not_found`: nothing close.
   - `absent`: value is null.
   - The matched spans' boxes are merged into one box, which the UI highlights.
4. **Value check** (code): the value must follow from the source text using that field's normaliser. If "12,450.00 KGS" becomes 12450 KG, fine. If the model's value doesn't follow from what's printed, it quietly changed something, and the field becomes uncertain.
5. **Format check** (code): HS code has at least 6 digits; the Incoterm is one of the 11 Incoterms 2020 rules; the weight is a number plus a unit; the invoice number isn't empty.
6. **Confidence** = the lowest of: model self-rating, grounding score (exact 1.0, near 0.5, not found 0.1), and source confidence (text layer 1.0, else the lowest OCR confidence among the matched spans). A failed value or format check caps it at 0.3. Using the lowest means a field is only as trustworthy as its weakest signal, so there are no weights to defend.
7. **Retry**: if any non-null field is `not_found` or `near`, do one extra Gemini call at 300 DPI for just those fields, then re-ground. It never loops.

Confidence below `CONFIDENCE_THRESHOLD` (config, starting at 0.85, tuned by the eval, see 10.1) makes the field uncertain.

### 3.4 Validator detail

Rules are keyed by customer ID and document type. `expected_fields` per document type is Nova's "schema routing": a field that isn't expected for that document type gets the verdict `not_applicable` and doesn't affect the decision. Our samples are commercial invoices, which carry all 8 fields.

Verdict logic per expected field:
1. Extraction confidence below the threshold → `uncertain` ("could not read reliably"), whatever the rules say.
2. Value null and the page is readable (text layer, or average OCR confidence ≥ 0.9) → `mismatch` (found: nothing, expected: present). If the page isn't readable → `uncertain`.
3. Otherwise apply the field's rule → `match` or `mismatch`, with found, expected, rule ID and reason.

Verdict confidence: for rules checked in code, it equals the extraction confidence. For LLM-judged rules, it's the lower of the extraction confidence and the LLM's own rating.

Two rules use Gemini, in at most one call together:
- **Consignee**: normalise first (case, punctuation, "Pte Ltd" = "Private Limited"), then compare exactly against the registered name and aliases. Only if RapidFuzz scores 85–99 does Gemini decide whether it's an acceptable variant of the legal name (a match) or a misspelling (a mismatch, because customs needs the exact name). "Acme Electronics Private Limited" is a match. "ACEM Electronics" is a mismatch.
- **Goods description**: is it specific enough for customs? ("Portable laptop computers, 14-inch" passes. "Electronics" fails.)

Example rules file (`rules/acme.yaml`):

```yaml
customer_id: acme
customer_name: ACME Electronics Pte. Ltd.
document_types:
  commercial_invoice:
    expected_fields: [consignee, hs_code, port_of_loading, port_of_discharge,
                      incoterms, goods_description, gross_weight, invoice_number]
rules:
  consignee:         {id: R-CON-1, type: entity_name, registered: "ACME Electronics Pte. Ltd.",
                      aliases: ["ACME Electronics Private Limited"]}
  hs_code:           {id: R-HS-1, type: in_list, compare_digits: 6, allowed: ["847130", "847160", "847330"]}
  port_of_loading:   {id: R-POL-1, type: in_list, allowed: ["Shanghai", "Yantian"]}
  port_of_discharge: {id: R-POD-1, type: equals, value: "Singapore"}
  incoterms:         {id: R-INC-1, type: equals, value: "CIF"}
  gross_weight:      {id: R-GW-1, type: quantity, unit: KG, min_exclusive: 0}
  invoice_number:    {id: R-INV-1, type: pattern, regex: "^INV-\\d{4}-\\d{4,}$",
                      reject: ["TBD", "N/A", "0000"]}
  goods_description: {id: R-DESC-1, type: llm_judgement, criterion: "specific enough for customs classification"}
```

Rule types are a small fixed set (`equals`, `in_list`, `pattern`, `quantity`, `entity_name`, `llm_judgement`), each with its own tested checker. A new customer means a new YAML file, not new code.

### 3.5 Router detail

**Step 1, allowed outcomes (code):**

| Validation result | Allowed |
|---|---|
| Every expected field matches | auto_approve, human_review |
| At least one mismatch, none uncertain | amendment_request, human_review |
| Uncertain fields only | human_review |
| Mismatches and uncertain fields | amendment_request, human_review |

Human review is always allowed, so the model can escalate something that looks odd, like an unrealistic weight. It can never approve.

**Step 2, decision (Gemini):** input is the validation result and the allowed outcomes. Output: outcome, reasoning (2–5 sentences citing field names), cited fields, and an amendment draft if the outcome is amendment_request.

**Step 3, checks (code):**
- Outcome not in the allowed set → overridden to human_review, `decision_source = code_override`, reason logged.
- Draft is missing a mismatched field, or its found or expected value → code appends a line for it. Logged.
- Gemini fails after retries, or the budget is used up → `decision_source = fallback`. The outcome is the safest allowed one (human_review), with reasoning from a template listing each non-matching field.

`decision_source` (`llm`, `code_override`, `fallback`) is stored and shown in the UI.

### 3.6 LLM wrapper

- Google `google-genai` SDK. Two models from config, pinned in Phase 1:
  - primary `GEMINI_MODEL` = `gemini-3.8-flash`;
  - fallback `GEMINI_FALLBACK_MODEL` = `gemini-3.5-flash-lite`. Empty turns the fallback off.
- Structured output: Pydantic model passed as the response schema. The response is checked again on arrival; a schema failure counts as a failed attempt.
- 60 s timeout. 2 retries with exponential backoff on 429, 5xx, timeout or schema failure. No retry on other 4xx.
- **Fallback to Lite:**
  - **When:** the primary fails after its retries, or returns a 429 whose suggested retry delay is longer than the timeout (the daily quota is used up, so waiting won't help). The wrapper then makes the same request to the fallback model, with the same retry rules.
  - **When not:** no fallback on other 4xx errors (a bad request fails on any model), or when the budget is used up.
  - **Budget:** a fallback call counts as one call against the budget.
  - **Same checks:** Lite's answer goes through the same schema, grounding, confidence and guardrails as the primary's. It gets no trust shortcut. It's a weaker reader of messy scans, but a misread shows up as low grounding or low confidence, which makes the field uncertain rather than approved.
  - **Visible:** each `llm_calls` row records the model that answered. The run is flagged `fallback_used`. The UI shows "answered by fallback model" on the affected step.
  - **If both fail:** the behaviour is unchanged. Escalate, or use the Router's template fallback.
- Budget: `llm_calls_used` in state, maximum 6 per document. The wrapper refuses a call over the limit and sets `state.error`.
- Every attempt is logged to `llm_calls`: run, agent, model, attempt, status, input and output tokens, latency, error. Cost = tokens × a configured price table (the free tier costs $0, but we report the paid-equivalent price for the write-up).
- Prompts live in files under `llm/prompts/`, with a version string stored alongside each call.
- Dev and eval only: an optional response cache keyed by model, prompt version and input hash, so reruns don't spend free quota. Off in the app.

Expected calls: 3 for a clean document (extract, the Validator's description and name check, route), 4 in a healthy worst case (plus one re-extraction), cap 6. Fallback calls come out of the same cap.

Eval reporting: every call records which model answered. The eval reports its headline numbers for runs answered only by the primary model, and lists any runs that used the fallback separately. That way the two models' accuracy is never silently mixed.

### 3.7 Crash recovery and duplicates

- Each run's `run_id` is the LangGraph thread ID. The checkpointer saves state after every node.
- On API start-up, runs still marked `processing` are resumed from their last checkpoint, so finished nodes and their LLM calls aren't repeated.
- File hash + customer ID: re-uploading the same file returns the existing run, unless the request asks for a rerun.
- Known gap: a crash inside a node repeats that node's LLM call on resume. It's bounded by the budget. Noted for the write-up.

## 4. Data model (`app.db`)

| Table | Key columns |
|---|---|
| shipments | id, customer_id, customer_name, reference, created_at |
| documents | id, shipment_id, filename, file_hash, mime_type, page_count, has_text_layer, stored_path, doc_type, created_at |
| runs | id, document_id, status (processing, completed), current_step, outcome (auto_approved, human_review, amendment_requested), reasoning, decision_source, override_reason, escalation_reason, amendment_draft, llm_calls, fallback_used, input_tokens, output_tokens, cost_usd, latency_ms, started_at, finished_at; reserved and empty in Part 1: reviewed_by, final_outcome, reviewed_at |
| field_results | id, run_id, field_name, value, source_text, page, box, model_rating, grounding, ocr_reading, format_ok, extraction_confidence, verdict, found, expected, rule_id, verdict_reason, verdict_confidence |
| llm_calls | id, run_id, agent, model, is_fallback, prompt_version, attempt, status, input_tokens, output_tokens, thinking_tokens, latency_ms, error, created_at |

In Part 1, each upload creates one shipment with one document. Part 2 attaches several documents to one shipment, with no schema change.

Query views, the only tables the query layer can see:
- `v_documents`: one row per run, with customer name, document type, filename, outcome, reasoning, counts of matched, mismatched and uncertain fields, whether the fallback model was used, cost, time taken, and timestamps.
- `v_fields`: one row per field result, joined to customer, document and outcome.

## 5. Query layer

1. Gemini gets the question, today's date, the view definitions with a description of each column, the rule that "this week" means since Monday, and 6 example question and SQL pairs. It returns SQL plus one sentence on what the SQL answers.
2. **Gate (code):**
   - One statement only.
   - The connection is opened read-only (SQLite `mode=ro`).
   - An SQLite authorizer allows reading the two views, and their underlying tables only when read through those views. It blocks everything else, including PRAGMAs and `sqlite_master`.
   - A progress handler aborts any query that runs too long.
   - The query is wrapped with `LIMIT 200`.
3. If the SQL fails, the error goes back to Gemini for one retry, then the question is refused with the error shown.
4. **Answer:** a single value is shown as the answer. Several rows are shown as a table. The explanation sentence and the SQL are always shown. No second LLM call rewrites the numbers, so the answer can only be what the database returned.

Sample questions (also used as tests):
- How many documents were flagged for review this week?
- Which documents need an amendment, and why?
- What is the most common mismatched field?
- Show everything with an uncertain HS code.
- What was the average cost per document?
- Which runs were overridden by code?

## 6. API

| Method and path | Purpose |
|---|---|
| `GET /api/health` | Liveness check |
| `GET /api/customers` | Customers from the YAML files |
| `POST /api/runs` (file, customer_id, rerun?) | Start a run in the background; returns `run_id` (202) or the existing run for a duplicate |
| `GET /api/runs` | Recent runs for the list |
| `GET /api/runs/{id}` | Full run: status, current step, fields, validation, decision, metrics |
| `GET /api/runs/{id}/pages/{n}` | Rendered page image, for the evidence highlight |
| `POST /api/query` (question) | Answer, explanation, SQL, columns, rows |

The API has no login, so it only listens on 127.0.0.1. No CORS is configured: in development, Vite forwards `/api` to the backend, and after `npm run build`, FastAPI serves the built UI itself. The browser only ever talks to one origin, and the grader runs one command. It rejects files over 10 MB, over 5 pages, or of any type other than PDF, PNG or JPG. The README states that it's local only.

## 7. UI

One page, three areas. React + Vite, plain CSS, no component library.

1. **Upload and history**: choose a customer and a file, start a run. A list of recent runs, each with an outcome label.
2. **Run view**:
   - Progress steps (prepare → extract → validate → route) while the run is going.
   - Decision card on top: outcome, a one-line reason, decision source, any code override, and counts (e.g. "6 match · 1 mismatch · 1 uncertain").
   - Full reasoning, then the amendment draft (read-only, with a copy button).
   - Field table: field, value, extraction confidence, verdict, found vs expected, rule ID. Clicking a row shows the page image with the evidence box highlighted, plus the OCR reading if it differs.
   - Run metrics: LLM calls, tokens, time, estimated cost.
3. **Ask**: question box, example-question buttons, answer, explanation, SQL and the results table.

Accessibility: verdicts use text and an icon as well as colour; semantic tables; labelled inputs; keyboard focus on rows; the page image has alt text naming the field and page.

## 8. Failure handling

| Failure | Behaviour | Where it shows |
|---|---|---|
| Gemini rate limit or outage | 2 retries with backoff, then the same request to the Lite fallback model (same checks). A 429 for daily quota goes straight to the fallback | Call log shows the model per call; "answered by fallback model" label |
| Primary and fallback both fail | Escalate (or the Router's template fallback) | Outcome human_review, reason on the card, call log |
| Invalid JSON from the model | Counts as a failed attempt, then as above | Call log |
| Validator's Gemini call fails | Only the fields it covers (description, unsettled consignee) become uncertain, reason "judge unavailable"; the run continues to the Router, which then can't approve | Field rows |
| Value not on the page | Grounding `not_found` → uncertain → no auto-approve | Field row: "not found on page" |
| Model "corrects" a value | `near` grounding or value check fails → uncertain, both readings shown | Field row shows both |
| Unreadable scan | Low OCR confidence → low field confidence → uncertain; missing fields become uncertain, not mismatches | Field rows, decision card |
| Router picks a blocked outcome | Code override to human_review | "Overridden by code" label |
| Draft misses a discrepancy | Code appends it | Draft and log |
| Budget or step limit hit | Escalate with the reason | Decision card |
| Crash mid-run | Resume from the last checkpoint on start-up | Run continues; step shown |
| Duplicate upload | Existing run returned | UI opens the existing run |
| Bad query SQL | One retry, then refused with the error | Ask panel |
| Query tries to write or read raw tables | Blocked by the authorizer | Ask panel: "query not allowed" |

## 9. Repo layout

```
GoCometrepo/
  README.md
  backend/
    .env.example            GEMINI_API_KEY, GEMINI_MODEL, CONFIDENCE_THRESHOLD (copy to backend/.env)
    pyproject.toml          pytest and ruff settings
    requirements.txt        pinned runtime versions
    requirements-dev.txt    runtime + pytest, ruff, httpx
    app/
      main.py               FastAPI app, start-up resume
      config.py
      api/                  routes
      graph/                state models, graph build, escalate and persist nodes
      agents/               extractor, validator, router
      ingest/               PDF rendering, text layer, image clean-up, OCR
      trust/                grounding, normalisers, formats, confidence, guardrails
      rules/                YAML loader and the rule-type checkers
      llm/                  client wrapper, prompts/
      store/                schema, views, repository
      query/                text-to-SQL, gate
    rules/acme.yaml
    tests/
  frontend/                 Vite + React
  samples/                  python -m samples.generate (run from the repo root)
    shipments.py            ground truth: V1, V2, error versions E1-E5, E1E4
    render.py               invoice layout (fpdf2); degrade.py scan conditions (Pillow, OpenCV)
    answers.py              answer-file schema (Pydantic); generate.py the one command
    grid/                   28 eval documents (<version>-<condition>.pdf) + .answer.json each
    submission/             01-clean-correct, 02-clean-two-errors, 03-messy-scan + answers
    jpg/                    V1-C1.jpg, the image-upload test, + answer
  eval/                     eval runner, reports/
  docs/                     this document, PRD, write-up, failure log, sample queries
  data/                     git-ignored: app.db, checkpoints.db, uploads/
  .gitattributes            PDFs and images binary, answer files LF (Windows autocrlf safety)
```

Dependencies, pinned in Phase 1 on Python 3.14.2 (exact versions in `backend/requirements.txt`): fastapi, uvicorn, python-multipart, langgraph, langgraph-checkpoint-sqlite, google-genai, pydantic, pymupdf, opencv-python, numpy, rapidocr (with onnxruntime), rapidfuzz, pyyaml, python-dotenv, fpdf2, pillow. Dev: pytest, ruff, httpx.

- `opencv-python`, not `-headless`: rapidocr depends on `opencv-python`, and installing both breaks the shared `cv2` module (see `failure-log.md`).
- RapidOCR's PP-OCRv6 models ship inside the package, so OCR needs no download at first run.

Frontend (exact versions in `frontend/package.json`): react, react-dom, vite, @vitejs/plugin-react. Lint is oxlint, which the current Vite React template ships instead of ESLint.

## 10. Build order and open items

Build order, detailed phase by phase in `implementation-plan.md`. Each step ends with its teaching doc in `Ansh/`, numbered from 02.

1. Setup: clone the repo, create a venv, check that every dependency installs on Python 3.14 (fall back to 3.12 in the venv if not), and pin the Gemini model.
2. Sample generator and answer files (items 13). Done first, so every later step has test data with known answers.
3. Page preparation (item 2).
4. LLM wrapper (item 8).
5. Extractor with grounding and confidence (items 3–4).
6. Rules and Validator (item 5).
7. Router (item 6).
8. Graph, checkpoints and storage (items 7, 9).
9. API (item 11).
10. UI (item 12).
11. Query layer (item 10).
12. Offline eval, then tune the threshold (item 14).
13. README, sample queries, failure log, PRD numbers, write-up, video (items 16–21).

### 10.1 Open items

- **PRD numbers (reminder you asked for, due at step 13).** Decide or replace the five placeholder numbers in the PRD:
  - Remove "40–60 document sets a day".
  - Replace 0.85 with the tuned threshold and state the rule used to set it.
  - Keep 6 calls, but check it against the call log.
  - Switch the eval to the 28-document grid.
  - Rethink the "40% touchless" pass mark: make it the share of error-free documents that the agent clears on its own.
  - Replace the other metric targets with measured values plus a margin.
- **Python 3.14 compatibility: resolved (Phase 1).** Every dependency installs and works on 3.14.2. The spike rendered a PDF page, read it with RapidOCR (line confidences 0.98–1.00) and saved and reloaded a LangGraph SQLite checkpoint. No 3.12 fallback needed.
- **Gemini model: `gemini-3.8-flash`, pinned by Ansh (Phase 1 spike, 2026-09-30).** It's set in `config.py` and `backend/.env.example`.
  - The key can call 44 models. `gemini-2.5-flash` returns 404 "no longer available to new users", and the error names 3.8 Flash as the replacement.
  - One call with a 900×220 image and a 3-field Pydantic schema returned valid JSON with the right value in 3.3 s.
  - Token counts are in `response.usage_metadata`: `prompt_token_count` (input, 1,097 here), `candidates_token_count` (visible output, 44), `thoughts_token_count` (hidden reasoning, 238) and `total_token_count` (1,379). Thinking tokens are billed as output. So cost and the `llm_calls` table must record them separately. Counting only visible output would have under-counted output tokens here by about 6× (44 instead of 282).
  - `response.parsed` returns the Pydantic object directly. The wrapper still validates it again (§3.6).
- **Fallback model: `gemini-3.5-flash-lite` (Ansh's decision, Phase 1 spike).** The same test call returned the correct value in the right shape in 1.4 s, with 1,097 tokens in and 57 out. It used no thinking tokens (`thoughts_token_count` is None). The switching rules are in §3.6. `gemini-3.1-flash-lite` is also callable but wasn't tested.
- **Free-tier limits: still open.** Ansh reads requests per minute and per day for both models in AI Studio. Quotas are counted per model, so the fallback adds its own allowance. Public reports say about 20 a day for 3.8 Flash and about 500 for Lite. Even so, the eval's headline numbers should come from the primary, so the cache, `--score-only` rescoring and resumable runs over several days still apply.
- **Thinking level:** 3.8 Flash reasons before answering by default (238 thinking tokens on a trivial task). Phase 4 should test a lower thinking budget for the extraction and routing calls, to cut cost and latency.
- **Operator override button**: not built in Part 1 (reserved columns only). Revisit if we want the online metric in the demo.
- **Threshold, grounding cut-offs (90% near match, 85–99 consignee band) and the 0.9 readable-page cut-off** are starting values. Tune all of them with the eval, and record the final values and why.
- **One HS code per document** is assumed, and the samples carry one. Invoices with several different HS codes are a known limitation for the write-up.
- **Outcome names differ between §3.5 and §4 (found in Phase 2).** §3.5 (Router) uses `auto_approve`, `human_review`, `amendment_request`. §4 (`runs.outcome`) uses `auto_approved`, `human_review`, `amendment_requested`. The answer files use the §3.5 names, because they score the Router's decision. Settle it in Phase 7 or 8: either use one set everywhere, or map one to the other in exactly one place.
- **Canonical values the answer files commit to (Phase 2).** Later normalisers must produce these forms:
  - HS code: digits only (`847130`).
  - Weight: amount plus `KG` or `LB` (`{"amount": 862.4, "unit": "KG"}`).
  - Incoterm: the code only (`CIF`, from "CIF Singapore").
  - Ports: the rule file's names. Phase 5's alias table must map the printed forms "SHANGHAI, CHINA", "YANTIAN, CHINA" and "SINGAPORE" to `Shanghai`, `Yantian` and `Singapore`.
  - Text fields: the printed text itself, compared after normalising case, spaces and punctuation.
  - E5's missing invoice number is `null`. Expected verdict `mismatch` on a readable page; on a scan the page may count as unreadable (§3.4), which makes it `uncertain`. Both lead to an acceptable outcome, and Phase 12 decides how to score it.
