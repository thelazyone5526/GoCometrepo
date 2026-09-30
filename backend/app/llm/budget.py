"""The per-run call budget (design section 3.6 and 8): at most 6 calls to any model, primary
or fallback, per document. `CallBudget` is created once per run and shared by every node's
calls into `LLMClient` for that run, so the 6-call ceiling is really per document, not per
node or per agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import BudgetExceededError

DEFAULT_MAX_CALLS = 6


@dataclass
class CallBudget:
    max_calls: int = DEFAULT_MAX_CALLS
    calls_used: int = field(default=0, init=False)

    @property
    def remaining(self) -> int:
        return self.max_calls - self.calls_used

    def charge(self) -> None:
        """Spend one call. Raises `BudgetExceededError`, and spends nothing, if none is left.

        Called once per model sequence attempted (once for the primary, again if a fallback
        is attempted), *before* the first request of that sequence goes out -- so a refused
        7th call never reaches the network, whether it would have been a primary or a
        fallback call (design section 3.6: "a fallback call counts against the budget").
        """
        if self.calls_used >= self.max_calls:
            raise BudgetExceededError(f"LLM call budget exhausted ({self.max_calls} calls per run)")
        self.calls_used += 1
