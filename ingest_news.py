#!/usr/bin/env python3
"""
VoteIQ news ingestion pipeline with human review queue.

Flow:
    fetch article → extract via Gemini → flag low-confidence → store
    Only review_status='approved' or 'auto_approved' items surface to users.
"""

import hashlib
import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "openstates_va.db"

CONFIDENCE_THRESHOLD = 0.7


# ── Schema ────────────────────────────────────────────────────────────────────

def init_va_news_table() -> None:
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS va_news (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id     TEXT UNIQUE NOT NULL,
            url            TEXT,
            headline       TEXT,
            source         TEXT,
            published_at   TEXT,
            body           TEXT,
            gemini_json    TEXT,
            flagged        INTEGER DEFAULT 0,
            flag_reasons   TEXT,
            review_status  TEXT DEFAULT 'pending',
            reviewed_at    INTEGER,
            created_at     INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_va_news_flagged
            ON va_news(flagged, review_status);
        CREATE INDEX IF NOT EXISTS idx_va_news_published
            ON va_news(published_at);
    """)
    conn.commit()
    conn.close()


# ── Flagging logic ─────────────────────────────────────────────────────────────

def should_flag_for_review(extracted: dict) -> tuple[bool, list[str]]:
    """
    Return (flagged, reasons). Any reason means human review required
    before the article surfaces in VoteIQ responses.
    """
    reasons: list[str] = []

    for field in ["politicians", "topics", "sentiment"]:
        conf = extracted.get(f"{field}_confidence", 0.0)
        if conf < CONFIDENCE_THRESHOLD:
            reasons.append(f"{field}_confidence={conf:.2f} (below {CONFIDENCE_THRESHOLD})")

    if extracted.get("_warning"):
        reasons.append(f"extraction_warning: {extracted['_warning']}")

    politicians = extracted.get("politicians") or []
    if not politicians:
        reasons.append("no_politicians_extracted")

    sentiment = extracted.get("sentiment")
    if sentiment not in ("positive", "negative", "neutral", "mixed", None):
        reasons.append(f"invalid_sentiment_value: {sentiment!r}")

    return bool(reasons), reasons


# ── Storage ────────────────────────────────────────────────────────────────────

def _article_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def store_news_article(
    *,
    url: str,
    headline: str,
    source: str,
    published_at: str,
    body: str,
    extracted: dict,
) -> dict:
    """
    Flag and store one article. Returns storage metadata.
    Only auto-approved articles (flagged=False) surface to users immediately.
    Flagged articles wait for human review at /admin/review.
    """
    article_id = _article_id(url)
    flagged, reasons = should_flag_for_review(extracted)

    extracted["_flagged"] = int(flagged)
    extracted["_flag_reasons"] = reasons

    review_status = "pending" if flagged else "auto_approved"

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT OR REPLACE INTO va_news
            (article_id, url, headline, source, published_at, body,
             gemini_json, flagged, flag_reasons, review_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            article_id, url, headline, source, published_at, body,
            json.dumps(extracted), int(flagged), json.dumps(reasons),
            review_status, int(time.time()),
        ),
    )
    conn.commit()
    conn.close()

    return {
        "article_id": article_id,
        "flagged": flagged,
        "flag_reasons": reasons,
        "review_status": review_status,
    }


# ── Queue helpers (used by main.py endpoints) ──────────────────────────────────

def get_review_queue(limit: int = 20, status: str = "pending") -> list[dict]:
    """Fetch flagged articles awaiting human review."""
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """
        SELECT id, article_id, url, headline, source, published_at,
               gemini_json, flag_reasons, review_status, created_at
        FROM va_news
        WHERE flagged = 1 AND review_status = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (status, limit),
    ).fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "article_id": r[1],
            "url": r[2],
            "headline": r[3],
            "source": r[4],
            "published_at": r[5],
            "extracted": _safe_json(r[6]),
            "flag_reasons": _safe_json(r[7]),
            "review_status": r[8],
            "created_at": r[9],
        }
        for r in rows
    ]


def set_review_status(article_id: str, status: str) -> bool:
    """Approve or reject a flagged article. Returns True if found."""
    if status not in ("approved", "rejected"):
        raise ValueError("status must be 'approved' or 'rejected'")
    if not DB_PATH.exists():
        return False
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "UPDATE va_news SET review_status=?, reviewed_at=? WHERE article_id=?",
        (status, int(time.time()), article_id),
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def queue_stats() -> dict:
    """Summary counts for the review dashboard."""
    if not DB_PATH.exists():
        return {}
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT review_status, COUNT(*) FROM va_news GROUP BY review_status"
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def _safe_json(val):
    try:
        return json.loads(val) if val else {}
    except Exception:
        return {}


init_va_news_table()
