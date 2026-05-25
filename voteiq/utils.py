from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def clean_json_text(text: str) -> str:
    text = text.strip()

    if text.startswith("```json"):
        text = text.removeprefix("```json").strip()
    elif text.startswith("```"):
        text = text.removeprefix("```").strip()

    if text.endswith("```"):
        text = text.removesuffix("```").strip()

    return text


_clean_json_text = clean_json_text


# ── Election Data Loading (shared by elections.py and main.py) ─────────────────

_BASE_DIR  = Path(__file__).resolve().parent.parent
_DATA_DIR  = Path(os.getenv("DATA_DIR", str(_BASE_DIR)))

# Cache for historical election results (2016-2020)
_historical_data_cache: dict[str, Any] = {}


def _data_path(*parts: str) -> Path:
    """Get path to data directory file."""
    return _DATA_DIR.joinpath(*parts)


def _load_historical_results(year: str) -> dict:
    """Load election results for a given year from JSON file.

    This function is used by both elections.py and main.py to load historical
    election data without creating a circular import.
    """
    year = str(year)
    if year in _historical_data_cache:
        return _historical_data_cache[year]
    try:
        with _data_path(f"election_results_{year}.json").open(encoding="utf-8") as f:
            _historical_data_cache[year] = json.load(f)
    except Exception as e:
        print(f"_load_historical_results {year}: {e}")
        _historical_data_cache[year] = {}
    return _historical_data_cache[year]
