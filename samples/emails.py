"""Build the ``.eml`` files that stand in for SU's mail, using only the stdlib.

Part 2's trigger is an email with attachments, so the samples have to be real email files, not
a folder of PDFs with a naming convention. ``email.message.EmailMessage`` and
``email.policy.default`` do the whole job -- headers, MIME structure, base64 attachments -- so
this costs no new dependency, and the watcher parses these files with the same stdlib package
that wrote them.

Two things take deliberate care, both so a regenerated inbox is byte-identical to the last one:

- **Nothing comes from the clock or from randomness.** The Message-ID is a hash of a caller-
  supplied key (the shipment reference), not ``make_msgid()``, which mixes in the time and the
  process id. The Date header is passed in. And the MIME boundary is set explicitly, because
  the stdlib generator would otherwise invent one with ``random`` the first time it writes a
  multipart message -- which would make every regeneration a different file.
- **LF line endings.** A real message on the wire uses CRLF, and the stdlib parses either.
  ``.gitattributes`` marks the sample PDFs binary but says nothing about ``.eml``, so a CRLF
  file here would be at the mercy of git's line-ending conversion on checkout. LF is what git
  stores anyway, so writing LF means the file on disk is the file in the repository.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.policy import default as default_policy

# Singapore time, where both ACME and the sample subjects live. Fixed offset, no DST, no
# dependency on the host's timezone database.
SGT = timezone(timedelta(hours=8), "SGT")

SENT_AT_FORMAT = "%d %b %Y %H:%M"

# A domain reserved for documentation, so no sample can accidentally address a real mailbox.
MESSAGE_ID_DOMAIN = "mail.example.com"

EML_POLICY = default_policy.clone(linesep="\n")


@dataclass(frozen=True)
class Attachment:
    """One file on the message: the name it arrives under and its bytes."""

    filename: str
    data: bytes
    subtype: str = "pdf"  # the MIME subtype under "application/"


def parse_sent_at(sent_at: str) -> datetime:
    """A ``30 SEP 2026 11:48``-style string as an aware datetime in Singapore time."""
    return datetime.strptime(sent_at, SENT_AT_FORMAT).replace(tzinfo=SGT)


def message_id_for(key: str) -> str:
    """A stable Message-ID derived from `key`, usually the shipment reference.

    The watcher deduplicates on this header, so a sample's id has to survive regeneration: if
    it changed every run, re-running the generator would look like new mail arriving. A hash
    keeps it opaque and realistic without smuggling in the clock.
    """
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return f"<{digest}@{MESSAGE_ID_DOMAIN}>"


def _boundary_for(key: str) -> str:
    digest = hashlib.sha256(f"boundary:{key}".encode()).hexdigest()[:24]
    return f"==============={digest}=="


def build_eml(
    *,
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    sent_at: datetime,
    message_id: str,
    attachments: tuple[Attachment, ...] = (),
    boundary_key: str | None = None,
) -> bytes:
    """One ``.eml`` file as bytes.

    With attachments the result is a ``multipart/mixed`` message whose first part is the plain
    text body; without them it is a plain ``text/plain`` message, which is exactly what an
    email with nothing attached looks like and is one of the failure cases the trigger has to
    handle.
    """
    msg = EmailMessage(policy=EML_POLICY)
    msg["Message-ID"] = message_id
    msg["Date"] = sent_at
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)
    for attachment in attachments:
        msg.add_attachment(
            attachment.data,
            maintype="application",
            subtype=attachment.subtype,
            filename=attachment.filename,
        )
    if attachments:
        # Pin the boundary; left alone, the generator invents a random one at write time.
        msg.set_boundary(_boundary_for(boundary_key or message_id))
    return msg.as_bytes()
