"""Build a "new" supplier email for the demo button (D9 in `docs/part2-decisions.md`).

The committed sample emails in `samples/inbox/` are recorded as processed after one pass, because
the watcher dedupes on Message-ID. This takes one of them, keeps its finished PDFs, and wraps
them in a fresh email: new Message-ID, new `SHP-` reference, new attachment names. The watcher
then treats it as genuinely new mail and runs the real pipeline on it.

Standard library only, and it reads the PDFs that already exist rather than drawing new ones, so
it needs no PDF renderer. Demo support, not part of the pipeline: nothing else imports it.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import format_datetime, make_msgid
from pathlib import Path
from typing import Literal

from app.inbox.eml import parse_eml

DemoKind = Literal["clean", "messy"]

SENDER = "Brightpath Export Documentation <docs@brightpath-computing.example>"
RECIPIENT = "ACME Shipment Desk <shipments@acme-logistics.example>"
MESSAGE_ID_DOMAIN = "mail.example.com"

# kind -> (committed sample email, the reference its attachment names currently carry)
SOURCES: dict[str, tuple[str, str]] = {
    "clean": ("01-clean-shipment.eml", "SHP-1042"),
    "messy": ("02-messy-shipment.eml", "SHP-1043"),
}


def next_reference(is_taken) -> str:
    """A `SHP-9xxx` reference that no earlier shipment uses.

    A reference that already exists would make the watcher link the new email to it as a
    resubmission (cycle 2), which is real behaviour but not what a demo button should do by
    accident. `is_taken(reference) -> bool` is the caller's lookup against stored shipments.
    """
    start = int(time.time()) % 1000
    for step in range(1000):
        reference = f"SHP-9{(start + step) % 1000:03d}"
        if not is_taken(reference):
            return reference
    raise RuntimeError("no unused demo reference left")


def build_demo_email(kind: DemoKind, *, sample_dir: Path, reference: str) -> tuple[str, bytes]:
    """`(file name, .eml bytes)` for a new email of the given kind, addressed as `reference`."""
    source_name, old_reference = SOURCES[kind]
    source = parse_eml((sample_dir / source_name).read_bytes())

    now = datetime.now(UTC)
    message = EmailMessage(policy=default_policy)
    message["Message-ID"] = make_msgid(domain=MESSAGE_ID_DOMAIN)
    message["Date"] = format_datetime(now)
    message["From"] = SENDER
    message["To"] = RECIPIENT
    message["Subject"] = f"{reference} acme - documents for checking"
    message.set_content(
        "Dear ACME Shipment Desk,\n\n"
        f"Please find attached the commercial invoice, bill of lading and packing list for "
        f"shipment {reference}.\n\n"
        "Best regards,\nLin Wei\nExport Documentation, Brightpath Computing Co., Ltd.\n"
    )
    for attachment in source.attachments:
        maintype, _, subtype = attachment.content_type.partition("/")
        message.add_attachment(
            attachment.data,
            maintype=maintype or "application",
            subtype=subtype or "pdf",
            filename=attachment.filename.replace(old_reference, reference),
        )
    return f"demo-{reference}.eml", message.as_bytes()


__all__ = ["DemoKind", "build_demo_email", "next_reference"]
