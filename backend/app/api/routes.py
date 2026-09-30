"""HTTP routes (design section 6 / Phase 9), built on top of the finished pipeline
(`app.graph.build`), repository (`app.store.repository`) and upload checks
(`app.ingest.files`). Every route opens its own SQLite connection (`app.store.db.connect`)
rather than sharing one across requests -- design section 4/9's "one connection per thread",
applied here as "one connection per request/background task".
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from langgraph.checkpoint.sqlite import SqliteSaver

from app.config import settings
from app.graph.build import resume_incomplete_runs, run_document
from app.ingest.files import (
    FileTooLarge,
    TooManyPages,
    UnsupportedFileType,
    UploadError,
    check_upload,
    store_upload,
)
from app.llm.budget import CallBudget
from app.llm.client import LLMClient
from app.llm.gemini_transport import GeminiTransport
from app.llm.recorder import CallRecorder
from app.query.service import answer_question
from app.store import repository as repo
from app.store.db import connect, db_path

from .schemas import (
    CreateRunResponse,
    CustomerOut,
    QueryAnswerOut,
    QueryRequest,
    RunDetailOut,
    RunSummaryOut,
    query_answer_from_result,
    run_detail_from_row,
)

router = APIRouter(prefix="/api")

CHECKPOINTS_FILENAME = "checkpoints.db"

# design section 6: "GET /api/customers -- customers from the YAML files." Only one customer
# has a rules file right now (`backend/rules/acme.yaml`); there's no metadata endpoint for
# the rules files yet (a rough edge -- see the CLI's own `_CUSTOMER_NAMES` note), so this is
# hardcoded the same way the CLI hardcodes it, until that endpoint exists.
_CUSTOMERS: list[CustomerOut] = [
    CustomerOut(customer_id="acme", customer_name="ACME Electronics Pte. Ltd.")
]
_CUSTOMER_NAMES = {c.customer_id: c.customer_name for c in _CUSTOMERS}


def _checkpoint_connection() -> sqlite3.Connection:
    base = settings.data_dir
    base.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(base / CHECKPOINTS_FILENAME, check_same_thread=False)


@router.get("/customers", response_model=list[CustomerOut])
def get_customers() -> list[CustomerOut]:
    return _CUSTOMERS


@router.get("/runs", response_model=list[RunSummaryOut])
def get_runs() -> list[RunSummaryOut]:
    conn = connect()
    try:
        rows = repo.list_runs(conn)
        summaries = []
        for row in rows:
            # `repo.list_runs()` joins in `filename` and `customer_name` but not
            # `customer_id` (design section 6's list shape needs it too). Rough edge: this
            # is an extra `get_run()` lookup per row rather than a repository change, since
            # `app/store/` is out of this session's scope -- worth folding `customer_id`
            # into `list_runs()`'s own SELECT at the final merge.
            full = repo.get_run(conn, row["id"])
            summaries.append(
                RunSummaryOut(
                    id=row["id"],
                    document_id=row["document_id"],
                    filename=row["filename"],
                    customer_id=full["customer_id"] if full else None,
                    customer_name=row["customer_name"],
                    status=row["status"],
                    current_step=row["current_step"],
                    outcome=row["outcome"],
                    started_at=row["started_at"],
                    finished_at=row["finished_at"],
                )
            )
        return summaries
    finally:
        conn.close()


@router.get("/runs/{run_id}", response_model=RunDetailOut)
def get_run(run_id: str) -> RunDetailOut:
    conn = connect()
    try:
        row = repo.get_run(conn, run_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No such run: {run_id}")
        field_rows = repo.list_field_results(conn, run_id)
        return run_detail_from_row(row, field_rows)
    finally:
        conn.close()


@router.get("/runs/{run_id}/pages/{page_number}")
def get_run_page(run_id: str, page_number: int) -> Response:
    """The rendered page PNG (design section 6), written by `graph.nodes._save_page_images`
    under `data/uploads/<hash>/pages/page-<n>.png`. `page_number` here matches
    `PreparedPage.index`, which is 0-based (`app.ingest.pages.prepare_pages` enumerates pages
    from 0) -- the same numbering the stored filenames use."""
    conn = connect()
    try:
        row = repo.get_run(conn, run_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No such run: {run_id}")
        stored_path = Path(row["stored_path"])
        file_hash = stored_path.parent.name  # data/uploads/<hash>/original.<ext>
        page_path = settings.data_dir / "uploads" / file_hash / "pages" / f"page-{page_number}.png"
        if not page_path.exists():
            raise HTTPException(status_code=404, detail=f"No page image: {page_path.name}")
        return Response(content=page_path.read_bytes(), media_type="image/png")
    finally:
        conn.close()


def _run_pipeline_in_background(
    *, file_path: Path, customer_id: str, document_id: str, run_id: str
) -> None:
    """The background task (design section 9 task 2): opens its own connection and
    checkpointer -- never the request's, which is closed by the time this runs -- and drives
    the pipeline exactly as `app.cli.cmd_run` does."""
    conn = connect()
    try:
        checkpoint_conn = _checkpoint_connection()
        checkpointer = SqliteSaver(checkpoint_conn)
        run_document(
            file_path=file_path,
            customer_id=customer_id,
            conn=conn,
            checkpointer=checkpointer,
            document_id=document_id,
            run_id=run_id,
        )
    finally:
        conn.close()


@router.post("/runs", response_model=CreateRunResponse, status_code=202)
async def create_run(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),  # noqa: B008 -- FastAPI's own dependency-injection idiom
    customer_id: str = Form(...),  # noqa: B008
    rerun: bool = Form(False),  # noqa: B008
) -> CreateRunResponse:
    """Design section 6 / 3.7: validates the upload, returns the existing run immediately for
    a duplicate (same file hash + customer) unless `rerun=true`, otherwise creates the
    shipment/document/run rows and starts the pipeline as a background task, returning 202
    before the pipeline runs."""
    data = await file.read()

    try:
        checked = check_upload(data)
    except UnsupportedFileType as exc:
        raise HTTPException(status_code=415, detail=exc.message) from exc
    except FileTooLarge as exc:
        raise HTTPException(status_code=413, detail=exc.message) from exc
    except TooManyPages as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    except UploadError as exc:  # pragma: no cover -- safety net for any future UploadError
        raise HTTPException(status_code=422, detail=exc.message) from exc

    conn = connect()
    try:
        existing = repo.find_document_by_hash(
            conn, customer_id=customer_id, file_hash=checked.file_hash
        )
        if existing is not None and not rerun:
            existing_run_id = repo.find_latest_run_for_document(conn, document_id=existing.id)
            if existing_run_id is not None:
                existing_row = repo.get_run(conn, existing_run_id)
                return CreateRunResponse(
                    run_id=existing_run_id,
                    status=existing_row["status"] if existing_row else "processing",
                    duplicate=True,
                )

        stored_path = store_upload(checked)

        if existing is not None:
            document = existing
        else:
            customer_name = _CUSTOMER_NAMES.get(customer_id, customer_id)
            document = repo.create_shipment_and_document(
                conn,
                customer_id=customer_id,
                customer_name=customer_name,
                filename=file.filename or "upload",
                file_hash=checked.file_hash,
                mime_type=checked.file_type,
                page_count=checked.page_count,
                has_text_layer=False,  # set precisely by prepare_node's own check
                stored_path=stored_path,
            )

        run_id = repo.new_id()
    finally:
        conn.close()

    background_tasks.add_task(
        _run_pipeline_in_background,
        file_path=stored_path,
        customer_id=customer_id,
        document_id=document.id,
        run_id=run_id,
    )
    return CreateRunResponse(run_id=run_id, status="processing", duplicate=False)


def _default_query_transport_factory() -> GeminiTransport:
    return GeminiTransport(api_key=settings.gemini_api_key)


# Overridable for tests, same seam as `app.graph.nodes.transport_factory`: monkeypatch this
# module attribute to inject a `FakeTransport` so `POST /api/query` never makes a real
# network call in the test suite.
query_transport_factory: object = _default_query_transport_factory


def _make_query_client() -> LLMClient:
    """One `LLMClient` per query request, mirroring `graph.nodes._make_client`'s pattern --
    a fresh `CallBudget` (the query layer isn't part of a document run, so it doesn't share a
    document's budget) and the real Gemini transport."""
    transport = query_transport_factory()
    return LLMClient(
        transport=transport,
        primary_model=settings.gemini_model,
        fallback_model=settings.gemini_fallback_model,
        budget=CallBudget(),
        recorder=CallRecorder(),
        run_id="query",
    )


@router.post("/query", response_model=QueryAnswerOut)
def post_query(body: QueryRequest) -> QueryAnswerOut:
    """Wires the query layer (`app.query.service.answer_question`, Phase 11) into the API
    (design section 5 and 6). Runs against the real `app.db` (not a fixture), read-only --
    `answer_question` itself never writes, per the gate's own read-only connection."""
    client = _make_query_client()
    answer = answer_question(question=body.question, db_path=db_path(), client=client)
    return query_answer_from_result(answer)


def startup_resume_incomplete_runs() -> None:
    """FastAPI start-up hook (design section 3.7): resumes every run still marked
    `processing` from its last checkpoint. Uses its own connection and checkpointer, closed
    once resuming is done -- this only runs once, at process start."""
    conn = connect()
    try:
        checkpoint_conn = _checkpoint_connection()
        checkpointer = SqliteSaver(checkpoint_conn)
        resume_incomplete_runs(conn=conn, checkpointer=checkpointer)
    finally:
        conn.close()
