"""The transport boundary: what an LLM client needs from "a model that answers".

`Transport` is the thing `LLMClient` (`client.py`) calls to actually reach a model. There are
two implementations: `GeminiTransport` (`gemini_transport.py`), which talks to the real Gemini
API, and `FakeTransport` (`fake_transport.py`), which returns scripted responses for tests.
Splitting this out means the retry, fallback and budget logic in `client.py` never touches the
`google-genai` SDK directly, and tests never make a real API call.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RawResponse:
    """One successful call to a model, before our own schema validation.

    `text` is the model's raw output (expected to be JSON, per `response_mime_type`), left
    unparsed here: schema validation is `LLMClient`'s job (design section 3.6: "the response is
    checked again on arrival"), not the transport's, so both the real and the fake transport
    are validated the exact same way.
    """

    text: str
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    cached: bool = False


class TransportError(Exception):
    """Base for every way a call to a model can fail, before we've seen its content."""

    def __init__(self, message: str, *, status_code: int | None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RetryableTransportError(TransportError):
    """429, 5xx, or a timeout: worth trying again.

    `retry_delay_seconds` carries the API's own suggested wait for a 429, when it gave one
    (parsed from `google.rpc.RetryInfo`); it's None for a 5xx, a timeout, or a 429 with no
    such hint. `LLMClient` is the one that decides what this means (design section 3.6): a
    short delay is an ordinary rate limit, worth backing off and retrying; a delay longer than
    the call's own timeout means the daily quota is gone, so it skips the primary's remaining
    retries and goes straight to the fallback model instead of waiting.
    """

    def __init__(
        self, message: str, *, status_code: int | None, retry_delay_seconds: float | None = None
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.retry_delay_seconds = retry_delay_seconds


class NonRetryableTransportError(TransportError):
    """Any other 4xx: a bad request fails on any model, so there's nothing to retry or fall
    back to (design section 3.6)."""


class Transport(Protocol):
    def call(
        self,
        *,
        model: str,
        system_instruction: str,
        text: str,
        images: Sequence[bytes],
        response_schema: type,
        timeout_seconds: float,
        prompt_version: str = "",
    ) -> RawResponse:
        """Make one call to `model`. Raises a `TransportError` subclass on any HTTP-level
        failure. Returns a `RawResponse` otherwise, whether or not its `text` turns out to
        satisfy `response_schema` -- that check happens one level up, in `LLMClient`.

        `prompt_version` doesn't affect the request itself (it's already implied by
        `system_instruction`'s text); it exists so `CachingTransport` can key its cache on
        model, prompt version and input hash exactly as design section 3.6 describes, without
        every other transport needing to care about it.
        """
        ...
