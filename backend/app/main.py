"""FastAPI application entry point.

Run from the backend folder:  python -m app.main
The server binds to 127.0.0.1 only: the API has no login, so it must not be reachable
from other machines on the network.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Trade Document Pipeline", version="0.1.0")


@app.get("/api/health")
def health() -> dict[str, str]:
    """Liveness check used by the UI and by the dev proxy smoke test."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
