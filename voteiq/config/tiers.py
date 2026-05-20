"""Model tier defaults."""
from __future__ import annotations

import os

_DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# ── Legacy model-tier map (V1 pipeline) ──────────────────────────────────────

MODEL_TIERS = {
    "fast":     "claude-haiku-4-5-20251001",
    "standard": "claude-sonnet-4-6",
    "analyst":  "claude-sonnet-4-6",
}

DEFAULT_TIER = "standard"

# ── Per-tier feature config (V2 chat pipeline) ────────────────────────────────

TIER_FEATURES = {
    "free":       {"model": _DEFAULT_MODEL},
    "pro":        {"model": _DEFAULT_MODEL},
    "newsroom":   {"model": _DEFAULT_MODEL},
    "campaign":   {"model": _DEFAULT_MODEL},
    "academic":   {"model": _DEFAULT_MODEL},
    "enterprise": {"model": _DEFAULT_MODEL},
}

# ── Per-tier token budgets ────────────────────────────────────────────────────

TIER_MAX_TOKENS = {
    "free":       800,
    "pro":        1500,
    "newsroom":   2000,
    "campaign":   2000,
    "academic":   2000,
    "enterprise": 3000,
}
