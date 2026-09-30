"""The call recorder (design section 3.6): logs every attempt, successful or not, primary or
fallback. It writes to memory for now; Phase 8 points it at the `llm_calls` table instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

# One record per HTTP-level attempt (an attempt that's retried counts as several records).
Status = str  # "success", "success_cached", "retryable_error", "daily_quota",
# "non_retryable_error", "invalid_schema"


@dataclass(frozen=True)
class CallRecord:
    run_id: str
    agent: str
    model: str
    is_fallback: bool
    prompt_name: str
    prompt_version: str
    attempt: int
    status: Status
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    latency_ms: float
    error: str | None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class CallRecorder:
    """An in-memory `llm_calls` log. One instance is shared across a run's calls."""

    def __init__(self) -> None:
        self._records: list[CallRecord] = []

    def record(
        self,
        *,
        run_id: str,
        agent: str,
        model: str,
        is_fallback: bool,
        prompt_name: str,
        prompt_version: str,
        attempt: int,
        status: Status,
        input_tokens: int,
        output_tokens: int,
        thinking_tokens: int,
        latency_ms: float,
        error: str | None,
    ) -> CallRecord:
        rec = CallRecord(
            run_id=run_id,
            agent=agent,
            model=model,
            is_fallback=is_fallback,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            attempt=attempt,
            status=status,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thinking_tokens=thinking_tokens,
            latency_ms=latency_ms,
            error=error,
        )
        self._records.append(rec)
        return rec

    @property
    def records(self) -> list[CallRecord]:
        return list(self._records)

    def for_run(self, run_id: str) -> list[CallRecord]:
        return [r for r in self._records if r.run_id == run_id]
