"""Checks on the Part 2 sample shipments: the two new document types and the inbox emails.

Three things are worth testing here and nothing else is:

1. The inbox regenerates to identical bytes. The end-to-end test and the demo both replay these
   files, so a sample that drifted between runs would turn a real failure into a coin toss.
2. Each ``.eml`` parses back with the same stdlib package that wrote it, and the headers the
   watcher depends on -- Message-ID, sender, subject -- survive the round trip.
3. The planted problems are the ones declared. Every assertion compares against the ground
   truth objects in ``samples.shipments``, or against ``rules/acme.yaml``, never against a
   string typed out here: a test that hardcodes "Zenith" would still pass if the bill of lading
   and the invoice silently agreed again.

Generating the whole sample set twice takes a few seconds, so both runs happen once per module
in a fixture and every test reads the result.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default as default_policy
from pathlib import Path

import pymupdf
import pytest
import yaml

from app.config import BACKEND_DIR
from samples.answers import (
    ShipmentAnswerFile,
    build_shipment_answer,
    load_shipment_answer,
    shipment_answer_path,
)
from samples.emails import message_id_for
from samples.generate import INBOX, SAMPLES_DIR, build_email, generate
from samples.shipments import (
    CG_INBOX,
    CLEAN_SHIPMENT,
    CROSS_CHECKED_FIELDS,
    MESSY_SHIPMENT,
    NO_ATTACHMENTS_EMAIL,
    SAMPLE_EMAILS,
    SAMPLE_SHIPMENTS,
    UNKNOWN_CUSTOMER_EMAIL,
    SampleEmail,
)

EXPECTED_EML_NAMES = (
    "01-clean-shipment.eml",
    "02-messy-shipment.eml",
    "03-unknown-customer.eml",
    "04-no-attachments.eml",
)

# Captured at collection time, before any test generates anything, so the "we did not touch the
# committed samples" test has something honest to compare against.
COMMITTED_SAMPLE_HASHES = {
    p.relative_to(SAMPLES_DIR).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
    for folder in ("grid", "submission", "jpg")
    for p in sorted((SAMPLES_DIR / folder).glob("*"))
    if p.is_file()
}


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, Path]]:
    """``samples.generate`` run twice, into two different folders."""
    first = tmp_path_factory.mktemp("samples-run-1")
    second = tmp_path_factory.mktemp("samples-run-2")
    generate(first)
    generate(second)
    yield first, second


@pytest.fixture(scope="module")
def inbox(generated: tuple[Path, Path]) -> Path:
    return generated[0] / INBOX


def _parse(eml: Path | bytes) -> EmailMessage:
    data = eml.read_bytes() if isinstance(eml, Path) else eml
    message = message_from_bytes(data, policy=default_policy)
    assert isinstance(message, EmailMessage)
    return message


def _pdf_attachments(message: EmailMessage) -> list[tuple[str, bytes]]:
    return [
        (part.get_filename() or "", part.get_payload(decode=True) or b"")
        for part in message.iter_attachments()
        if part.get_content_type() == "application/pdf"
    ]


def _pdf_text(data: bytes) -> str:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return " ".join("".join(page.get_text() for page in doc).split())


def _collapse(s: str) -> str:
    return " ".join(s.split())


def _acme_rules() -> dict:
    return yaml.safe_load((BACKEND_DIR / "rules" / "acme.yaml").read_text(encoding="utf-8"))


# --- What the generator writes ---------------------------------------------------------------


def test_generate_writes_the_four_emails_and_two_answer_files(inbox: Path) -> None:
    assert tuple(sorted(p.name for p in inbox.glob("*.eml"))) == EXPECTED_EML_NAMES
    assert tuple(sorted(p.name for p in inbox.glob("*.shipment.json"))) == (
        "01-clean-shipment.shipment.json",
        "02-messy-shipment.shipment.json",
    )
    # The two failure cases deliberately have no answer file: there is no outcome to expect.
    for sample in (UNKNOWN_CUSTOMER_EMAIL, NO_ATTACHMENTS_EMAIL):
        assert not shipment_answer_path(inbox / f"{sample.name}.eml").exists()


def test_the_generator_is_still_one_command_and_writes_nothing_else(generated) -> None:
    first, _ = generated
    assert sorted(p.name for p in first.iterdir()) == ["grid", "inbox", "jpg", "submission"]


def test_generating_twice_gives_byte_identical_output(generated) -> None:
    first, second = generated
    differing = [
        path.relative_to(first).as_posix()
        for path in sorted(first.rglob("*"))
        if path.is_file() and path.read_bytes() != (second / path.relative_to(first)).read_bytes()
    ]
    assert differing == []


def test_building_one_email_twice_is_repeatable() -> None:
    for sample in SAMPLE_EMAILS:
        first, second = build_email(sample), build_email(sample)
        assert first.eml == second.eml, sample.name
        assert first.answer == second.answer, sample.name


# --- The .eml round trip ---------------------------------------------------------------------


@pytest.mark.parametrize("sample", SAMPLE_EMAILS, ids=lambda s: s.name)
def test_eml_round_trips_through_the_stdlib(sample: SampleEmail, inbox: Path) -> None:
    built = build_email(sample)
    message = _parse(inbox / built.file)

    assert message["Message-ID"] == message_id_for(sample.reference or sample.name)
    assert message["From"] == sample.sender
    assert message["To"] == CG_INBOX
    assert message["Subject"] == sample.subject
    assert message["Date"] is not None and "2026" in message["Date"]

    attachments = _pdf_attachments(message)
    assert [name for name, _ in attachments] == list(sample.attachment_filenames)
    assert len(attachments) == len(sample.documents)

    body = message.get_body(preferencelist=("plain",))
    assert body is not None
    assert body.get_content() == sample.body


@pytest.mark.parametrize("sample", SAMPLE_EMAILS, ids=lambda s: s.name)
def test_subject_carries_the_customer_and_the_reference(sample: SampleEmail) -> None:
    named_customer = sample.customer_id or "globex"
    assert named_customer in sample.subject
    assert sample.reference is not None and sample.reference in sample.subject


def test_the_two_subjects_put_the_customer_and_reference_in_opposite_orders() -> None:
    """The subject parser has to cope with either order, so the samples exercise both."""
    clean, messy = CLEAN_SHIPMENT.subject, MESSY_SHIPMENT.subject
    assert clean.index("SHP-1042") < clean.index("acme")
    assert messy.index("acme") < messy.index("SHP-1043")


def test_attached_pdfs_are_text_layer_pages_showing_every_printed_value(inbox: Path) -> None:
    for sample in SAMPLE_SHIPMENTS:
        attachments = dict(_pdf_attachments(_parse(inbox / f"{sample.name}.eml")))
        for doc in sample.documents:
            text = _pdf_text(attachments[doc.filename])
            assert len(text) > 500, f"{doc.filename} has no usable text layer"
            for name, truth in doc.fields.as_dict().items():
                if truth.printed is not None:
                    assert _collapse(truth.printed) in text, f"{doc.filename}: {name}"


# --- The clean shipment ----------------------------------------------------------------------


def test_clean_shipment_has_one_of_each_document_type() -> None:
    assert [d.doc_type for d in CLEAN_SHIPMENT.documents] == [
        "commercial_invoice",
        "bill_of_lading",
        "packing_list",
    ]


@pytest.mark.parametrize("field_name", ["consignee", "hs_code", "gross_weight"])
def test_clean_shipment_documents_agree_on_the_cross_checked_fields(field_name: str) -> None:
    values = [getattr(d.fields, field_name).value for d in CLEAN_SHIPMENT.documents]
    assert len(values) == 3
    assert all(v is not None for v in values)
    assert all(v == values[0] for v in values), field_name
    assert CLEAN_SHIPMENT.cross_check(field_name)[0] == "consistent"


def test_clean_shipment_has_nothing_to_flag_and_is_approvable() -> None:
    assert CLEAN_SHIPMENT.discrepancy_fields == ()
    assert CLEAN_SHIPMENT.planted_errors == ()
    assert CLEAN_SHIPMENT.expected_outcome == "auto_approve"
    assert all(
        d.verdict(n) in ("match", "not_applicable")
        for d in CLEAN_SHIPMENT.documents
        for n in d.fields.as_dict()
    )


def test_clean_shipment_values_all_pass_acme_rules() -> None:
    """Agreement is not enough: the shared values also have to be inside ACME's rules, or the
    'clean' sample would not reach auto-approve for a reason the samples never showed."""
    rules = _acme_rules()["rules"]
    doc = CLEAN_SHIPMENT.documents[0]
    assert doc.fields.consignee.value == rules["consignee"]["registered"]
    assert doc.fields.hs_code.value[:6] in rules["hs_code"]["allowed"]
    assert doc.fields.port_of_loading.value in rules["port_of_loading"]["allowed"]
    assert doc.fields.port_of_discharge.value == rules["port_of_discharge"]["value"]
    assert doc.fields.incoterms.value == rules["incoterms"]["value"]
    assert doc.fields.gross_weight.value.unit == rules["gross_weight"]["unit"]


# --- The messy shipment ----------------------------------------------------------------------


def test_messy_shipment_bol_consignee_really_differs_from_the_invoice() -> None:
    invoice, bol, packing_list = MESSY_SHIPMENT.documents
    assert bol.doc_type == "bill_of_lading"
    assert bol.fields.consignee.value != invoice.fields.consignee.value
    # A different company, not a near-miss: no fuzzy match and no model judgement needed.
    assert bol.fields.consignee.value.split()[0] != invoice.fields.consignee.value.split()[0]
    assert packing_list.fields.consignee.value == invoice.fields.consignee.value
    assert MESSY_SHIPMENT.cross_check("consignee")[0] == "inconsistent"
    assert len(MESSY_SHIPMENT.cross_check("consignee")[1]) == 3


def test_messy_shipment_hs_code_is_not_on_acmes_approved_list() -> None:
    allowed = _acme_rules()["rules"]["hs_code"]["allowed"]
    for doc in MESSY_SHIPMENT.documents:
        if "hs_code" not in doc.expected_fields:
            continue
        value = doc.fields.hs_code.value
        assert value[:6] not in allowed, doc.filename
        assert doc.verdict("hs_code") == "mismatch"
    # Wrong everywhere, so it is a rule break rather than a disagreement between documents.
    assert MESSY_SHIPMENT.cross_check("hs_code")[0] == "consistent"


def test_messy_shipment_declares_exactly_two_problems() -> None:
    assert {e.code for e in MESSY_SHIPMENT.planted_errors} == {"E1", "X1"}
    assert MESSY_SHIPMENT.discrepancy_fields == ("consignee", "hs_code")
    assert MESSY_SHIPMENT.expected_outcome == "amendment_request"


def test_messy_shipment_agrees_with_the_clean_one_everywhere_else() -> None:
    for field_name in CROSS_CHECKED_FIELDS:
        if field_name == "consignee":
            continue
        assert MESSY_SHIPMENT.cross_check(field_name)[0] == "consistent", field_name


# --- The two failure cases -------------------------------------------------------------------


def test_unknown_customer_email_names_a_customer_with_no_rules_file(inbox: Path) -> None:
    rule_files = {p.stem for p in (BACKEND_DIR / "rules").glob("*.yaml")}
    assert "acme" in rule_files
    assert "globex" not in rule_files
    assert "globex" in UNKNOWN_CUSTOMER_EMAIL.subject
    assert UNKNOWN_CUSTOMER_EMAIL.customer_id is None
    assert UNKNOWN_CUSTOMER_EMAIL.expected_failure
    # Otherwise an ordinary email: one real attachment, so the only problem is the customer.
    message = _parse(inbox / f"{UNKNOWN_CUSTOMER_EMAIL.name}.eml")
    assert len(_pdf_attachments(message)) == 1


def test_no_attachment_email_has_a_body_and_no_attachments(inbox: Path) -> None:
    assert NO_ATTACHMENTS_EMAIL.documents == ()
    assert NO_ATTACHMENTS_EMAIL.expected_failure
    message = _parse(inbox / f"{NO_ATTACHMENTS_EMAIL.name}.eml")
    assert list(message.iter_attachments()) == []
    assert not message.is_multipart()
    body = message.get_body(preferencelist=("plain",))
    assert body is not None and len(body.get_content()) > 50


# --- The shipment answer files ---------------------------------------------------------------


@pytest.mark.parametrize("sample", SAMPLE_SHIPMENTS, ids=lambda s: s.name)
def test_answer_file_matches_the_ground_truth(sample: SampleEmail, inbox: Path) -> None:
    committed = load_shipment_answer(shipment_answer_path(inbox / f"{sample.name}.eml"))
    built = build_email(sample)
    assert committed == built.answer
    assert committed.discrepancy_count == len(sample.discrepancy_fields)
    assert committed.expected_outcome == sample.expected_outcome


def test_schema_refuses_to_approve_a_shipment_with_a_discrepancy(inbox: Path) -> None:
    path = shipment_answer_path(inbox / f"{MESSY_SHIPMENT.name}.eml")
    data = load_shipment_answer(path).model_dump()
    data["acceptable_outcomes"] = ["auto_approve", "amendment_request"]
    with pytest.raises(ValueError, match="amendment_request or human_review"):
        ShipmentAnswerFile.model_validate(data)


def test_schema_refuses_a_cross_check_that_calls_differing_values_consistent(inbox: Path) -> None:
    path = shipment_answer_path(inbox / f"{MESSY_SHIPMENT.name}.eml")
    data = load_shipment_answer(path).model_dump()
    data["cross_checks"][0]["result"] = "consistent"
    with pytest.raises(ValueError, match="compared values are not all equal"):
        ShipmentAnswerFile.model_validate(data)


def test_build_shipment_answer_refuses_a_failure_case() -> None:
    with pytest.raises(ValueError, match="failure case"):
        build_shipment_answer(
            NO_ATTACHMENTS_EMAIL, file="x.eml", message_id="<x@y>", recipient=CG_INBOX
        )


# --- The Part 1 samples are untouched --------------------------------------------------------


def test_the_committed_part_1_samples_still_exist() -> None:
    assert len(list((SAMPLES_DIR / "grid").glob("*.pdf"))) == 28
    assert len(list((SAMPLES_DIR / "submission").glob("*.pdf"))) == 3
    assert len(list((SAMPLES_DIR / "jpg").glob("*.jpg"))) == 1


def test_generating_elsewhere_does_not_touch_the_committed_samples(generated) -> None:
    """`generated` has already run the generator twice into temp folders by the time this runs."""
    now = {
        p.relative_to(SAMPLES_DIR).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for folder in ("grid", "submission", "jpg")
        for p in sorted((SAMPLES_DIR / folder).glob("*"))
        if p.is_file()
    }
    assert now == COMMITTED_SAMPLE_HASHES
