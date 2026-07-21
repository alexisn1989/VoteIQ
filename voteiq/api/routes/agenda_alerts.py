"""
Agenda email alerts — subscribe to a digest of new City Council / Planning
Commission agenda items near an address and/or matching keywords, across all
7 Hampton Roads cities VoteIQ covers.

POST /api/agenda-alerts/subscribe        create a pending subscription, emails a confirm link
GET  /api/agenda-alerts/confirm          confirm via the emailed token
GET  /api/agenda-alerts/unsubscribe      unsubscribe via the emailed token
POST /api/agenda-alerts/run-digest       admin: run send_agenda_digest.py now
"""
from __future__ import annotations

import os
import re
import secrets
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

from voteiq.api.dependencies import require_admin_token
from voteiq.services.geo_lite import geocode_lite

router = APIRouter(tags=["agenda-alerts"])

_BASE_DIR = Path(__file__).resolve().parents[3]
_POLLS_DB = os.path.join(os.getenv("DATA_DIR", str(_BASE_DIR)), "polls.db")
_SERVICE_URL = os.getenv("VOTEIQ_SERVICE_URL", "https://voteiq.io").rstrip("/")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

CITY_CHOICES = [
    "Chesapeake", "Suffolk", "Hampton", "Newport News", "Portsmouth",
    "Norfolk", "Virginia Beach",
]


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_POLLS_DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    return c


_schema_ensured = False


def _ensure_schema(conn: sqlite3.Connection) -> None:
    global _schema_ensured
    if _schema_ensured:
        return
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agenda_alert_subscriptions (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            email             TEXT NOT NULL,
            address           TEXT,
            lat               REAL,
            lng               REAL,
            radius_miles      REAL DEFAULT 2.0,
            cities            TEXT,
            keywords          TEXT,
            confirmed         INTEGER DEFAULT 0,
            confirm_token     TEXT NOT NULL,
            unsubscribe_token TEXT NOT NULL,
            created_at        TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS agenda_alert_sent (
            subscription_id INTEGER NOT NULL,
            source_table    TEXT NOT NULL,
            item_id         INTEGER NOT NULL,
            sent_at         TEXT DEFAULT (datetime('now')),
            UNIQUE(subscription_id, source_table, item_id)
        );
    """)
    conn.commit()
    _schema_ensured = True


class SubscribeRequest(BaseModel):
    email: str
    address: Optional[str] = Field(None, description="Street address to center 'near me' matching on")
    radius_miles: float = Field(2.0, ge=0.1, le=25)
    cities: Optional[list[str]] = Field(None, description="Restrict to specific cities; omit for all 7")
    keywords: Optional[str] = Field(None, description="Comma-separated keywords to also match in item titles")

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("invalid email address")
        return v


@router.post("/agenda-alerts/subscribe", status_code=201)
def subscribe(body: SubscribeRequest):
    # Imported lazily so a broken/missing RESEND_API_KEY at startup can't take
    # down this whole route module via _safe_include's import-time try/except.
    from voteiq.services.email import send_email

    if not body.address and not body.cities and not body.keywords:
        raise HTTPException(400, "Provide at least one of: address, cities, keywords")

    lat = lng = None
    matched_address = None
    if body.address:
        geo = geocode_lite(body.address)
        if not geo:
            raise HTTPException(422, f"Could not geocode address: {body.address!r}")
        lat, lng, matched_address = geo["lat"], geo["lng"], geo["matched_address"]

    if body.cities:
        bad = [c for c in body.cities if c not in CITY_CHOICES]
        if bad:
            raise HTTPException(400, f"Unknown cities: {bad}. Choose from {CITY_CHOICES}")

    conn = _conn()
    _ensure_schema(conn)
    confirm_token = secrets.token_urlsafe(24)
    unsub_token = secrets.token_urlsafe(24)
    cur = conn.execute(
        "INSERT INTO agenda_alert_subscriptions "
        "(email, address, lat, lng, radius_miles, cities, keywords, confirm_token, unsubscribe_token) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (body.email, matched_address or body.address, lat, lng, body.radius_miles,
         ",".join(body.cities) if body.cities else None, body.keywords,
         confirm_token, unsub_token),
    )
    conn.commit()
    sub_id = cur.lastrowid
    conn.close()

    confirm_url = f"{_SERVICE_URL}/api/agenda-alerts/confirm?token={confirm_token}"
    html = (
        f"<p>Confirm your VoteIQ agenda alert subscription"
        f"{' for ' + matched_address if matched_address else ''}.</p>"
        f'<p><a href="{confirm_url}">Confirm subscription</a></p>'
        f"<p>If you didn't request this, ignore this email.</p>"
    )
    result = send_email(body.email, "Confirm your VoteIQ agenda alerts", html)
    return JSONResponse({
        "ok": True,
        "subscription_id": sub_id,
        "email_sent": result["ok"],
        "email_error": result.get("error"),
    }, status_code=201)


@router.get("/agenda-alerts/confirm", response_class=HTMLResponse)
def confirm(token: str = Query(...)):
    conn = _conn()
    _ensure_schema(conn)
    n = conn.execute(
        "UPDATE agenda_alert_subscriptions SET confirmed=1 WHERE confirm_token=?", (token,)
    ).rowcount
    conn.commit()
    conn.close()
    if not n:
        return HTMLResponse("<p>Invalid or already-used confirmation link.</p>", status_code=404)
    return HTMLResponse("<p>Subscription confirmed — you'll get a digest email when new agenda items match.</p>")


@router.get("/agenda-alerts/unsubscribe", response_class=HTMLResponse)
def unsubscribe(token: str = Query(...)):
    conn = _conn()
    _ensure_schema(conn)
    n = conn.execute(
        "DELETE FROM agenda_alert_subscriptions WHERE unsubscribe_token=?", (token,)
    ).rowcount
    conn.commit()
    conn.close()
    if not n:
        return HTMLResponse("<p>Invalid or already-used unsubscribe link.</p>", status_code=404)
    return HTMLResponse("<p>You've been unsubscribed.</p>")


@router.post("/agenda-alerts/run-digest")
def run_digest_now(_: None = Depends(require_admin_token)):
    """Admin: trigger the digest script immediately."""
    script = str(_BASE_DIR / "send_agenda_digest.py")
    result = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=120)
    return JSONResponse({
        "ok": result.returncode == 0,
        "stdout": result.stdout[-2000:],
        "stderr": result.stderr[-500:] if result.returncode != 0 else "",
    })
