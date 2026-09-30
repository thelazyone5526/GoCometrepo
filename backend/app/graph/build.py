"""The graph itself (design section 3.1): `prepare -> extract -> validate -> route ->
persist -> END`, with a single conditional edge from every node to `escalate` whenever that
node set `state["error"]`.

`run_document()` is the entry point used by the CLI (and, later, the API): it creates the
run's row in `app.db`, builds a fresh graph with a SQLite checkpointer, and invokes it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from langgraph.graph import END, StateGraph

from app.llm.recorder import CallRecorder
from app.store import repository as repo

from .nodes import (
    escalate_node,
    extract_node,
    persist_node,
    prepare_node,
    route_node,
    validate_node,
)
from .state import RunState

# design section 3.1: "The recursion limit is set to 10 as a backstop."
RECURSION_LIMIT = 10


def _has_error(state: dict[str, Any]) -> str:
    return "escalate" if state.get("error") else "continue"


def build_graph(*, conn: sqlite3.Connection, recorder: CallRecorder, checkpointer: Any):
    """One graph instance per run. `conn` and `recorder` are closed over by the node
    wrappers below rather than threaded through `RunState` itself, since neither an open
    SQLite connection nor the call log belongs in checkpointed, serialisable state (design
    section 3.2's state is plain data only)."""
    graph = StateGraph(RunState)

    graph.add_node("prepare", prepare_node)
    graph.add_node("extract", lambda s: extract_node(s, conn=conn, recorder=recorder))
    graph.add_node("validate", lambda s: validate_node(s, conn=conn, recorder=recorder))
    graph.add_node("route", lambda s: route_node(s, conn=conn, recorder=recorder))
    graph.add_node("persist", lambda s: persist_node(s, conn=conn, recorder=recorder))
    graph.add_node("escalate", lambda s: escalate_node(s, conn=conn, recorder=recorder))

    graph.set_entry_point("prepare")
    next_node_of = {
        "prepare": "extract",
        "extract": "validate",
        "validate": "route",
        "route": "persist",
    }
    for node, next_node in next_node_of.items():
        graph.add_conditional_edges(
            node, _has_error, {"continue": next_node, "escalate": "escalate"}
        )
    graph.add_edge("persist", END)
    graph.add_edge("escalate", END)

    return graph.compile(checkpointer=checkpointer)


def run_document(
    *,
    file_path: Path,
    customer_id: str,
    conn: sqlite3.Connection,
    checkpointer: Any,
    document_id: str,
    run_id: str,
) -> dict[str, Any]:
    """Start one run from scratch. The caller (the CLI's `run` command) is responsible for
    the duplicate-upload check and creating the `shipments`/`documents` rows first -- this
    function only creates the `runs` row and drives the graph."""
    repo.create_run(conn, run_id=run_id, document_id=document_id)
    recorder = CallRecorder()
    graph = build_graph(conn=conn, recorder=recorder, checkpointer=checkpointer)

    initial_state: RunState = {
        "run_id": run_id,
        "customer_id": customer_id,
        "file_path": str(file_path),
        "llm_calls_used": 0,
        "current_step": "prepare",
    }
    config = {"configurable": {"thread_id": run_id}, "recursion_limit": RECURSION_LIMIT}
    return graph.invoke(initial_state, config=config)


def resume_document(*, conn: sqlite3.Connection, checkpointer: Any, run_id: str) -> dict[str, Any]:
    """Continue a run from its last checkpoint (design section 3.7). LangGraph resumes from
    exactly the last saved state when invoked with `None` and the same `thread_id` -- nodes
    already completed (and their LLM calls) are not repeated."""
    recorder = CallRecorder()
    graph = build_graph(conn=conn, recorder=recorder, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": run_id}, "recursion_limit": RECURSION_LIMIT}
    return graph.invoke(None, config=config)


def resume_incomplete_runs(*, conn: sqlite3.Connection, checkpointer: Any) -> list[str]:
    """design section 3.7 / Phase 8 verify list: "finds runs still marked `processing` and
    continues each from its last checkpoint." Returns the run IDs that were resumed."""
    run_ids = repo.list_processing_runs(conn)
    for run_id in run_ids:
        resume_document(conn=conn, checkpointer=checkpointer, run_id=run_id)
    return run_ids
