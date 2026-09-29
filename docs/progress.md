# Progress and Handoff

Read this at the start of every phase chat. Update it at the end of every phase.

Current: **Phase 2 done.** Next: Phase 3 (page preparation). See "For Phase 3" at the end.

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

### For Phase 3

- Test inputs:
  - C0 text layer: any `samples/grid/*-C0.pdf`.
  - Scans: C1, C2 and C3 in `samples/grid/`.
  - Image upload: `samples/jpg/V1-C1.jpg`.
- The expected printed strings are in each answer file (`fields.<name>.printed`). Skip E5 in the "OCR finds the invoice number" test, because its value is null.
- The C1 skew angle is `degradation.rotation_deg` in the answer file, counter-clockwise positive as in OpenCV (`getRotationMatrix2D`). Use it for the ±0.5° straightening test.
- C3 images are 827×1170 px (100 DPI). Rendering the PDF at 200 DPI upsamples them 2×. C1 and C2 are 1654×2339 px (200 DPI).
- Every scan PDF has exactly one embedded JPEG. `page.get_images()` finds it, and `extract_image` returns the original bytes.
- RapidOCR took about 2.5–3.2 s per scan page on this laptop, with the engine already loaded and no clean-up step (measured in the throwaway check).
