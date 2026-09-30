"""Tests for the prompt file loader (design section 3.6: files with a version line)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.llm.prompt_files import PromptFileError, load_prompt


def test_ping_v1_loads_with_its_version() -> None:
    prompt = load_prompt("ping_v1")
    assert prompt.name == "ping_v1"
    assert prompt.version == "1"
    assert "JSON object" in prompt.text
    assert not prompt.text.startswith("version:")


def test_missing_file_raises_prompt_file_error(tmp_path: Path) -> None:
    with pytest.raises(PromptFileError):
        load_prompt("does_not_exist", prompts_dir=tmp_path)


def test_missing_header_raises_prompt_file_error(tmp_path: Path) -> None:
    (tmp_path / "bad.txt").write_text("just some text, no header", encoding="utf-8")
    with pytest.raises(PromptFileError):
        load_prompt("bad", prompts_dir=tmp_path)
