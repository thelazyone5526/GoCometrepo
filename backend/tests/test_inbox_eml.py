"""Tests for reading one `.eml` file (requirements 1.2, 1.6).

Every message here is built inline with the stdlib `email` package rather than read from
`samples/`, so these tests say exactly which header or part they are about and do not depend on
what the sample generator happens to produce.
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from app.inbox.eml import Attachment, EmlError, ParsedEmail, message_id_fallback, parse_eml

PDF_BYTES = b"%PDF-1.4 pretend invoice"
OTHER_PDF_BYTES = b"%PDF-1.4 pretend bill of lading"


def _message(
    *,
    subject: str = "ACME SHP-1042 documents",
    sender: str = "ops@acme.example",
    body: str | None = "Please verify the attached documents.",
    message_id: str | None = "<abc123@acme.example>",
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "cg@gocomet.example"
    msg["Subject"] = subject
    if message_id is not None:
        msg["Message-ID"] = message_id
    if body is not None:
        msg.set_content(body)
    return msg


def _attach(msg: EmailMessage, filename: str, data: bytes = PDF_BYTES) -> None:
    msg.add_attachment(data, maintype="application", subtype="pdf", filename=filename)


# --- The ordinary case ------------------------------------------------------------------------


def test_parses_simple_message_with_one_pdf_attachment() -> None:
    msg = _message()
    _attach(msg, "invoice.pdf")

    parsed = parse_eml(msg.as_bytes())

    assert isinstance(parsed, ParsedEmail)
    assert parsed.message_id == "abc123@acme.example"
    assert parsed.sender == "ops@acme.example"
    assert parsed.subject == "ACME SHP-1042 documents"
    assert "Please verify the attached documents." in parsed.body
    assert len(parsed.attachments) == 1
    attachment = parsed.attachments[0]
    assert isinstance(attachment, Attachment)
    assert attachment.filename == "invoice.pdf"
    assert attachment.data == PDF_BYTES
    assert attachment.content_type == "application/pdf"


def test_parses_multipart_message_with_two_attachments_in_order() -> None:
    msg = _message()
    _attach(msg, "invoice.pdf", PDF_BYTES)
    _attach(msg, "bol.pdf", OTHER_PDF_BYTES)

    parsed = parse_eml(msg.as_bytes())

    assert [a.filename for a in parsed.attachments] == ["invoice.pdf", "bol.pdf"]
    assert [a.data for a in parsed.attachments] == [PDF_BYTES, OTHER_PDF_BYTES]
    # The body part is never returned as an attachment.
    assert parsed.body.strip() == "Please verify the attached documents."


def test_message_with_no_attachments_parses_with_empty_tuple() -> None:
    parsed = parse_eml(_message().as_bytes())

    assert parsed.attachments == ()
    assert parsed.subject == "ACME SHP-1042 documents"


# --- Message-ID and the hash fallback (requirement 1.3) ---------------------------------------


def test_message_id_strips_angle_brackets_and_whitespace() -> None:
    parsed = parse_eml(_message(message_id="  <spaced@acme.example>  ").as_bytes())

    assert parsed.message_id == "spaced@acme.example"


def test_missing_message_id_falls_back_to_content_hash() -> None:
    data = _message(message_id=None).as_bytes()

    parsed = parse_eml(data)

    assert parsed.message_id.startswith("sha256:")
    assert parsed.message_id == message_id_fallback(data)


def test_blank_message_id_falls_back_to_content_hash() -> None:
    parsed = parse_eml(_message(message_id="   ").as_bytes())

    assert parsed.message_id.startswith("sha256:")


def test_fallback_is_stable_for_same_bytes_and_differs_for_other_bytes() -> None:
    first = _message(subject="ACME SHP-1042 documents", message_id=None).as_bytes()
    second = _message(subject="ACME SHP-1043 corrected", message_id=None).as_bytes()

    # Same bytes parsed twice: the dedup key must not move, or a restart would reprocess.
    assert parse_eml(first).message_id == parse_eml(first).message_id
    assert parse_eml(first).message_id != parse_eml(second).message_id


# --- Headers ----------------------------------------------------------------------------------


def test_rfc2047_encoded_word_subject_is_decoded() -> None:
    # "Rückfrage SHP-1042" as an encoded word, the form a non-ASCII subject arrives in.
    raw = (
        b"From: ops@acme.example\r\n"
        b"Subject: =?utf-8?q?R=C3=BCckfrage_SHP-1042?=\r\n"
        b"Message-ID: <enc@acme.example>\r\n"
        b"\r\n"
        b"body\r\n"
    )

    assert parse_eml(raw).subject == "Rückfrage SHP-1042"


def test_display_name_from_header_yields_bare_address() -> None:
    parsed = parse_eml(_message(sender="Jo Operator <jo@acme.example>").as_bytes())

    assert parsed.sender == "jo@acme.example"


def test_missing_subject_is_empty_string() -> None:
    raw = b"From: ops@acme.example\r\nMessage-ID: <nosub@acme.example>\r\n\r\nbody\r\n"

    assert parse_eml(raw).subject == ""


def test_unparseable_from_header_falls_back_to_raw_text() -> None:
    raw = (
        b"From: not an address at all\r\nSubject: hi\r\nMessage-ID: <bad@x>\r\n\r\nbody\r\n"
    )

    sender = parse_eml(raw).sender

    assert sender  # never empty, never a crash
    assert "not an address" in sender


# --- Bodies -----------------------------------------------------------------------------------


def test_html_only_body_is_kept_as_raw_html() -> None:
    msg = _message(body=None)
    msg.set_content("<p>Please verify</p>", subtype="html")

    body = parse_eml(msg.as_bytes()).body

    assert "<p>Please verify</p>" in body


def test_plain_part_is_preferred_over_html_alternative() -> None:
    msg = _message(body="plain version")
    msg.add_alternative("<p>html version</p>", subtype="html")

    assert parse_eml(msg.as_bytes()).body.strip() == "plain version"


def test_message_with_no_body_part_gives_empty_body() -> None:
    raw = b"From: ops@acme.example\r\nSubject: hi\r\nMessage-ID: <nobody@x>\r\n\r\n"

    assert parse_eml(raw).body.strip() == ""


# --- Attachments without a filename -----------------------------------------------------------


def test_attachment_without_filename_gets_synthetic_name() -> None:
    raw = (
        b"From: ops@acme.example\r\n"
        b"Subject: ACME SHP-1042\r\n"
        b"Message-ID: <nofn@acme.example>\r\n"
        b'Content-Type: multipart/mixed; boundary="BOUND"\r\n'
        b"\r\n"
        b"--BOUND\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"see attached\r\n"
        b"--BOUND\r\n"
        b"Content-Type: application/pdf\r\n"
        b"Content-Disposition: attachment\r\n"
        b"\r\n"
        b"%PDF-1.4 unnamed\r\n"
        b"--BOUND--\r\n"
    )

    parsed = parse_eml(raw)

    assert len(parsed.attachments) == 1
    assert parsed.attachments[0].filename == "attachment-1.pdf"
    assert parsed.attachments[0].content_type == "application/pdf"
    assert b"%PDF-1.4 unnamed" in parsed.attachments[0].data


def test_named_and_unnamed_attachments_are_both_kept() -> None:
    raw = (
        b"From: ops@acme.example\r\n"
        b"Subject: ACME SHP-1042\r\n"
        b"Message-ID: <mixed@acme.example>\r\n"
        b'Content-Type: multipart/mixed; boundary="BOUND"\r\n'
        b"\r\n"
        b"--BOUND\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"see attached\r\n"
        b"--BOUND\r\n"
        b'Content-Type: application/pdf; name="invoice.pdf"\r\n'
        b'Content-Disposition: attachment; filename="invoice.pdf"\r\n'
        b"\r\n"
        b"%PDF-1.4 invoice\r\n"
        b"--BOUND\r\n"
        b"Content-Type: application/octet-stream\r\n"
        b"Content-Disposition: attachment\r\n"
        b"\r\n"
        b"anonymous bytes\r\n"
        b"--BOUND--\r\n"
    )

    names = [a.filename for a in parse_eml(raw).attachments]

    assert names == ["invoice.pdf", "attachment-2.bin"]


def test_file_type_is_not_validated_here() -> None:
    """A `.docx` attachment still comes back: deciding what is acceptable is
    `app.ingest.files.check_upload`'s job, in intake."""
    msg = _message()
    msg.add_attachment(
        b"PK\x03\x04 not a pdf",
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="notes.docx",
    )

    parsed = parse_eml(msg.as_bytes())

    assert [a.filename for a in parsed.attachments] == ["notes.docx"]


# --- Failures (requirement 1.6) ---------------------------------------------------------------


def test_empty_input_raises_eml_error() -> None:
    with pytest.raises(EmlError):
        parse_eml(b"")


def test_whitespace_only_input_raises_eml_error() -> None:
    with pytest.raises(EmlError):
        parse_eml(b"   \r\n  \r\n")


def test_non_email_bytes_raise_eml_error() -> None:
    with pytest.raises(EmlError):
        parse_eml(b"this is just a sentence, not an email")


def test_binary_junk_raises_eml_error_with_plain_language_message() -> None:
    with pytest.raises(EmlError) as excinfo:
        parse_eml(b"\x00\x01\x02\x03\x04 binary junk")

    assert "email" in str(excinfo.value).lower()
