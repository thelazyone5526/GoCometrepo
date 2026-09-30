"""The two errors `LLMClient.generate` can raise, and nothing else.

Both are meant to be caught by the calling node (Phase 5 onward) and turned into
`state.error` or field-level uncertainty (design section 3.6 and 8): the wrapper's job is to
make the failure unambiguous, not to decide what the pipeline does about it.
"""

from __future__ import annotations


class BudgetExceededError(Exception):
    """The per-run call budget (`CallBudget`, default 6) has no room for another call, so
    no request was made at all -- not even a first attempt."""


class LLMUnavailableError(Exception):
    """Every model that was tried failed for a real reason (not the budget): the primary's
    retries were exhausted, and either the fallback is disabled, or it failed too."""
