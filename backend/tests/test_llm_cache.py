"""Tests for the dev/eval response cache (Phase 4, item 7). Off in the app; only the eval
runner and manual dev scripts should use it."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from app.llm.cache import CachingTransport
from app.llm.fake_transport import FakeTransport
from app.llm.transport import RawResponse


class Answer(BaseModel):
    value: str


def _ok(value: str) -> RawResponse:
    return RawResponse(
        text=Answer(value=value).model_dump_json(),
        input_tokens=10,
        output_tokens=5,
        thinking_tokens=0,
    )


def test_a_repeated_call_is_served_from_the_cache_without_touching_the_inner_transport(
    tmp_path: Path,
) -> None:
    inner = FakeTransport()
    inner.queue("gemini-3.8-flash", _ok("first"))
    caching = CachingTransport(inner, cache_dir=tmp_path)

    kwargs = dict(
        model="gemini-3.8-flash",
        system_instruction="system",
        text="hello",
        images=(),
        response_schema=Answer,
        timeout_seconds=60.0,
        prompt_version="1",
    )
    first = caching.call(**kwargs)
    second = caching.call(**kwargs)

    assert first.text == second.text == Answer(value="first").model_dump_json()
    assert first.cached is False
    assert second.cached is True
    assert len(inner.calls) == 1  # the inner transport was only ever hit once


def test_a_different_prompt_version_is_a_cache_miss(tmp_path: Path) -> None:
    inner = FakeTransport()
    inner.queue("gemini-3.8-flash", _ok("v1"), _ok("v2"))
    caching = CachingTransport(inner, cache_dir=tmp_path)

    base = dict(
        model="gemini-3.8-flash",
        system_instruction="system",
        text="hello",
        images=(),
        response_schema=Answer,
        timeout_seconds=60.0,
    )
    first = caching.call(**base, prompt_version="1")
    second = caching.call(**base, prompt_version="2")

    assert first.text != second.text
    assert len(inner.calls) == 2


def test_a_different_input_hash_is_a_cache_miss(tmp_path: Path) -> None:
    inner = FakeTransport()
    inner.queue("gemini-3.8-flash", _ok("a"), _ok("b"))
    caching = CachingTransport(inner, cache_dir=tmp_path)

    base = dict(
        model="gemini-3.8-flash",
        system_instruction="system",
        response_schema=Answer,
        timeout_seconds=60.0,
        prompt_version="1",
    )
    caching.call(**base, text="hello", images=())
    caching.call(**base, text="goodbye", images=())

    assert len(inner.calls) == 2
