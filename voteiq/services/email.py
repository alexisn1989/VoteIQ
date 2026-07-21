"""Thin Resend REST API wrapper — one requests.post call, no SDK dependency.

Requires RESEND_API_KEY in the environment (set in the Render dashboard).
AGENDA_ALERTS_FROM_EMAIL sets the sender; it must be on a domain verified in
the Resend dashboard, or Resend's own sandbox address (only deliverable to
the Resend account's own verified email) before a domain is set up.
"""
from __future__ import annotations

import os

import requests

RESEND_API_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "VoteIQ Agenda Alerts <onboarding@resend.dev>"


def send_email(to: str, subject: str, html: str) -> dict:
    """Send one email via Resend. Returns {"ok": bool, "error": str | None}."""
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        return {"ok": False, "error": "RESEND_API_KEY not set"}
    from_addr = os.getenv("AGENDA_ALERTS_FROM_EMAIL", DEFAULT_FROM)
    try:
        r = requests.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"from": from_addr, "to": [to], "subject": subject, "html": html},
            timeout=15,
        )
        if r.status_code >= 300:
            return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:300]}"}
        return {"ok": True, "error": None}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
