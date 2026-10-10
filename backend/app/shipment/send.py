"""The single send path (part2-shipment-workflow design section 2 and 5): `send_draft` is the
only function anywhere in this codebase allowed to write to `outbox` or move a shipment to
`approved_sent` / `amendment_sent` (requirement 7.1, 15.1). Nothing upstream of this -- not the
decision module, not the draft renderer, not the API layer -- gets to call `insert_outbox`
itself; everything funnels through here so the "the system never sends" guarantee is a property
of the code, not a convention someone has to remember.

Why insert-then-catch instead of check-then-insert (requirement 7.2): `outbox.shipment_id` is
UNIQUE, so the database itself refuses a second send. If this module first ran
`get_outbox(conn, shipment_id)` to see whether a row already existed and only inserted when it
didn't, there would be a window between that read and the write where two concurrent send
requests could both see "nothing sent yet" and both proceed -- exactly the race the unique index
exists to close. Attempting the insert and catching `sqlite3.IntegrityError` means the database's
own atomicity is what decides who wins, not a check this module performs and could get wrong
under concurrency.

`mark_reviewed_no_send` (requirement 7.5, 7.6) is the sibling path: the operator looking at a
flagged shipment and deciding not to send anything. It never touches `outbox`, and when it
records a note it writes only `operator_decision` / `operator_decided_at` via
`record_operator_decision` -- the system's own `outcome` and `reasoning` are never overwritten,
so the audit trail keeps both what the pipeline concluded and what the person did about it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.store import repository as repo


class SendError(Exception):
    """Raised when a draft cannot be sent: no draft, no version, or a missing field. A later
    API task is expected to map this to a 4xx response."""


class AlreadySentError(SendError):
    """Raised when `outbox.shipment_id`'s unique index refuses a second send (requirement 7.2).

    Kept distinct from the base `SendError` so a caller can tell "someone already sent this"
    (likely a 409) apart from "there is nothing to send yet" or "a required field is empty"
    (likely a 400).
    """

    def __init__(self, shipment_id: str) -> None:
        self.shipment_id = shipment_id
        super().__init__(f"shipment {shipment_id!r} has already been sent")


@dataclass(frozen=True)
class SendResult:
    outbox_id: str
    sent_at: str
    review_state: str


def send_draft(
    conn: sqlite3.Connection,
    *,
    shipment_id: str,
    recipient: str,
    subject: str | None,
    body: str,
) -> SendResult:
    """The operator's Send (requirement 7.1-7.3).

    1. There must be a draft, and the draft must have a current version -- otherwise there is
       nothing to send.
    2. `recipient` and `body` must be non-blank; `subject` may be empty (some drafts carry no
       subject change over the original email's).
    3. The insert is attempted directly; a second send for the same shipment raises
       `AlreadySentError` from the database's own unique index, not from a pre-check.
    4. On success, the shipment moves to `approved_sent` when the system's own outcome was
       `auto_approve`, and to `amendment_sent` for everything else -- including a `human_review`
       shipment the operator chose to send anyway, which design section 5 treats the same as an
       amendment for this purpose.
    """
    draft = repo.get_draft(conn, shipment_id)
    if draft is None:
        raise SendError(f"shipment {shipment_id!r} has no draft to send")

    versions = repo.list_draft_versions(conn, draft["id"])
    if not versions:
        raise SendError(f"shipment {shipment_id!r}'s draft has no version to send")
    current_version = versions[-1]

    if recipient is None or not recipient.strip():
        raise SendError("recipient is required and cannot be empty")
    if body is None or not body.strip():
        raise SendError("body is required and cannot be empty")

    try:
        outbox_id = repo.insert_outbox(
            conn,
            shipment_id=shipment_id,
            draft_id=draft["id"],
            draft_version_id=current_version["id"],
            recipient=recipient,
            subject=subject,
            body=body,
        )
    except sqlite3.IntegrityError as exc:
        raise AlreadySentError(shipment_id) from exc

    shipment = repo.get_shipment(conn, shipment_id)
    review_state = (
        "approved_sent"
        if shipment and shipment["outcome"] == "auto_approve"
        else ("amendment_sent")
    )
    # The send already happened in the simulated outbox above; a failure here must not look
    # like it was undone -- there is nothing to roll back and no attempt is made to.
    repo.set_review_state(conn, shipment_id=shipment_id, review_state=review_state)

    sent_row = repo.get_outbox(conn, shipment_id)
    sent_at = sent_row["sent_at"] if sent_row is not None else repo.now_iso()

    return SendResult(outbox_id=outbox_id, sent_at=sent_at, review_state=review_state)


def mark_reviewed_no_send(
    conn: sqlite3.Connection,
    *,
    shipment_id: str,
    operator_note: str | None = None,
) -> None:
    """Requirement 7.5, 7.6: the operator looked and chose not to send anything.

    Writes `review_state = 'reviewed_no_send'` and nothing to `outbox`. If `operator_note` is
    given, it is recorded through `record_operator_decision`, which writes only
    `operator_decision` / `operator_decided_at` -- the shipment's own `outcome` and `reasoning`
    are never touched, so what the pipeline concluded and what the operator decided both stay
    on the record. Safe to call even when no draft exists yet, since it never reads one.
    """
    repo.set_review_state(conn, shipment_id=shipment_id, review_state="reviewed_no_send")
    if operator_note is not None:
        repo.record_operator_decision(conn, shipment_id=shipment_id, decision=operator_note)
