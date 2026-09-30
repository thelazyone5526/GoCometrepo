"""Shared pytest fixtures for the backend test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from rapidocr import RapidOCR


@pytest.fixture(scope="session")
def ocr_engine() -> Iterator[RapidOCR]:
    """One RapidOCR engine for the whole test session.

    Loading the ONNX models takes a couple of seconds; every test that needs OCR shares this
    instance instead of paying that cost per test.
    """
    yield RapidOCR()
