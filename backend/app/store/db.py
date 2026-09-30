"""The one place a SQLite connection to `app.db` gets opened (design section 4 and 9: "WAL
mode, one connection per thread, short transactions").

WAL (write-ahead logging) mode lets a reader (the UI, later) read the database while a run is
still writing to it, without blocking either side -- the whole reason design section 4 calls
it out explicitly for a pipeline that writes progress as it goes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.config import settings

from .schema import init_db

DB_FILENAME = "app.db"


def db_path(*, data_dir: Path | None = None) -> Path:
    base = data_dir if data_dir is not None else settings.data_dir
    return base / DB_FILENAME


def connect(*, data_dir: Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) `app.db`, in WAL mode, with the schema applied.

    Each call opens its own connection -- SQLite connections aren't meant to be shared across
    threads, and design section 9 calls for "one connection per thread". Short-lived scripts
    (the CLI, a test) open one, use it, and close it; the API (Phase 9, not built this
    session) would open one per request/background task.
    """
    path = db_path(data_dir=data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn
