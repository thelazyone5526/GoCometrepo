"""Tests for the subject parser (requirements 1.4, 1.5, 5.1).

`parse_subject` is pure, so these are a table of strings: no files, no database, no rules
loading. The known customer ids are passed in exactly as a caller would after loading them.
"""

from __future__ import annotations

from app.inbox.subject import (
    SHIPMENT_REFERENCE_PATTERN,
    SubjectParts,
    find_customer_id,
    find_shipment_reference,
    parse_subject,
)

KNOWN = ["acme", "globex"]


# --- Order independence (requirement 1.4) -----------------------------------------------------


def test_reference_before_customer_parses_fully() -> None:
    parts = parse_subject("SHP-1042 acme - docs", KNOWN)

    assert parts == SubjectParts(customer_id="acme", reference="SHP-1042")


def test_customer_before_reference_parses_fully() -> None:
    parts = parse_subject("acme SHP-1043 corrected", KNOWN)

    assert parts == SubjectParts(customer_id="acme", reference="SHP-1043")


def test_tokens_buried_in_prose_still_parse() -> None:
    parts = parse_subject("Re: FW: documents for acme, shipment SHP-77 (urgent)", KNOWN)

    assert parts == SubjectParts(customer_id="acme", reference="SHP-77")


# --- The reference (requirement 5.1) ----------------------------------------------------------


def test_first_reference_wins_when_two_are_present() -> None:
    assert find_shipment_reference("SHP-1042 and also SHP-9999") == "SHP-1042"
    assert parse_subject("Re: SHP-1042 / superseded SHP-9999", KNOWN).reference == "SHP-1042"


def test_lower_case_reference_is_normalised_to_upper() -> None:
    assert find_shipment_reference("corrected invoice shp-1042") == "SHP-1042"
    assert parse_subject("shp-1042 acme", KNOWN).reference == "SHP-1042"


def test_mixed_case_reference_is_normalised_to_upper() -> None:
    assert find_shipment_reference("Shp-1042") == "SHP-1042"


def test_no_reference_found() -> None:
    assert find_shipment_reference("acme documents attached") is None
    assert parse_subject("acme documents attached", KNOWN).reference is None


def test_reference_needs_at_least_one_digit() -> None:
    assert find_shipment_reference("SHP- pending") is None
    assert find_shipment_reference("SHP-") is None


def test_reference_pattern_is_exported_and_case_insensitive() -> None:
    assert SHIPMENT_REFERENCE_PATTERN.search("shp-1") is not None
    assert SHIPMENT_REFERENCE_PATTERN.search("SHP-1") is not None


# --- The customer id (requirement 1.4) --------------------------------------------------------


def test_customer_matching_is_case_insensitive_and_returns_canonical_id() -> None:
    assert find_customer_id("ACME SHP-1042", KNOWN) == "acme"
    assert find_customer_id("Acme SHP-1042", KNOWN) == "acme"
    assert find_customer_id("aCmE SHP-1042", KNOWN) == "acme"


def test_customer_id_is_returned_as_supplied_not_as_written() -> None:
    parts = parse_subject("ACME SHP-1042", ["AcMe"])

    assert parts.customer_id == "AcMe"


def test_substring_does_not_match_on_word_boundary() -> None:
    assert find_customer_id("acmecorp SHP-1042", KNOWN) is None
    assert find_customer_id("theacme SHP-1042", KNOWN) is None
    assert parse_subject("acmecorp SHP-1042", KNOWN) == SubjectParts(
        customer_id=None, reference="SHP-1042"
    )


def test_punctuation_counts_as_a_boundary() -> None:
    assert find_customer_id("[acme] SHP-1042", KNOWN) == "acme"
    assert find_customer_id("docs for acme.", KNOWN) == "acme"


def test_unknown_customer_is_not_invented() -> None:
    assert find_customer_id("initech SHP-1042", KNOWN) is None
    assert parse_subject("initech SHP-1042", KNOWN).customer_id is None


def test_no_customer_found_in_empty_known_list() -> None:
    assert find_customer_id("acme SHP-1042", []) is None


def test_earliest_match_wins_when_two_customers_appear() -> None:
    assert find_customer_id("globex forwarding for acme", KNOWN) == "globex"
    assert find_customer_id("acme via globex", KNOWN) == "acme"


def test_longer_id_wins_at_the_same_position() -> None:
    assert find_customer_id("acme-corp SHP-1", ["acme", "acme-corp"]) == "acme-corp"
    assert find_customer_id("acme-corp SHP-1", ["acme-corp", "acme"]) == "acme-corp"


def test_blank_known_ids_are_ignored() -> None:
    assert find_customer_id("acme SHP-1042", ["", "   ", "acme"]) == "acme"


# --- Never raises (requirement 1.5: a strange subject is data, not an error) -------------------


def test_empty_whitespace_and_punctuation_subjects_return_nothing() -> None:
    for subject in ["", "   ", "\t\n ", "---", "!!! ??? ...", "()"]:
        assert parse_subject(subject, KNOWN) == SubjectParts(customer_id=None, reference=None)
        assert find_customer_id(subject, KNOWN) is None
        assert find_shipment_reference(subject) is None


def test_very_long_subject_does_not_raise() -> None:
    subject = ("re: " * 5000) + "acme SHP-1042"

    assert parse_subject(subject, KNOWN) == SubjectParts(customer_id="acme", reference="SHP-1042")


def test_regex_special_characters_in_subject_and_in_known_ids_do_not_raise() -> None:
    assert find_customer_id(r"a.*c[me] (SHP-1)", KNOWN) is None
    # A customer id containing regex metacharacters is escaped, not compiled as a pattern.
    assert find_customer_id("docs for a.c+me", ["a.c+me"]) == "a.c+me"


def test_known_ids_may_be_any_iterable() -> None:
    assert find_customer_id("acme SHP-1", iter(["acme"])) == "acme"
    assert parse_subject("acme SHP-1", {"acme", "globex"}).customer_id == "acme"
