"""Application settings.

Every setting comes from an environment variable, with its default defined here and
nowhere else. Values in backend/.env are loaded first; real environment variables win.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent

# override=False: a variable already set in the shell takes precedence over the file.
load_dotenv(BACKEND_DIR / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_model: str
    # Used only when the primary model fails after its retries or its daily quota is gone.
    # Empty string disables the fallback.
    gemini_fallback_model: str
    confidence_threshold: float
    data_dir: Path

    @property
    def has_gemini_key(self) -> bool:
        return bool(self.gemini_api_key)


def load_settings() -> Settings:
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip(),
        gemini_fallback_model=os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite").strip(),
        confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.85")),
        data_dir=Path(os.getenv("DATA_DIR", str(REPO_DIR / "data"))),
    )


settings = load_settings()
