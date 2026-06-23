"""Bill profile page and JSON API."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["bills"])

_BASE_DIR = Path(__file__).resolve().parents[3]


def _polls_conn() -> sqlite3.Connection:
    db_path = Path(os.environ.get("DATA_DIR", str(_BASE_DIR))) / "polls.db"
    if not db_path.exists():
        db_path = _BASE_DIR / "polls.db"
    conn = sqlite3.connect(str(db_path), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _openstates_conn() -> sqlite3.Connection | None:
    """Read-only connection to openstates_va.db, or None if the file is absent."""
    for candidate in [
        Path(os.environ.get("DATA_DIR", str(_BASE_DIR))) / "openstates_va.db",
        _BASE_DIR / "openstates_va.db",
    ]:
        if candidate.exists():
            conn = sqlite3.connect(str(candidate), timeout=10)
            conn.row_factory = sqlite3.Row
            return conn
    return None


def _inject(template: str, key: str, data) -> str:
    safe = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    with (_BASE_DIR / "templates" / template).open("r", encoding="utf-8") as f:
        html = f.read()
    return html.replace("</head>", f"<script>window.{key}={safe};</script></head>", 1)


def _bill_number_from_id(bill_id: str) -> str:
    """Extract bill number from bill_id (e.g. 'HB429_123' -> 'HB429')."""
    idx = bill_id.find("_")
    return bill_id[:idx] if idx > 0 else bill_id


def _fetch_bill_profile(conn: sqlite3.Connection, bill_number: str, session: str) -> dict:
    bill_number = bill_number.upper().strip()

    # Core bill data
    bill = conn.execute(
        """
        SELECT b.bill_number, b.session, b.title, b.description, b.status,
               pa.policy_area
        FROM va_bills b
        LEFT JOIN va_bill_policy_areas pa
               ON pa.bill_number = b.bill_number AND pa.session = b.session
        WHERE b.bill_number = ? AND b.session = ?
        LIMIT 1
        """,
        (bill_number, session),
    ).fetchone()

    if not bill:
        raise HTTPException(status_code=404, detail=f"Bill {bill_number} not found for {session} session")

    # Sponsors (primary first by sponsor_order)
    sponsors_raw = conn.execute(
        """
        SELECT s.legislator_name, s.sponsor_order,
               ni.canonical_name, ni.party, ni.chamber
        FROM legiscan_va_bill_sponsors s
        LEFT JOIN va_name_index ni ON ni.votes_table_name = s.legislator_name
        WHERE s.bill_id = (? || '_' || ?) AND s.session = ?
        ORDER BY s.sponsor_order, s.legislator_name
        LIMIT 20
        """,
        (bill_number, session, session),
    ).fetchall()

    # All non-procedural votes on this bill
    votes_raw = conn.execute(
        """
        SELECT v.voter_name, v.option,
               ni.canonical_name, ni.party, ni.chamber, ni.district
        FROM va_legislator_recent_votes v
        LEFT JOIN va_name_index ni ON ni.votes_table_name = v.voter_name
        WHERE v.bill_id = ? AND v.session = ?
          AND v.option IN ('yes','no','not voting','absent')
          AND v.is_procedural = 0
        ORDER BY ni.chamber, ni.party, v.voter_name
        """,
        (bill_number, session),
    ).fetchall()

    yes_n = sum(1 for v in votes_raw if v["option"] == "yes")
    no_n  = sum(1 for v in votes_raw if v["option"] == "no")
    nv_n  = sum(1 for v in votes_raw if v["option"] in ("not voting", "absent"))
    total = len(votes_raw)

    by_party: dict[str, dict] = {}
    for v in votes_raw:
        p = v["party"] or "Unknown"
        by_party.setdefault(p, {"yes": 0, "no": 0, "nv": 0})
        if v["option"] == "yes":        by_party[p]["yes"] += 1
        elif v["option"] == "no":       by_party[p]["no"]  += 1
        else:                           by_party[p]["nv"]  += 1

    # Available sessions for navigation
    sessions_avail = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT session FROM va_bills WHERE bill_number = ? ORDER BY session DESC",
            (bill_number,),
        ).fetchall()
    ]

    return {
        "bill_number":  bill["bill_number"],
        "session":      bill["session"],
        "title":        bill["title"] or "",
        "description":  bill["description"] or "",
        "status":       bill["status"] or "Unknown",
        "policy_area":  (bill["policy_area"] or "").title(),
        "sponsors": [
            {
                "name":          s["legislator_name"],
                "canonical_name": s["canonical_name"],
                "party":         s["party"] or "",
                "chamber":       s["chamber"] or "",
                "is_primary":    (s["sponsor_order"] or 99) == 1,
                "profile_url":   f"/legislators/{s['canonical_name']}" if s["canonical_name"] else None,
            }
            for s in sponsors_raw
        ],
        "vote_breakdown": {
            "yes":     yes_n,
            "no":      no_n,
            "nv":      nv_n,
            "total":   total,
            "yes_pct": round(100 * yes_n / total, 1) if total else None,
            "passed":  (bill["status"] or "").lower() in ("passed", "signed", "enacted"),
            "by_party": by_party,
        },
        "votes": [
            {
                "voter_name":     v["voter_name"],
                "canonical_name": v["canonical_name"] or v["voter_name"],
                "option":         v["option"],
                "party":          v["party"] or "",
                "chamber":        v["chamber"] or "",
                "district":       str(v["district"] or ""),
            }
            for v in votes_raw
        ],
        "sessions_available": sessions_avail,
    }


def _fetch_bill_layered_profile(conn: sqlite3.Connection, bill_number: str, session: str) -> dict:
    """
    Return a 5-layer structured bill profile with hard epistemic boundaries.

    fact_layer      — official record only, no interpretation
    procedure_layer — chronological actions, no commentary
    impact_layer    — plain-English description (neutral)
    context_layer   — finance/lobbying placeholders for future expansion
    analysis_layer  — party patterns WITH mandatory disclaimer
    """
    base = _fetch_bill_profile(conn, bill_number, session)
    vb = base["vote_breakdown"]

    # ── FACT LAYER ─────────────────────────────────────────────────────────────
    fact_layer = {
        "bill_number": base["bill_number"],
        "session":     base["session"],
        "title":       base["title"],
        "status":      base["status"],
        "policy_area": base["policy_area"],
        "sponsors": [
            {
                "name":           s["name"],
                "canonical_name": s["canonical_name"],
                "party":          s["party"],
                "chamber":        s["chamber"],
                "is_primary":     s["is_primary"],
                "profile_url":    s["profile_url"],
            }
            for s in base["sponsors"]
        ],
        "vote_totals": {
            "yes":    vb["yes"],
            "no":     vb["no"],
            "nv":     vb["nv"],
            "total":  vb["total"],
            "passed": vb["passed"],
        },
        "roll_call": base["votes"],
        "sources": ["Virginia LIS", "OpenStates"],
    }

    # ── PROCEDURE LAYER ────────────────────────────────────────────────────────
    procedure_layer: dict = {"actions": [], "note": ""}
    os_conn = _openstates_conn()
    if os_conn:
        try:
            # OpenStates stores identifiers with a space: "HB 67" not "HB67"
            os_id = re.sub(r"^([A-Z]+)(\d+)$", r"\1 \2", bill_number.upper())
            actions = os_conn.execute(
                """
                SELECT date, description, chamber
                FROM bill_actions
                WHERE bill_id = ? AND session = ?
                ORDER BY date ASC, id ASC
                """,
                (os_id, session),
            ).fetchall()
            procedure_layer["actions"] = [
                {
                    "date":        a["date"],
                    "description": a["description"],
                    "chamber":     a["chamber"],
                }
                for a in actions
            ]
        except Exception:
            procedure_layer["note"] = "Legislative timeline unavailable."
        finally:
            os_conn.close()
    else:
        procedure_layer["note"] = "Legislative timeline database not connected."

    # ── IMPACT LAYER ───────────────────────────────────────────────────────────
    impact_layer = {
        "plain_english_summary": base["description"],
        "policy_area":           base["policy_area"],
    }

    # ── CONTEXT LAYER ──────────────────────────────────────────────────────────
    context_layer = {
        "sessions_available": base["sessions_available"],
        "campaign_finance":   None,
        "lobbying_presence":  None,
        "related_bills":      [],
    }

    # ── ANALYSIS LAYER ─────────────────────────────────────────────────────────
    analysis_layer = {
        "_disclaimer": (
            "This section does not imply causation or intent. "
            "It reflects observed patterns in public roll-call records only."
        ),
        "party_vote_breakdown": vb["by_party"],
        "yes_pct":              vb["yes_pct"],
    }

    return {
        "fact_layer":      fact_layer,
        "procedure_layer": procedure_layer,
        "impact_layer":    impact_layer,
        "context_layer":   context_layer,
        "analysis_layer":  analysis_layer,
    }


@router.get("/api/bills/{bill_number}/layered-profile")
def bill_layered_profile_api(bill_number: str, session: str = Query(default="2026")):
    """JSON — epistemically layered bill profile (fact / procedure / impact / context / analysis)."""
    bill_number = unquote(bill_number).upper().strip()
    conn = _polls_conn()
    try:
        return _fetch_bill_layered_profile(conn, bill_number, session)
    except HTTPException:
        raise
    finally:
        conn.close()


@router.get("/bills/{bill_number}", response_class=HTMLResponse)
def bill_profile_page(bill_number: str, session: str = Query(default="2026")):
    """Bill profile page: vote breakdown, sponsors, your-rep lookup, chat."""
    bill_number = unquote(bill_number).upper().strip()
    conn = _polls_conn()
    try:
        profile = _fetch_bill_profile(conn, bill_number, session)
        return _inject("bill_profile.html", "_BILL_DATA", profile)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Bill profile unavailable: {exc}") from exc
    finally:
        conn.close()


@router.get("/api/bills/{bill_number}/profile")
def bill_profile_api(bill_number: str, session: str = Query(default="2026")):
    """JSON — bill profile data."""
    bill_number = unquote(bill_number).upper().strip()
    conn = _polls_conn()
    try:
        return _fetch_bill_profile(conn, bill_number, session)
    except HTTPException:
        raise
    finally:
        conn.close()
