"""FastAPI application entry point.

Run from the backend folder:  python -m app.main
The server binds to 127.0.0.1 only: the API has no login, so it must not be reachable
from other machines on the network.

No CORS middleware is configured (design section 6): in development, Vite's dev server
proxies `/api` to this backend, and after `npm run build`, FastAPI serves the built UI
itself. Either way the browser only ever talks to one origin, so cross-origin requests are
never a real case here -- adding CORS would only open a hole that isn't needed.
"""

from __future__ import annotations

import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.demo_routes import router as demo_router
from app.api.routes import router as api_router
from app.api.routes import startup_resume_incomplete_runs
from app.api.shipment_routes import router as shipment_router

# Part 2's inbox trigger, run in-process so the backend alone can demo it (design section 2's
# decision table: "a lifespan thread also calls it so the backend alone demos"). Off by
# default: it is opt-in via ENABLE_INBOX_WATCHER=1, so importing `app.main` -- which nothing
# in the test suite currently does, but which must stay safe if that changes -- never starts a
# background thread that opens its own `app.db` / checkpoints connection against whatever
# `settings.data_dir` happens to be at import time (a test's `tmp_path`, mid-teardown, in the
# worst case). `cli watch` is the documented, always-available way to run the watcher; this
# thread is a convenience for the single-process demo, not something tests should ever trigger.
_ENABLE_INBOX_WATCHER = os.getenv("ENABLE_INBOX_WATCHER", "").strip() == "1"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Design section 3.7: resume every run still marked `processing` from its last
    checkpoint on start-up, so a crash mid-run doesn't leave it stuck forever."""
    startup_resume_incomplete_runs()

    stop_event: threading.Event | None = None
    watcher_thread: threading.Thread | None = None
    if _ENABLE_INBOX_WATCHER:
        # Imported here, not at module level: these pull in the whole graph/LLM stack, which
        # every other route in this file already depends on transitively, but keeping the
        # import inside the opt-in branch means a plain `import app.main` with the watcher
        # disabled (the only way the test suite could ever touch this file) pays no extra cost
        # and takes on no extra risk from this feature.
        from pathlib import Path

        from langgraph.checkpoint.sqlite import SqliteSaver

        from app.cli import CHECKPOINTS_FILENAME, DEFAULT_INBOX_DIR
        from app.config import settings
        from app.inbox.watcher import watch_loop
        from app.store.db import connect

        conn = connect()
        checkpoint_conn = sqlite3_connect_for_watcher(settings.data_dir, CHECKPOINTS_FILENAME)
        checkpointer = SqliteSaver(checkpoint_conn)
        stop_event = threading.Event()
        watcher_thread = threading.Thread(
            target=watch_loop,
            kwargs={
                "conn": conn,
                "checkpointer": checkpointer,
                "inbox_dir": Path(DEFAULT_INBOX_DIR),
                "stop_event": stop_event,
            },
            daemon=True,
            name="inbox-watcher",
        )
        watcher_thread.start()

    yield

    if stop_event is not None:
        stop_event.set()
    if watcher_thread is not None:
        watcher_thread.join(timeout=5.0)


def sqlite3_connect_for_watcher(data_dir, filename: str):
    """A `checkpoints.db` connection for the lifespan watcher thread, same file the CLI's
    `_checkpoint_connection` uses. Kept as a tiny local helper (rather than importing
    `app.cli`'s private `_checkpoint_connection`) so `app.main` depends on `app.cli` for its
    two public constants only."""
    import sqlite3

    data_dir.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(data_dir / filename, check_same_thread=False)


app = FastAPI(title="Trade Document Pipeline", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)
app.include_router(shipment_router)
app.include_router(demo_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    """Liveness check used by the UI and by the dev proxy smoke test."""
    return {"status": "ok"}


from fastapi.staticfiles import StaticFiles
from pathlib import Path

dist_dir = Path(__file__).parent.parent.parent / "frontend" / "dist"
if dist_dir.exists():
    app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
