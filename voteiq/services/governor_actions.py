from __future__ import annotations

import json

from voteiq.analysis.governor_action_money import (
    build_governor_action_money_payload,
)
from voteiq.api.claude import get_claude_client, get_model
from voteiq.config.prompts import GOVERNOR_ACTION_MONEY_PROMPT
from voteiq.queries.governor_actions import (
    get_governor_action_bills,
    get_governor_action_counts,
    get_governor_action_money_patterns,
    get_top_sponsors_by_action,
)


def build_governor_action_money_analysis(
    session: str = "2026",
    finance_cycle: str = "2025",
    governor: str = "Spanberger",
    voice: str = "pro",
) -> dict:
    """
    Build structured data + Claude narrative for Governor Action Money Analyst.
    """
    action_counts = get_governor_action_counts(
        session=session,
        governor=governor,
    )

    money_rows = get_governor_action_money_patterns(
        session=session,
        finance_cycle=finance_cycle,
        governor=governor,
    )

    sponsor_rows = get_top_sponsors_by_action(
        session=session,
        governor=governor,
        finance_cycle=finance_cycle,
    )

    payload = build_governor_action_money_payload(
        action_counts=action_counts,
        money_rows=money_rows,
        sponsor_rows=sponsor_rows,
        session=session,
        finance_cycle=finance_cycle,
        governor=governor,
    )

    model, max_tokens = get_model(voice)

    prompt = GOVERNOR_ACTION_MONEY_PROMPT.format(
        payload=json.dumps(payload, indent=2),
    )

    client = get_claude_client()

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=(
            "You are VoteIQ's nonpartisan civic intelligence analyst. "
            "Use only the provided data. Do not infer causation."
        ),
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
    )

    return {
        "status": "success",
        "analyst": "governor_action_money",
        "session": session,
        "finance_cycle": finance_cycle,
        "governor": governor,
        "payload": payload,
        "report": response.content[0].text,
    }


def list_governor_action_bills(
    session: str = "2026",
    governor: str = "Spanberger",
    action_label: str | None = None,
) -> dict:
    rows = get_governor_action_bills(
        session=session,
        governor=governor,
        action_label=action_label,
    )

    return {
        "status": "success",
        "session": session,
        "governor": governor,
        "action_label": action_label,
        "count": len(rows),
        "results": [dict(r) for r in rows],
    }
