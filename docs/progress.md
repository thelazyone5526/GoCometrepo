# Progress and Handoff

Read this at the start of every phase chat. Update it at the end of every phase.

## Phase 1: Setup and spikes

Status: **done, 2026-09-30.** One gate item is still open: the free-tier limits, which Ansh reads in AI Studio. Not committed yet (the commit waits for Ansh's approval, and git needs an identity first, see "Open reminders").

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
- **First commit:** `main` has no commits. After approval, the Phase 1 commit goes on `part1`. Merging into `main` at the end will just fast-forward `main` to `part1`.
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
