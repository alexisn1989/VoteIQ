"""News routes — Virginia political news feed + admin review queue."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from voteiq.api.dependencies import require_admin_token
from voteiq.utils import clean_json_text

router = APIRouter(tags=["news"])

_BASE_DIR = str(Path(__file__).resolve().parent.parent.parent.parent)
_POLLS_DB = os.path.join(os.getenv("DATA_DIR", _BASE_DIR), "polls.db")


def _ingest_news():
    import ingest_news as _m
    return _m


@router.get("/news", response_class=HTMLResponse)
def news_page():
    with open(os.path.join(_BASE_DIR, "templates", "news.html"), encoding="utf-8") as f:
        return f.read()


@router.get("/api/va-news")
def va_news(limit: int = 30, topic: str | None = None, politician: str | None = None):
    """Return Virginia political news extracted by Gemini."""
    import sqlite3
    if not os.path.exists(_POLLS_DB):
        return {"count": 0, "results": [], "source": "missing_polls_db"}
    limit = max(1, min(int(limit or 30), 100))
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='va_news'"
        ).fetchone()
        if not tbl:
            conn.close()
            return {"count": 0, "results": [], "source": "va_news_not_initialized"}
        rows = conn.execute(
            "SELECT article_id, source, url, title, published_at, gemini_json, fetched_at "
            "FROM va_news ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT ?",
            (limit * 3,),
        ).fetchall()
        conn.close()
        results = []
        for row in rows:
            item = dict(row)
            gj = item.pop("gemini_json", None)
            item["data"] = json.loads(clean_json_text(gj)) if gj else {}
            if topic:
                topics = item["data"].get("topics") or []
                if not any(topic.lower() in t.lower() for t in topics):
                    continue
            if politician:
                pols = [p.get("name", "") for p in (item["data"].get("politicians") or [])]
                if not any(politician.lower() in p.lower() for p in pols):
                    continue
            results.append(item)
            if len(results) >= limit:
                break
        return {"count": len(results), "source": "va_news", "results": results}
    except Exception as exc:
        return {"count": 0, "results": [], "source": "va_news_error", "error": str(exc)}


@router.get("/api/admin/ingest-news")
def admin_ingest_news(limit: int = 50, _: None = Depends(require_admin_token)):
    """Trigger Virginia political news ingestion via Gemini."""
    if not os.getenv("GEMINI_API_KEY"):
        return {"ok": False, "error": "GEMINI_API_KEY not set"}
    script = os.path.join(_BASE_DIR, "ingest_va_news.py")
    if not os.path.exists(script):
        return {"ok": False, "error": "ingest_va_news.py not found"}
    cmd = [sys.executable, script, "--db", _POLLS_DB, "--limit", str(limit)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-500:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timed out after 300s"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/admin/review", response_class=HTMLResponse)
async def admin_review_page(request: Request, _: None = Depends(require_admin_token)):
    """Human review queue for flagged news articles."""
    with open(os.path.join(_BASE_DIR, "templates", "review.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


@router.get("/api/admin/review-queue")
def api_review_queue(
    limit: int = 20,
    status: str = "pending",
    _: None = Depends(require_admin_token),
):
    """Return flagged articles awaiting review."""
    m = _ingest_news()
    return {"items": m.get_review_queue(limit=limit, status=status), "stats": m.queue_stats()}


@router.post("/api/admin/review/{article_id}/approve")
def api_review_approve(article_id: str, _: None = Depends(require_admin_token)):
    m = _ingest_news()
    if not m.set_review_status(article_id, "approved"):
        raise HTTPException(status_code=404, detail="Article not found")
    return {"ok": True, "article_id": article_id, "status": "approved"}


@router.post("/api/admin/review/{article_id}/reject")
def api_review_reject(article_id: str, _: None = Depends(require_admin_token)):
    m = _ingest_news()
    if not m.set_review_status(article_id, "rejected"):
        raise HTTPException(status_code=404, detail="Article not found")
    return {"ok": True, "article_id": article_id, "status": "rejected"}
