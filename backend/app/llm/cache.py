"""The dev/eval response cache (design section 3.6 and Phase 4 item 7): "so reruns don't use
free quota. It's off in the app."

`CachingTransport` wraps any other `Transport` and is a `Transport` itself, keyed by model,
prompt version and a hash of the input (the prompt text, the caller's text and the images).
Nothing here is wired into the FastAPI app (Phase 9): only the eval runner (Phase 12) and
manual dev scripts should construct one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from .transport import RawResponse, Transport


def _cache_key(
    *, model: str, prompt_version: str, system_instruction: str, text: str, images: Sequence[bytes]
) -> str:
    hasher = hashlib.sha256()
    hasher.update(model.encode("utf-8"))
    hasher.update(prompt_version.encode("utf-8"))
    hasher.update(system_instruction.encode("utf-8"))
    hasher.update(text.encode("utf-8"))
    for image in images:
        hasher.update(image)
    return hasher.hexdigest()


class CachingTransport:
    """Caches successful calls to disk, one JSON file per cache key, under `cache_dir`.

    Only successful calls are cached: a `TransportError` is never stored, so a cached run
    still exercises the real retry and fallback paths whenever a fresh call is actually made.
    """

    def __init__(self, inner: Transport, *, cache_dir: Path) -> None:
        self._inner = inner
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

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
        key = _cache_key(
            model=model,
            prompt_version=prompt_version,
            system_instruction=system_instruction,
            text=text,
            images=images,
        )
        path = self._path_for(key)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return RawResponse(
                text=data["text"],
                input_tokens=data["input_tokens"],
                output_tokens=data["output_tokens"],
                thinking_tokens=data["thinking_tokens"],
                cached=True,
            )

        response = self._inner.call(
            model=model,
            system_instruction=system_instruction,
            text=text,
            images=images,
            response_schema=response_schema,
            timeout_seconds=timeout_seconds,
            prompt_version=prompt_version,
        )
        path.write_text(
            json.dumps(
                {
                    "text": response.text,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "thinking_tokens": response.thinking_tokens,
                }
            ),
            encoding="utf-8",
        )
        return response
