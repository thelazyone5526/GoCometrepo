"""One real call to each model (Phase 4 verify list). Marked `live`: skipped by default
(`pyproject.toml`'s `addopts = "-m 'not live'"`), run explicitly with `pytest -m live`.

Every call here spends real Gemini free-tier quota. Ask before running this file.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.config import settings
from app.llm.budget import CallBudget
from app.llm.cache import CachingTransport
from app.llm.client import LLMClient
from app.llm.gemini_transport import GeminiTransport
from app.llm.prompt_files import load_prompt
from app.llm.recorder import CallRecorder

pytestmark = pytest.mark.live

# Dev/eval response cache (design section 3.6, item 7): a rerun of this file replays the
# cached response instead of spending quota again. Under data/, which is git-ignored.
LIVE_TEST_CACHE_DIR = settings.data_dir / "llm_cache" / "live_test"


class Greeting(BaseModel):
    message: str


@pytest.fixture
def client_for(request: pytest.FixtureRequest):
    if not settings.has_gemini_key:
        pytest.skip("GEMINI_API_KEY is not set in backend/.env")

    def _make(model: str, fallback: str = "") -> LLMClient:
        transport = CachingTransport(
            GeminiTransport(api_key=settings.gemini_api_key), cache_dir=LIVE_TEST_CACHE_DIR
        )
        return LLMClient(
            transport=transport,
            primary_model=model,
            fallback_model=fallback,
            budget=CallBudget(max_calls=2),
            recorder=CallRecorder(),
            run_id="live-test",
        )

    return _make


def test_live_call_to_the_primary_model(client_for) -> None:
    client = client_for(settings.gemini_model)
    prompt = load_prompt("ping_v1")
    result = client.generate(
        agent="live_test",
        prompt=prompt,
        text='Reply with a short JSON object: {"message": "pong"}.',
        response_schema=Greeting,
    )
    assert result.model == settings.gemini_model
    assert result.is_fallback is False
    assert isinstance(result.value.message, str)
    assert result.value.message


def test_live_call_to_the_fallback_model(client_for) -> None:
    if not settings.gemini_fallback_model:
        pytest.skip("GEMINI_FALLBACK_MODEL is empty")
    client = client_for(settings.gemini_fallback_model)
    prompt = load_prompt("ping_v1")
    result = client.generate(
        agent="live_test",
        prompt=prompt,
        text='Reply with a short JSON object: {"message": "pong"}.',
        response_schema=Greeting,
    )
    assert result.model == settings.gemini_fallback_model
    assert isinstance(result.value.message, str)
    assert result.value.message
