# Implementation Plan: Trade Document Pipeline (Part 1)

Status: v1.1 · September 2026 · Companion to `design-architecture.md`, which covers what we build and how. This file is the order of work. Both have lived in `GoCometrepo/docs/` since Phase 1. Current phase status is in `progress.md`.

Phases follow the design's build order (§10), numbered 1–13. Each phase lists its goal, tasks, how it's verified, the checklist items it closes (design §1), its teaching doc and any review gate. File paths are relative to the repo; backend code lives under `backend/app/`.

## Working rules for every phase

- **Tests never spend quota.** Unit tests use a fake LLM client. Tests that call Gemini are marked `live` and only run when asked.
- **A phase is done when** its tests pass, lint is clean, its checklist items are ticked in the design doc, its teaching doc is written in `Ansh/`, `00-architecture-overview.md` is updated, and any real failure seen is added to `docs/failure-log.md`.
- **One phase per chat.** Each phase runs in its own new chat and stops when that phase is done (see "Running each phase in a new chat" below). A phase's gate is what you check before opening the next chat.
- **Git:** work on a `part1` branch. I commit at the end of a phase only after you approve it. Merge into `main` at the end.
- **Secrets:** the Gemini key lives only in `backend/.env`, which git ignores. You add it yourself. It never goes into chat, logs or the database.
- **Code style:** type hints and small modules with one job each. Ruff handles lint and formatting on the backend. On the frontend (plain JavaScript), lint comes from the linter the Vite template ships, which is now oxlint (`npm run lint`). Settings come from environment variables, with defaults in one config module.
- **Logging:** every log line carries the run ID, so one run can be traced end to end.

## Running each phase in a new chat

A new chat has no memory of earlier ones. So everything a phase needs lives in files, and every phase ends by writing its status back.

Where the files are:
- Phase 1 reads this plan and the design doc from `Ansh/`, then moves both to `GoCometrepo/docs/`.
- From Phase 2 on, both are read from `GoCometrepo/docs/`.
- Teaching docs, the PRD draft (until Phase 13) and the reference copies of the brief and JD stay in `Ansh/`.

Kickoff message to paste at the start of each chat, with N changed:

> Do Phase N of `GoCometrepo/docs/implementation-plan.md`, and nothing beyond it. First read the plan's working rules and Phase N, the design sections Phase N lists under "Read first", and `docs/progress.md`. Tell me if anything in them conflicts before you start.

For Phase 1, point it at `Ansh/implementation-plan.md` instead, since the repo doesn't exist yet.

Handoff at the end of each phase: update `docs/progress.md` with:
- the phase's status and date;
- what was built, with the main files and commands;
- decisions made and any deviation from the design, with the design doc updated to match;
- known issues and open reminders (the PRD numbers stay listed until Phase 13);
- anything the next phase needs to know.

The steering rule for teaching docs loads in every chat automatically, so it carries over without being repeated.

## Phase 1: Setup and spikes (≈1–1.5 h)

Goal: the repo cloned, every dependency proven to work, and the unknowns in design §10.1 settled before any feature code.

Read first: this plan's working rules and Phase 1; design §9 and §10.1. In this chat, both files are still in `Ansh/`.

Tasks:
1. Clone `thelazyone5526/GoCometrepo` to `d:\dev\_ANSH\GoCometrepo`. Keep whatever it already contains. Create the `part1` branch.
2. Create the folder layout from design §9, plus `.gitignore` (`data/`, `.env`, `.venv/`, `node_modules/`, caches), `.env.example` and `backend/pyproject.toml` for pytest and ruff settings.
3. Create `backend/.venv` with Python 3.14, install the dependencies, and pin exact versions in `requirements.txt` (dev tools in `requirements-dev.txt`).
4. Compatibility spike, using a throwaway script deleted afterwards: import every library, render a PDF page with PyMuPDF, run RapidOCR on one image, and save one LangGraph checkpoint to SQLite. If anything fails on 3.14 (most likely onnxruntime, which RapidOCR needs), I stop and ask you about installing Python 3.12.
5. Gemini spike: you create `backend/.env` with your key. The script lists the available models. We pin the current Flash model and make one structured-output call with a tiny schema and an image. We then record in design §10.1:
   - the model name;
   - where token counts appear in the response;
   - the free-tier limits you see in Google AI Studio (requests per minute and per day).
6. Frontend: scaffold Vite with the React (JavaScript) template, strip the demo content, and set the dev proxy so `/api` goes to `127.0.0.1:8000`. With the proxy, no CORS setup is needed.
7. Add a stub `GET /api/health`, so the proxy can be checked end to end.
8. Move `design-architecture.md` and this plan into `docs/`. Create `docs/failure-log.md` (date, what happened, cause, fix) and `docs/progress.md`, the handoff file described above. The PRD stays in `Ansh/` while you write Section 1.

Verify: the spike passes; `pytest` and `ruff` run clean; `npm run build` and `npm run lint` pass; the Vite page shows the health response through the proxy.

Teaching doc: `02-project-setup.md` (virtual environments, pinned versions, secrets in `.env`, repo layout, the dev proxy).

Gate: you confirm the model name and limits, and that your key works.

## Phase 2: Sample documents and answer files (≈1.5–2 h) · item 13

Goal: test documents with known correct answers, before any agent exists.

Read first: design §1.2 (item 13) and §3.4; PRD §6 (offline eval).

Tasks:
1. Write ACME's rules to `backend/rules/acme.yaml` (design §3.4). This is only the file for now; the loader comes in Phase 6.
2. Define the ground truth in `samples/shipments.py`:
   - Two correct invoices. V1: Shanghai, HS 8471.30, laptops. V2: Yantian, HS 8471.60, keyboards and mice.
   - Five error versions of V1, each with one planted error:
     - E1: HS code not on ACME's list (8504.40)
     - E2: consignee misspelled as "ACEM Electronics Pte. Ltd."
     - E3: Incoterm FOB
     - E4: gross weight in LBS
     - E5: invoice number missing
   - Realistic distractors on every invoice, so the Extractor has to pick the right value: a shipper name, a notify party, net weight next to gross weight, vessel and container numbers.
3. Invoice renderer (fpdf2): a plausible commercial invoice layout with a real text layer.
4. Degradations (Pillow, OpenCV), with a fixed random seed so the files regenerate identically:
   - C0 clean: the text PDF.
   - C1 skewed scan: rotated 2–4°.
   - C2 blurred scan.
   - C3 noisy, low-resolution scan with a "RECEIVED" stamp partly covering a field.
   - Scans are saved as image-only PDFs (no text layer), plus one JPG to test the image-upload path.
   - 7 versions × 4 conditions gives the 28-document eval grid.
5. One answer file per document, holding:
   - the expected normalised value for each field;
   - the expected verdict for each field;
   - the planted error and condition;
   - the acceptable outcomes. Correct documents: auto-approve when clean; auto-approve or human review when degraded. Error documents: amendment request or human review, never auto-approve.
6. Three submission samples in `samples/submission/`, one for each outcome:
   - clean and correct (V1-C0), which should be auto-approved;
   - clean with two planted errors, which should produce an amendment draft;
   - a messy scan (V2-C3), which shows how uncertainty is handled.
7. One command regenerates everything: `python -m samples.generate`. The generated files are committed, since the grader needs the samples.

Verify:
- I render the three submission samples and look at them.
- PyMuPDF confirms that only C0 files have a text layer.
- The answer files pass their schema.
- A test checks that each error version differs from V1 in exactly one field.

Teaching doc: `03-sample-document-generator.md`.

Gate: you look at the clean and messy samples and say whether they look realistic.

## Phase 3: Page preparation (≈1.5–2 h) · item 2

Goal: every upload, whatever its type, becomes page images plus a list of text spans with positions and confidence.

Read first: design §3.2 (`pages`), §3.3 step 1 and §6 (upload limits).

Tasks:
1. `ingest/files.py`:
   - file hash;
   - file type detected from the file's first bytes, not its extension;
   - limits: 10 MB, 5 pages, PDF, PNG or JPG only;
   - storage under `data/uploads/<hash>/`.
2. `ingest/render.py`: renders PDF pages to PNG at 200 DPI, and loads a PNG or JPG as a single page.
3. `ingest/textlayer.py`:
   - a PDF page counts as digital if it has at least 30 non-space characters of text;
   - its words are grouped into lines, and their boxes converted from PDF points to pixels of the 200-DPI image;
   - confidence is 1.0.
4. `ingest/cleanup.py`: for scans, converts to grayscale, straightens, removes noise and raises contrast (OpenCV).
5. `ingest/ocr.py`: runs RapidOCR on the cleaned image and returns line spans with box and confidence. The engine is created once and reused.
6. Page quality stats: average OCR confidence and the share of low-confidence spans.

Design refinement: OCR returns lines of text, not single words, so the shared unit is a text span (one line). Boxes always refer to the stored page image (the cleaned image for scans), so the UI highlights line up. PDFs that mix digital and scanned pages are handled page by page.

Verify, with tests on the generated samples:
- C0 spans contain the consignee name exactly, at confidence 1.0.
- C1–C3 OCR spans contain the invoice number.
- Straightening brings C1 pages within ±0.5° of level.
- A wrong file type, an oversize file and too many pages are all rejected.
- Time per page is recorded, for the write-up's latency section.

Teaching doc: `04-page-preparation-and-ocr.md`.

## Phase 4: LLM client (≈1 h) · item 8

Goal: one wrapper that every Gemini call goes through, with retries, a budget and a call log.

Read first: design §3.6 and §8.

Tasks:
1. `llm/client.py`: one method that takes the agent name, prompt name, inputs (text and images) and a Pydantic output schema, and returns the validated object.
   - Temperature 0 and a 60 s timeout.
   - 2 retries with backoff on 429, 5xx or timeout, using the API's suggested retry delay when it gives one.
   - A schema failure counts as a failed attempt.
2. Fallback to `GEMINI_FALLBACK_MODEL` (`gemini-3.5-flash-lite`), following the rules in design §3.6:
   - it's used once the primary's retries are used up, or straight away on a 429 for daily quota;
   - never on other 4xx errors;
   - the fallback call counts against the budget;
   - its answer gets the same validation as the primary's;
   - the returned object says which model answered.
3. A per-run budget counter capped at 6 calls, with clear "budget exceeded" and "LLM unavailable" errors. "Unavailable" means both models failed. The nodes turn these into `state.error` or field-level uncertainty.
4. A call recorder that logs every attempt: agent, model, whether it was the fallback, prompt version, attempt number, status, input, output and thinking tokens, latency and error. It writes to memory until the database exists in Phase 8.
5. Prompt files in `llm/prompts/`, each with a version line.
6. A fake client for tests. It returns canned responses per prompt and model, and can simulate 429s (short and daily-quota), timeouts and bad JSON.
7. For dev and eval only: an optional response cache keyed by model, prompt version and input hash, so reruns don't use free quota. It's off in the app.

Verify, with the fake client:
- a retry followed by success;
- primary retries used up leads to one fallback call, which succeeds and is marked as the fallback;
- a daily-quota 429 skips the primary's remaining retries and goes straight to the fallback;
- a 400 error doesn't fall back;
- both models failing gives "unavailable";
- the fallback is off when `GEMINI_FALLBACK_MODEL` is empty;
- bad JSON counts as an attempt;
- a 7th call is refused, including when fallback calls used up the budget;
- every attempt is recorded with its model.

One `live` test makes a real call to each model.

Teaching doc: `05-llm-client-retries-and-budget.md` (it also covers the fallback model).

## Phase 5: Extractor agent (≈2.5–3 h) · items 3, 4

Goal: all 8 fields, each with evidence and a computed confidence (design §3.3).

Read first: design §3.2, §3.3 and §8.

Tasks:
1. Output schema for Gemini, kept flat and simple:
   - document type: commercial invoice, bill of lading, packing list, certificate of origin, or other;
   - for each field: value or null, the exact source text, the page and a self-rating.
2. Prompt `extract_v1`: defines each field and spells out the traps:
   - the consignee is not the shipper or the notify party;
   - gross weight is not net weight;
   - the port of loading is not the port of discharge.

   It tells the model to copy the source text exactly as printed, never correct typos, return null when a field is absent, and never infer.
3. `trust/normalise.py`, with one normaliser per field:
   - HS code: digits only.
   - Weight: number plus unit (KG, KGS or KILOGRAMS; LB or LBS).
   - Incoterm: the code, with any place split off ("CIF Singapore").
   - Ports: matched through a small alias table.
   - Text: case, spaces and punctuation normalised.
4. `trust/grounding.py`: gives each field a grounding result:
   - exact;
   - near: at least 90% similar using RapidFuzz, storing the page's actual text;
   - not found;
   - absent.

   It searches within one span or across two adjacent spans, on the page Gemini named first and then on all pages. It merges the matched boxes into one.
5. The value check (the value must follow from the source text) and format checks in `trust/formats.py`, and weakest-signal confidence in `trust/confidence.py`.
6. One targeted retry at 300 DPI for fields that are `not_found` or `near`, if the budget allows. Keep whichever reading grounds better.
7. The `extract` node runs all of the above. On LLM errors, it sets `state.error`.

Verify:
- Unit tests for every normaliser.
- Grounding tests: an invented value gives not found; "ACME" read from a page printing "ACEM" gives near, with both readings shown; "12,450.00 KGS" gives 12450 KG.
- Confidence tests.
- A `live` smoke run on the three submission samples, compared with their answer files. Any misses go into the failure log.

Teaching doc: `06-extractor-agent-and-grounding.md`.

## Phase 6: Rules and Validator agent (≈2 h) · item 5

Goal: a verdict for every field against ACME's rules (design §3.4).

Read first: design §3.4 and §8.

Tasks:
1. `rules/loader.py`: converts the YAML to a Pydantic rule set. An unknown rule type or a missing field fails loudly when the file loads, not in the middle of a run.
2. `rules/checkers.py`: one checker per rule type (equals, in_list, pattern, quantity, entity_name, llm_judgement). Each returns the verdict, found, expected, rule ID and reason.
3. Company names:
   - Normalise suffixes and punctuation first: "Pte. Ltd." = "Private Limited", "Ltd" = "Limited", "Co." = "Company".
   - Then compare with the registered name and aliases.
   - An exact match is a match. A similarity score of 85–99 goes to Gemini to decide. Below 85 is a mismatch.
4. A weight in LBS is a mismatch, with "expected KG". Converting it would hide a document the customer's rules reject.
5. The `validate` node, which follows the verdict order from design §3.4:
   - low confidence is uncertain, whatever the rules say;
   - a field missing from a readable page is a mismatch;
   - a field missing from an unreadable page is uncertain;
   - otherwise the rule decides.

   One batched Gemini call covers the goods description and any unsettled names.
6. If that Gemini call fails, only those fields become uncertain ("judge unavailable") and the run continues. The Router then can't approve.

Verify:
- One test per rule type and per planted error.
- Fields a document type doesn't expect get `not_applicable` (tested with a bill-of-lading rule set).
- No field below the threshold is ever a match.

Teaching doc: `07-rules-and-validator-agent.md`.

## Phase 7: Router agent (≈1.5 h) · item 6

Goal: a decision with reasoning, inside code limits (design §3.5).

Read first: design §3.5 and §8.

Tasks:
1. `trust/guardrails.py`:
   - works out the allowed outcomes from the verdicts;
   - checks the model's decision and overrides it when needed;
   - checks the draft is complete: each mismatch's field, found value and expected value must appear, or a standard line is appended;
   - provides a template fallback when Gemini is unavailable.
2. Prompt `route_v1`: takes the validation result and the allowed outcomes. It returns the outcome, 2–5 sentences of reasoning citing fields, and a draft. The draft is addressed to the supplier and lists only mismatches, since uncertain fields are for CG to check, not the supplier.
3. The `route` node, which records `decision_source` (llm, code_override or fallback).

Verify:
- The key test tries every combination of match, mismatch and uncertain across the 8 fields: 6,561 cases. The fake model always tries to auto-approve. It must never succeed unless all 8 fields match.
- A draft missing a discrepancy gets it appended.
- A Gemini failure gives a fallback decision with template reasoning.

Teaching doc: `08-router-agent-and-guardrails.md`.

## Phase 8: Graph, checkpoints and storage (≈2 h) · items 7, 9

Goal: the full pipeline running end to end from the command line, with every run stored.

Read first: design §3.1, §3.2, §3.7 and §4.

Tasks:
1. `store/`:
   - the schema and the two query views (design §4);
   - a connection helper using WAL mode, so the UI can read while a run writes;
   - a repository that handles all reads and writes.
2. `graph/`:
   - the shared state (design §3.2), stored as plain data so checkpoints save cleanly, and validated through Pydantic at each node;
   - the `prepare`, `persist` and `escalate` nodes;
   - the graph, with the error edge and a recursion limit of 10;
   - the SQLite checkpointer.
3. Each node updates `runs.current_step` when it starts, so the UI can show progress. The call recorder now writes to `llm_calls`.
4. A duplicate check on file hash plus customer ID.
5. `resume_incomplete_runs()`: finds runs still marked `processing` and continues each from its last checkpoint.
6. A CLI for debugging, also used by the eval: `python -m app.cli run <file> --customer acme` and `python -m app.cli resume`.

Verify:
- The CLI on the three submission samples fills every table, and the views return the right counts.
- Crash test: a debug setting stops the run right after extraction. After `resume`, `llm_calls` shows extraction was called only once.
- A duplicate upload returns the same run.
- With the fake client, hitting the budget escalates the run with the reason.

Teaching doc: `09-langgraph-pipeline-and-storage.md`.

## Phase 9: API (≈1 h) · item 11

Goal: the endpoints in design §6.

Read first: design §3.7 and §6.

Tasks:
1. Routes for health, customers, runs (create, list, detail, page image) and a query stub.
2. Runs execute in the background using FastAPI background tasks, with one database connection per thread.
3. The upload checks from Phase 3, returning clear errors: 413 for too large, 415 for the wrong type, 422 for too many pages.
4. A start-up hook that calls `resume_incomplete_runs()`.
5. The server listens on 127.0.0.1 only. When `frontend/dist` exists, FastAPI serves it, so after one build the grader runs a single command.

Verify, using FastAPI's test client and the fake model:
- Upload returns 202, and polling reaches completed.
- Each error code comes back correctly.
- A duplicate upload returns the existing run.
- The page image endpoint returns a PNG.
- The auto-generated docs at `/docs` list every endpoint.

Teaching doc: `10-api-layer.md`.

## Phase 10: Operator UI (≈3 h) · item 12

Goal: the screen from design §7, showing real runs.

Read first: design §6 and §7.

Tasks:
1. `api.js`: one function per endpoint, so components never build URLs themselves. Response shapes follow the API's auto-generated docs at `/docs`.
2. An upload panel (customer picker, file, start button) and a list of recent runs.
3. The run view:
   - progress steps, polling every second and stopping when the run is done;
   - the decision card first: outcome, one-line reason, decision source, override label and counts;
   - the reasoning;
   - the draft, with a copy button;
   - the field table: both confidences, verdict, found vs expected, and rule ID;
   - the evidence viewer: the page image with the box drawn over it, plus the OCR reading when it differs;
   - run metrics.
4. Clear messages for loading, an unreachable API and failed runs.
5. Accessibility, from design §7: text and icons alongside colour, real table markup, labelled inputs, rows reachable by keyboard, and alt text on page images.

Verify: `npm run build` and `npm run lint` finish with no errors. I start both servers and check all three samples in the browser against this list:
- the decision comes first;
- both confidences are visible;
- clicking a flagged row highlights the right spot;
- the override label appears when an override is forced;
- the whole flow works by keyboard.

Teaching doc: `11-operator-ui.md`.

Gate: you review the screen on all three samples. This is essentially the demo, so it's the most important review.

## Phase 11: Plain-English query layer (≈1.5 h) · item 10

Goal: questions answered from the database, with the SQL shown (design §5).

Read first: design §4 (the views) and §5.

Tasks:
1. `query/schema_doc.py`: the two views, with a plain description of every column.
2. Prompt `query_v1`: takes the question, today's date, the rule that "this week" means since Monday, the view descriptions and 6 example question-and-SQL pairs. It returns the SQL and one sentence on what it answers.
3. `query/gate.py`, with these safety checks:
   - a read-only connection (`mode=ro`);
   - one statement only (Python's SQLite driver already refuses more);
   - an authorizer that allows reading the two views, and the tables beneath them only through those views. It denies everything else, including PRAGMA, ATTACH and `sqlite_master`;
   - a progress handler that stops slow queries;
   - `LIMIT 200` wrapped around the query.
4. One retry with the SQL error fed back to Gemini, then a clear refusal.
5. The answer is shaped without a second LLM call. A single value becomes the answer, and several rows become a table. The explanation and the SQL are always shown.
6. The Ask panel is wired up in the UI.

Verify:
- The 6 sample questions match hand-written SQL on a test database with fixed dates.
- Security tests refuse all of these: DELETE, DROP, UPDATE, PRAGMA, ATTACH, reading `runs` directly, reading `sqlite_master`, and two statements at once.
- A slow query is stopped.

Teaching doc: `12-plain-english-query-layer.md`.

## Phase 12: Offline eval and tuning (≈2 h) · item 14

Goal: real numbers for the PRD and write-up, and a threshold set from data.

Read first: design §1.2 (item 14) and §10.1; PRD §6 and §7.

Tasks:
1. `eval/run_eval.py`: runs all 28 documents through the pipeline, throttled to the free-tier rate. It skips documents already done, so a stopped run can resume. `--score-only` rescores saved results without making any calls.
2. `eval/score.py` reports:
   - field accuracy by field and condition;
   - invented values on absent fields;
   - verdict accuracy;
   - whether each outcome was acceptable;
   - wrong auto-approvals, where any single one makes the command fail;
   - calls, tokens, cost and time per document (p50 and p95).
3. The report is written as Markdown and JSON to `eval/reports/`.
4. A threshold sweep from the saved results, with no new calls. For each threshold from 0.50 to 0.95, count wrong approvals and documents cleared with no human touch. Pick the lowest threshold with zero wrong approvals, add 0.05, and write it to config with the reasoning. The same data is used to sanity-check the 90% grounding cut-off and the 85–99 name range.
5. Rerun the eval at the chosen threshold.

Quota note: a full run is about 100 Gemini calls (28 documents × about 3.5 each). Run it once per major change, and use the cache and score-only in between.

Verify: the report exists, score-only reproduces the same numbers, and there are zero wrong auto-approvals. If not, that goes in the failure log and gets fixed before moving on.

Teaching doc: `13-offline-evaluation.md`.

## Phase 13: Submission package (≈4 h plus the video) · items 16–21

Goal: everything the brief asks to be submitted, checked against the brief itself.

Read first: design §1.3 and §10.1; the PRD; the submission and evaluation sections of `Ansh/reference-assignment-full-text.md`.

Tasks:
1. **Failure log:** pick the three worst real failures, with their cause and fix.
2. **Sample queries:** a script runs the questions against the eval database and writes each question, its SQL and its result to `docs/sample-queries.md`. That way the file shows real output.
3. **README:** what it is, requirements, setup, how to run it, trying the samples, tests, the eval, the repo layout, and a note that it runs locally with no login. Tested by following it word for word on a fresh clone in a new folder.
4. **PRD numbers (your reminder):** we go through the five placeholder numbers using the real eval data (design §10.1). You write Section 1. Then it's exported to PDF.
5. **Technical write-up**, exported to PDF:
   - the architecture diagram;
   - the three worst failures;
   - observability for 50 customers;
   - cost per document, from `llm_calls` at paid prices;
   - latency, from the step timings;
   - what would change with a week instead of a day.
6. **Demo video script (2–3 minutes):** upload the messy scan, show the progress and the decision card, click an uncertain field and show its evidence on the page, show the reasoning, ask "how many were flagged this week?" and show the SQL. You record it.
7. **Final pass:**
   - every checklist item ticked, with its evidence;
   - check that no key or `.env` file is committed;
   - merge `part1` into `main` with your approval, and tag it `part1-submission`.

Teaching docs: none new. `00-architecture-overview.md` gets its final update.

## Time and cut line

These estimates are elapsed time, including your review and learning. I write most of the code, so the real limits are review time and debugging against the live API.

| Phases | Estimate |
|---|---|
| 1–4: setup, samples, page preparation, LLM client | 5–6.5 h |
| 5–7: the three agents | 6–6.5 h |
| 8–11: pipeline, API, UI, queries | 7.5 h |
| 12–13: eval and submission | 6 h, plus the video |
| Total | about 25 h |

That's above the brief's 8–16 hour guide. Most of the extra is trust and eval work, which the AI craft score (20%) rewards. If time gets tight, cut in this order. None of these breaks the required behaviours A–E:
1. The evidence box on the page image. Show the snippet text instead. Saves about 1 h.
2. The 300-DPI targeted retry. About 0.5 h.
3. The recent runs list in the UI. About 0.5 h.
4. Automatic resume at start-up. Keep the CLI `resume`, and the crash test still proves recovery. About 0.5 h.
5. Halve the eval grid to 14 documents (clean plus one degraded condition). About 0.5 h, and half the quota.

Never cut: grounding, the guardrails, visible uncertainty, the query gate, the samples or the README.

## Risks

| Risk | Mitigation |
|---|---|
| A library has no Python 3.14 build (most likely onnxruntime) | Found in Phase 1. Fall back to a 3.12 venv, which needs your OK for a system install |
| Free-tier limits hit during the eval | Throttling, resumable runs, the cache, score-only and a smaller grid |
| Gemini rejects a complex output schema | Keep schemas flat. Fall back to plain JSON mode plus Pydantic validation |
| OCR fails on the heaviest degradation | Safe by design, since it goes to review. Degrade realistically. A real finding for the write-up |
| Correct values fail grounding because of OCR noise | Lowers the touchless rate, not safety. Tune the near cut-off with the eval |
| Invoices with several different HS codes | Out of scope. The samples use one HS code. Noted as a limitation |
| SQLite contention between a run and the UI | WAL mode, one connection per thread, short transactions |
| Library API changes | Exact pinned versions, and spikes in Phase 1 |
| Time overrun | The cut line above |

## Decisions settled

1. **Pace:** one phase per chat, with the kickoff message and handoff file above.
2. **Frontend language:** plain JavaScript.
3. **Git:** a `part1` branch, one commit per phase after your approval, merged into `main` at the end.
4. **Samples:** three submission samples (clean and correct, clean with errors, messy scan).
