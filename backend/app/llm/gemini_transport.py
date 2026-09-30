"""The real Gemini transport (`google-genai`). One HTTP call in, one `RawResponse` out, or a
`TransportError` -- no retry, fallback or budget logic here (that's `client.py`'s job).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from .transport import (
    NonRetryableTransportError,
    RawResponse,
    RetryableTransportError,
    TransportError,
)

# google.rpc.RetryInfo's `retryDelay` is a protobuf Duration string, e.g. "13s" or "1.5s".
_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)s$")


def _parse_retry_delay_seconds(details: object) -> float | None:
    """The server's suggested wait, in seconds, from a 429's `error.details[]`, or None if
    the response didn't include a `google.rpc.RetryInfo` entry."""
    if not isinstance(details, list):
        return None
    for item in details:
        if not isinstance(item, dict):
            continue
        type_url = item.get("@type", "")
        if not isinstance(type_url, str) or not type_url.endswith("RetryInfo"):
            continue
        delay = item.get("retryDelay")
        if isinstance(delay, str):
            match = _DURATION_RE.match(delay)
            if match:
                return float(match.group(1))
    return None


def _error_details_list(exc: genai_errors.APIError) -> object:
    """The `details[]` array from the API's error body, whichever of the two shapes
    `APIError.details` ends up holding: `{"details": [...]}` or `{"error": {"details": [...]}}`.
    """
    raw = exc.details
    if not isinstance(raw, dict):
        return None
    if "details" in raw:
        return raw["details"]
    nested = raw.get("error")
    if isinstance(nested, dict):
        return nested.get("details")
    return None


def _client_error_to_transport_error(exc: genai_errors.ClientError) -> TransportError:
    status_code = exc.code
    if status_code == 429:
        retry_delay = _parse_retry_delay_seconds(_error_details_list(exc))
        return RetryableTransportError(
            str(exc), status_code=status_code, retry_delay_seconds=retry_delay
        )
    return NonRetryableTransportError(str(exc), status_code=status_code)


class GeminiTransport:
    """Wraps one `google.genai.Client`. Automatic function calling and the SDK's own retries
    are both switched off: we pass no tools, and our own wrapper does the retrying (design
    section 3.6 and progress.md's Phase 1 spike notes), so leaving either on would either do
    nothing or retry twice."""

    def __init__(self, api_key: str) -> None:
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
        )

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
        parts: list[types.Part] = [
            types.Part.from_bytes(data=image, mime_type="image/png") for image in images
        ]
        parts.append(types.Part.from_text(text=text))

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0,
            response_mime_type="application/json",
            response_schema=response_schema,
            http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000)),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        try:
            response = self._client.models.generate_content(
                model=model, contents=[types.Content(parts=parts)], config=config
            )
        except genai_errors.ClientError as exc:
            raise _client_error_to_transport_error(exc) from exc
        except genai_errors.ServerError as exc:
            raise RetryableTransportError(str(exc), status_code=exc.code) from exc
        except TimeoutError as exc:
            raise RetryableTransportError(str(exc), status_code=None) from exc

        usage = response.usage_metadata
        return RawResponse(
            text=response.text or "",
            input_tokens=(usage.prompt_token_count if usage else None) or 0,
            output_tokens=(usage.candidates_token_count if usage else None) or 0,
            # Thinking tokens are billed as output (progress.md, Phase 1 spike); kept separate
            # here so the recorder and cost table can add them in explicitly, never by accident.
            thinking_tokens=(usage.thoughts_token_count if usage else None) or 0,
            cached=False,
        )
