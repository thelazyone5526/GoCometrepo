"""Gemini client wrapper (retries, budget, call log). Prompt files live in prompts/.

`LLMClient` (`client.py`) is the entry point every node should use. It's built from a
`Transport` (`GeminiTransport` for real calls, `FakeTransport` for tests -- never construct
`LLMClient` directly against `google-genai`), a `CallBudget` and a `CallRecorder`.
"""

from .budget import CallBudget
from .client import GenerateResult, LLMClient
from .errors import BudgetExceededError, LLMUnavailableError
from .gemini_transport import GeminiTransport
from .prompt_files import Prompt, load_prompt
from .recorder import CallRecorder

__all__ = [
    "BudgetExceededError",
    "CallBudget",
    "CallRecorder",
    "GeminiTransport",
    "GenerateResult",
    "LLMClient",
    "LLMUnavailableError",
    "Prompt",
    "load_prompt",
]
