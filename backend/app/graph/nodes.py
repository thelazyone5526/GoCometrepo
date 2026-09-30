"""The graph's nodes (design section 3.1): `prepare`, `extract`, `validate`, `route`,
`persist`, `escalate`. Each node reads and writes only its own section of `RunState`
(design section 3.2), and returns just the keys it changed -- LangGraph merges that into the
running state.

Every node that can fail sets `state["error"]` instead of raising, so the graph's single
conditional edge (design section 3.1) can route to `escalate` uniformly, whatever went wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agents.extract import extract as run_extract
from app.agents.route import route as run_route
from app.agents.validate import validate as run_validate
from app.ingest.files import CheckedUpload, check_upload, store_upload, upload_dir
from app.ingest.pages import PreparedPage, prepare_pages
from app.llm.budget import CallBudget
from app.llm.client import LLMClient
from app.llm.recorder import CallRecorder
from app.rules.loader import load_rules_for_customer
from app.store import repository as repo

from .state import (
    decision_to_dict,
    extraction_from_dict,
    extraction_to_dict,
    page_from_dict,
    page_to_dict,
    validation_fields_from_dict,
    validation_to_dict,
)


def _save_page_images(pages: list[PreparedPage], *, hash_: str) -> list[dict[str, Any]]:
    folder = upload_dir(hash_) / "pages"
    folder.mkdir(parents=True, exist_ok=True)
    result = []
    for page in pages:
        path = folder / f"page-{page.index}.png"
        path.write_bytes(page.to_png())
        result.append(page_to_dict(page, image_path=str(path)))
    return result


def prepare_node(state: dict[str, Any]) -> dict[str, Any]:
    """Design section 3.1: "Nova stages 1-2: scope resolution and context compilation."
    Checks the upload, renders and OCRs every page, loads the customer's rules. Never expects
    to fail on a well-formed call (the API layer, Phase 9, is what turns a bad upload into an
    HTTP error before a run even starts) -- but any failure here is still caught and turned
    into `state["error"]`, since `check_upload`/`prepare_pages` can still raise on a corrupt
    file reaching this node some other way (e.g. the CLI, which skips the API's own checks).
    """
    try:
        data = Path(state["file_path"]).read_bytes()
        checked: CheckedUpload = check_upload(data)
        store_upload(checked)
        pages = prepare_pages(checked)
        pages_data = _save_page_images(pages, hash_=checked.file_hash)
        rules = load_rules_for_customer(state["customer_id"])
        return {
            "file_hash": checked.file_hash,
            "pages": pages_data,
            "rules": rules.model_dump(),
            "current_step": "extract",
        }
    except Exception as exc:  # noqa: BLE001 -- any failure here becomes a safe escalation
        return {"error": f"prepare failed: {exc}", "current_step": "prepare"}


def default_transport_factory() -> Any:
    """The real Gemini transport, built lazily so importing this module (and every test that
    imports it) never requires a Gemini API key to be configured."""
    from app.config import settings
    from app.llm.gemini_transport import GeminiTransport

    return GeminiTransport(api_key=settings.gemini_api_key)


# Overridable for tests (`tests/test_graph.py` swaps this for a `FakeTransport` factory so no
# node ever makes a real network call). Production code never needs to touch this.
transport_factory: Any = default_transport_factory


def _make_client(state: dict[str, Any], *, recorder: CallRecorder) -> tuple[LLMClient, CallBudget]:
    """Builds one `LLMClient` (and its `CallBudget`) per node call, continuing the run's
    running call count from `state["llm_calls_used"]` -- the budget is per-*document*, not
    per-node (design section 3.6), so each node has to pick up where the last one left off,
    not start over with a fresh allowance."""
    from app.config import settings

    budget = CallBudget()
    budget.calls_used = state.get("llm_calls_used", 0)
    client = LLMClient(
        transport=transport_factory(),
        primary_model=settings.gemini_model,
        fallback_model=settings.gemini_fallback_model,
        budget=budget,
        recorder=recorder,
        run_id=state["run_id"],
    )
    return client, budget


def extract_node(state: dict[str, Any], *, conn: Any, recorder: CallRecorder) -> dict[str, Any]:
    """Design section 3.1: Agent 1. The 300 DPI retry callback (design section 3.3 step 7,
    noted as a Phase 8 job in the Phase 5 handoff) closes over the original upload bytes,
    which this node -- unlike `extract()` itself -- does hold."""
    repo.update_run_step(conn, run_id=state["run_id"], step="extract")
    try:
        pages = [page_from_dict(p) for p in state["pages"]]
        client, budget = _make_client(state, recorder=recorder)

        def retry_at_higher_dpi() -> list[PreparedPage]:
            data = Path(state["file_path"]).read_bytes()
            checked = check_upload(data)
            return prepare_pages(checked, dpi=300)

        outcome = run_extract(
            pages=pages, client=client, retry_pages_at_higher_dpi=retry_at_higher_dpi
        )
        return {
            "extraction": extraction_to_dict(outcome),
            "llm_calls_used": budget.calls_used,
            "current_step": "validate",
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"extract failed: {exc}", "current_step": "extract"}


def validate_node(state: dict[str, Any], *, conn: Any, recorder: CallRecorder) -> dict[str, Any]:
    """Design section 3.1: Agent 2."""
    repo.update_run_step(conn, run_id=state["run_id"], step="validate")
    try:
        from app.rules.loader import RuleSet

        pages = [page_from_dict(p) for p in state["pages"]]
        extraction = extraction_from_dict(state["extraction"])
        rules = RuleSet.model_validate(state["rules"])
        client, budget = _make_client(state, recorder=recorder)

        outcome = run_validate(extraction=extraction, pages=pages, rules=rules, client=client)
        return {
            "validation": validation_to_dict(outcome),
            "llm_calls_used": budget.calls_used,
            "current_step": "route",
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"validate failed: {exc}", "current_step": "validate"}


def route_node(state: dict[str, Any], *, conn: Any, recorder: CallRecorder) -> dict[str, Any]:
    """Design section 3.1: Agent 3. Unlike `extract`/`validate`, this node never escalates on
    an LLM failure -- `route()` already falls back to a safe `human_review` decision on its
    own (design section 3.5/3.6), so there's nothing left here that would count as an error."""
    repo.update_run_step(conn, run_id=state["run_id"], step="route")
    fields = validation_fields_from_dict(state["validation"])
    client, budget = _make_client(state, recorder=recorder)
    decision = run_route(fields=fields, client=client)
    return {
        "decision": decision_to_dict(decision),
        "llm_calls_used": budget.calls_used,
        "current_step": "persist",
    }


def persist_node(state: dict[str, Any], *, conn: Any, recorder: CallRecorder) -> dict[str, Any]:
    """Writes the final result to `app.db` (design section 3.1) and marks the run completed.
    Every field's extraction and verdict is written as one `field_results` row, and every
    logged LLM call (design section 3.6) as one `llm_calls` row -- from the shared
    `CallRecorder`, not from `state`, since the recorder (not the checkpointed state) is what
    every node's `LLMClient` writes attempts into as the run goes."""
    run_id = state["run_id"]
    extraction = state["extraction"]
    validation = state["validation"]
    decision = state["decision"]

    from app.trust.fields import FIELD_NAMES

    for name in FIELD_NAMES:
        ext = extraction["fields"].get(name)
        val = validation["fields"].get(name)
        repo.insert_field_result(
            conn,
            run_id=run_id,
            field_name=name,
            value=ext["value"] if ext else None,
            source_text=ext["source_text"] if ext else None,
            page=ext["page"] if ext else None,
            box=tuple(ext["grounding"]["box"]) if ext and ext["grounding"]["box"] else None,
            model_rating=ext["self_rating"] if ext else None,
            grounding=ext["grounding"]["status"] if ext else None,
            ocr_reading=ext["grounding"]["ocr_reading"] if ext else None,
            format_ok=ext["format_check_ok"] if ext else None,
            extraction_confidence=ext["confidence"] if ext else None,
            verdict=val["verdict"] if val else None,
            found=val["found"] if val else None,
            expected=val["expected"] if val else None,
            rule_id=val["rule_id"] if val else None,
            verdict_reason=val["reason"] if val else None,
            verdict_confidence=val["verdict_confidence"] if val else None,
        )

    total_input_tokens = 0
    total_output_tokens = 0
    total_latency_ms = 0.0
    fallback_used = False
    for call in recorder.for_run(run_id):
        repo.insert_llm_call(
            conn,
            run_id=run_id,
            agent=call.agent,
            model=call.model,
            is_fallback=call.is_fallback,
            prompt_version=call.prompt_version,
            attempt=call.attempt,
            status=call.status,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            thinking_tokens=call.thinking_tokens,
            latency_ms=call.latency_ms,
            error=call.error,
        )
        total_input_tokens += call.input_tokens
        total_output_tokens += call.output_tokens
        total_latency_ms += call.latency_ms
        fallback_used = fallback_used or call.is_fallback

    repo.complete_run(
        conn,
        run_id=run_id,
        outcome=repo.to_stored_outcome(decision["outcome"]),
        reasoning=decision["reasoning"],
        decision_source=decision["decision_source"],
        override_reason=decision["override_reason"],
        amendment_draft=decision["amendment_draft"],
        llm_calls=state.get("llm_calls_used", 0),
        fallback_used=fallback_used or decision["decision_source"] == "fallback",
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        cost_usd=0.0,
        latency_ms=total_latency_ms,
    )
    return {"current_step": "done"}


def escalate_node(state: dict[str, Any], *, conn: Any, recorder: CallRecorder) -> dict[str, Any]:
    """Design section 3.1: "Human review with the reason." Reached whenever any node set
    `state["error"]`. Never fails itself -- there is nothing left to escalate to.

    Whatever LLM calls were actually made before the failure (e.g. a successful `extract`
    call, followed by a `validate` call that then failed) are still written to `llm_calls` --
    the call log's whole point (design section 3.6) is a complete record of every attempt,
    escalated run or not, so a real quota/cost report never has to treat escalations as a
    blind spot.
    """
    run_id = state["run_id"]
    for call in recorder.for_run(run_id):
        repo.insert_llm_call(
            conn,
            run_id=run_id,
            agent=call.agent,
            model=call.model,
            is_fallback=call.is_fallback,
            prompt_version=call.prompt_version,
            attempt=call.attempt,
            status=call.status,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            thinking_tokens=call.thinking_tokens,
            latency_ms=call.latency_ms,
            error=call.error,
        )
    repo.escalate_run(conn, run_id=run_id, reason=state.get("error") or "unknown error")
    return {"current_step": "escalated"}
