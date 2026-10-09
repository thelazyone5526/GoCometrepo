"""Turn one email attachment into a `documents` row, or record why it did not become one
(requirements 2.1, 2.2).

This is the Part 2 sibling of the Part 1 upload route: same `check_upload` / `file_hash` /
`store_upload` building blocks from `app.ingest.files`, reused rather than re-implemented, but
writing against an *existing* shipment (`create_document_for_shipment`) instead of creating one
of its own. `app/cli.py::cmd_run` is left exactly as it was (design section 9, open item 3): this
module duplicates a little of its logic on purpose rather than factor it out from underneath
Part 1.

Deliberately *not* done here:

* No decision about which attachments belong to which email, no subject parsing, no pipeline
  run. `accept_attachment` handles exactly one attachment and is called once per attachment by
  `inbox/watcher.py`, which also decides when to run the Part 1 pipeline on an accepted one.
* Never raises. A broken attachment is data (`UploadError.message`, recorded and returned), not
  an exception that would stop the rest of the email's attachments from being processed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.ingest.files import UploadError, check_upload, store_upload
from app.store import repository as repo

AttachmentStatus = Literal["accepted", "duplicate", "rejected"]


@dataclass(frozen=True)
class AttachmentOutcome:
    """What happened to one attachment. `document_id` is set for `accepted` (the new document)
    and `duplicate` (the document it duplicates), and is `None` for `rejected`."""

    status: AttachmentStatus
    document_id: str | None
    reason: str | None


def accept_attachment(
    conn,
    *,
    email_id: str,
    shipment_id: str,
    shipment_chain_ids: list[str],
    attachment,
    doc_type: str | None = None,
) -> AttachmentOutcome:
    """Check, dedupe and (if accepted) store one attachment, recording the outcome either way.

    1. `check_upload` first (requirement 2.1): a file that is not a PDF/PNG/JPG, is too large,
       or has too many pages is `rejected`, with `UploadError.message` as the human-readable
       reason, and no `documents` row is created.
    2. A byte-identical match anywhere in this shipment's resubmission chain
       (`shipment_chain_ids`) is a `duplicate` (requirement 2.2): the existing document is
       reused, nothing is written to disk a second time, and no second `documents` row is
       created.
    3. Otherwise the file is stored and a new `documents` row is created against
       `shipment_id`. `doc_type` is `None` at this point -- the Extractor has not run yet, so
       the watcher fills it in after the Part 1 pipeline reports the document type (see
       `inbox/watcher.py`'s own comment on this).

    Every outcome, including a rejection, is written to `shipment_attachments` via
    `record_attachment`, so an attachment that never became a document still shows up for an
    operator asking "what arrived with this email" (requirement 2.1).
    """
    try:
        checked = check_upload(attachment.data)
    except UploadError as exc:
        repo.record_attachment(
            conn,
            email_id=email_id,
            filename=attachment.filename,
            file_hash=None,
            status="rejected",
            reason=exc.message,
            document_id=None,
        )
        return AttachmentOutcome("rejected", None, exc.message)

    existing = repo.find_document_by_hash_in_chain(
        conn, shipment_ids=shipment_chain_ids, file_hash=checked.file_hash
    )
    if existing is not None:
        reason = "byte-identical to an earlier document"
        repo.record_attachment(
            conn,
            email_id=email_id,
            filename=attachment.filename,
            file_hash=checked.file_hash,
            status="duplicate",
            reason=reason,
            document_id=existing.id,
        )
        return AttachmentOutcome("duplicate", existing.id, reason)

    stored_path: Path = store_upload(checked)
    document = repo.create_document_for_shipment(
        conn,
        shipment_id=shipment_id,
        filename=attachment.filename,
        file_hash=checked.file_hash,
        mime_type=checked.file_type,
        page_count=checked.page_count,
        has_text_layer=False,
        stored_path=stored_path,
        doc_type=doc_type,
    )
    repo.record_attachment(
        conn,
        email_id=email_id,
        filename=attachment.filename,
        file_hash=checked.file_hash,
        status="accepted",
        reason=None,
        document_id=document.id,
    )
    return AttachmentOutcome("accepted", document.id, None)


__all__ = ["AttachmentOutcome", "accept_attachment"]
