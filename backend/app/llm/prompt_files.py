"""Load a prompt file and split off its version line.

Every file in `llm/prompts/` starts with a `version: N` line, then `---` on its own line, then
the prompt text (design section 3.6: "prompts live in files ... with a version string stored
alongside each call"). Keeping the version in the file itself, rather than in a separate
manifest, means a prompt's text and its version can never drift apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_HEADER_RE = re.compile(r"^version:\s*(\S+)\s*\n---\s*\n", re.MULTILINE)


class PromptFileError(Exception):
    """A prompt file is missing, or doesn't start with the `version: N\\n---\\n` header."""


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    text: str


def load_prompt(name: str, *, prompts_dir: Path = PROMPTS_DIR) -> Prompt:
    """Load `<prompts_dir>/<name>.txt`, e.g. `load_prompt("extract_v1")`."""
    path = prompts_dir / f"{name}.txt"
    if not path.exists():
        raise PromptFileError(f"No prompt file at {path}")
    raw = path.read_text(encoding="utf-8")
    match = _HEADER_RE.match(raw)
    if not match:
        raise PromptFileError(f"{path} must start with 'version: N' then '---' on its own line")
    return Prompt(name=name, version=match.group(1), text=raw[match.end() :].strip())
