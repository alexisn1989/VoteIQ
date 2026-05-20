import os
from typing import Optional

import anthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config.voices import (
    TIER_VOICE_MAP,
    TIER_MAX_TOKENS,
    VOICE_PROMPTS,
    VOICE_CONFIG,
)

router = APIRouter()

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
_MODEL  = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-6")


# ── Request / response models ─────────────────────────────────────────────────

class VoiceQueryRequest(BaseModel):
    message:   str
    tier:      str          = "free"
    voice:     Optional[str] = None
    member_id: Optional[str] = None
    level:     str           = "federal"
    cycle:     Optional[str] = None
    context:   Optional[str] = None


class VoiceQueryResponse(BaseModel):
    response: str
    voice:    str
    tier:     str


# ── Shared handler ────────────────────────────────────────────────────────────

def _handle_voice_query(
    req: VoiceQueryRequest,
    required_voice: str,
) -> VoiceQueryResponse:
    tier  = req.tier
    voice = req.voice or required_voice

    allowed = TIER_VOICE_MAP.get(tier, ["free"])
    if voice not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"Voice '{voice}' is not available on the '{tier}' tier. "
                   f"Allowed: {allowed}",
        )

    system_prompt = VOICE_PROMPTS[voice]
    max_tokens    = TIER_MAX_TOKENS.get(tier, 800)

    user_content = req.message
    if req.context:
        user_content = f"{req.context}\n\n---\n\n{req.message}"

    response = _client.messages.create(
        model      = _MODEL,
        max_tokens = max_tokens,
        system     = system_prompt,
        messages   = [{"role": "user", "content": user_content}],
    )

    return VoiceQueryResponse(
        response = response.content[0].text,
        voice    = voice,
        tier     = tier,
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/campaign/query", response_model=VoiceQueryResponse)
async def campaign_query(req: VoiceQueryRequest) -> VoiceQueryResponse:
    return _handle_voice_query(req, required_voice="campaign")


@router.post("/academic/query", response_model=VoiceQueryResponse)
async def academic_query(req: VoiceQueryRequest) -> VoiceQueryResponse:
    return _handle_voice_query(req, required_voice="academic")


@router.post("/enterprise/query", response_model=VoiceQueryResponse)
async def enterprise_query(req: VoiceQueryRequest) -> VoiceQueryResponse:
    return _handle_voice_query(req, required_voice="enterprise")
