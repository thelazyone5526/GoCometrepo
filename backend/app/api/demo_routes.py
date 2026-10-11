"""Demo-only route: simulate a supplier email arriving (D9 in `docs/part2-decisions.md`).

`POST /api/demo/incoming-email` writes one fresh `.eml` into the watched inbox folder. It does
nothing else: the watcher picks the file up and the real pipeline runs, exactly as for any other
email. It never touches the database itself, never calls a model and never sends anything.

Security note: like the rest of the API this has no login and is reachable only on 127.0.0.1.
The only input is an enum, so a caller cannot choose a path or file content; the worst a caller
can do is create more fake emails in the inbox. Remove this module for anything but a demo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import REPO_DIR
from app.inbox.demo_mail import build_demo_email, next_reference
from app.store import repository as repo
from app.store.db import connect

router = APIRouter(prefix="/api/demo")


def _sample_dir() -> Path:
    return REPO_DIR / "samples" / "inbox"


def _inbox_dir() -> Path:
    # The same folder `app.cli watch` scans by default (`app.cli.DEFAULT_INBOX_DIR`).
    return REPO_DIR / "samples" / "inbox"


class IncomingEmailIn(BaseModel):
    kind: Literal["clean", "messy"]


class IncomingEmailOut(BaseModel):
    reference: str
    file: str


@router.post("/incoming-email", response_model=IncomingEmailOut, status_code=201)
def post_incoming_email(body: IncomingEmailIn) -> IncomingEmailOut:
    inbox = _inbox_dir()
    inbox.mkdir(parents=True, exist_ok=True)

    # A reference is taken if a shipment already uses it, OR if its email file is still sitting
    # in the inbox waiting for the watcher. The second case matters for a fast double click:
    # the first email is not in the database yet, and without this check both clicks would get
    # the same reference and the second file would overwrite the first.
    conn = connect()
    try:
        reference = next_reference(
            lambda ref: (
                (inbox / f"demo-{ref}.eml").exists()
                or repo.find_latest_shipment_by_reference(conn, customer_id="acme", reference=ref)
                is not None
            )
        )
    finally:
        conn.close()

    file_name, data = build_demo_email(body.kind, sample_dir=_sample_dir(), reference=reference)
    (inbox / file_name).write_bytes(data)
    return IncomingEmailOut(reference=reference, file=file_name)


__all__ = ["router"]
