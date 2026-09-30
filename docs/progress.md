# Progress and Handoff

Read this at the start of every phase chat. Update it at the end of every phase.

Current: **Phases 3, 4 and 5 done** (built together in one chat, by exception to the "one phase per chat" rule, with the user's explicit sign-off). Next: Phase 6 (rules and Validator agent). See "For Phase 6" at the end.

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
- Free-tier limits: **still open.** Ansh reads requests per minute and per day for both models in AI Studio, and they get recorded in design §10.1. Each model has its own quota. Public reports say about 20 a day for 3.8 Flash and about 500 for Lite. The eval's headline numbers still come from the primary, with fallback runs reported separately.

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

### For Phase 6

- The trust layer's public surface for the Validator: `app.trust.normalise` (per-field normalisers plus `text_comparison_key`), `app.trust.formats.format_ok`, and `FieldResult` (from `app.agents.extract`) carrying `value`, `confidence`, `grounding.status` per field.
- `rules/acme.yaml` (Phase 2) is data only; Phase 6 writes the loader (`rules/loader.py`) and checkers (`rules/checkers.py`).
- Design section 3.4's verdict order: confidence-below-threshold → `uncertain`, regardless of the rule; null value on a readable page → `mismatch`; null on an unreadable page → `uncertain`; otherwise the rule decides. "Readable page" isn't defined yet in code — Phase 3's `PageQuality.avg_confidence` (text layer is always 1.0) is the natural signal to use.
- The entity-name rule (consignee) needs its own suffix normalisation ("Pte Ltd" = "Private Limited") ahead of the exact-match / 85–99-band-to-Gemini / below-85-mismatch logic in design section 3.4 — this is separate from `trust.normalise.text_comparison_key`, which only strips case and punctuation, not suffixes.
- `CONFIDENCE_THRESHOLD` is already in `config.py` (0.85, starting value).
