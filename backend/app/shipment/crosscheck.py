"""Cross-document validation (design section 3, requirements 3.1-3.6, 15.3): compares the
four fields that two document types in a shipment's effective set can both carry --
consignee, hs_code, gross_weight, invoice_number -- and settles one of `consistent`,
`inconsistent`, `uncertain` or `not_applicable` per field, in plain code, with zero model
calls (R15.3; see the no-`app.llm`-import test).

Input shape: `DocumentFieldValue`, one row per (document, field) pair that was *extracted*
for that document -- including a row with `value=None` for a field the Extractor looked for
but didn't find, which is different from the field never being asked for at all (a document
whose doc_type doesn't list the field in `expected_fields_by_doc_type` simply has no row, or
an un-carrying row that this module ignores). The caller (a later task, wiring this to
`field_results`) is expected to emit one row per `field_results` entry it has for a document
that is part of the effective set.

Point 4 of the task asks for a way to tell "missing because the Extractor/Validator was itself
uncertain about this field" apart from "missing because the field is genuinely absent or wrong
on this document". There's no verdict or confidence in the row shape above, so `uncertain: bool
= False` is added to `DocumentFieldValue` for exactly that purpose: the caller sets it from
Part 1's own verdict (`needs_judgement`/`uncertain`) before calling in. A `None` value with
`uncertain=False` is a real absence and makes the field `inconsistent`; a `None` value (or a
value present but flagged) with `uncertain=True` makes the field `uncertain` instead, per point
6 -- never silently promoted to `consistent` even when every other carrier agrees.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from typing import Literal

from app.rules.checkers import entity_name_key
from app.trust.normalise import normalise_hs_code, normalise_weight, text_comparison_key

CrossCheckVerdict = Literal["consistent", "inconsistent", "uncertain", "not_applicable"]

# design section 2: the four fields two document types in `rules/acme.yaml` both carry.
CHECKED_FIELDS: tuple[str, ...] = ("consignee", "hs_code", "gross_weight", "invoice_number")


@dataclass(frozen=True)
class DocumentFieldValue:
    """One (document, field) extraction result from the effective set. `value` is the raw
    string Part 1's `field_results.value` stores, or `None` if the field was looked for and
    not found. `uncertain` is set by the caller from Part 1's own verdict for this field
    (`needs_judgement`/uncertain-style outcomes), distinguishing "missing/unclear because the
    extraction itself wasn't sure" from "missing because the document genuinely lacks or
    contradicts the field" -- see the module docstring."""

    document_id: str
    filename: str
    doc_type: str | None
    field_name: str
    value: str | None
    source_text: str | None = None
    page: int | None = None
    field_result_id: str | None = None
    uncertain: bool = False


@dataclass(frozen=True)
class CrossCheckResult:
    field_name: str
    result: CrossCheckVerdict
    reason: str | None
    compared: list[dict] = field(default_factory=list)


# field_name -> normaliser, mirroring app.trust.normalise.NORMALISERS except consignee, which
# uses the same tolerant entity_name_key the Validator's entity_name rule compares with (so
# "Pte Ltd" vs "Private Limited" cross-checks the same way it validates), not the generic
# text_comparison_key every other text field gets.
_FIELD_NORMALISERS: dict[str, object] = {
    "consignee": entity_name_key,
    "hs_code": normalise_hs_code,
    "invoice_number": text_comparison_key,
}


def _normalise(field_name: str, value: str) -> object | None:
    if field_name == "gross_weight":
        return normalise_weight(value)
    normaliser = _FIELD_NORMALISERS[field_name]
    return normaliser(value)


def _carries(doc_type: str | None, field_name: str, expected: dict[str, frozenset[str]]) -> bool:
    if doc_type is None:
        return False
    return field_name in expected.get(doc_type, frozenset())


def _sort_key(entry: DocumentFieldValue) -> tuple[str, str, str]:
    # R3.1/R3.6: sorted before any comparison so shuffling the input never changes the
    # outcome or the order of `compared`.
    return (entry.doc_type or "", entry.filename, entry.document_id)


def _json_safe(normalised: object | None) -> object | None:
    """`compared` is stored as JSON (`store/repository.py::upsert_cross_check_result`), but
    `normalise_weight` returns a `Weight` dataclass, not a JSON-native type -- this is the one
    place that difference has to be papered over, so every other normaliser (which already
    returns a plain `str`) is untouched."""
    if is_dataclass(normalised) and not isinstance(normalised, type):
        return {f.name: getattr(normalised, f.name) for f in fields(normalised)}
    return normalised


def _compared_row(entry: DocumentFieldValue, normalised: object | None) -> dict:
    return {
        "document_id": entry.document_id,
        "filename": entry.filename,
        "doc_type": entry.doc_type,
        "value": entry.value,
        "normalised": _json_safe(normalised),
        "source_text": entry.source_text,
        "page": entry.page,
        "field_result_id": entry.field_result_id,
    }


def _check_field(field_name: str, carriers: list[DocumentFieldValue]) -> CrossCheckResult:
    if len(carriers) < 2:
        # R3.5: fewer than two carriers, nothing to compare.
        return CrossCheckResult(field_name=field_name, result="not_applicable", reason=None)

    carriers = sorted(carriers, key=_sort_key)

    normalised_by_doc: dict[str, object | None] = {}
    compared: list[dict] = []
    missing: list[DocumentFieldValue] = []
    uncertain_entries: list[DocumentFieldValue] = []

    for entry in carriers:
        normalised: object | None = None
        if entry.value is not None:
            normalised = _normalise(field_name, entry.value)
        normalised_by_doc[entry.document_id] = normalised
        compared.append(_compared_row(entry, normalised))

        if entry.value is None or normalised is None:
            if entry.uncertain:
                uncertain_entries.append(entry)
            else:
                missing.append(entry)
        elif entry.uncertain:
            uncertain_entries.append(entry)

    if missing:
        # R3.4: a carrying document with no parseable value is an immediate mismatch, named.
        names = ", ".join(f"{m.filename} ({m.field_name})" for m in missing)
        return CrossCheckResult(
            field_name=field_name,
            result="inconsistent",
            reason=f"no usable value for {field_name} on: {names}",
            compared=compared,
        )

    # Compare the values of entries that are NOT flagged uncertain -- an uncertain entry's
    # own value never participates in a mismatch/match decision, only in the uncertain check
    # below (R3.3: uncertain is never silently promoted to consistent, nor does it force a
    # mismatch just because the comparison skipped it).
    settled = [e for e in carriers if not e.uncertain]
    settled_values = {normalised_by_doc[e.document_id] for e in settled}

    if len(settled_values) > 1:
        # R3.2: two or more settled documents disagree.
        details = ", ".join(f"{e.filename}={normalised_by_doc[e.document_id]!r}" for e in settled)
        return CrossCheckResult(
            field_name=field_name,
            result="inconsistent",
            reason=f"{field_name} differs across documents: {details}",
            compared=compared,
        )

    if uncertain_entries:
        # R3.3: at least one carrier uncertain and no two other values differ -> uncertain.
        names = ", ".join(e.filename for e in uncertain_entries)
        return CrossCheckResult(
            field_name=field_name,
            result="uncertain",
            reason=f"{field_name} is unresolved (uncertain) on: {names}",
            compared=compared,
        )

    return CrossCheckResult(
        field_name=field_name, result="consistent", reason="matches", compared=compared
    )


def cross_check(
    entries: list[DocumentFieldValue],
    expected_fields_by_doc_type: dict[str, frozenset[str]],
) -> dict[str, CrossCheckResult]:
    """Compare each of `CHECKED_FIELDS` across every document in `entries` whose `doc_type`
    carries that field (per `expected_fields_by_doc_type`), independently of the others.
    Pure function, zero model calls (R15.3): no I/O, no `app.llm` import anywhere in this
    module (see `test_crosscheck.py`'s static check)."""
    results: dict[str, CrossCheckResult] = {}
    for field_name in CHECKED_FIELDS:
        carriers = [
            entry
            for entry in entries
            if entry.field_name == field_name
            and _carries(entry.doc_type, field_name, expected_fields_by_doc_type)
        ]
        results[field_name] = _check_field(field_name, carriers)
    return results
