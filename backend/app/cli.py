"""A CLI for debugging the pipeline and for the eval (design section 3.1 / Phase 8 item 6):

    python -m app.cli run <file> --customer acme
    python -m app.cli resume

Uses its own SQLite checkpoint connection (`data/checkpoints.db`, separate from `app.db`,
design section 2), so a crash test can kill the process between nodes and `resume` picks up
from exactly the last saved checkpoint without repeating any LLM call already made.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from app.config import settings
from app.graph.build import resume_document, resume_incomplete_runs, run_document
from app.ingest.files import check_upload
from app.store import repository as repo
from app.store.db import connect
from app.store.repository import new_id

CHECKPOINTS_FILENAME = "checkpoints.db"


def _checkpoint_connection(*, data_dir: Path | None = None) -> sqlite3.Connection:
    base = data_dir if data_dir is not None else settings.data_dir
    base.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(base / CHECKPOINTS_FILENAME, check_same_thread=False)

# Default customer names, since design section 6's `GET /api/customers` (Phase 9, not built
# this session) isn't available to the CLI. Kept tiny and explicit rather than guessed from
# the rules file, so a typo'd customer_id fails clearly instead of silently.
_CUSTOMER_NAMES = {"acme": "ACME Electronics Pte. Ltd."}


def cmd_run(args: argparse.Namespace) -> int:
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"No such file: {file_path}", file=sys.stderr)
        return 1

    conn = connect()
    checkpoint_conn = _checkpoint_connection()
    checkpointer = SqliteSaver(checkpoint_conn)

    data = file_path.read_bytes()
    checked = check_upload(data)

    existing = repo.find_document_by_hash(
        conn, customer_id=args.customer, file_hash=checked.file_hash
    )
    if existing is not None and not args.rerun:
        run_id = repo.find_latest_run_for_document(conn, document_id=existing.id)
        print(f"Duplicate upload: reusing document {existing.id}, run {run_id}")
        return 0

    if existing is not None:
        document = existing
    else:
        customer_name = _CUSTOMER_NAMES.get(args.customer, args.customer)
        document = repo.create_shipment_and_document(
            conn,
            customer_id=args.customer,
            customer_name=customer_name,
            filename=file_path.name,
            file_hash=checked.file_hash,
            mime_type=checked.file_type,
            page_count=checked.page_count,
            has_text_layer=False,  # set precisely by prepare_node's own check; unknown here
            stored_path=file_path,
        )

    run_id = new_id()
    final_state = run_document(
        file_path=file_path,
        customer_id=args.customer,
        conn=conn,
        checkpointer=checkpointer,
        document_id=document.id,
        run_id=run_id,
    )
    print(f"Run {run_id}: {final_state.get('current_step')}")
    if final_state.get("error"):
        print(f"  error: {final_state['error']}")
    elif final_state.get("decision"):
        print(f"  outcome: {final_state['decision']['outcome']}")
    return 0


def cmd_resume(_args: argparse.Namespace) -> int:
    conn = connect()
    checkpoint_conn = _checkpoint_connection()
    checkpointer = SqliteSaver(checkpoint_conn)
    resumed = resume_incomplete_runs(conn=conn, checkpointer=checkpointer)
    print(f"Resumed {len(resumed)} run(s): {resumed}")
    return 0


def cmd_resume_one(args: argparse.Namespace) -> int:
    conn = connect()
    checkpoint_conn = _checkpoint_connection()
    checkpointer = SqliteSaver(checkpoint_conn)
    final_state = resume_document(conn=conn, checkpointer=checkpointer, run_id=args.run_id)
    print(f"Run {args.run_id}: {final_state.get('current_step')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Run one document through the pipeline")
    run_parser.add_argument("file")
    run_parser.add_argument("--customer", required=True)
    run_parser.add_argument("--rerun", action="store_true")
    run_parser.set_defaults(func=cmd_run)

    resume_parser = sub.add_parser("resume", help="Resume every run still marked processing")
    resume_parser.set_defaults(func=cmd_resume)

    resume_one_parser = sub.add_parser("resume-one", help="Resume one run by ID")
    resume_one_parser.add_argument("run_id")
    resume_one_parser.set_defaults(func=cmd_resume_one)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
