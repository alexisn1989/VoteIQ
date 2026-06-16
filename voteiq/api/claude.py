from __future__ import annotations

import json
import os
from typing import AsyncIterator

import anthropic

_HAIKU_MODEL  = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
_SONNET_MODEL = os.getenv("ANTHROPIC_MODEL",     "claude-sonnet-4-6")
_OPUS_MODEL   = os.getenv("CLAUDE_OPUS_MODEL",   "claude-opus-4-8")


def get_claude_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    return anthropic.Anthropic(api_key=api_key)


def cached_system_prompt(system: str) -> list[dict]:
    """Wrap a system prompt as an Anthropic cacheable text block."""
    return [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def get_model(tier: str, use_haiku: bool = False, simple: bool = False,
              complex_query: bool = False) -> tuple[str, int]:
    """
    Returns (model_string, max_tokens) for the given tier.
    use_haiku=True     — Haiku (exact bill cache hits)
    complex_query=True — Opus  (multi-signal analysis; paid tiers only)
    simple=True        — Sonnet (legacy short-form shortcut; kept for callers)
    default            — Sonnet (all tiers; Opus is opt-in via complex_query)
    """
    from voteiq.config.tiers import TIER_FEATURES, TIER_MAX_TOKENS
    tier = (tier or "free").lower().strip()
    if use_haiku:
        model = _HAIKU_MODEL
    elif complex_query and tier != "free":
        model = _OPUS_MODEL
    else:
        model = TIER_FEATURES.get(tier, TIER_FEATURES["free"])["model"]
    max_tokens = TIER_MAX_TOKENS.get(tier, 1500)
    return model, max_tokens


async def stream_claude(
    messages:   list[dict],
    system:     str,
    model:      str,
    max_tokens: int,
) -> AsyncIterator[str]:
    """
    Async SSE stream from Claude.
    Yields data: {...} lines, closes with data: [DONE]
    """
    client = get_claude_client()
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        cache_control={"type": "ephemeral"},
        system=cached_system_prompt(system),
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            yield f"data: {json.dumps({'token': text})}\n\n"
    yield "data: [DONE]\n\n"


async def call_claude(
    messages:   list[dict],
    system:     str,
    model:      str,
    max_tokens: int,
) -> str:
    """
    Non-streaming Claude call. Returns full response text.
    """
    client = get_claude_client()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        cache_control={"type": "ephemeral"},
        system=cached_system_prompt(system),
        messages=messages,
    )
    return response.content[0].text
