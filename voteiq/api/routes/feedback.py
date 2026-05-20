from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["feedback"])

_BASE_DIR    = Path(__file__).resolve().parents[3]
_FEEDBACK_DB = _BASE_DIR / "feedback.db"


class FeedbackRequest(BaseModel):
    rating:     int = Field(..., ge=1, le=5)
    query:      str = Field(..., min_length=1, max_length=4000)
    reply_hash: str = ""
    district:   str = ""


def _ensure_feedback_table():
    conn = sqlite3.connect(str(_FEEDBACK_DB))
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            rating     INTEGER NOT NULL,
            query      TEXT    NOT NULL,
            reply_hash TEXT,
            district   TEXT,
            created_at INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()


@router.post("/api/feedback")
async def feedback(req: FeedbackRequest):
    _ensure_feedback_table()
    conn = sqlite3.connect(str(_FEEDBACK_DB))
    conn.execute(
        "INSERT INTO feedback (rating, query, reply_hash, district, created_at) VALUES (?, ?, ?, ?, ?)",
        (req.rating, req.query, req.reply_hash, req.district, int(time.time())),
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Feedback saved"}
