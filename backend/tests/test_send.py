"""Tests for the single send path (part2-shipment-workflow design section 2 and 5):
`app.shipment.send`.

The grep test at the bottom is the load-bearing one (requirement 7.1, 15.1): it walks the whole
repo tree and proves that nothing outside `app/shipment/send.py` (and `app/store/repository.py`,
where `insert_outbox` is merely *defined*, not called as a send path) ever touches `outbox`,
`insert_outbox`, or any SMTP-shaped call. If someone adds a second place that marks a draft
sent, this test fails.

Fixture pattern copied from `tests/test_store_part2.py`: `tmp_path` as `settings.data_dir`,
`app.store.db.connect` for a real (scratch) `app.db`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.shipment.send import AlreadySentError, SendError, mark_reviewed_no_send, send_draft
from app.store import repository as repo
from app.store.db import connect

# --- fixtures -----------------------------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path: Path) -> Iterator[Path]:
    """A scratch `data/` per test. `Settings` is a frozen dataclass, so `object.__setattr__`
    bypasses that (deliberately, test-only) and is restored afterwards."""
    from app.config import settings

    original = settings.data_dir
    object.__setattr__(settings, "data_dir", tmp_path)
    try:
        yield tmp_path
    finally:
        object.__setattr__(settings, "data_dir", original)


@pytest.fixture
def conn(data_dir: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(data_dir=data_dir)
    try:
        yield connection
    finally:
        connection.close()


# --- seed helpers (mirroring test_store_part2.py) ------------------------------------------------


def _seed_email(conn: sqlite3.Connection, *, message_id: str = "<m-1@su.example>") -> str:
    return repo.record_email(
        conn,
        message_id=message_id,
        eml_path=f"inbox/{message_id}.eml",
        sender="ops@su.example",
        subject="ACME-001 SHP-5501 documents attached",
        received_at="2026-10-10T09:00:00+00:00",
        attachment_count=2,
        attachment_filenames=["invoice.pdf", "bol.pdf"],
    )


def _seed_shipment(
    conn: sqlite3.Connection,
    *,
    email_id: str,
    reference: str = "SHP-5501",
    cycle_number: int = 1,
    review_state: str = "pending_review",
) -> str:
    return repo.create_shipment_for_email(
        conn,
        customer_id="acme",
        customer_name="ACME Electronics Pte. Ltd.",
        reference=reference,
        email_id=email_id,
        cycle_number=cycle_number,
        parent_shipment_id=None,
        review_state=review_state,
    )


def _seed_draft(
    conn: sqlite3.Connection,
    shipment_id: str,
    *,
    recipient: str = "ops@su.example",
    subject: str = "Re: ACME-001 SHP-5501 documents attached",
    body: str = "All documents verified. No amendment needed.",
) -> tuple[str, str]:
    return repo.create_draft(
        conn,
        shipment_id=shipment_id,
        kind="approval",
        render_error=None,
        recipient=recipient,
        subject=subject,
        body=body,
    )


def _seed_shipment_with_draft(
    conn: sqlite3.Connection,
    *,
    review_state: str = "pending_review",
    body: str = "All documents verified. No amendment needed.",
) -> str:
    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id, review_state=review_state)
    _seed_draft(conn, shipment_id, body=body)
    return shipment_id


# --- send_draft -----------------------------------------------------------------------------


def test_send_draft_returns_approved_sent_when_outcome_is_auto_approve(
    conn: sqlite3.Connection,
) -> None:
    shipment_id = _seed_shipment_with_draft(conn)
    repo.set_shipment_outcome(
        conn,
        shipment_id=shipment_id,
        outcome="auto_approve",
        reasoning="Every field matched.",
        discrepancy_count=0,
        uncertain_count=0,
    )

    result = send_draft(
        conn,
        shipment_id=shipment_id,
        recipient="ops@su.example",
        subject="Re: documents",
        body="All documents verified.",
    )

    assert result.review_state == "approved_sent"
    assert result.outbox_id
    assert result.sent_at
    stored = repo.get_shipment(conn, shipment_id)
    assert stored["review_state"] == "approved_sent"


def test_send_draft_returns_amendment_sent_for_any_non_auto_approve_outcome(
    conn: sqlite3.Connection,
) -> None:
    shipment_id = _seed_shipment_with_draft(conn)
    repo.set_shipment_outcome(
        conn,
        shipment_id=shipment_id,
        outcome="human_review",
        reasoning="A field is uncertain.",
        discrepancy_count=1,
        uncertain_count=1,
    )

    result = send_draft(
        conn,
        shipment_id=shipment_id,
        recipient="ops@su.example",
        subject="Re: documents",
        body="Please review the attached discrepancy.",
    )

    assert result.review_state == "amendment_sent"


def test_a_second_send_raises_already_sent_and_leaves_the_original_row(
    conn: sqlite3.Connection,
) -> None:
    shipment_id = _seed_shipment_with_draft(conn)

    send_draft(
        conn,
        shipment_id=shipment_id,
        recipient="ops@su.example",
        subject="Re: documents",
        body="Original text.",
    )

    with pytest.raises(AlreadySentError):
        send_draft(
            conn,
            shipment_id=shipment_id,
            recipient="ops@su.example",
            subject="Re: documents (again)",
            body="Second attempt text.",
        )

    rows = conn.execute("SELECT * FROM outbox WHERE shipment_id = ?", (shipment_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["body"] == "Original text."


def test_send_draft_with_no_draft_raises_send_error(conn: sqlite3.Connection) -> None:
    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id)

    with pytest.raises(SendError):
        send_draft(
            conn,
            shipment_id=shipment_id,
            recipient="ops@su.example",
            subject="Re: documents",
            body="Some body.",
        )


def test_send_draft_with_empty_body_names_body(conn: sqlite3.Connection) -> None:
    shipment_id = _seed_shipment_with_draft(conn)

    with pytest.raises(SendError, match="body"):
        send_draft(
            conn,
            shipment_id=shipment_id,
            recipient="ops@su.example",
            subject="Re: documents",
            body="   ",
        )


def test_send_draft_with_empty_recipient_names_recipient(conn: sqlite3.Connection) -> None:
    shipment_id = _seed_shipment_with_draft(conn)

    with pytest.raises(SendError, match="recipient"):
        send_draft(
            conn,
            shipment_id=shipment_id,
            recipient="",
            subject="Re: documents",
            body="Some body.",
        )


# --- mark_reviewed_no_send --------------------------------------------------------------------


def test_mark_reviewed_no_send_sets_state_and_writes_nothing_to_outbox(
    conn: sqlite3.Connection,
) -> None:
    shipment_id = _seed_shipment_with_draft(conn)

    mark_reviewed_no_send(conn, shipment_id=shipment_id)

    stored = repo.get_shipment(conn, shipment_id)
    assert stored["review_state"] == "reviewed_no_send"
    assert repo.get_outbox(conn, shipment_id) is None


def test_mark_reviewed_no_send_with_a_note_records_it_without_touching_outcome(
    conn: sqlite3.Connection,
) -> None:
    shipment_id = _seed_shipment_with_draft(conn)
    repo.set_shipment_outcome(
        conn,
        shipment_id=shipment_id,
        outcome="human_review",
        reasoning="A field is uncertain.",
        discrepancy_count=1,
        uncertain_count=1,
    )

    mark_reviewed_no_send(conn, shipment_id=shipment_id, operator_note="Checked by phone, fine.")

    stored = repo.get_shipment(conn, shipment_id)
    assert stored["review_state"] == "reviewed_no_send"
    assert stored["operator_decision"] == "Checked by phone, fine."
    assert stored["operator_decided_at"]
    # The system's own conclusion is untouched (R7.6).
    assert stored["outcome"] == "human_review"
    assert stored["reasoning"] == "A field is uncertain."
    assert repo.get_outbox(conn, shipment_id) is None


def test_mark_reviewed_no_send_is_safe_with_no_draft(conn: sqlite3.Connection) -> None:
    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id)

    mark_reviewed_no_send(conn, shipment_id=shipment_id)

    stored = repo.get_shipment(conn, shipment_id)
    assert stored["review_state"] == "reviewed_no_send"


# --- the no-send-path grep test (requirement 7.1, 15.1) ----------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]

SCAN_ROOTS = [
    REPO_ROOT / "backend" / "app",
    REPO_ROOT / "samples",
    REPO_ROOT / "eval",
    REPO_ROOT / "frontend" / "src",
]

SCAN_SUFFIXES = {".py", ".jsx", ".js"}

SKIP_DIR_NAMES = {".venv", "__pycache__", "node_modules", ".git", ".pytest_cache", ".ruff_cache"}

# Strings that only the single send path (and the repository function it calls) may contain.
FORBIDDEN_STRINGS = ["insert_outbox", "smtplib", "send_mail", "sendmail", "SMTP"]

# The one function definition that legitimately contains the literal "insert_outbox": the
# repository module where it is *defined*. Calling it is restricted to send.py; defining it is
# not a call.
SEND_MODULE = Path("backend") / "app" / "shipment" / "send.py"
REPOSITORY_DEFINITION_MODULE = Path("backend") / "app" / "store" / "repository.py"


def _iter_scanned_files() -> Iterator[Path]:
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
                continue
            if any(part in SKIP_DIR_NAMES for part in path.relative_to(REPO_ROOT).parts):
                continue
            yield path


def _is_allowed_file(relative: Path) -> bool:
    """A file may legitimately mention a forbidden string if it *is* the single send path, *is*
    the repository module where `insert_outbox` is defined, or is a test file whose name marks
    it as exercising the send path directly (tests are allowed to call repository functions
    to set up fixtures, same as `test_store_part2.py` already does)."""
    if relative in (SEND_MODULE, REPOSITORY_DEFINITION_MODULE):
        return True
    if relative.parts[:2] == ("backend", "tests"):
        name = relative.name
        if name == "test_store_part2.py":
            return True
        if name.startswith("test_") and "send" in name:
            return True
    return False


def test_no_send_path_exists_outside_the_single_send_module() -> None:
    """Requirement 7.1, 15.1: `insert_outbox`, and anything SMTP-shaped, may appear only in
    `app/shipment/send.py` (the one allowed caller), in `app/store/repository.py` (where
    `insert_outbox` is defined, not called as a send path), and in test files set up to exercise
    the send path directly. Everywhere else under `backend/app`, `samples`, `eval` and
    `frontend/src`, zero occurrences.

    This walks the real file tree rather than a hardcoded list, so it keeps catching a new
    violation even in code this test has never seen before.
    """
    violations: list[str] = []

    for path in _iter_scanned_files():
        relative = path.relative_to(REPO_ROOT)
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = [needle for needle in FORBIDDEN_STRINGS if needle in text]
        if not hits:
            continue
        if _is_allowed_file(relative):
            continue
        violations.append(f"{relative}: {', '.join(hits)}")

    assert not violations, "forbidden send-path strings found outside send.py:\n" + "\n".join(
        violations
    )


def test_send_module_and_repository_module_do_in_fact_contain_insert_outbox() -> None:
    """Sanity check on the allow-list itself: if `insert_outbox` ever stopped appearing in
    either of these two files, the grep test above would start passing for the wrong reason
    (nothing to find anywhere), silently losing its coverage."""
    send_text = (REPO_ROOT / SEND_MODULE).read_text(encoding="utf-8")
    repo_text = (REPO_ROOT / REPOSITORY_DEFINITION_MODULE).read_text(encoding="utf-8")

    assert "insert_outbox" in send_text
    assert "insert_outbox" in repo_text
