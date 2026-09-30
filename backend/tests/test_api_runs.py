"""Tests for `app/api/` (design section 6 / Phase 9): the FastAPI routes for customers,
runs and page images. Uses FastAPI's `TestClient` against a fake LLM transport (mirroring
`tests/test_graph.py`'s pattern: swap `app.graph.nodes.transport_factory` for a
`FakeTransport`-returning lambda, and point `settings.data_dir` at a `tmp_path` per test), so
no run ever spends real Gemini quota or touches the real `data/` folder.

`POST /api/runs` starts the pipeline via `BackgroundTasks`. `TestClient` runs background
tasks synchronously before returning the response in the version pinned here (starlette,
via fastapi==0.142.0), so polling in these tests is a formality, not a real race -- but the
tests still poll a few times to mirror how the real server (with an ASGI event loop actually
running the task in the background) behaves, and to guard against that assumption changing.
"""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest
from fpdf import FPDF
from PIL import Image

from app.agents.route import RouteDecision
from app.agents.schema import ExtractionResult, FieldExtraction
from app.agents.validate import FieldJudgement, ValidationJudgementResult
from app.graph import nodes as graph_nodes
from app.llm.fake_transport import FakeTransport
from app.llm.transport import RawResponse

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"
SUBMISSION = SAMPLES_DIR / "submission"
PRIMARY = "gemini-3.8-flash"

V1_CORRECT_FIELDS = {
    "consignee": ("ACME Electronics Pte. Ltd.", "ACME Electronics Pte. Ltd."),
    "hs_code": ("847130", "8471.30"),
    "port_of_loading": ("Shanghai", "SHANGHAI, CHINA"),
    "port_of_discharge": ("Singapore", "SINGAPORE"),
    "incoterms": ("CIF", "CIF Singapore"),
    "goods_description": (
        "Portable laptop computers, 14-inch, 16 GB RAM",
        "Portable laptop computers, 14-inch, 16 GB RAM",
    ),
    "gross_weight": ("862.40 KG", "862.40 KGS"),
    "invoice_number": ("INV-2026-00417", "INV-2026-00417"),
}


def _extraction_response(fields: dict[str, tuple[str, str]]) -> RawResponse:
    field_objs = {
        name: FieldExtraction(value=value, source_text=source, page=1, self_rating=0.95)
        for name, (value, source) in fields.items()
    }
    result = ExtractionResult(document_type="commercial_invoice", **field_objs)
    return RawResponse(
        text=result.model_dump_json(), input_tokens=500, output_tokens=100, thinking_tokens=0
    )


def _judgement_response() -> RawResponse:
    result = ValidationJudgementResult(
        consignee=FieldJudgement(verdict="match", reasoning="exact", self_rating=1.0),
        goods_description=FieldJudgement(verdict="match", reasoning="specific", self_rating=1.0),
    )
    return RawResponse(
        text=result.model_dump_json(), input_tokens=200, output_tokens=50, thinking_tokens=0
    )


def _route_response(outcome: str = "auto_approve") -> RawResponse:
    decision = RouteDecision(
        outcome=outcome,
        reasoning="Every field matched the customer's rules." if outcome == "auto_approve"
        else "See the field verdicts.",
        cited_fields=[],
        amendment_draft=None,
    )
    return RawResponse(
        text=decision.model_dump_json(), input_tokens=300, output_tokens=80, thinking_tokens=0
    )


def _queue_clean_run(transport: FakeTransport) -> None:
    transport.queue(PRIMARY, _extraction_response(V1_CORRECT_FIELDS))
    transport.queue(PRIMARY, _judgement_response())
    transport.queue(PRIMARY, _route_response("auto_approve"))


@pytest.fixture
def fake_transport(monkeypatch: pytest.MonkeyPatch) -> FakeTransport:
    """Same pattern as `tests/test_graph.py`: no node -- and no query request, via
    `api.routes.query_transport_factory` -- ever makes a real network call."""
    transport = FakeTransport()
    monkeypatch.setattr(graph_nodes, "transport_factory", lambda: transport)
    from app.api import routes as api_routes

    monkeypatch.setattr(api_routes, "query_transport_factory", lambda: transport)
    return transport


@pytest.fixture
def data_dir(tmp_path: Path):
    """A scratch `data/` directory per test, so runs never touch the real `data/app.db`.
    Same approach as `test_graph.py`'s `data_dir` fixture: `Settings` is a frozen dataclass,
    so `object.__setattr__` bypasses that (deliberately, test-only) and is restored after."""
    from app.config import settings

    original = settings.data_dir
    object.__setattr__(settings, "data_dir", tmp_path)
    yield tmp_path
    object.__setattr__(settings, "data_dir", original)


@pytest.fixture
def client(data_dir: Path):
    """A `TestClient` built after `data_dir` is patched in, so the app's startup hook (which
    calls `resume_incomplete_runs()` against `settings.data_dir`) also runs against the
    scratch directory rather than the real one."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _poll_until_completed(client, run_id: str, *, timeout_seconds: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        last = response.json()
        if last["status"] == "completed":
            return last
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not complete in time: {last}")


# --- GET /api/customers -----------------------------------------------------------------------


def test_get_customers_returns_acme(client) -> None:
    response = client.get("/api/customers")
    assert response.status_code == 200
    body = response.json()
    assert body == [{"customer_id": "acme", "customer_name": "ACME Electronics Pte. Ltd."}]


# --- POST /api/runs + GET /api/runs/{id} ----------------------------------------------------


def test_upload_a_submission_sample_and_poll_to_completion(client, fake_transport) -> None:
    _queue_clean_run(fake_transport)
    file_path = SUBMISSION / "01-clean-correct.pdf"

    with file_path.open("rb") as fh:
        response = client.post(
            "/api/runs",
            files={"file": ("01-clean-correct.pdf", fh, "application/pdf")},
            data={"customer_id": "acme"},
        )
    assert response.status_code == 202
    body = response.json()
    assert body["duplicate"] is False
    assert body["status"] == "processing"
    run_id = body["run_id"]
    assert run_id

    final = _poll_until_completed(client, run_id)
    assert final["id"] == run_id
    assert final["status"] == "completed"
    assert final["outcome"] == "auto_approved"
    assert final["customer_id"] == "acme"
    assert final["customer_name"] == "ACME Electronics Pte. Ltd."
    assert final["filename"] == "01-clean-correct.pdf"
    assert len(final["field_results"]) == 8
    field_names = {f["field_name"] for f in final["field_results"]}
    assert field_names == {
        "consignee", "hs_code", "port_of_loading", "port_of_discharge", "incoterms",
        "goods_description", "gross_weight", "invoice_number",
    }


def test_get_runs_lists_the_run_summary(client, fake_transport) -> None:
    _queue_clean_run(fake_transport)
    file_path = SUBMISSION / "01-clean-correct.pdf"
    with file_path.open("rb") as fh:
        response = client.post(
            "/api/runs",
            files={"file": ("01-clean-correct.pdf", fh, "application/pdf")},
            data={"customer_id": "acme"},
        )
    run_id = response.json()["run_id"]
    _poll_until_completed(client, run_id)

    list_response = client.get("/api/runs")
    assert list_response.status_code == 200
    runs = list_response.json()
    assert len(runs) == 1
    summary = runs[0]
    assert summary["id"] == run_id
    assert summary["filename"] == "01-clean-correct.pdf"
    assert summary["customer_name"] == "ACME Electronics Pte. Ltd."
    assert summary["status"] == "completed"
    assert summary["outcome"] == "auto_approved"
    # The summary shape (design section 6) has no field_results.
    assert "field_results" not in summary


def test_get_run_404_for_unknown_id(client) -> None:
    response = client.get("/api/runs/does-not-exist")
    assert response.status_code == 404


# --- Duplicate upload --------------------------------------------------------------------------


def test_duplicate_upload_returns_the_existing_run_without_a_second_run(
    client, fake_transport
) -> None:
    _queue_clean_run(fake_transport)
    file_path = SUBMISSION / "01-clean-correct.pdf"

    with file_path.open("rb") as fh:
        first = client.post(
            "/api/runs",
            files={"file": ("01-clean-correct.pdf", fh, "application/pdf")},
            data={"customer_id": "acme"},
        )
    first_run_id = first.json()["run_id"]
    _poll_until_completed(client, first_run_id)

    # Second upload of the identical bytes, same customer, no rerun flag: no fake responses
    # queued for it, so if the pipeline ran again it would raise (empty FakeTransport queue).
    with file_path.open("rb") as fh:
        second = client.post(
            "/api/runs",
            files={"file": ("01-clean-correct.pdf", fh, "application/pdf")},
            data={"customer_id": "acme"},
        )
    assert second.status_code == 202
    second_body = second.json()
    assert second_body["duplicate"] is True
    assert second_body["run_id"] == first_run_id

    list_response = client.get("/api/runs")
    assert len(list_response.json()) == 1


def test_rerun_true_starts_a_second_run_for_the_same_file(client, fake_transport) -> None:
    _queue_clean_run(fake_transport)
    file_path = SUBMISSION / "01-clean-correct.pdf"

    with file_path.open("rb") as fh:
        first = client.post(
            "/api/runs",
            files={"file": ("01-clean-correct.pdf", fh, "application/pdf")},
            data={"customer_id": "acme"},
        )
    first_run_id = first.json()["run_id"]
    _poll_until_completed(client, first_run_id)

    _queue_clean_run(fake_transport)
    with file_path.open("rb") as fh:
        second = client.post(
            "/api/runs",
            files={"file": ("01-clean-correct.pdf", fh, "application/pdf")},
            data={"customer_id": "acme", "rerun": "true"},
        )
    assert second.status_code == 202
    second_body = second.json()
    assert second_body["duplicate"] is False
    assert second_body["run_id"] != first_run_id
    _poll_until_completed(client, second_body["run_id"])

    list_response = client.get("/api/runs")
    assert len(list_response.json()) == 2


# --- Upload validation errors -------------------------------------------------------------------


def test_upload_rejects_unsupported_file_type(client) -> None:
    bad_file = io.BytesIO(b"plain text, not one of the three types")
    response = client.post(
        "/api/runs",
        files={"file": ("notes.txt", bad_file, "text/plain")},
        data={"customer_id": "acme"},
    )
    assert response.status_code == 415
    assert "PDF, PNG or JPG" in response.json()["detail"]


def test_upload_rejects_oversized_file(client) -> None:
    oversize = b"%PDF-1.7\n" + b"0" * (10 * 1024 * 1024)
    response = client.post(
        "/api/runs",
        files={"file": ("big.pdf", io.BytesIO(oversize), "application/pdf")},
        data={"customer_id": "acme"},
    )
    assert response.status_code == 413
    assert "exceeds" in response.json()["detail"]


def test_upload_rejects_too_many_pages(client) -> None:
    pdf = FPDF()
    for _ in range(6):
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(text="page")
    data = bytes(pdf.output())
    response = client.post(
        "/api/runs",
        files={"file": ("six-pages.pdf", io.BytesIO(data), "application/pdf")},
        data={"customer_id": "acme"},
    )
    assert response.status_code == 422
    assert "5 pages" in response.json()["detail"]


# --- GET /api/runs/{id}/pages/{n} ---------------------------------------------------------------


def test_get_run_page_returns_valid_png_bytes(client, fake_transport) -> None:
    _queue_clean_run(fake_transport)
    file_path = SUBMISSION / "01-clean-correct.pdf"
    with file_path.open("rb") as fh:
        response = client.post(
            "/api/runs",
            files={"file": ("01-clean-correct.pdf", fh, "application/pdf")},
            data={"customer_id": "acme"},
        )
    run_id = response.json()["run_id"]
    _poll_until_completed(client, run_id)

    page_response = client.get(f"/api/runs/{run_id}/pages/0")
    assert page_response.status_code == 200
    assert page_response.headers["content-type"] == "image/png"
    image = Image.open(io.BytesIO(page_response.content))
    assert image.format == "PNG"


def test_get_run_page_404_for_unknown_page_number(client, fake_transport) -> None:
    _queue_clean_run(fake_transport)
    file_path = SUBMISSION / "01-clean-correct.pdf"
    with file_path.open("rb") as fh:
        response = client.post(
            "/api/runs",
            files={"file": ("01-clean-correct.pdf", fh, "application/pdf")},
            data={"customer_id": "acme"},
        )
    run_id = response.json()["run_id"]
    _poll_until_completed(client, run_id)

    page_response = client.get(f"/api/runs/{run_id}/pages/99")
    assert page_response.status_code == 404


# --- GET /api/runs/{run_id}/llm-calls --------------------------------------------------------


def test_get_run_llm_calls_lists_every_call_in_order(client, fake_transport) -> None:
    """One call per node (extract, validate, route), oldest first -- backs the run's own
    `llm_calls`/`fallback_used`/token-total summary fields with the individual attempts that
    produced them."""
    _queue_clean_run(fake_transport)
    file_path = SUBMISSION / "01-clean-correct.pdf"
    with file_path.open("rb") as fh:
        response = client.post(
            "/api/runs",
            files={"file": ("01-clean-correct.pdf", fh, "application/pdf")},
            data={"customer_id": "acme"},
        )
    run_id = response.json()["run_id"]
    _poll_until_completed(client, run_id)

    calls_response = client.get(f"/api/runs/{run_id}/llm-calls")
    assert calls_response.status_code == 200
    calls = calls_response.json()
    assert [c["agent"] for c in calls] == ["extract", "validate", "route"]
    for call in calls:
        assert call["model"] == PRIMARY
        assert call["is_fallback"] is False
        assert call["status"] == "success"
        assert call["input_tokens"] > 0
        assert call["latency_ms"] >= 0


def test_get_run_llm_calls_404_for_unknown_id(client) -> None:
    response = client.get("/api/runs/does-not-exist/llm-calls")
    assert response.status_code == 404


# --- POST /api/query -------------------------------------------------------------------------
# Wired to app.query.service.answer_question (Phase 11) once both sessions' work was
# reconciled -- see docs/progress.md. Runs against the real data_dir's app.db (patched to
# tmp_path by the `data_dir` fixture), through the same fake transport as every other test
# here, so this still spends no real Gemini quota.


def test_query_endpoint_answers_a_single_value_question(client, fake_transport) -> None:
    from app.query.schema import SqlAnswer

    _queue_clean_run(fake_transport)
    file_path = SUBMISSION / "01-clean-correct.pdf"
    with file_path.open("rb") as fh:
        response = client.post(
            "/api/runs",
            files={"file": ("01-clean-correct.pdf", fh, "application/pdf")},
            data={"customer_id": "acme"},
        )
    run_id = response.json()["run_id"]
    _poll_until_completed(client, run_id)

    fake_transport.queue(
        PRIMARY,
        RawResponse(
            text=SqlAnswer(
                sql="SELECT COUNT(*) FROM v_documents", explanation="counts every run"
            ).model_dump_json(),
            input_tokens=100,
            output_tokens=20,
            thinking_tokens=0,
        ),
    )

    query_response = client.post("/api/query", json={"question": "How many runs are there?"})
    assert query_response.status_code == 200
    body = query_response.json()
    assert body["answer"] == 1
    assert body["sql"] == "SELECT COUNT(*) FROM v_documents"
    assert body["explanation"]
    assert body["columns"] == []
    assert body["rows"] == []


# --- /docs lists every endpoint ------------------------------------------------------------------


def test_docs_page_is_served(client) -> None:
    response = client.get("/docs")
    assert response.status_code == 200


def test_openapi_schema_lists_every_endpoint(client) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    paths = set(schema["paths"].keys())
    assert paths == {
        "/api/health",
        "/api/customers",
        "/api/runs",
        "/api/runs/{run_id}",
        "/api/runs/{run_id}/llm-calls",
        "/api/runs/{run_id}/pages/{page_number}",
        "/api/query",
    }
