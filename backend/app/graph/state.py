"""The shared graph state (design section 3.2) and the plain-data <-> domain-object
conversions each node needs.

Design section 3.2: "Each section is stored as plain data, so checkpoints save cleanly, and
is validated through its Pydantic model when written." LangGraph's checkpointer serialises
whatever's in the state dict, so nothing in here may hold a numpy array or an open file handle
-- a page's image is written to disk once (`prepare`) and referenced by path afterward; every
node that actually needs the pixels (`extract`) reloads them from that path.
"""

from __future__ import annotations

from typing import Any, TypedDict

import numpy as np
from PIL import Image

from app.agents.extract import ExtractionOutcome, FieldResult
from app.agents.schema import DocumentType
from app.agents.validate import FieldVerdict, ValidationOutcome
from app.ingest.pages import PageQuality, PreparedPage
from app.ingest.textlayer import TextSpan
from app.trust.grounding import GroundingResult
from app.trust.guardrails import Decision


class RunState(TypedDict, total=False):
    """Design section 3.2's table, one key per row. `total=False`: most keys don't exist yet
    when a run starts, and a node only ever writes its own section."""

    run_id: str
    customer_id: str
    file_path: str
    file_hash: str

    pages: list[dict[str, Any]]
    rules: dict[str, Any]
    extraction: dict[str, Any]
    validation: dict[str, Any]
    decision: dict[str, Any]

    llm_calls_used: int
    current_step: str
    error: str | None


# --- pages: PreparedPage <-> plain dict ---------------------------------------------------------


def page_to_dict(page: PreparedPage, *, image_path: str) -> dict[str, Any]:
    return {
        "index": page.index,
        "image_path": image_path,
        "source": page.source,
        "spans": [
            {"text": s.text, "box": list(s.box), "confidence": s.confidence} for s in page.spans
        ],
        "quality": {
            "avg_confidence": page.quality.avg_confidence,
            "low_confidence_share": page.quality.low_confidence_share,
        },
    }


def page_from_dict(data: dict[str, Any]) -> PreparedPage:
    """Reload a page's pixels from disk (`image_path`) and rebuild the `PreparedPage` the
    agents' functions expect. `prepare_seconds` isn't meaningful once reloaded, so it's 0.0 --
    that timing was already recorded once, at `prepare` time, in the page dict itself if
    needed later; the agents never read it."""
    with Image.open(data["image_path"]) as img:
        image = np.array(img.convert("RGB"))
    spans = [
        TextSpan(text=s["text"], box=tuple(s["box"]), confidence=s["confidence"])
        for s in data["spans"]
    ]
    quality = PageQuality(
        avg_confidence=data["quality"]["avg_confidence"],
        low_confidence_share=data["quality"]["low_confidence_share"],
    )
    return PreparedPage(
        index=data["index"], image=image, source=data["source"], spans=spans,
        quality=quality, prepare_seconds=0.0,
    )


# --- extraction: ExtractionOutcome <-> plain dict -----------------------------------------------


def extraction_to_dict(outcome: ExtractionOutcome) -> dict[str, Any]:
    return {
        "document_type": outcome.document_type,
        "retried_fields": list(outcome.retried_fields),
        "fields": {
            name: {
                "value": r.value,
                "source_text": r.source_text,
                "page": r.page,
                "self_rating": r.self_rating,
                "grounding": {
                    "status": r.grounding.status,
                    "box": list(r.grounding.box) if r.grounding.box else None,
                    "ocr_reading": r.grounding.ocr_reading,
                    "score": r.grounding.score,
                    "source_confidence": r.grounding.source_confidence,
                },
                "value_check_ok": r.value_check_ok,
                "format_check_ok": r.format_check_ok,
                "confidence": r.confidence,
                "retried": r.retried,
            }
            for name, r in outcome.fields.items()
        },
    }


def extraction_from_dict(data: dict[str, Any]) -> ExtractionOutcome:
    document_type: DocumentType = data["document_type"]
    fields: dict[str, FieldResult] = {}
    for name, r in data["fields"].items():
        g = r["grounding"]
        grounding = GroundingResult(
            status=g["status"],
            box=tuple(g["box"]) if g["box"] else None,
            ocr_reading=g["ocr_reading"],
            score=g["score"],
            source_confidence=g["source_confidence"],
        )
        fields[name] = FieldResult(
            field_name=name,
            value=r["value"],
            source_text=r["source_text"],
            page=r["page"],
            self_rating=r["self_rating"],
            grounding=grounding,
            value_check_ok=r["value_check_ok"],
            format_check_ok=r["format_check_ok"],
            confidence=r["confidence"],
            retried=r["retried"],
        )
    return ExtractionOutcome(
        document_type=document_type, fields=fields, retried_fields=tuple(data["retried_fields"])
    )


# --- validation: ValidationOutcome <-> plain dict -----------------------------------------------


def validation_to_dict(outcome: ValidationOutcome) -> dict[str, Any]:
    return {
        "judge_unavailable_fields": list(outcome.judge_unavailable_fields),
        "fields": {
            name: {
                "verdict": v.verdict,
                "found": v.found,
                "expected": v.expected,
                "rule_id": v.rule_id,
                "reason": v.reason,
                "verdict_confidence": v.verdict_confidence,
            }
            for name, v in outcome.fields.items()
        },
    }


def validation_fields_from_dict(data: dict[str, Any]) -> dict[str, FieldVerdict]:
    return {
        name: FieldVerdict(
            field_name=name,
            verdict=v["verdict"],
            found=v["found"],
            expected=v["expected"],
            rule_id=v["rule_id"],
            reason=v["reason"],
            verdict_confidence=v["verdict_confidence"],
        )
        for name, v in data["fields"].items()
    }


# --- decision: Decision <-> plain dict -----------------------------------------------------------


def decision_to_dict(decision: Decision) -> dict[str, Any]:
    return {
        "outcome": decision.outcome,
        "reasoning": decision.reasoning,
        "cited_fields": list(decision.cited_fields),
        "amendment_draft": decision.amendment_draft,
        "decision_source": decision.decision_source,
        "override_reason": decision.override_reason,
    }
