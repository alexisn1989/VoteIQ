"""Congressional Record (CREC) floor statement proxy via api.govinfo.gov."""
from __future__ import annotations

import json
import os
import sqlite3
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Query

router = APIRouter(tags=["floor"])

_BASE_DIR = str(Path(__file__).resolve().parent.parent.parent.parent)
_POLLS_DB = os.path.join(os.getenv("DATA_DIR", _BASE_DIR), "polls.db")
_GOVINFO_BASE = "https://api.govinfo.gov"
_USER_AGENT = "VoteIQ/1.0 (civic data; alexisnieuwenhuys89@gmail.com)"


def _govinfo_get(path: str, **params) -> dict:
    api_key = os.getenv("GOVINFO_API_KEY", "")
    if api_key:
        params["api_key"] = api_key
    qs = urllib.parse.urlencode(params)
    url = f"{_GOVINFO_BASE}/{path}?{qs}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _member_row(bioguide_id: str) -> dict | None:
    try:
        conn = sqlite3.connect(_POLLS_DB)
        row = conn.execute(
            "SELECT name, chamber FROM congress_members WHERE bioguide_id = ?",
            (bioguide_id,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return {"name": row[0], "chamber": row[1]}
    except Exception:
        return None


def _last_name(full_name: str) -> str:
    """'Kiggans, Jennifer' → 'KIGGANS'"""
    return full_name.split(",")[0].strip().upper()


def _db_floor_statements(
    bioguide_id: str | None = None,
    q: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Query congress_floor_statements from polls.db."""
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        clauses: list[str] = []
        params: list = []

        if bioguide_id:
            clauses.append("bioguide_id = ?")
            params.append(bioguide_id)
        if q:
            clauses.append("(lower(title) LIKE ? OR lower(text) LIKE ?)")
            kw = f"%{q.lower()}%"
            params.extend([kw, kw])

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"""SELECT bioguide_id, member_name, congress, chamber,
                       statement_date, title, text, source_url
                FROM congress_floor_statements
                {where}
                ORDER BY statement_date DESC
                LIMIT ?""",
            params + [limit],
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


@router.get("/api/congress/floor-statements/search")
def floor_statements_search(
    q: str = Query(..., description="Keyword to search in title and text"),
    bioguide_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Search stored floor statements by keyword (local DB)."""
    rows = _db_floor_statements(bioguide_id=bioguide_id, q=q, limit=limit)
    return {"count": len(rows), "query": q, "results": rows}


@router.get("/api/congress/floor-statements/{bioguide_id}/stored")
def floor_statements_stored(
    bioguide_id: str,
    limit: int = Query(default=20, ge=1, le=100),
):
    """Return locally stored floor statements for a member (polls.db)."""
    member = _member_row(bioguide_id)
    rows = _db_floor_statements(bioguide_id=bioguide_id, limit=limit)
    return {
        "bioguide_id": bioguide_id,
        "member_name": member["name"] if member else None,
        "count": len(rows),
        "source": "polls.db congress_floor_statements",
        "statements": rows,
    }


@router.get("/api/congress/floor-statements/{bioguide_id}")
def floor_statements(
    bioguide_id: str,
    start: str = Query(default="2025-01-01", description="Start date YYYY-MM-DD"),
    end: str = Query(default="", description="End date YYYY-MM-DD (defaults to today)"),
    limit: int = Query(default=20, ge=1, le=100),
):
    """List Congressional Record floor statements for a VA member."""
    member = _member_row(bioguide_id)
    if not member:
        return {"statements": [], "error": "member not found"}

    end_date = end or date.today().isoformat()
    last = _last_name(member["name"])

    try:
        data = _govinfo_get(
            "search",
            query=last,
            collections="CREC",
            resultLevel="granule",
            dateIssuedStartDate=start,
            dateIssuedEndDate=end_date,
            pageSize=limit,
            offsetMark="*",
        )
    except Exception as exc:
        return {"statements": [], "error": str(exc)}

    results = data.get("results", [])
    statements = [
        {
            "date": r.get("dateIssued"),
            "title": r.get("title"),
            "package_id": r.get("packageId"),
            "granule_id": r.get("granuleId"),
            "granule_class": r.get("granuleClass"),
            "pdf_url": (r.get("download") or {}).get("pdfLink"),
            "txt_url": (r.get("download") or {}).get("txtLink"),
        }
        for r in results
    ]

    return {
        "bioguide_id": bioguide_id,
        "member_name": member["name"],
        "chamber": member["chamber"],
        "start": start,
        "end": end_date,
        "count": len(statements),
        "statements": statements,
    }


@router.get("/api/congress/floor-statement-text")
def floor_statement_text(package_id: str, granule_id: str):
    """Fetch the plain-text body of a single CREC granule."""
    try:
        data = _govinfo_get(f"packages/{package_id}/granules/{granule_id}/summary")
    except Exception as exc:
        return {"text": None, "error": str(exc)}

    download = data.get("download") or {}
    txt_url = download.get("txtLink")
    if not txt_url:
        return {"text": None, "error": "no text link in granule summary"}

    try:
        api_key = os.getenv("GOVINFO_API_KEY", "")
        url = f"{txt_url}?api_key={api_key}" if api_key else txt_url
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return {"text": None, "error": str(exc)}

    return {
        "package_id": package_id,
        "granule_id": granule_id,
        "title": data.get("title"),
        "date": data.get("dateIssued"),
        "text": text,
    }
