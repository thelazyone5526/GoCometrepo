"""The effective-set builder (part2-shipment-workflow design section 2 and 5.2-5.4): this
email's own documents plus, for every Doc_Type they do not cover, the latest earlier document
of that type anywhere in the resubmission chain.

Why this exists at all: a corrected invoice resubmitted on its own should not force the
customer to resend the bill of lading and packing list too, and it should not force the
pipeline to re-run them either (zero model calls on a carried-over document). So the set this
shipment is judged against is built, not just stored -- "what documents is SU's reply about"
is a real question with a real answer, and it has to survive a restart and a rerun without
drifting (R5.3's at-most-once).

Pure code against `conn`: no `app.llm` import, no network call, nothing here can spend budget.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

from app.store import repository as repo

Origin = Literal["own_email", "carried_over"]


@dataclass(frozen=True)
class SetEntry:
    """One document in a shipment's effective set, as design section 2 defines it."""

    document_id: str
    run_id: str | None
    doc_type: str | None
    origin: Origin
    source_shipment_id: str | None
    source_cycle: int | None
    filename: str


def build_effective_set(conn: sqlite3.Connection, shipment_id: str) -> list[SetEntry]:
    """Build (and persist) the effective set for `shipment_id` (R5.2-R5.4).

    Step 1 is reading, not creating: this shipment's own documents are the `shipment_documents`
    rows with `origin = 'own_email'` already recorded against `shipment_id`, materialised by
    whoever ingested this email's attachments before this function ever runs. This function
    never invents an `own_email` row -- it only decides what, if anything, needs to be carried
    over on top of them, and then makes the materialised table match that decision.

    The chain is walked ancestor by ancestor, nearest first (R5.2: "the latest earlier
    document"). For each Doc_Type still missing, the walk stops at the first ancestor that has
    one -- an earlier shipment that also lacked it is not a match, and a shipment further back
    than the nearest match is never consulted for that Doc_Type. This is a per-field nearest
    search, not "copy everything the parent had": cycle 3 can carry one document from cycle 2
    and a different one from cycle 1, if that is where each type was last seen.

    A Doc_Type covered by this shipment's own documents is never carried over even if some
    ancestor also has it (R5.3: no redundant entry for a type already satisfied), and a
    Doc_Type that no ancestor ever carried is simply absent from the result -- there is nothing
    to pad it with, and padding would invent a document that was never received.
    """
    own_rows = [
        row
        for row in repo.list_shipment_documents(conn, shipment_id)
        if row["origin"] == "own_email"
    ]

    entries: list[SetEntry] = [
        SetEntry(
            document_id=row["document_id"],
            run_id=row["run_id"],
            doc_type=row["doc_type"],
            origin="own_email",
            source_shipment_id=None,
            source_cycle=None,
            filename=row["filename"],
        )
        for row in own_rows
    ]

    covered_types = {entry.doc_type for entry in entries if entry.doc_type is not None}
    carried_document_ids = {entry.document_id for entry in entries}

    this_shipment = repo.get_shipment(conn, shipment_id)
    ancestor_id = this_shipment["parent_shipment_id"] if this_shipment is not None else None

    # Nearest ancestor first: for each hop, anything the ancestor has of a Doc_Type not yet
    # covered is picked up right there, and that Doc_Type is marked covered so no shipment
    # further back is ever consulted for it again. This is what makes "latest earlier
    # document" mean nearest, not oldest.
    while ancestor_id is not None:
        ancestor = repo.get_shipment(conn, ancestor_id)
        if ancestor is None:
            break

        # Only the ancestor's *own* documents count as "this ancestor has it" -- a Doc_Type
        # the ancestor itself only carried forward from a still-earlier cycle must not be
        # relabelled as if it belonged to this ancestor. Each hop re-attributes to its true
        # origin, which is what lets a chain of several cycles find the right source cycle
        # for each Doc_Type independently.
        for row in repo.list_shipment_documents(conn, ancestor_id):
            if row["origin"] != "own_email":
                continue
            dt = row["doc_type"]
            if dt is None or dt in covered_types:
                continue
            if row["document_id"] in carried_document_ids:
                continue
            entries.append(
                SetEntry(
                    document_id=row["document_id"],
                    run_id=row["run_id"],
                    doc_type=dt,
                    origin="carried_over",
                    source_shipment_id=ancestor_id,
                    source_cycle=ancestor["cycle_number"],
                    filename=row["filename"],
                )
            )
            covered_types.add(dt)
            carried_document_ids.add(row["document_id"])

        ancestor_id = ancestor["parent_shipment_id"]

    for entry in entries:
        repo.add_shipment_document(
            conn,
            shipment_id=shipment_id,
            document_id=entry.document_id,
            run_id=entry.run_id,
            doc_type=entry.doc_type,
            origin=entry.origin,
            source_shipment_id=entry.source_shipment_id,
            source_cycle=entry.source_cycle,
        )

    return entries
