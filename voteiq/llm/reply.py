"""Shared LLM reply layer — Claude and Gemini call plumbing for the chat routes.

Extracted verbatim from the root ``main.py`` god module (phase 1 of its
decomposition). ``main.py`` re-imports these names so legacy
``import main as _m`` callers keep working; new code should import from
``voteiq.llm.reply`` directly.

Distinct from ``voteiq.llm.claude_client`` (report generation with guardrails)
and ``voteiq.api.claude`` (streaming + tier model selection).
"""
from __future__ import annotations

import os
import re
import time

import anthropic

_CLAUDE_TEMPORARY_MESSAGE = (
    "VoteIQ is getting a lot of AI traffic right now. Please try your question again in a moment."
)

_CLAUDE_SONNET_MODEL = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-6")
_CLAUDE_HAIKU_MODEL = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")

_GEMINI_MODEL = "gemini-2.5-flash"

# Lazy so importing this module never requires ANTHROPIC_API_KEY (tests import
# the chat routes without any LLM credentials configured).
_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def _is_retryable_claude_error(error):
    status_code = getattr(error, "status_code", None)
    text = str(error).lower()
    return (
        status_code in {429, 500, 502, 503, 504, 529}
        or "overloaded" in text
        or "rate_limit" in text
        or "temporarily unavailable" in text
    )


def _friendly_claude_error(error):
    if _is_retryable_claude_error(error):
        return _CLAUDE_TEMPORARY_MESSAGE
    return "VoteIQ's AI assistant is temporarily unavailable. Please try again shortly."


def _claude_reply(system_prompt, messages, max_tokens, model: str | None = None,
                  cache_ttl: str | None = None):
    # cache_ttl ("5m" or "1h") rides on the system block's cache_control;
    # 1h costs 2x on cache writes but keeps research sessions warm across
    # the gaps between questions.
    model_name = model or _CLAUDE_SONNET_MODEL
    cache_control: dict = {"type": "ephemeral"}
    if cache_ttl and cache_ttl != "5m":
        cache_control["ttl"] = cache_ttl
    system_blocks = [{
        "type": "text",
        "text": system_prompt,
        "cache_control": cache_control,
    }]
    last_error = None
    for attempt in range(3):
        try:
            response = _get_client().messages.create(
                model=model_name,
                max_tokens=max_tokens,
                system=system_blocks,
                messages=[{"role": m.role, "content": m.content} for m in messages],
            )
            return response.content[0].text
        except Exception as error:
            last_error = error
            if not _is_retryable_claude_error(error) or attempt == 2:
                raise
            time.sleep(0.75 * (attempt + 1))
    raise last_error


def _gemini_reply(system_prompt, messages, max_tokens):
    """Helper to call Gemini API with system instructions."""
    import google.genai as genai
    from google.genai import types as _gtypes
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    contents = [
        _gtypes.Content(
            role="user" if m.role == "user" else "model",
            parts=[_gtypes.Part(text=m.content)],
        )
        for m in messages
    ]
    response = client.models.generate_content(
        model=_GEMINI_MODEL,
        contents=contents,
        config=_gtypes.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_tokens,
        ),
    )
    return response.text


_SENTENCE_END_RE = re.compile(r'[.!?]\s')


def _graceful_truncate(reply: str) -> str:
    """Trim to last complete sentence when the reply appears to end mid-sentence."""
    if not reply:
        return reply
    stripped = reply.rstrip()
    # Ends cleanly: sentence punct, markdown fence/table/list/emphasis terminators
    if re.search(r'[.!?)”`|*_~-]\s*$', stripped):
        return reply
    # Search the last 1 000 chars — the truncation point is always near the end
    # regardless of total response length, so this avoids the len//2 cut-too-much problem
    search_start = max(0, len(stripped) - 1000)
    matches = list(_SENTENCE_END_RE.finditer(stripped, search_start))
    if not matches:
        return reply  # no clean cut point — return as-is
    cut = matches[-1].end()
    return stripped[:cut].rstrip() + '\n\n*(Response cut short — ask a follow-up for more.)*'
