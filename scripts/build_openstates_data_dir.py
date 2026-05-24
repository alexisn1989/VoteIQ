#!/usr/bin/env python3
"""Build openstates_va.db in DATA_DIR for Render persistent disks."""
from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
DATA_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR))

import build_bill_descriptions  # noqa: E402
import build_openstates_db  # noqa: E402


build_openstates_db.DB_PATH = str(DATA_DIR / "openstates_va.db")
build_bill_descriptions.DB_PATH = DATA_DIR / "openstates_va.db"


if __name__ == "__main__":
    build_openstates_db.main()
