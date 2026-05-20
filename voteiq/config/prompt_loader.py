"""Prompt loading helpers."""
from __future__ import annotations

from voteiq.config import prompts


def get_prompt(name: str) -> str:
    try:
        return prompts.PROMPTS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown prompt: {name}") from exc

