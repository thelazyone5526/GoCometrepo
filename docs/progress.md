# Progress and Handoff

Read this at the start of every phase chat. Update it at the end of every phase.

Current: **Phases 3-6 done**, and Phases 7-9 are being built in the same chat under an explicit, time-boxed exception: the user has a hard deadline and asked to skip the "one phase per chat" and per-phase approval-before-committing rules for this stretch, accepting a reduced scope (see "Scope cuts for this session" below). Two more chats are running in parallel on independent pieces (frontend UI against mock data; Phase 11 query layer against a fixture database) — neither has touched `backend/app/graph`, `backend/app/store`, `backend/app/agents`, `backend/app/rules`, or `frontend/`+`app/query/` respectively, so there should be no file conflicts, but their output hasn't been reconciled with the real backend yet. Next: Phase 7 (Router agent).

### Scope cuts for this session (user-approved, deadline-driven)

- No stop-and-approve gate between phases 6-9; commits are batched and shown to the user at the end instead of one at a time.
- Phase 10 (UI) will be bare-bones: upload panel, run list, progress steps, decision card, field table only. No evidence-box page-image overlay, no metrics panel, no keyboard-nav polish pass.
- Phase 11 (query layer) will be minimal: the 6 sample questions and the core SQL safety gate, without the full authorizer hardening test suite the plan calls for.
- Phase 12's full 28-document eval grid is deferred. Instead: a smoke run of the 3 submission samples through the real API (roughly 10-12 Gemini calls), no threshold tuning from data. The 0.85 default threshold stays as-is.
- Phase 13 (README, sample-queries script, PRD numbers, write-up, video) is deferred beyond this session except for anything cheap to draft (README, sample-queries script). PRD Section 1, the write-up's final numbers, and the demo video need the user directly regardless of how fast the agent works.
- These cuts are meant to be reversible: the plan's original scope can still be filled in later (more eval documents, UI polish, full query-gate test suite) once the deadline pressure is off.

## Phase 1: Setup and spikes

Status: **done, 2026-09-30.** Committed on `part1` as `18060d2` and pushed to GitHub (`origin/part1`, tracking set up). One gate item is still open: the free-tier limits, which Ansh reads in AI Studio.

### What was built

- Repo cloned to `d:\dev\_ANSH\GoCometrepo`. It was empty (no commits). Work is on branch `part1`, which has no commits yet either.
- Folder layout from design §9: `backend/app/` with one package per area (`api`, `graph`, `agents`, `ingest`, `trust`, `rules`, `llm/prompts`, `store`, `query`), `backend/rules/`, `backend/tests/`, `frontend/`, `samples/`, `eval/reports/`, `docs/`. `data/` is created at runtime and git-ignored.
- `.gitignore` (data, `.env`, venv, node_modules, caches, `frontend/dist`).
- `backend/.env.example`, `backend/pyproject.toml` (pytest and ruff settings; `live` marker skipped by default), `backend/requirements.txt` and `requirements-dev.txt` with exact pins.
- `backend/app/config.py`: all settings from environment variables, defaults in one place, `backend/.env` loaded first.
- `backend/app/main.py`: FastAPI app with the stub `GET /api/health`, run on 127.0.0.1:8000 only. Test in `backend/tests/test_health.py`.
- `frontend/`: Vite + React (JavaScript) template, demo content removed, exact version pins, dev proxy `/api` → `http://127.0.0.1:8000`. The page shows the health result.
- `backend/.env` (git-ignored, confirmed with `git check-ignore`) holds Ansh's key, `GEMINI_MODEL=gemini-3.8-flash` and `GEMINI_FALLBACK_MODEL=gemini-3.5-flash-lite`.
- Three throwaway spike scripts (compatibility, Gemini, Lite), all deleted after they passed.
- `docs/`: this file, `failure-log.md`, and the design and plan (moved from `Ansh/`).

### Commands (from the repo root, Windows PowerShell)

```
# Backend setup (once)
cd backend
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt

# Run the API (127.0.0.1:8000)
.venv\Scripts\python -m app.main

# Tests and lint
.venv\Scripts\python -m pytest            # skips `live` tests
.venv\Scripts\python -m pytest -m live    # only the tests that call Gemini
.venv\Scripts\ruff check .
.venv\Scripts\ruff format --check .

# Frontend (use npm.cmd if PowerShell blocks npm.ps1)
cd frontend
npm install
npm run dev      # http://localhost:5173, proxies /api to the backend
npm run build
npm run lint     # oxlint
```

### Verified

- Compatibility spike on Python 3.14.2, all passed: every library imports; fpdf2 made a text PDF; PyMuPDF read its 10 words and rendered it at 200 DPI (1654×2339 px, 0.06 s); RapidOCR read all 3 lines of the render with confidences 0.98–1.00 (2.9 s including engine start-up); LangGraph saved a checkpoint to SQLite and a fresh saver reloaded it. The spike script was deleted.
- `pytest`: 1 passed. `ruff check` and `ruff format --check`: clean.
- `npm run build` and `npm run lint`: exit 0.
- Through the proxy, `GET http://localhost:5173/api/health` returns 200 `{"status":"ok"}`. A headless Edge render of the page shows "Backend status: ok".
- `pip install -r requirements-dev.txt --dry-run` finds nothing to install, so the pins match the working venv.
- Fallback spike: `gemini-3.5-flash-lite` passed the same test call (1.4 s, correct value, valid schema).
- Gemini spike: the key works and lists 44 models. `gemini-2.5-flash` is refused with 404 ("no longer available to new users"; failure log #5). With `gemini-3.8-flash`, one call with an image and a Pydantic response schema returned valid JSON with the correct invoice number in 3.3 s. Tokens: 1,097 in, 44 out, 238 thinking. Details in design §10.1.

### Decisions and deviations (design and plan updated to match)

- **Python 3.14 kept.** Nothing needed the 3.12 fallback.
- **`opencv-python` instead of `opencv-python-headless`.** rapidocr needs it, and both together break `cv2`. See failure log #2.
- **`.env.example` lives in `backend/`**, next to where `backend/.env` goes, not at the repo root.
- **Frontend lint is oxlint**, which the Vite template now ships in place of ESLint.
- **Pins:** direct dependencies are pinned exactly in `requirements.txt`. Transitive ones aren't locked. The frontend is fully locked by `package-lock.json`.
- **RapidOCR models are bundled** (PP-OCRv6 small), so there's no model download at first run.
- **Health endpoint** returns only `{"status": "ok"}`.

- **Model: `gemini-3.8-flash`**, Ansh's choice. `gemini-2.5-flash` isn't available to new users. It's the default in `config.py` and `.env.example`.
- **Fallback: `gemini-3.5-flash-lite`**, used only when the primary fails after its retries or its daily quota is used up. Its answers get the same checks, and it's logged and labelled wherever it answered.

### Gate status

- Key works: yes.
- Model confirmed: yes (`gemini-3.8-flash`).
- Fallback confirmed: **`gemini-3.5-flash-lite`** (Ansh's decision). A test call returned the correct value in the right shape in 1.4 s, with 1,097 tokens in, 57 out and no thinking tokens. It's in `config.py` (`gemini_fallback_model`), `.env.example` and `.env`. Leave `GEMINI_FALLBACK_MODEL` empty to turn it off. The switching rules are in design §3.6 and get built in Phase 4.
- Free-tier limits: **confirmed, 2026-09-30 (this session, mid-Phase 6-9 work).** Read by Ansh in AI Studio for the primary model (`gemini-3.8-flash`): **10 requests/minute, 250 requests/day.** (Earlier note in this file said "about 20 a day" from public reports; that was an estimate, now replaced with the real measured value.) The fallback model's own limits weren't re-checked this session; design §3.6's fallback logic doesn't depend on knowing them exactly. At 10/min, a single document's 3-4 sequential agent calls never come close to the per-minute ceiling; the per-day ceiling (250) comfortably covers the 3-document submission-sample smoke run (~10-12 calls) and would also cover the full 28-document eval grid (design section 12 estimates ~100 calls) in one calendar day if that's reinstated later. The eval's headline numbers still come from the primary, with fallback runs reported separately.

### Known issues

- The Kiro shell in this workspace sometimes drops command output, or runs a command late. Workaround: write the output to a file under `d:\dev\_ANSH\.tools\` and read it back. This doesn't affect the project.

### Open reminders

- **Rotate the Gemini key.** It was pasted into chat, so it's in the chat history. Delete it in AI Studio, create a new one, and put the new one straight into `backend/.env`.

- **Git identity:** set for this repo only, at Ansh's request: `user.name` = `thelazyone5526`, `user.email` = Ansh's GitHub no-reply address (keeps his real email out of the public history). Ready to commit.
- **Branches:** `part1` holds the Phase 1 commit. `main` still has no commits, so merging at the end just moves `main` up to `part1`. `part1` is pushed to GitHub, and pushes need Ansh's go-ahead. GitHub sign-in is saved by Git Credential Manager, so later pushes won't ask again.
- **PRD numbers (due in Phase 13):** decide or replace the five placeholder numbers in the PRD (design §10.1): remove "40–60 document sets a day"; replace 0.85 with the tuned threshold and its rule; check the 6-call cap against the call log; switch the eval to the 28-document grid; rethink the "40% touchless" pass mark; replace the other targets with measured values plus a margin.

### For Phase 2

- Use `backend\.venv\Scripts\python` for everything. `samples/` sits at the repo root, and the plan runs the generator as `python -m samples.generate` from the root. That needs `samples/__init__.py` and the backend venv's Python.
- `backend/rules/` is empty (`.gitkeep`). Phase 2 writes `acme.yaml` there.
- fpdf2 2.8.9: use `new_x`/`new_y` in `cell()`. The old `ln=` argument is deprecated.

### For Phase 4 (LLM client), learned in the spike

- Structured output works with `types.GenerateContentConfig(temperature=0, response_mime_type="application/json", response_schema=<Pydantic model>, http_options=types.HttpOptions(timeout=60_000))`. `response.parsed` is already the Pydantic object.
- Tokens are in `response.usage_metadata`: `prompt_token_count`, `candidates_token_count`, `thoughts_token_count`, `total_token_count`. Store thinking tokens and bill them as output.
- 3.8 Flash thinks by default. Test a lower thinking budget via `thinking_config` to cut cost and latency.
- The SDK prints an "automatic function calling" warning on every call. It's harmless, since we pass no tools. It can be silenced by disabling AFC in the config.
- The SDK retries on its own (it uses tenacity internally). Our wrapper also retries, so turn the SDK's retries off to avoid retrying twice.

## Phase 2: Sample documents and answer files

Status: **done, 2026-09-30.** Gate passed: Ansh reviewed the clean and messy submission samples and approved them. Committed on `part1` as "Phase 2: sample documents and answer files" (together with Phase 1's leftover `progress.md` edit) and pushed to `origin/part1`. Run `git log` for the commit hash.

### What was built

- `backend/rules/acme.yaml`: ACME's rules, copied from design §3.4. Data only for now; the loader comes in Phase 6.
- `samples/` (a package at the repo root):
  - `shipments.py`: the ground truth.
    - V1: Shanghai, HS 8471.30, laptops.
    - V2: Yantian, HS 8471.60, keyboard and mouse sets.
    - E1–E5: V1 with one planted error each (HS 8504.40; "ACEM Electronics Pte. Ltd."; FOB Shanghai; weights in LBS; blank invoice number).
    - E1E4: V1 with two errors, used only for submission sample 2.
    - Distractors on every invoice: shipper, notify party, net weight next to gross weight, vessel, container number (valid ISO 6346 check digit), PO number, final destination, shipping marks.
  - `render.py`: a one-page A4 commercial invoice drawn with fpdf2, with a real text layer. The creation date is fixed, so the bytes repeat. It refuses any value that would overflow its box, so every printed value stays on one line.
  - `degrade.py`: the scan conditions, made with PyMuPDF, OpenCV and Pillow.
    - C1: rotated 2–4°, grayscale, 200 DPI.
    - C2: Gaussian blur, sigma 1.9–2.3 px, grayscale, 200 DPI.
    - C3: colour, a red RECEIVED stamp over the end of the gross weight, then 100 DPI with noise, speckles and JPEG quality 55.

    Each scan is wrapped in an image-only PDF.
  - `answers.py`: the answer-file schema (Pydantic). Its validators enforce the outcome rules: an error document can only list amendment_request or human_review, and a mismatch must line up with a planted error.
  - `generate.py`: the one command.
- Generated and committed:
  - `samples/grid/`: 28 PDFs;
  - `samples/submission/`: `01-clean-correct` (V1-C0), `02-clean-two-errors` (E1E4-C0) and `03-messy-scan` (V2-C3);
  - `samples/jpg/`: `V1-C1.jpg`;
  - one `.answer.json` beside each document.

  That's 32 documents and 6.6 MB in total.
- `.gitattributes` at the repo root marks PDFs and images as binary and answer files as LF, so Windows autocrlf can't corrupt them.
- `backend/tests/test_samples.py` (50 tests).
- `backend/pyproject.toml`: pytest `pythonpath` and ruff `src` now include `..`, so tests import `samples` and ruff treats it as first-party.

### Commands (Phase 2 additions)

```
# From the repo root: regenerate every sample and answer file (about 3 s)
backend\.venv\Scripts\python -m samples.generate
backend\.venv\Scripts\python -m samples.generate --out <dir>   # somewhere else

# From backend/: lint and format now cover samples/ too
.venv\Scripts\ruff check . ..\samples --config pyproject.toml
.venv\Scripts\ruff format --check . ..\samples --config pyproject.toml
```

### Verified

- `pytest`: 51 passed (50 new, plus health) in 2.3 s. The tests check that:
  - every answer file passes its schema;
  - the schema rejects an error document that allows auto_approve;
  - each committed answer equals one rebuilt from `shipments.py`;
  - each of E1–E5 differs from V1 in exactly one field, the planted one, and E1E4 in exactly its two;
  - the expected verdicts agree with `acme.yaml` for every rule code can settle;
  - PyMuPDF finds a text layer (at least 30 characters) only on C0 files, and exactly 0 characters on every scan;
  - every printed value appears exactly in the C0 text layer;
  - E5 keeps the "Invoice No." label but has no invoice number;
  - building a document twice gives identical bytes (one per condition);
  - the JPG is byte-identical to the image inside `grid/V1-C1.pdf`;
  - every file is 1 page and under 10 MB.
- Ruff check and format, over backend and samples (21 files): clean.
- Reproducibility: a fresh `generate()` into a scratch folder matched all 64 committed files byte for byte.
- Visual check: I rendered and inspected the three submission samples. Sample 1 shows the correct V1. Sample 2 shows 8504.40 and weights in LBS. Sample 3 is an image-only scan whose stamp covers ".00 KGS" of the gross weight.
- A throwaway OCR check with RapidOCR on six scans (V1-C1, V1-C2, E1-C2, V1-C3, V2-C3, E4-C3), since deleted:
  - the invoice number was read at confidence 1.0 on all six;
  - mean line confidence was 0.977–0.993, and the lowest single line was 0.574;
  - the stamp changed the gross-weight reading on C3: "3,120.00KGS" on V2-C3, and no "LBS" reading on E4-C3.

### Decisions and deviations (design updated to match)

- **Folder layout:** `samples/grid/`, `samples/submission/`, `samples/jpg/`, each document with `<name>.answer.json` beside it. Doc IDs are `<version>-<condition>`, plus `V1-C1-JPG` for the JPG.
- **Two-error sample = E1 + E4** (HS code, LBS). Both are settled by plain code, so the demo's amendment draft doesn't depend on the LLM's name judgement.
- **JPG = V1-C1**, the same scan as the grid PDF.
- **Stamp placement:** over the end of the gross weight, and never over the invoice number, which Phase 3's OCR test needs.
- **Answer-file contents beyond the plan:** the exact printed text per field (for grounding tests) and the exact degradation parameters (for example the C1 skew angle, for Phase 3's straightening test).
- **Outcome vocabulary:** answer files use §3.5's `auto_approve`, `human_review`, `amendment_request`. §4 uses different past-tense names. Logged as an open item in design §10.1.
- **Canonical values** that later normalisers must produce are listed in design §10.1.
- **E4:** the net weight is also printed in LBS, as a real supplier would. It isn't one of the 8 fields, so E4 still differs from V1 in exactly one field.
- **Every document carries a footer** saying it's a fictional test document.
- Design doc: item 13 ticked; items 1, 3 and 12 say "three submission samples" instead of "both samples"; §9 layout updated; two §10.1 open items added.

### Known issues

- No real failures this phase, so no failure-log entries. The renderer's overflow guard fired once, as designed, on a box label that was too long. The label was shortened.
- Byte-identical regeneration is verified on this machine. On another OS, OpenCV or zlib builds could produce slightly different scan or PDF bytes. The tests only compare a fresh build with itself, and the answer files as parsed data, so they pass anywhere.

### Open reminders

- Everything in Phase 1's list still stands: rotate the Gemini key, read the free-tier limits, and the PRD numbers in Phase 13. On the PRD: its §6 still says "24 synthetic documents, half with seeded errors". The grid is now 28 documents, 20 of them with errors.

## Phases 3, 4 and 5: page preparation, LLM client, Extractor agent

Status: **done, 2026-09-30.** Built together in one chat, at the user's explicit request, overriding the plan's "one phase per chat" rule for this chat only. Not yet committed as of this writing — commits happen after the user reviews and approves, one commit per phase, in order (Phase 3, then 4, then 5).

### What was built (Phase 3, item 2)

- `backend/app/ingest/`:
  - `files.py`: upload checks (magic-byte type sniffing, 10 MB / 5 page limits), file hashing, content-addressed storage under `data/uploads/<hash>/`.
  - `render.py`: PDF pages and PNG/JPG uploads rendered to RGB images at a fixed DPI (200 by default, 300 for Phase 5's retry). `RenderedPage` carries the page's `pymupdf.Page` reference for digital pages, avoiding a second PDF open.
  - `textlayer.py`: a PDF page counts as digital at ≥30 non-space characters; its lines become `TextSpan`s (text, pixel box, confidence 1.0) via PyMuPDF's own line grouping.
  - `cleanup.py`: scan clean-up (OpenCV) — grayscale, straighten (projection-profile skew search, coarse then fine), denoise, raise contrast (CLAHE).
  - `ocr.py`: a shared, lazily-created RapidOCR engine; `run_ocr()` returns line-level `TextSpan`s from the cleaned image.
  - `pages.py`: `prepare_pages()`, the entry point — dispatches each page to the text-layer or OCR path, and computes page quality stats (average confidence, low-confidence share).
- `backend/tests/test_ingest.py` (45 tests).

### What was built (Phase 4, item 8)

- `backend/app/llm/`:
  - `transport.py`: the `Transport` protocol and its error types (`RetryableTransportError`, `NonRetryableTransportError`), decoupling the client from `google-genai`.
  - `gemini_transport.py`: the real transport. Parses `google.rpc.RetryInfo`'s `retryDelay` off a 429 when present; SDK retries and automatic function calling both disabled.
  - `fake_transport.py`: a scripted transport for tests — queue `RawResponse`s or `TransportError`s per model; never touches the network.
  - `client.py`: `LLMClient.generate()` — the single entry point. Retries (2, with backoff or the API's suggested delay), falls back to a second model once the primary's retries are exhausted or a 429's suggested delay exceeds the timeout, enforces a 6-call budget, records every attempt. A non-retryable 4xx short-circuits straight to `LLMUnavailableError` without ever trying the fallback.
  - `budget.py`, `recorder.py`, `errors.py`: the call budget, the in-memory call log, and the two errors a node ever sees (`BudgetExceededError`, `LLMUnavailableError`).
  - `cache.py`: `CachingTransport`, the dev/eval response cache (item 7), keyed by model, prompt version and input hash. Off in the app; used by the live tests and (later) the eval runner.
  - `prompt_files.py`: loads `llm/prompts/<name>.txt`, each starting with `version: N` then `---`.
  - `llm/prompts/ping_v1.txt`: a minimal prompt used only to exercise the wrapper's own tests.
- `backend/tests/test_llm_client.py` (15 tests, `FakeTransport` only), `test_llm_cache.py` (3), `test_prompt_files.py` (3), `test_llm_client_live.py` (2, `live`-marked, cached under `data/llm_cache/live_test/`).

### What was built (Phase 5, items 3 and 4)

- `backend/app/trust/`:
  - `fields.py`: `FIELD_NAMES` and `DocumentType`, shared by the schema, normalisers and (later) the Validator.
  - `normalise.py`: one normaliser per field. Text fields normalise to the printed text, trimmed; `text_comparison_key()` is the separate case/punctuation-insensitive key used only for *comparing* two text values.
  - `formats.py`: per-field format checks (HS code digit count, the 11 Incoterms 2020 codes, weight has a unit, invoice number non-empty).
  - `grounding.py`: `ground_field()` — searches one or two adjacent spans, on the model's named page first then every page, for the model's claimed source text. Returns `exact` / `near` (≥90% via RapidFuzz) / `not_found` / `absent`, with the merged box and the page's own reading.
  - `value_check.py`: does the model's `value` follow from its `source_text`, per field, using that field's normaliser (or a case/punctuation-insensitive text comparison).
  - `confidence.py`: weakest-signal confidence — the lowest of self-rating, grounding score, and source confidence — capped at 0.3 on a failed value or format check. An absent field's self-rating is not fed in as a limiting signal (see deviations).
- `backend/app/agents/`:
  - `schema.py`: `FieldExtraction`, `ExtractionResult` (all 8 fields required), `RetryExtractionResult` (all optional, for the targeted retry).
  - `extract.py`: `extract()` — one Gemini call, grounding/value/format checks and confidence per field, then one optional targeted retry (only for `not_found`/`near` fields, only if a `retry_pages_at_higher_dpi` callback is given and the budget has room), keeping whichever reading grounds better.
  - `llm/prompts/extract_v1.txt` and `extract_v2.txt` (current): the retry prompt is `extract_retry_v1.txt`.
- `backend/tests/test_trust_normalise.py` (23), `test_trust_grounding.py` (8), `test_trust_value_check.py` (10), `test_trust_confidence.py` (7), `test_trust_formats.py` (14), `test_extract.py` (7, `FakeTransport`), `test_extract_live.py` (3, `live`-marked, cached under `data/llm_cache/extract_smoke/`).

### Commands

```
# From backend/, with the venv active
.venv\Scripts\python -m pytest                 # 182 passed, 2 deselected (live), ~35 s
.venv\Scripts\python -m pytest -m live          # 5 passed (2 LLM-client, 3 extract smoke), real Gemini calls
.venv\Scripts\ruff check . ..\samples --config pyproject.toml     # All checks passed
.venv\Scripts\ruff format --check . ..\samples --config pyproject.toml
```

### Verified

- Full suite: 182 passed, 2 deselected, ~35 s. Ruff check and format: clean.
- Phase 3: skew estimate on V1-C1 (true skew -2.77°) came out -2.75°, within the ±0.5° tolerance. OCR found the invoice number on every scan condition (C1, C2, C3) and on the JPG upload.
- Phase 4: fake-transport tests cover every scenario in the plan's verify list (retry-then-success, primary-exhausted-then-fallback, daily-quota-skips-to-fallback, no-fallback-on-400, both-fail, fallback-disabled, bad-JSON-counts-as-attempt, 7th-call-refused including via fallback spend, every attempt recorded with its model). The `live` test made one real call to each model; both passed, both are now cached.
- Phase 5: unit tests cover an invented value → `not_found`; "ACME" read from a page saying "ACEM" → `near` with both readings; "12,450.00 KGS" → `12450 KG`; the 0.3 cap on a failed check; the targeted retry keeping the better-grounding reading; the retry never firing when the budget has no room. The `live` smoke run against all three submission samples (see below) passed after one prompt fix.

### Decisions and deviations (design doc updated to match)

- **A `Transport` seam** between `LLMClient` and `google-genai` (not specified by §3.6): `GeminiTransport` for real calls, `FakeTransport` for tests, `CachingTransport` wrapping either for the dev cache.
- **Daily-quota classification lives in `LLMClient`, not the transport**: a 429 always raises the same `RetryableTransportError`, carrying the API's suggested delay when given one; the client compares it against its own timeout, since only the client knows what timeout is in effect.
- **Non-retryable failures short-circuit past the fallback**, via an internal `_NonRetryableFailure`, so a 4xx other than 429 can never accidentally trigger a fallback attempt.
- **Grounding's fallback scorer is `fuzz.ratio`, not `fuzz.partial_ratio`** (failure log #6): the latter let an invented value score 100% against an unrelated short span.
- **An absent field's `self_rating` (always 0, per the prompt) is not used as a confidence signal**: it's substituted with 1.0 before weakest-signal confidence runs, so an absent field's confidence is driven only by grounding (`absent`, never a limiting factor), leaving the mismatch-vs-uncertain call entirely to the Validator (Phase 6), as design section 3.4 intends.
- **The 300 DPI retry is injected as a callback** (`retry_pages_at_higher_dpi`), since `extract()` only receives already-`PreparedPage`s and doesn't hold the original upload bytes needed to re-render. `graph.extract_node` (Phase 8) will supply it.
- **Extractor prompt bumped to `extract_v2`** after the live smoke run (failure log #7): the model was reading the whole CONSIGNEE box (name plus every address line) as one `source_text`, which grounding's two-span limit could never match. `extract_v2` tells it explicitly to report only the company name line. `extract_v1.txt` is kept as the pre-fix record.
- Design doc: items 2, 3, 4 and 8 ticked; §10.1 gained entries for the skew algorithm, the low-confidence threshold, the `Transport` seam, daily-quota classification, SDK retry/AFC disabling, the non-retryable short-circuit, the grounding scorer fix, the absent-field self-rating fix, and the retry-callback design.

### Known issues

- Failure log #6 (grounding's fuzzy-match bug, caught by a unit test) and #7 (the consignee multi-line read, caught only by the live smoke run) are both real failures from this session, now in `docs/failure-log.md`.
- The live smoke run's per-sample comparison helper in `test_extract_live.py` is deliberately loose (it reports mismatches rather than asserting), since some conditions (the messy scan) are designed to test uncertainty handling, not perfect reads.

### Open reminders

- Everything from Phases 1 and 2 still stands (key rotation, free-tier limits, PRD numbers).
- Free-tier limits: still open (Phase 1 gate item).

## Phase 6: Rules and Validator agent

Status: **done, 2026-09-30.** Built under the deadline-driven scope cuts above: no per-phase approval gate before this commit; committed together with 7-9 as one batch, pending final user review of the whole batch.

### What was built

- `backend/app/rules/`:
  - `loader.py`: `load_rules()` / `load_rules_for_customer()` — YAML to a strict, typed `RuleSet` (Pydantic, discriminated union on `type`). Fails loudly (`RuleLoadError`) on an unrecognised rule type, a rule missing a required field, or an `expected_fields` entry with no matching rule — all at load time, never mid-run.
  - `checkers.py`: one checker per rule type (`equals`, `in_list`, `pattern`, `quantity`, `entity_name`, `llm_judgement`), dispatched by `check_rule()`. Returns a `CheckOutcome` with verdict `match` / `mismatch` / `needs_judgement`. `entity_name_key()` expands legal-suffix abbreviations ("Pte"→"Private", "Ltd"→"Limited", "Co"→"Company") ahead of the usual case/punctuation-insensitive key, kept separate from `trust.normalise.text_comparison_key` since suffix expansion is specific to company names.
- `backend/app/agents/validate.py`: `validate()` — the entry point. Implements design section 3.4's verdict order (confidence-below-threshold → uncertain; null on a readable page → mismatch, on an unreadable page → uncertain; otherwise the rule decides), collects every `needs_judgement` field into one batched Gemini call (`validate_v1` prompt), and degrades gracefully (only the judged fields become uncertain, reason "judge unavailable") if that call fails or the budget has no room.
- `backend/app/llm/prompts/validate_v1.txt`: the batched judgement prompt, covering both possible judgement types (entity-name variant, goods-description specificity) in one call.
- `backend/tests/test_rules_loader.py` (10 tests), `test_rules_checkers.py` (20, one per rule type plus one per planted error E1-E5), `test_validate.py` (16, covering the full verdict order, the judgement call, judge-unavailable degradation, and schema routing with a bill-of-lading rule set).

### Commands

```
.venv\Scripts\python.exe -m pytest                 # 228 passed, 5 deselected (live)
.venv\Scripts\ruff.exe check . ..\samples --config pyproject.toml     # All checks passed
```

### Verified

- Full suite: 228 passed, 5 deselected, no regressions from Phases 1-5. Ruff clean (also cleaned up two pre-existing lint issues in `test_extract_live.py`, left over from Phase 5, unrelated to this phase's own code).
- One test per rule type against ACME's real rules file, plus one test per planted error (E1-E5).
- The verdict order: a field with confidence just under the threshold is never a match even if the rule would pass it; a null value is a mismatch on a readable page and uncertain on an unreadable one; a document with every field correct matches everywhere.
- The judgement call: a close consignee variant Gemini accepts is a match, one it rejects is a mismatch; a vague goods description is a mismatch. A failed judgement call marks only the pending fields uncertain — verified with a case where only `goods_description` needed judgement (consignee was an exact match, settled by code) and a case where both did.
- Schema routing: a field not in a document type's `expected_fields` gets `not_applicable`, tested with a hand-built bill-of-lading rule set.

### Decisions and deviations (design doc updated to match)

- Entity-name suffix normalisation lives in `rules/checkers.py` (`entity_name_key`), not folded into the shared `trust.normalise.text_comparison_key`, since it's specific to comparing company names, not text fields generally.
- `RuleLoadError` also catches a rule type Pydantic's discriminated union would otherwise reject with a less specific error, and an `expected_fields` entry with no matching rule (checked separately, since Pydantic alone can't express "these two collections must have matching keys").
- `check_rule()`'s three-way outcome (`match` / `mismatch` / `needs_judgement`) keeps every checker a pure function with no LLM dependency; `validate()` is the only place that calls Gemini, once per document at most.
- Design doc: item 5 ticked; §10.1 gained five new entries (suffix normalisation location, `RuleLoadError`'s load-time checks, the `needs_judgement` outcome and batched call, and graceful judgement-failure degradation).

### Known issues

- No new real failures this phase.

### Open reminders

- Everything from Phases 1-5 still stands (key rotation, free-tier limits, PRD numbers).
- The scope-cut list above (this session) replaces the "For Phase N" notes below for Phases 7-13: those phases proceed under the reduced scope, not the plan's original verify lists in full.

## Phase 7: Router agent

Status: **done, 2026-09-30.** Built under the same deadline-driven scope cuts as Phase 6 (no per-phase approval gate; committed together with 6, 8 and 9 as one batch).

### What was built

- `backend/app/trust/guardrails.py`: `allowed_outcomes()` (design section 3.5's table, keyed off match/mismatch/uncertain across every expected field, `not_applicable` fields excluded), `apply_guardrails()` (checks the model's proposed outcome against the allowed list, overrides to `human_review` and logs the reason if it's not allowed, completes an incomplete amendment draft), `complete_amendment_draft()`, and `fallback_decision()` (a code-only, network-free `human_review` decision with template reasoning, used when the AI is unreachable).
- `backend/app/agents/route.py`: `route()` — the entry point. Calls Gemini once (`route_v1` prompt) for a proposed outcome, reasoning, cited fields and (if relevant) a draft, then runs it through the guardrails. Catches `LLMUnavailableError`/`BudgetExceededError` and falls through to `fallback_decision()` -- `route()` never raises.
- `backend/app/llm/prompts/route_v1.txt`: the Router's prompt, given the verdict summary and the allowed outcomes list, told to pick only from that list, cite real field names, and (for amendment_request) draft a supplier-facing message that never mentions merely-uncertain fields.
- `backend/tests/test_guardrails.py` (18 tests, including the key combinatorial sweep: `3**8 = 6561` combinations of match/mismatch/uncertain across all 8 fields, run twice — once against `apply_guardrails` with a model that always tries `auto_approve`, once directly against `allowed_outcomes` — confirming auto-approval never succeeds unless every field matched), `tests/test_route.py` (6, covering approval, override, draft completion, and the two ways `route()` falls back to human review without ever calling Gemini again).

### Commands

```
.venv\Scripts\python.exe -m pytest tests\test_guardrails.py tests\test_route.py -q   # 13140 passed (dominated by the 2x 6561-case sweep)
.venv\Scripts\ruff.exe check . ..\samples --config pyproject.toml                     # All checks passed
```

### Verified

- The combinatorial test: for every one of the 6,561 verdict combinations, `auto_approve` is in the allowed list, and `apply_guardrails` lets it through, if and only if every field matched; every other combination gets overridden to `human_review` with `decision_source = code_override`.
- A disallowed outcome from the model is overridden and the reason is recorded.
- An amendment draft missing a real mismatch gets the missing field appended, and that appended-to decision is marked `code_override` (not `llm`), so it's visible that code had to intervene.
- `fallback_decision()` always returns `human_review`, including (deliberately) on a document where every field actually matched -- an AI outage means a person confirms, never a silent auto-approval by default.
- `route()` itself never raises: both a failed judgement call and an already-exhausted budget fall through cleanly to the same safe fallback.

### Decisions and deviations (design doc updated to match)

- `RouteDecision`'s Pydantic schema deliberately accepts any of the three outcome names, not just the currently-allowed ones -- restricting it at the schema level would silently turn a disallowed proposal into a validation retry, hiding exactly the event the override path exists to catch and log.
- An override discards the model's amendment draft (if any) but keeps its reasoning text, since a supplier-facing draft doesn't make sense once the outcome has been overridden to `human_review`.
- Design doc: item 6 ticked; §10.1 gained four new entries (guardrails' zero-LLM-dependency design, the permissive-schema/strict-code split, what an override keeps vs discards, and `fallback_decision`'s guarantees).

### Known issues

- No new real failures this phase.

### Open reminders

- Everything from Phases 1-6 still stands (key rotation, free-tier limits, PRD numbers).
- Scope-cut list (top of this file) governs Phases 8-13 for this session.

### For Phase 8

- The Router's public surface for the graph: `app.agents.route.route()` takes `fields: dict[str, FieldVerdict]` and an `LLMClient`, returns `app.trust.guardrails.Decision` (`outcome`, `reasoning`, `cited_fields`, `amendment_draft`, `decision_source`, `override_reason`).
- The three agents' entry points the graph's nodes will call, in order: `app.agents.extract.extract()`, `app.agents.validate.validate()`, `app.agents.route.route()`. Each needs an `LLMClient` sharing one `CallBudget`/`CallRecorder` per run, per design section 3.6.
- `validate()` needs `pages: list[PreparedPage]` (for the readable-page check) in addition to the Extractor's `ExtractionOutcome` and the loaded `RuleSet` -- the graph's `prepare` node is what will load the customer's rules once per run via `app.rules.loader.load_rules_for_customer()`.

## Phase 8: Graph, checkpoints and storage

Status: **done, 2026-09-30.** Built under the deadline-driven scope cuts above (no per-phase approval gate; committed together with 6, 7 and 9 as one batch). This is the biggest phase of the session, and the pipeline now runs end to end from a CLI.

### What was built

- `backend/app/store/`:
  - `schema.py`: `init_db()`, one `CREATE TABLE`/`CREATE VIEW` script matching design section 4 exactly -- `shipments`, `documents`, `runs` (with the reserved, empty online-metric columns), `field_results`, `llm_calls`, and the two read-only views `v_documents`/`v_fields`.
  - `db.py`: `connect()` -- one place `app.db` is opened, in WAL mode, schema applied on every connect.
  - `repository.py`: every read and write to `app.db`, as plain functions (create shipment+document, create/update/complete/escalate a run, insert field results and LLM call log rows, the duplicate-hash lookup, `list_processing_runs()` for resume). `to_stored_outcome()` is the one place design section 10.1's Router-vs-stored outcome name mapping (`auto_approve` -> `auto_approved`, etc.) happens.
- `backend/app/graph/`:
  - `state.py`: `RunState` (design section 3.2's table, as a `TypedDict`), plus the plain-dict <-> domain-object conversions every node needs (`PreparedPage`, `ExtractionOutcome`, `ValidationOutcome`'s fields, `Decision`). A page's pixels are written to disk once and referenced by path in the checkpointed state, never held as a numpy array in state itself.
  - `nodes.py`: `prepare_node`, `extract_node`, `validate_node`, `route_node`, `persist_node`, `escalate_node`. Every node that can fail catches its own exceptions and sets `state["error"]` rather than raising; `route_node` never does, since `route()` already has its own safe fallback. `transport_factory` is a module-level override point so tests swap in `FakeTransport` without touching production code.
  - `build.py`: `build_graph()` (the `prepare -> extract -> validate -> route -> persist -> END` graph, one conditional edge per node to `escalate`, recursion limit 10), `run_document()`, `resume_document()`, `resume_incomplete_runs()`.
- `backend/app/cli.py`: `python -m app.cli run <file> --customer acme [--rerun]`, `python -m app.cli resume`, `python -m app.cli resume-one <run_id>`.
- `backend/tests/test_graph.py` (8 tests, `FakeTransport` throughout): a clean document auto-approves end to end and fills every table; the query views return the right counts; a planted error (E1, HS code) is never auto-approved; a duplicate upload is recognised by hash; an exhausted budget escalates to human review with the reason recorded; `resume_incomplete_runs` finds nothing once a run is complete; a simulated crash right after extraction, followed by resume, shows the extraction call was made exactly once in the log; the CLI's own `run` command works end to end.

### Commands

```
# From backend/, with the venv active
python -m app.cli run ..\samples\submission\01-clean-correct\*.pdf --customer acme
python -m app.cli resume

.venv\Scripts\python.exe -m pytest tests\test_graph.py -v
.venv\Scripts\ruff.exe check . ..\samples --config pyproject.toml
```

### Verified

- `tests/test_graph.py`: 8 passed, against `FakeTransport` -- no Gemini quota spent.
- The full backend suite (including Phases 1-7's tests): **13,376 passed, 5 deselected (live), 0 failed**, ruff clean across `backend/` and `samples/`.
- The crash/resume test: killing a run right after `extract` (by making `validate`'s LLM call fail, simulating the process dying there) and then re-running from the same `run_id`'s checkpoint shows exactly one `extract`-agent row in `llm_calls`, not two.
- The budget-exceeded test: with the call budget maxed out before the run starts, `extract_node` catches the resulting `BudgetExceededError`, sets `state["error"]`, and the run ends up `completed` with `outcome = human_review` and a recorded `escalation_reason` -- never left in a stuck `processing` state.
- The query views (`v_documents`, `v_fields`) return the right match/mismatch/uncertain counts and field rows for a real run, confirming Phase 11's fixture assumptions (built in parallel, see below) match the real schema closely enough to reconcile easily.

### Decisions and deviations (design doc updated to match)

- **`transport_factory` as a module-level override point in `app/graph/nodes.py`** (not specified by design): the real `GeminiTransport` is built lazily inside a function so importing the nodes module never requires a configured Gemini key, and tests monkeypatch this one attribute to inject `FakeTransport` -- no other test seam was needed across the whole graph layer.
- **`escalate_node` also writes the call log** (an addition beyond the original Phase 8 task list): the first version only wrote `llm_calls` rows in `persist_node`, which meant an escalated run's already-made AI calls (e.g. a successful `extract` before `validate` failed) never made it into the log at all -- caught by the crash/resume test itself, before it ever reached the failure log, so it's not a failure-log entry, just a design refinement made while writing the test.
- **Checkpoints live in their own SQLite file (`checkpoints.db`), separate from `app.db`** (design section 2 already calls for this split; implemented here). The CLI opens its own checkpoint connection per invocation.
- **A minimal, hardcoded `_CUSTOMER_NAMES` map in `app/cli.py`**: the real customer-name lookup is `GET /api/customers` (design section 6, Phase 9, not built this session under the scope cuts) -- the CLI needs *some* name to store, and doesn't have that endpoint to call.
- Design doc: items 7 and 9 ticked.

### Known issues

- No new real failures this phase (the `escalate_node` call-log gap above was caught by the crash/resume test during development, before any run using it existed, so it isn't logged as a failure).
- Under the scope cuts, `app/api` (design section 6, Phase 9) still doesn't exist. The CLI is the only way to run the pipeline this session.

### Open reminders

- Everything from Phases 1-7 still stands (key rotation, free-tier limits, PRD numbers).
- Scope-cut list (top of this file) still governs Phases 9-13.

### For Phase 9

- `app.graph.build.run_document()` and `resume_incomplete_runs()` are what the API's `POST /api/runs` and start-up hook should call -- the CLI (`app/cli.py`) already shows the exact call shape needed (open a `connect()`, a `SqliteSaver` checkpointer, look up or create the shipment/document via `app.store.repository`, then call `run_document`).
- The repository's `find_document_by_hash()` is the duplicate-upload check design section 3.7 and the API's `POST /api/runs` (design section 6) both need.
- `repo.get_run()` and `repo.list_field_results()`/`list_llm_calls()` are the reads `GET /api/runs/{id}` needs; `repo.list_runs()` is what `GET /api/runs` needs.

## Backend follow-up: LLM call log endpoint and CLI dump

Status: **done, 2026-09-30 (separate session, after Phases 9-13's initial pass).**

The run's summary numbers (`llm_calls`, `fallback_used`, token totals, latency) were already
stored and returned by `GET /api/runs/{id}`, but nothing showed the individual call attempts
behind those totals -- which agent made each call, whether it was a retry or a fallback-model
call, its own tokens and latency. Added once the screen needed somewhere to show that
evidence (see `11-operator-ui.md`'s note on this and the "Frontend follow-up" entry below).

### What was built

- `backend/app/api/schemas.py`: `LlmCallOut` and `llm_call_from_row()`.
- `backend/app/api/routes.py`: `GET /api/runs/{run_id}/llm-calls`, returning every logged
  attempt for that run, oldest first, or 404 for an unknown run ID.
- `backend/app/cli.py`: `python -m app.cli dump-llm-calls`, writing every run's call log to a
  Markdown file (`data/llm-calls-report.md` by default) -- the same data the endpoint serves,
  readable without running the app or a browser. `eval/reports/llm-calls-sample.md` is a
  checked-in example, generated from real runs made while building this.
- `backend/app/graph/nodes.py` / `build.py`: `extract_node`/`validate_node`/`route_node` now
  also call `repo.update_run_step()` at their own start (previously only `prepare_node` did,
  via the initial insert), so a run's `current_step` reflects whichever step is actually
  in flight, not just "prepare" until the whole run finishes. Needs the `conn` each node
  didn't previously take, threaded through from `build_graph()`.
- Frontend: `CallLogTable.jsx` (a collapsed-by-default table on the run view, fetched lazily
  on first expand) and a `run-metrics` strip on `DecisionCard.jsx` (call count, tokens,
  latency, whether the fallback model was used).
- `backend/tests/test_api_runs.py`: two new tests (every call listed in order with the right
  shape; 404 on an unknown run ID) plus the new route added to the OpenAPI-schema coverage
  test.

### Verified

- Full backend suite: 13,430 passed, 5 deselected (live), ruff clean.
- `npm run build` and `npm run lint`: clean (pre-existing, unrelated `setState`-in-effect
  warning in `App.jsx` only).

### Decisions and deviations

- `current_step` updates happen inside each node itself, right before its own work starts,
  rather than the graph wrapping every node with a generic "mark this step active" step --
  keeps each node's own responsibility (and its `conn` dependency) explicit at the call site.

### Known issues

- No new real failures.

## Frontend follow-up: Ask box wired into the screen

Status: **done, 2026-09-30 (separate session, after Phases 9-13's initial pass).**

Phase 10's original scope cut this area out (see "Scope cuts for this session" at the top of
this file: "the query layer's own Ask panel UI is out of scope for this pass"). The backend
endpoint (`POST /api/query`, Phase 11) was already built, tested and reachable from
`frontend/src/api/api.js`'s `postQuery()`, but no component ever called it.

### What was built

- `frontend/src/components/AskPanel.jsx`: a question input, three clickable example
  questions (from `query_v1`'s worked examples), and a result area that shows either a
  single value or a results table, always followed by the explanation sentence and a
  collapsible "SQL used" block — including on a refusal, so a denied query still shows what
  was tried (design section 8).
- Wired into `App.jsx` as the third area of the screen (design section 7 area 3 / section 2's
  diagram), alongside the existing upload/history and run-view areas.
- CSS additions in `index.css` for the example-question buttons and the result table.

### Verified

- `npm run lint` (oxlint): clean (one pre-existing, unrelated warning in `App.jsx` about
  `setState` inside an effect, present before this change).
- `npm run build` (vite): succeeds, 28 modules transformed.
- Not covered: no live run against the real API was made as part of this change (that would
  spend a real Gemini call); the component was checked against the existing `QueryAnswerOut`
  shape in `backend/app/api/schemas.py` and `postQuery()`'s existing fetch call, both already
  covered by the backend's own tests (`test_query_service.py`, `test_query_gate.py`).

### Decisions and deviations

- The panel always renders (not conditionally shown only after a run completes), since
  asking a question isn't tied to any one run — it queries across everything stored so far,
  matching design section 5's framing ("a person types a question... about the stored data").
- A query is refused vs answered is told apart by shape alone (`sql === null` with no columns
  or rows means refused), matching `QueryAnswerOut`'s existing shape rather than adding a new
  field to the backend response.

### Known issues

- No new real failures.

### Open reminders

- Everything from Phases 1-13 still stands (key rotation, free-tier limits, PRD numbers,
  the still-unwritten write-up, PRD Section 1, demo video, sample-queries file, and an actual
  eval run against the real API for real numbers).
