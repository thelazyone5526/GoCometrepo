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

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router as api_router
from app.api.routes import startup_resume_incomplete_runs


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Design section 3.7: resume every run still marked `processing` from its last
    checkpoint on start-up, so a crash mid-run doesn't leave it stuck forever."""
    startup_resume_incomplete_runs()
    yield


app = FastAPI(title="Trade Document Pipeline", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    """Liveness check used by the UI and by the dev proxy smoke test."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
