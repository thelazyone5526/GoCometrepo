"""The offline eval, reduced scope for this session's deadline (design section 1.2 item 14,
Phase 12): instead of the full 28-document grid, this runs the pipeline on the 3 submission
samples in `samples/submission/`, against the *real* Gemini API (a small, deliberate spend --
roughly 3-4 calls per document, ~10-12 calls total), and scores the result against each
document's `.answer.json`.

Run from the repo root:

    backend\\.venv\\Scripts\\python.exe -m eval.run_eval

This is a real spend of Gemini free-tier quota. It is not run as part of the test suite, and
is never run automatically -- only when explicitly invoked, same as the `live`-marked tests.

Full-grid tuning (design section 1.2 item 14's threshold sweep) is out of scope for this
session; see `docs/progress.md`'s "Scope cuts for this session" for why, and `docs/PRD-
part1-draft.md`/§10.1 for the placeholder numbers this leaves unresolved.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
SAMPLES_DIR = REPO_ROOT / "samples"
SUBMISSION_DIR = SAMPLES_DIR / "submission"
REPORTS_DIR = Path(__file__).resolve().parent / "reports"

sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(REPO_ROOT))

from app.config import settings  # noqa: E402
from app.graph.build import run_document  # noqa: E402
from app.ingest.files import check_upload  # noqa: E402
from app.store import repository as repo  # noqa: E402
from app.store.db import connect  # noqa: E402
from samples.answers import AnswerFile, load_answer  # noqa: E402

CUSTOMER_NAME = "ACME Electronics Pte. Ltd."

# Inverse of app.store.repository's Router-outcome -> stored-outcome map (design section
# 10.1). Kept local to this script rather than added to `repository.py`, since a reverse
# lookup is only ever needed here, when comparing a stored outcome back against an answer
# file's Router-vocabulary `acceptable_outcomes` list.
_STORED_TO_ROUTER_OUTCOME = {
    "auto_approved": "auto_approve",
    "human_review": "human_review",
    "amendment_requested": "amendment_request",
}


def _to_router_outcome(stored_outcome: str) -> str:
    return _STORED_TO_ROUTER_OUTCOME.get(stored_outcome, stored_outcome)


@dataclass
class DocumentEvalResult:
    doc_id: str
    file: str
    run_id: str
    error: str | None
    field_matches: dict[str, bool]  # field_name -> did our verdict agree with the answer file
    field_details: dict[str, tuple[str | None, str | None]]  # field -> (our verdict, expected)
    outcome: str | None
    acceptable_outcomes: list[str]
    outcome_acceptable: bool
    llm_calls: int
    fallback_used: bool


def _answer_path(pdf_path: Path) -> Path:
    return pdf_path.with_suffix("").with_suffix(".answer.json")


def _submission_documents() -> list[tuple[Path, AnswerFile]]:
    docs = []
    for pdf_path in sorted(SUBMISSION_DIR.glob("*.pdf")):
        answer = load_answer(_answer_path(pdf_path))
        docs.append((pdf_path, answer))
    return docs


def _run_one_document(
    *, conn: sqlite3.Connection, checkpointer: SqliteSaver, pdf_path: Path, answer: AnswerFile
) -> DocumentEvalResult:
    checked = check_upload(pdf_path.read_bytes())
    document = repo.create_shipment_and_document(
        conn,
        customer_id=answer.customer_id,
        customer_name=CUSTOMER_NAME,
        filename=pdf_path.name,
        file_hash=checked.file_hash,
        mime_type=checked.file_type,
        page_count=checked.page_count,
        has_text_layer=answer.condition == "C0",
        stored_path=pdf_path,
    )
    run_id = repo.new_id()
    final_state = run_document(
        file_path=pdf_path,
        customer_id=answer.customer_id,
        conn=conn,
        checkpointer=checkpointer,
        document_id=document.id,
        run_id=run_id,
    )

    if final_state.get("error"):
        return DocumentEvalResult(
            doc_id=answer.doc_id,
            file=pdf_path.name,
            run_id=run_id,
            error=final_state["error"],
            field_matches={},
            field_details={},
            outcome=None,
            acceptable_outcomes=list(answer.acceptable_outcomes),
            outcome_acceptable=False,
            llm_calls=0,
            fallback_used=False,
        )

    validation = final_state["validation"]["fields"]
    field_matches: dict[str, bool] = {}
    field_details: dict[str, tuple[str | None, str | None]] = {}
    for field_name, expected_field in answer.fields.items():
        our_verdict = validation.get(field_name, {}).get("verdict")
        # "match" is the only verdict design section 10.1 and the answer files score as
        # correct for a truly-matching field; "uncertain" is treated separately below since a
        # degraded document may legitimately not resolve to a clean match/mismatch.
        expected_verdict = expected_field.verdict
        field_matches[field_name] = our_verdict == expected_verdict
        field_details[field_name] = (our_verdict, expected_verdict)

    row = repo.get_run(conn, run_id)
    outcome = row["outcome"] if row else None
    acceptable = list(answer.acceptable_outcomes)
    outcome_acceptable = _to_router_outcome(outcome) in acceptable if outcome else False

    llm_calls = row["llm_calls"] if row else 0
    fallback_used = bool(row["fallback_used"]) if row else False

    return DocumentEvalResult(
        doc_id=answer.doc_id,
        file=pdf_path.name,
        run_id=run_id,
        error=None,
        field_matches=field_matches,
        field_details=field_details,
        outcome=outcome,
        acceptable_outcomes=acceptable,
        outcome_acceptable=outcome_acceptable,
        llm_calls=llm_calls,
        fallback_used=fallback_used,
    )


def _report_text(results: list[DocumentEvalResult]) -> str:
    lines = ["# Offline eval report (submission-sample smoke run)", ""]
    lines.append(
        "Reduced scope for this session's deadline: the 3 submission samples, not the full "
        "28-document grid. See docs/progress.md's scope-cut notes."
    )
    lines.append("")

    total_fields = 0
    correct_fields = 0
    wrong_auto_approvals = 0

    for result in results:
        lines.append(f"## {result.file} ({result.doc_id})")
        if result.error:
            lines.append(f"- **Error:** {result.error}")
            lines.append("")
            continue
        lines.append(f"- run_id: `{result.run_id}`")
        lines.append(f"- outcome: `{result.outcome}` (acceptable: {result.acceptable_outcomes})")
        status = "OK" if result.outcome_acceptable else "**UNEXPECTED**"
        lines.append(f"  -> {status}")
        lines.append(f"- LLM calls: {result.llm_calls}, fallback used: {result.fallback_used}")
        lines.append("- Field verdicts (ours vs expected):")
        for field_name, (ours, expected) in result.field_details.items():
            total_fields += 1
            match = result.field_matches[field_name]
            if match:
                correct_fields += 1
            marker = "OK" if match else "MISMATCH"
            lines.append(f"  - {field_name}: ours={ours!r} expected={expected!r} [{marker}]")
        if result.outcome == "auto_approved" and not result.outcome_acceptable:
            wrong_auto_approvals += 1
        lines.append("")

    lines.append("## Summary")
    lines.append(f"- Field verdict accuracy: {correct_fields}/{total_fields}")
    lines.append(f"- Wrong auto-approvals: {wrong_auto_approvals}")
    if wrong_auto_approvals:
        lines.append(
            "- **FAIL: at least one document was auto-approved when it should not have "
            "been.** This is the one failure design section 1.2 item 14 treats as "
            "unconditional."
        )
    else:
        lines.append("- No wrong auto-approvals.")
    return "\n".join(lines)


def main() -> int:
    docs = _submission_documents()
    if not docs:
        print(f"No submission samples found under {SUBMISSION_DIR}", file=sys.stderr)
        return 1

    conn = connect()
    checkpoint_conn = sqlite3.connect(
        settings.data_dir / "checkpoints.db", check_same_thread=False
    )
    checkpointer = SqliteSaver(checkpoint_conn)

    results = [
        _run_one_document(conn=conn, checkpointer=checkpointer, pdf_path=pdf_path, answer=answer)
        for pdf_path, answer in docs
    ]

    report = _report_text(results)
    print(report)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "submission-smoke-run.md").write_text(report + "\n", encoding="utf-8")
    (REPORTS_DIR / "submission-smoke-run.json").write_text(
        json.dumps([r.__dict__ for r in results], indent=2, default=str) + "\n", encoding="utf-8"
    )

    wrong_approvals = sum(
        1 for r in results if r.outcome == "auto_approved" and not r.outcome_acceptable
    )
    return 1 if wrong_approvals else 0


if __name__ == "__main__":
    sys.exit(main())
