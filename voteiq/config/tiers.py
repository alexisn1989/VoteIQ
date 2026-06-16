"""Model tier defaults."""
from __future__ import annotations

import os

_DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
_OPUS_MODEL = os.getenv("CLAUDE_OPUS_MODEL", "claude-opus-4-8")

# ── Legacy model-tier map (V1 pipeline) ──────────────────────────────────────

MODEL_TIERS = {
    "fast":     "claude-haiku-4-5-20251001",
    "standard": "claude-sonnet-4-6",
    "analyst":  "claude-sonnet-4-6",
}

DEFAULT_TIER = "standard"

# ── Per-tier feature config (V2 chat pipeline) ────────────────────────────────
# Paid tiers get Opus for deeper analysis; free stays on the Sonnet default.
# Override either via env (ANTHROPIC_MODEL / CLAUDE_OPUS_MODEL) without a deploy.

TIER_FEATURES = {
    "free":       {"model": _DEFAULT_MODEL},
    "pro":        {"model": _DEFAULT_MODEL},   # Sonnet default; Opus via get_model(complex_query=True)
    "newsroom":   {"model": _DEFAULT_MODEL},
    "campaign":   {"model": _DEFAULT_MODEL},
    "academic":   {"model": _DEFAULT_MODEL},
    "enterprise": {"model": _DEFAULT_MODEL},
}

# ── Per-tier token budgets ────────────────────────────────────────────────────

TIER_MAX_TOKENS = {
    "free":       1200,   # raised from 800 — bill-detail / spike-alert responses truncated mid-sentence
    "pro":        2500,   # raised from 1500 — demo beats 2/4/5/6 need ~1800-2400 for complete analytical output
    "newsroom":   3500,   # raised from 2000 — investigative analyses run long
    "campaign":   2500,   # raised from 2000
    "academic":   2500,   # raised from 2000
    "enterprise": 4000,   # raised from 3000
}
