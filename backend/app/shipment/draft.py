"""The draft generator (design section 2 and 5, requirements 6.1-6.6, 15.3): fills an approval
or amendment template to SU from stored results only, zero model calls.

Why a template and not a model call, even though the Part 1 Router already drafts amendment
text per document: the shipment layer sits a level above individual documents, and
D5/decision-session note in `docs/part2-decisions.md` records the actual choice -- one
deterministic reply per shipment beats three model-written per-document drafts, and it is
free. A render failure (missing fields on the inputs, for example) must never crash the
pipeline or leave the operator with nothing (R6.6): `render_draft` always returns a
`RenderedDraft`, falling back to `kind="empty"` with `render_error` set.

No `app.llm` import anywhere in this module (R15.3; see `test_draft.py`'s static check,
mirroring `test_crosscheck.py`'s and `test_decision.py`'s).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.shipment.crosscheck import CrossCheckResult
from app.shipment.decision import (
    CrossCheckDiscrepancy,
    Discrepancy,
    DocumentEntry,
    FieldMismatch,
    ShipmentOutcome,
    UncertainCrossCheck,
    UncertainField,
    UncertainItem,
    collect_shipment_items,
)

DraftKind = Literal["approval", "amendment", "empty"]


@dataclass(frozen=True)
class RenderedDraft:
    kind: DraftKind
    recipient: str
    subject: str
    body: str
    uncertain_items: tuple[str, ...]
    render_error: str | None = None


def _uncertain_item_text(item: UncertainItem) -> str:
    """R6.4: "needs your check" text for the operator -- never sent to SU (see `render_draft`'s
    docstring)."""
    if isinstance(item, UncertainField):
        return f"{item.document_filename}: {item.field_name} is uncertain"
    if isinstance(item, UncertainCrossCheck):
        return f"cross-document check for {item.field_name} is unresolved (uncertain)"
    # UncertainDocType: the only remaining case.
    return f"{item.document_filename}'s document type could not be determined"


def _discrepancy_lines(discrepancies: tuple[Discrepancy, ...]) -> list[str]:
    """R6.2: each discrepancy as its own entry, with document name and field for a per-document
    mismatch, and the value found in each document for a cross-document one."""
    lines: list[str] = []
    for d in discrepancies:
        if isinstance(d, FieldMismatch):
            lines.append(
                f"- {d.document_filename}: {d.field_name} -- found {d.found!r}, "
                f"expected {d.expected!r}"
            )
        else:
            assert isinstance(d, CrossCheckDiscrepancy)
            values = ", ".join(
                f"{row.get('filename')}: {row.get('value')!r}" for row in d.compared
            )
            lines.append(f"- {d.field_name} (across documents): {values}")
    return lines


def _subject_for(kind: DraftKind, original_subject: str | None) -> str:
    base = original_subject or "your shipment"
    if kind == "approval":
        return f"Re: {base} -- approved"
    if kind == "amendment":
        return f"Re: {base} -- amendment requested"
    return f"Re: {base}"


def _render_approval(recipient: str, subject: str | None) -> RenderedDraft:
    body = (
        "Hello,\n\n"
        "Thank you for your submission. We have reviewed the documents for this shipment and "
        "everything matches our records. This shipment is approved.\n\n"
        "Best regards,\nACME Trade Documentation Team"
    )
    return RenderedDraft(
        kind="approval",
        recipient=recipient,
        subject=_subject_for("approval", subject),
        body=body,
        uncertain_items=(),
    )


def _render_amendment(
    recipient: str, subject: str | None, discrepancies: tuple[Discrepancy, ...]
) -> RenderedDraft:
    lines = _discrepancy_lines(discrepancies)
    body = (
        "Hello,\n\n"
        "Thank you for your submission. We found the following issues that need to be "
        "corrected before we can approve this shipment:\n\n"
        + "\n".join(lines)
        + "\n\nPlease send corrected documents at your earliest convenience.\n\n"
        "Best regards,\nACME Trade Documentation Team"
    )
    return RenderedDraft(
        kind="amendment",
        recipient=recipient,
        subject=_subject_for("amendment", subject),
        body=body,
        uncertain_items=(),
    )


def _render_empty(recipient: str, subject: str | None, render_error: str | None) -> RenderedDraft:
    return RenderedDraft(
        kind="empty",
        recipient=recipient or "",
        subject=_subject_for("empty", subject),
        body="",
        uncertain_items=(),
        render_error=render_error,
    )


def render_draft(
    *,
    outcome: ShipmentOutcome,
    sender: str | None,
    subject: str | None,
    documents: list[DocumentEntry],
    cross_checks: dict[str, CrossCheckResult],
) -> RenderedDraft:
    """R6.1-R6.6: fill a Draft from stored values only. `sender` becomes the recipient (R6.1:
    "addressed to the Email sender"); `subject` is the original Email subject, carried into the
    reply's own subject.

    Uncertain items (R6.4) are always returned separately in `uncertain_items`, for the
    operator's "needs your check" section, and never appear in `body` -- not even for an
    amendment draft, since an uncertain field is not a confirmed discrepancy and must not be
    put in front of SU as though it were one.
    """
    items = collect_shipment_items(documents, cross_checks)
    uncertain_texts = tuple(_uncertain_item_text(item) for item in items.uncertain_items)

    if sender is None or not sender.strip():
        draft = _render_empty(
            sender or "", subject, "no sender address on the email to address the reply to"
        )
        return RenderedDraft(
            kind=draft.kind,
            recipient=draft.recipient,
            subject=draft.subject,
            body=draft.body,
            uncertain_items=uncertain_texts,
            render_error=draft.render_error,
        )

    try:
        if outcome == "auto_approve":
            draft = _render_approval(sender, subject)
        elif items.discrepancies:
            # R6.3: amendment_request always has discrepancies by construction (decision.py);
            # human_review generates an amendment draft too, if a discrepancy exists.
            draft = _render_amendment(sender, subject, items.discrepancies)
        else:
            # R6.3: human_review with no discrepancy (for example an uncertain-only shipment)
            # gets an empty editable draft, not a template with nothing to say.
            draft = _render_empty(sender, subject, None)
    except Exception as exc:  # noqa: BLE001 - R6.6: a render failure must never propagate.
        draft = _render_empty(sender, subject, f"draft rendering failed: {exc}")

    return RenderedDraft(
        kind=draft.kind,
        recipient=draft.recipient,
        subject=draft.subject,
        body=draft.body,
        uncertain_items=uncertain_texts,
        render_error=draft.render_error,
    )


__all__ = ["DraftKind", "RenderedDraft", "render_draft"]
