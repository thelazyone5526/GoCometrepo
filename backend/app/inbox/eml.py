"""Read one `.eml` file into the pieces the inbox trigger needs: who sent it, what the subject
says, the message body and the attachments (requirement 1.2).

Everything here uses the standard library `email` package, so Part 2 adds no new dependency for
its trigger. `email.policy.default` is what makes that cheap: it decodes RFC 2047 encoded-word
subjects, unfolds long headers and hands back `str` for text parts, so this module carries no
header-decoding code of its own.

Deliberately *not* done here:

* No file-type, size or page-count checking. An attachment is returned exactly as it was
  declared, bytes and content type included; `app.ingest.files.check_upload` is the one place
  that decides what is acceptable, and it runs later in intake. This module does not import
  `app.ingest` at all, so "what arrived" and "what we accept" stay separate questions.
* No HTML-to-text conversion. If the only body part is `text/html`, the raw html is returned as
  the body rather than passed through a converter this project would then have to trust.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from email.errors import MissingHeaderBodySeparatorDefect
from email.parser import BytesParser
from email.policy import default as default_policy
from email.utils import parseaddr

# Synthetic-filename suffixes for attachment parts that arrive without a filename. Anything not
# listed falls back to the content subtype itself, which is good enough for a name nobody parses.
_SUBTYPE_SUFFIX = {
    "octet-stream": "bin",
    "jpeg": "jpg",
    "plain": "txt",
}


class EmlError(Exception):
    """The bytes are not usable as an email at all.

    Raised instead of letting a stdlib parser error escape, so the watcher has one exception type
    to catch and can record the email as failed with a reason a person can read (requirement 1.6).
    """


@dataclass(frozen=True)
class Attachment:
    """One attached file, as declared by the sender. Unvalidated on purpose -- see module
    docstring."""

    filename: str
    data: bytes
    content_type: str


@dataclass(frozen=True)
class ParsedEmail:
    message_id: str
    sender: str
    subject: str
    body: str
    attachments: tuple[Attachment, ...]


def message_id_fallback(data: bytes) -> str:
    """The deduplication key for an email with no usable `Message-ID` header.

    `emails.message_id` is UNIQUE, and the watcher skips ids it has already recorded, so a
    restart mid-scan reprocesses nothing (requirement 1.3). An email with no `Message-ID` would
    break that guarantee, so it gets a key derived from its own bytes instead: the same file
    always hashes to the same id, and any different file to a different one.
    """
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _header_text(message, name: str) -> str:
    """One header as plain text, never raising.

    Address and date headers under `policy.default` are parsed objects, and a malformed one can
    make attribute access unhappy. A header we cannot read is treated as absent rather than as a
    reason to reject the whole email.
    """
    try:
        value = message.get(name)
    except Exception:
        return ""
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:  # pragma: no cover - defensive, a header that cannot even be stringified
        return ""


def _parse_message(data: bytes):
    """Parse the bytes, or raise `EmlError`.

    The stdlib parser is deliberately forgiving: hand it arbitrary text and it returns a message
    with no headers and a `MissingHeaderBodySeparatorDefect`, rather than failing. That is the
    wrong answer for a watched folder, where a file that is not an email should be reported as
    such, so both signals are checked here and turned into `EmlError`.
    """
    if not data or not data.strip():
        raise EmlError("File is empty, so there is no email to read")

    try:
        message = BytesParser(policy=default_policy).parsebytes(data)
    except Exception as exc:  # the parser rarely raises, but an EmlError is what callers handle
        raise EmlError(f"File could not be read as an email: {exc}") from exc

    if any(isinstance(d, MissingHeaderBodySeparatorDefect) for d in message.defects):
        raise EmlError("File has no email headers, so it is not an email")
    if not message.keys():
        raise EmlError("File has no email headers, so it is not an email")
    return message


def _body_part(message):
    """The part whose text is the body, or None.

    `get_body` already encodes the rule wanted here -- first `text/plain`, else `text/html`, and
    never a part marked as an attachment -- so there is no hand-rolled walk for it.
    """
    try:
        return message.get_body(preferencelist=("plain", "html"))
    except Exception:  # pragma: no cover - defensive against a malformed multipart structure
        return None


def _body_text(part) -> str:
    if part is None:
        return ""
    try:
        content = part.get_content()
    except Exception:
        payload = part.get_payload(decode=True)
        if not payload:
            return ""
        content = payload.decode("utf-8", errors="replace")
    return content if isinstance(content, str) else ""


def _part_bytes(part) -> bytes:
    """The attachment's decoded bytes. Falls back to encoding its text, so a text/plain
    attachment is still returned rather than silently dropped."""
    try:
        payload = part.get_payload(decode=True)
    except Exception:
        payload = None
    if payload is not None:
        return payload
    try:
        content = part.get_content()
    except Exception:
        return b""
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8", errors="replace")
    return b""


def _synthetic_filename(part, index: int) -> str:
    """A stable name for an attachment that arrived without one.

    Dropping such a part would mean a document silently never reaching the pipeline, which is the
    one failure mode this project refuses (requirement 13.1). A made-up name is recorded in
    `shipment_attachments` like any other, so the operator sees what arrived.
    """
    subtype = (part.get_content_subtype() or "bin").lower()
    suffix = _SUBTYPE_SUFFIX.get(subtype, subtype)
    return f"attachment-{index}.{suffix}"


def _attachments(message, body_part) -> tuple[Attachment, ...]:
    """Every part that is a file rather than the message text.

    A part counts as an attachment when it has a filename, or when it declares
    `Content-Disposition: attachment`. The part chosen as the body is skipped unless it carries a
    filename of its own, in which case the sender meant it as a file.
    """
    found: list[Attachment] = []
    for part in message.walk():
        if part.is_multipart():
            continue

        try:
            filename = part.get_filename()
        except Exception:
            filename = None
        try:
            disposition = part.get_content_disposition()
        except Exception:
            disposition = None

        if part is body_part and not filename:
            continue
        if not filename and disposition != "attachment":
            continue

        name = filename.strip() if filename and filename.strip() else ""
        if not name:
            name = _synthetic_filename(part, len(found) + 1)
        found.append(
            Attachment(filename=name, data=_part_bytes(part), content_type=part.get_content_type())
        )
    return tuple(found)


def parse_eml(data: bytes) -> ParsedEmail:
    """One `.eml` file's bytes -> the fields the inbox trigger records (requirement 1.2).

    Raises `EmlError` when the bytes are not an email. Anything milder -- a missing subject, an
    unreadable `From`, an attachment with no filename -- degrades to a sensible value instead,
    because the watcher's job is to record what arrived, including the ugly cases, and let the
    shipment layer decide what to do about it (requirements 1.5, 1.6).
    """
    message = _parse_message(data)

    raw_message_id = _header_text(message, "Message-ID").strip().strip("<>").strip()
    message_id = raw_message_id or message_id_fallback(data)

    raw_from = _header_text(message, "From").strip()
    # parseaddr pulls `jo@x.com` out of `Jo <jo@x.com>`; on a header it cannot make sense of it
    # returns an empty address, and the raw text is more useful to an operator than nothing.
    try:
        address = parseaddr(raw_from)[1].strip()
    except Exception:  # pragma: no cover - parseaddr is total, but the raw header is the fallback
        address = ""
    sender = address or raw_from

    subject = _header_text(message, "Subject").strip()

    body_part = _body_part(message)
    return ParsedEmail(
        message_id=message_id,
        sender=sender,
        subject=subject,
        body=_body_text(body_part),
        attachments=_attachments(message, body_part),
    )


__all__ = [
    "Attachment",
    "EmlError",
    "ParsedEmail",
    "message_id_fallback",
    "parse_eml",
]
