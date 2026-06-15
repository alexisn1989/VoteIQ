"""Single source of truth for the polls.db path.

Priority:
  1. DB_PATH env var  — explicit file path (override / testing)
  2. DATA_DIR env var — directory; polls.db expected inside it
     If DATA_DIR is set but polls.db is missing: fatal exit (disk not mounted)
  3. Local dev fallback: project root

On Render, render.yaml sets DATA_DIR=/var/data and mounts the persistent disk there.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent


def _resolve_polls_db() -> Path:
    if explicit := os.getenv("DB_PATH"):
        p = Path(explicit)
        if not p.exists():
            sys.exit(
                f"[FATAL] DB_PATH={explicit} does not exist — "
                "is the persistent disk mounted?"
            )
        return p
    if data_dir := os.getenv("DATA_DIR"):
        p = Path(data_dir) / "polls.db"
        if not p.exists():
            sys.exit(
                f"[FATAL] DATA_DIR={data_dir} is set but {p} does not exist. "
                "Persistent disk may not be mounted."
            )
        return p
    return _BASE_DIR / "polls.db"


POLLS_DB: Path = _resolve_polls_db()
