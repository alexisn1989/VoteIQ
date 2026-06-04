"""
Follow the Money — traces the donor chain behind any Virginia bill.

Chain:
  Bill → primary sponsors → each sponsor's top donors
       → lobbyist registrations for those donors
       → other legislators those donors funded
       → related bills in the same sector

Endpoint:
  GET /api/follow-the-money/{bill_number}?session=2026
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

router = APIRouter(tags=["follow_the_money"])

_BASE_DIR    = Path(__file__).resolve().parents[3]
_DATA_DIR    = os.getenv("DATA_DIR", str(_BASE_DIR))
_POLLS_DB    = os.path.join(_DATA_DIR, "polls.db")
_LEG_DB      = os.path.join(_DATA_DIR, "legislative_intelligence.db")


def _polls() -> sqlite3.Connection:
    c = sqlite3.connect(_POLLS_DB, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _leg() -> sqlite3.Connection:
    c = sqlite3.connect(_LEG_DB, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _brand_key(name: str) -> str:
    """First 2 significant words (>=4 chars) after stripping PAC/Inc/etc.
    Returns empty string when the name is too generic (initials-only, etc.)"""
    stop = re.compile(
        r"\b(pac|political action committee|political committee|fund|inc|llc|"
        r"corp|association|committee|foundation|action|for\b)\b\.?",
        re.I,
    )
    s = stop.sub(" ", name or "").strip()
    # Require words >= 4 chars to avoid single-initial or 2-letter noise
    words = [w for w in s.split() if len(w) >= 4][:2]
    if not words:
        return ""
    return " ".join(words).lower()


def _lobbyist_count(conn: sqlite3.Connection, donor_name: str) -> int:
    key = _brand_key(donor_name)
    words = key.split() if key else []
    # Require at least 2 significant words to avoid generic single-name matches
    if len(words) < 2:
        return 0
    clause = " AND ".join(f"lower(principal_name) LIKE ?" for w in words)
    params = [f"%{w}%" for w in words]
    row = conn.execute(
        f"SELECT COUNT(DISTINCT lobbyist_name) FROM lobbyist_registrations WHERE {clause}",
        params,
    ).fetchone()
    return row[0] if row else 0


def _plain_narrative(
    bill_number: str, bill_title: str, session: str,
    sponsors: list[dict], donor_web: list[dict], related: list[dict],
) -> str:
    """Generate a readable paragraph summarising the money chain."""
    lines = [f"Follow the Money — {bill_number} ({session})"]
    lines.append(f'Bill: "{bill_title}"')
    lines.append("")

    if sponsors:
        sp_names = [s["name"] for s in sponsors[:3]]
        lines.append(f"Primary sponsor(s): {', '.join(sp_names)}.")
        for s in sponsors[:2]:
            fin = s.get("finance", {})
            top = fin.get("top_sector", "")
            total = fin.get("total_raised", 0)
            if top and total:
                lines.append(
                    f"  {s['name']} raised ${total:,.0f} total; "
                    f"top funding sector: {top}."
                )
            dva = s.get("vote_alignment")
            if dva and dva.get("delta") is not None:
                delta = dva["delta"]
                sector = dva.get("sector", top)
                dir_s = "above" if delta > 0 else "below"
                lines.append(
                    f"  Vote alignment: {s['name']} votes YES on {sector} bills "
                    f"{abs(delta):.1f}pp {dir_s} their overall average."
                )

    lines.append("")
    if donor_web:
        lob_donors = [d for d in donor_web if d.get("lobbyist_count", 0) > 0]
        if lob_donors:
            lines.append("Donors with registered lobbyists who also funded sponsors:")
            for d in lob_donors[:5]:
                n_lob = d["lobbyist_count"]
                total = d.get("total_to_sponsors", 0)
                also = d.get("also_funded_count", 0)
                lines.append(
                    f"  {d['donor_name']}: ${total:,.0f} to bill sponsors, "
                    f"{n_lob} registered VA lobbyist{'s' if n_lob != 1 else ''}, "
                    f"also funded {also} other VA legislators."
                )

    lines.append("")
    if related:
        lines.append(f"Related bills in the same sector this session ({len(related)} found):")
        for r in related[:5]:
            lines.append(f"  {r['bill_number']}: {r['title'][:65]}")

    lines.append("")
    lines.append(
        "All data from Virginia SBE campaign finance and lobbyist registration public records. "
        "Correlation does not imply causation or intent."
    )
    return "\n".join(lines)


@router.get("/follow-the-money/{bill_number}")
def follow_the_money(
    bill_number: str,
    session: Optional[str] = Query(None, description="Session year, e.g. 2026"),
):
    """
    Full donor chain for a Virginia bill.
    Returns: bill metadata, sponsor finance profiles, donor web,
             lobbyist connections, related bills, and plain-English narrative.
    """
    p = _polls()
    li = _leg()

    # ── 1. Resolve bill ───────────────────────────────────────────────────────
    bill_upper = bill_number.upper().replace(" ", "")

    query = "SELECT * FROM legiscan_va_bills WHERE bill_number = ?"
    params: list = [bill_upper]
    if session:
        query += " AND session = ?"
        params.append(session)
    else:
        query += " ORDER BY session DESC"
    bill_row = p.execute(query + " LIMIT 1", params).fetchone()

    if not bill_row:
        raise HTTPException(404, f"Bill {bill_number} not found")

    bill_session = bill_row["session"]
    bill_id      = bill_row["bill_id"]
    bill_title   = bill_row["title"] or ""
    bill_status  = bill_row["status_label"] or bill_row["status"] or ""

    # ── 2. Get sponsors ───────────────────────────────────────────────────────
    sponsor_rows = li.execute(
        """
        SELECT bs.legislator_name, bs.sponsor_order
        FROM va_bill_sponsors bs
        JOIN va_bills vb ON bs.bill_id = vb.bill_id
        WHERE vb.bill_number = ? AND vb.session = ?
          AND bs.legislator_name != ''
        ORDER BY bs.sponsor_order
        LIMIT 6
        """,
        (bill_upper, bill_session),
    ).fetchall()

    # Fallback to primary_sponsor column
    if not sponsor_rows and bill_row["primary_sponsor"]:
        sponsor_names = [bill_row["primary_sponsor"]]
    else:
        sponsor_names = [r["legislator_name"] for r in sponsor_rows]

    # ── 3. Enrich each sponsor ────────────────────────────────────────────────
    sponsors_out: list[dict] = []
    all_top_donors: dict[str, dict] = {}  # donor_name -> aggregated data

    for sp_name in sponsor_names:
        sp = {"name": sp_name, "sponsor_order": None}

        # Finance profile
        fin_row = p.execute(
            """
            SELECT total_raised, top_sector, top_sector_pct,
                   by_sector_json, top_donors_json, cycles_json, latest_cycle
            FROM campaign_finance_summary
            WHERE name LIKE ? AND source = 'va_sbe'
            ORDER BY total_raised DESC LIMIT 1
            """,
            (f"%{sp_name.split()[-1]}%",),
        ).fetchone()

        if fin_row:
            try:
                top_donors = json.loads(fin_row["top_donors_json"] or "[]")
            except Exception:
                top_donors = []
            try:
                by_sector = json.loads(fin_row["by_sector_json"] or "[]")
            except Exception:
                by_sector = []

            # Skip vague top-level buckets; find first meaningful sector
            _SKIP = {"ideological", "individual/other", "unknown"}
            top_sector = fin_row["top_sector"] or ""
            top_pct    = fin_row["top_sector_pct"]
            if top_sector.lower() in _SKIP:
                for s_entry in by_sector[1:]:
                    if s_entry.get("sector", "").lower() not in _SKIP:
                        top_sector = s_entry["sector"]
                        total_r = fin_row["total_raised"] or 1
                        top_pct = round(s_entry.get("total", 0) / total_r * 100, 1)
                        break

            sp["finance"] = {
                "total_raised":   fin_row["total_raised"] or 0,
                "top_sector":     top_sector,
                "top_sector_pct": top_pct,
                "latest_cycle":   fin_row["latest_cycle"],
                "top_donors":     top_donors[:6],
            }
            # Accumulate into donor web
            for d in top_donors[:8]:
                dn = d.get("contributor_name") or d.get("employer") or ""
                if not dn:
                    continue
                if dn not in all_top_donors:
                    all_top_donors[dn] = {
                        "donor_name":      dn,
                        "sector":          d.get("sector", ""),
                        "total_to_sponsors": 0,
                        "sponsor_list":    [],
                    }
                all_top_donors[dn]["total_to_sponsors"] += d.get("total", 0)
                all_top_donors[dn]["sponsor_list"].append(sp_name)

        # Vote alignment
        dva_row = p.execute(
            """
            SELECT top_donor_sector, sector_yes_rate, other_yes_rate,
                   alignment_delta, sector_vote_count
            FROM donor_vote_alignment dva
            JOIN campaign_finance_summary cfs
              ON dva.legislator_id = cfs.legislator_id
            WHERE cfs.name LIKE ? AND cfs.source = 'va_sbe'
            LIMIT 1
            """,
            (f"%{sp_name.split()[-1]}%",),
        ).fetchone()

        if dva_row and dva_row["alignment_delta"] is not None:
            sp["vote_alignment"] = {
                "sector":     dva_row["top_donor_sector"],
                "yes_rate":   dva_row["sector_yes_rate"],
                "other_rate": dva_row["other_yes_rate"],
                "delta":      dva_row["alignment_delta"],
                "vote_count": dva_row["sector_vote_count"],
            }

        sponsors_out.append(sp)

    # ── 4. Build donor web ────────────────────────────────────────────────────
    donor_web_out: list[dict] = []
    for dn, ddata in sorted(
        all_top_donors.items(),
        key=lambda x: -x[1]["total_to_sponsors"]
    ):
        n_lob = _lobbyist_count(p, dn)

        # How many other VA legislators did this donor fund?
        brand = _brand_key(dn)
        brand_words = brand.split()
        if brand_words:
            clause = " AND ".join(
                f"lower(last_or_company) LIKE ?" for _ in brand_words
            )
            also_row = p.execute(
                f"""
                SELECT COUNT(DISTINCT candidate_name) FROM va_cf_schedule_a
                WHERE {clause}
                  AND candidate_name NOT IN ({','.join('?' for _ in sponsors_out)})
                """,
                [f"%{w}%" for w in brand_words] + [s["name"] for s in sponsors_out],
            ).fetchone()
            also_count = also_row[0] if also_row else 0
        else:
            also_count = 0

        entry = dict(ddata)
        entry["lobbyist_count"]    = n_lob
        entry["also_funded_count"] = also_count
        donor_web_out.append(entry)

    # Sort: donors with lobbyists first, then by amount
    donor_web_out.sort(
        key=lambda d: (-d["lobbyist_count"], -d["total_to_sponsors"])
    )

    # ── 5. Related bills ──────────────────────────────────────────────────────
    # Classify this bill's sector, find similar bills same session
    # Simple sector detection from title
    SECTOR_KWS = {
        "Energy/Utilities": ["electric", "energy", "utility", "solar", "grid",
                              "wind", "net metering", "rate", "dominion"],
        "Healthcare":       ["health", "medicaid", "hospital", "pharmacy",
                              "behavioral", "mental"],
        "Alcohol/Gambling": ["casino", "gaming", "cannabis", "marijuana",
                              "alcohol", "mixed beverage"],
        "Transportation":   ["toll", "highway", "transit", "vehicle", "rideshare"],
        "Housing":          ["eviction", "rent", "zoning", "housing", "landlord"],
        "Finance":          ["bank", "lending", "insurance", "mortgage", "credit"],
    }
    title_lower = bill_title.lower()
    bill_sector = next(
        (s for s, kws in SECTOR_KWS.items() if any(k in title_lower for k in kws)),
        None,
    )

    related_out: list[dict] = []
    if bill_sector:
        kws = SECTOR_KWS[bill_sector]
        kw_clause = " OR ".join(f"lower(title) LIKE ?" for _ in kws[:6])
        related_rows = p.execute(
            f"""
            SELECT bill_number, title, primary_sponsor, status_label
            FROM legiscan_va_bills
            WHERE session = ?
              AND bill_id != ?
              AND ({kw_clause})
            ORDER BY bill_number
            LIMIT 12
            """,
            [bill_session, bill_id] + [f"%{k}%" for k in kws[:6]],
        ).fetchall()
        related_out = [dict(r) for r in related_rows]

    # ── 6. Narrative ──────────────────────────────────────────────────────────
    narrative = _plain_narrative(
        bill_upper, bill_title, bill_session,
        sponsors_out, donor_web_out, related_out,
    )

    li.close()
    p.close()

    return JSONResponse({
        "bill": {
            "bill_number": bill_upper,
            "session":     bill_session,
            "title":       bill_title,
            "status":      bill_status,
            "sector":      bill_sector,
        },
        "sponsors":    sponsors_out,
        "donor_web":   donor_web_out[:20],
        "related_bills": related_out,
        "narrative":   narrative,
    })
