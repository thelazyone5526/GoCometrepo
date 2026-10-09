"""The inbox trigger that drives everything else in Part 2 (requirements 1.1-1.6, 2.1-2.3,
5.1, 13.6): list `*.eml` files, turn each unseen one into a shipment, run the Part 1 pipeline
on each of its accepted attachments, and materialise the effective set.

`scan_inbox` does one pass and returns; `watch_loop` is the thin wrapper that calls it on a
timer. Nothing here decides *when* to run -- that is `app.cli`'s `watch` / `scan-inbox`
subcommands, or the opt-in lifespan thread in `app.main` -- this module only knows how to do
one pass correctly.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from app.graph.build import run_document
from app.inbox.eml import EmlError, ParsedEmail, message_id_fallback, parse_eml
from app.inbox.intake import accept_attachment
from app.inbox.subject import parse_subject
from app.rules.loader import DEFAULT_RULES_DIR, RuleLoadError, load_rules_for_customer
from app.shipment.effective_set import build_effective_set
from app.store import repository as repo

# The four Doc_Types the Extractor and the rest of Part 2 know about (design section 2,
# decision 3). Anything the Extractor reports that isn't one of these -- including "it
# reported nothing at all" -- becomes "uncertain" rather than a crash or a made-up type
# (requirement 2.3).
_SUPPORTED_DOC_TYPES = {"commercial_invoice", "bill_of_lading", "packing_list"}

_MAX_CHAIN_HOPS = 50


def _known_customer_ids(rules_dir: Path) -> list[str]:
    """Every customer this install has rules for, read straight off the filesystem rather than
    some separate registry: a `rules/<id>.yaml` file is what makes a customer id "known" to
    `parse_subject` (requirement 1.4)."""
    if not rules_dir.exists():
        return []
    return sorted(path.stem for path in rules_dir.glob("*.yaml"))


def _shipment_chain_ids(conn, shipment_id: str) -> list[str]:
    """`shipment_id` and every ancestor reachable through `parent_shipment_id`, nearest first.

    This is the chain a byte-identical attachment is deduplicated against (requirement 2.2):
    a document already seen anywhere in this resubmission's history is reused rather than
    stored and processed a second time. Bounded at `_MAX_CHAIN_HOPS` so a corrupt
    `parent_shipment_id` cycle can't loop forever.
    """
    ids = [shipment_id]
    current_id: str | None = shipment_id
    for _ in range(_MAX_CHAIN_HOPS):
        shipment = repo.get_shipment(conn, current_id)
        if shipment is None:
            break
        parent_id = shipment["parent_shipment_id"]
        if parent_id is None:
            break
        ids.append(parent_id)
        current_id = parent_id
    return ids


def _document_type_from_state(final_state: dict[str, Any]) -> str:
    """What `doc_type` to write back for a document the Part 1 pipeline just ran on
    (requirement 2.3). The Extractor's own result lives at `state["extraction"]["document_type"]`
    (see `app/graph/state.py::extraction_to_dict`); a run that errored before extraction wrote
    anything, or that reported a type this install doesn't know about, is recorded as
    "uncertain" rather than guessed at."""
    extraction = final_state.get("extraction") or {}
    document_type = extraction.get("document_type")
    if document_type in _SUPPORTED_DOC_TYPES:
        return document_type
    return "uncertain"


def _set_document_doc_type(conn, *, document_id: str, doc_type: str) -> None:
    """The one direct SQL statement in this module.

    `app/store/repository.py` has no "update a document's doc_type" function yet --
    `create_document_for_shipment` only ever sets it once, at creation, and that is correct for
    every other caller. Here the type genuinely is not known until *after* the Part 1 pipeline
    has run (the whole reason intake passes `doc_type=None` into `accept_attachment`), so this
    writes the one UPDATE repository.py doesn't provide rather than adding a new, single-use
    function to a module this task does not own. `repository.py` already does exactly this
    pattern -- a single `UPDATE ... WHERE id = ?` -- for `set_review_state` and `update_run_step`;
    this statement reads the same way.
    """
    conn.execute("UPDATE documents SET doc_type = ? WHERE id = ?", (doc_type, document_id))
    conn.commit()


def _record_failed_email(
    conn,
    *,
    customer_id: str,
    customer_name: str,
    reference: str | None,
    email_id: str,
    failure_reason: str,
) -> str:
    """A shipment that cannot be processed still gets a row, so it is visible rather than
    silently dropped (requirements 1.5, 1.6, 13.1)."""
    return repo.create_shipment_for_email(
        conn,
        customer_id=customer_id,
        customer_name=customer_name,
        reference=reference,
        email_id=email_id,
        cycle_number=1,
        parent_shipment_id=None,
        review_state="failed",
        failure_reason=failure_reason,
    )


def _process_email(
    conn,
    checkpointer,
    *,
    eml_path: Path,
    parsed: ParsedEmail,
    rules_dir: Path,
) -> str | None:
    """Everything from "email recorded" to "effective set materialised" for one parsed email.
    Returns the shipment_id touched, or `None` if the email produced no shipment (should not
    happen in practice -- every branch below ends in a shipment)."""
    email_id = repo.record_email(
        conn,
        message_id=parsed.message_id,
        eml_path=str(eml_path),
        sender=parsed.sender,
        subject=parsed.subject,
        received_at=repo.now_iso(),
        attachment_count=len(parsed.attachments),
        attachment_filenames=[a.filename for a in parsed.attachments],
    )

    known_customer_ids = _known_customer_ids(rules_dir)
    parts = parse_subject(parsed.subject, known_customer_ids)

    if parts.customer_id is None:
        # requirement 1.5: an unrecognised customer is recorded as failed, and nothing about
        # its attachments is even looked at -- "start no processing" means exactly that.
        return _record_failed_email(
            conn,
            customer_id="unknown",
            customer_name="Unknown",
            reference=parts.reference,
            email_id=email_id,
            failure_reason="unknown customer",
        )

    try:
        rules = load_rules_for_customer(parts.customer_id, rules_dir=rules_dir)
    except RuleLoadError as exc:
        # A known-looking customer id whose rules file is missing or broken is, from the
        # watcher's point of view, exactly as unprocessable as an unknown one.
        return _record_failed_email(
            conn,
            customer_id=parts.customer_id,
            customer_name=parts.customer_id,
            reference=parts.reference,
            email_id=email_id,
            failure_reason=f"customer rules could not be loaded: {exc}",
        )

    if not parsed.attachments:
        # requirement 1.6: no attachments at all is a failure, recorded with a reason, not a
        # shipment silently waiting forever for documents that were never actually attached.
        return _record_failed_email(
            conn,
            customer_id=parts.customer_id,
            customer_name=rules.customer_name,
            reference=parts.reference,
            email_id=email_id,
            failure_reason="no attachments",
        )

    # requirement 5.1: a reference ties this email to its most recent earlier cycle, if any.
    # No reference at all always starts a fresh cycle 1 with no parent.
    cycle_number = 1
    parent_shipment_id: str | None = None
    if parts.reference is not None:
        latest = repo.find_latest_shipment_by_reference(
            conn, customer_id=parts.customer_id, reference=parts.reference
        )
        if latest is not None:
            cycle_number = latest["cycle_number"] + 1
            parent_shipment_id = latest["shipment_id"]

    shipment_id = repo.create_shipment_for_email(
        conn,
        customer_id=parts.customer_id,
        customer_name=rules.customer_name,
        reference=parts.reference,
        email_id=email_id,
        cycle_number=cycle_number,
        parent_shipment_id=parent_shipment_id,
        review_state="pending_review",
    )

    chain_ids = _shipment_chain_ids(conn, shipment_id)

    for attachment in parsed.attachments:
        outcome = accept_attachment(
            conn,
            email_id=email_id,
            shipment_id=shipment_id,
            shipment_chain_ids=chain_ids,
            attachment=attachment,
        )

        if outcome.status == "rejected":
            continue

        if outcome.status == "duplicate":
            # Reused content: still "this email's own" set membership, but zero model calls --
            # it was already run once, against whichever shipment it first belonged to.
            repo.add_shipment_document(
                conn,
                shipment_id=shipment_id,
                document_id=outcome.document_id,
                run_id=None,
                doc_type=None,
                origin="own_email",
                source_shipment_id=None,
                source_cycle=None,
            )
            continue

        # outcome.status == "accepted": a genuinely new document, run through the unchanged
        # Part 1 pipeline exactly once.
        document_id = outcome.document_id
        assert document_id is not None  # accepted outcomes always carry a document id
        stored_path = _stored_path_for(conn, document_id)
        run_id = repo.new_id()
        final_state = run_document(
            file_path=stored_path,
            customer_id=parts.customer_id,
            conn=conn,
            checkpointer=checkpointer,
            document_id=document_id,
            run_id=run_id,
        )
        doc_type = _document_type_from_state(final_state)
        _set_document_doc_type(conn, document_id=document_id, doc_type=doc_type)
        repo.add_shipment_document(
            conn,
            shipment_id=shipment_id,
            document_id=document_id,
            run_id=run_id,
            doc_type=doc_type,
            origin="own_email",
            source_shipment_id=None,
            source_cycle=None,
        )

    build_effective_set(conn, shipment_id)
    return shipment_id


def _stored_path_for(conn, document_id: str) -> Path:
    row = conn.execute(
        "SELECT stored_path FROM documents WHERE id = ?", (document_id,)
    ).fetchone()
    return Path(row["stored_path"])


def scan_inbox(
    conn,
    checkpointer,
    *,
    inbox_dir: Path,
    rules_dir: Path = DEFAULT_RULES_DIR,
) -> list[str]:
    """One pass over `inbox_dir`: every `.eml` not already in `emails` becomes a shipment, its
    attachments are checked and (if accepted) run through the Part 1 pipeline, and its
    effective set is materialised. Returns the shipment_ids touched this pass.

    Idempotent (requirements 1.1, 1.3, 13.6): an email already recorded by `message_id` is
    skipped entirely, so calling this again -- after a restart, or on the usual 2 s timer --
    reprocesses nothing. One bad `.eml` file is recorded as failed and does not stop the rest
    of the pass from running.
    """
    if not inbox_dir.exists():
        return []

    touched: list[str] = []
    for eml_path in sorted(inbox_dir.glob("*.eml")):
        data = eml_path.read_bytes()

        try:
            parsed = parse_eml(data)
        except EmlError as exc:
            # requirement 1.6: bytes that aren't a usable email at all. A hash of the raw
            # bytes is still a stable dedup key, so a restart doesn't re-record this file
            # either.
            fallback_id = message_id_fallback(data)
            if repo.find_email_by_message_id(conn, fallback_id) is not None:
                continue
            try:
                email_id = repo.record_email(
                    conn,
                    message_id=fallback_id,
                    eml_path=str(eml_path),
                    sender=None,
                    subject=None,
                    received_at=repo.now_iso(),
                    attachment_count=0,
                    attachment_filenames=[],
                )
                shipment_id = _record_failed_email(
                    conn,
                    customer_id="unknown",
                    customer_name="Unknown",
                    reference=None,
                    email_id=email_id,
                    failure_reason=f"not a readable email: {exc}",
                )
                touched.append(shipment_id)
            except Exception as inner_exc:  # pragma: no cover - defensive, see module docstring
                print(f"inbox watcher: could not record {eml_path}: {inner_exc}", file=sys.stderr)
            continue

        if repo.find_email_by_message_id(conn, parsed.message_id) is not None:
            # requirement 1.3: the restart guarantee. Already recorded, so nothing further is
            # even looked at for this file.
            continue

        shipment_id = _process_email(
            conn, checkpointer, eml_path=eml_path, parsed=parsed, rules_dir=rules_dir
        )
        if shipment_id is not None:
            touched.append(shipment_id)

    return touched


def watch_loop(
    conn,
    checkpointer,
    *,
    inbox_dir: Path,
    interval_seconds: float = 2.0,
    stop_event: Any = None,
) -> None:
    """Call `scan_inbox` forever, `interval_seconds` apart, until `stop_event` (a
    `threading.Event`) is set. With no `stop_event`, loops forever -- the right behaviour for
    `cli watch`, which is meant to be killed as a process, not stopped from the inside.

    A single bad pass (a transient I/O error, a bug) is caught and logged rather than killing
    the loop, so one failure doesn't quietly stop all future ingestion until someone notices
    the process died.
    """
    while stop_event is None or not stop_event.is_set():
        try:
            scan_inbox(conn, checkpointer, inbox_dir=inbox_dir)
        except Exception as exc:  # pragma: no cover - exercised only by a genuinely broken pass
            print(f"inbox watcher: scan failed: {exc}", file=sys.stderr)

        if stop_event is None:
            time.sleep(interval_seconds)
        else:
            stop_event.wait(interval_seconds)


__all__ = ["scan_inbox", "watch_loop"]
