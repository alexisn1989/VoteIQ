"""
CSV / JSON export endpoints for Virginia civic data.

All endpoints return CSV by default. Add ?format=json for JSON.
Add ?format=tsv for tab-separated (Excel-friendly without encoding issues).

Endpoints:
  GET /api/export/donor-vote-alignment
  GET /api/export/sponsor-correlation
  GET /api/export/donor-cycle-trends
  GET /api/export/legislator-profiles
  GET /api/export/campaign-finance
  GET /api/export/lobbyists
  GET /api/export/follow-the-money/{bill_number}

Common filters: chamber, party, sector, session, min_amount
"""
from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import Response, JSONResponse

router = APIRouter(tags=["export"])

_BASE_DIR = Path(__file__).resolve().parents[3]
_POLLS_DB = os.path.join(os.getenv("DATA_DIR", str(_BASE_DIR)), "polls.db")
_LEG_DB   = os.path.join(os.getenv("DATA_DIR", str(_BASE_DIR)), "legislative_intelligence.db")

DISCLAIMER = (
    "All figures are from Virginia SBE campaign finance and lobbyist registration "
    "public records. Correlation does not imply causation or intent. "
    "Source: VoteIQ / Virginia SBE / VA Lobbyist Registrations. "
    f"Downloaded: {datetime.utcnow().strftime('%Y-%m-%d')} UTC"
)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_POLLS_DB, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _respond(rows: list[dict], filename: str, fmt: str, include_disclaimer: bool = True) -> Response:
    """Serialise rows to CSV, TSV, or JSON response."""
    if not rows:
        if fmt == "json":
            return JSONResponse([])
        return Response("# No data found\n", media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    if fmt == "json":
        return JSONResponse(rows)

    sep = "\t" if fmt == "tsv" else ","
    mt  = "text/tab-separated-values" if fmt == "tsv" else "text/csv"
    ext = ".tsv" if fmt == "tsv" else ".csv"

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()),
                            delimiter=sep, lineterminator="\n",
                            extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    if include_disclaimer:
        buf.write(f"\n# {DISCLAIMER}\n")

    fname = filename.rsplit(".", 1)[0] + ext
    return Response(
        content=buf.getvalue(),
        media_type=mt,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ── 1. Donor-vote alignment ────────────────────────────────────────────────────

@router.get("/export/donor-vote-alignment")
def export_donor_vote_alignment(
    format:   str = Query("csv", description="csv | tsv | json"),
    chamber:  Optional[str] = Query(None),
    party:    Optional[str] = Query(None),
    sector:   Optional[str] = Query(None, description="Filter by top_donor_sector"),
    min_delta: Optional[float] = Query(None, description="Min |alignment_delta|"),
):
    """
    Per-legislator YES-rate on donor-sector bills vs. all other bills.
    Each row = one legislator.
    """
    conn = _conn()
    params: list = []
    where: list[str] = ["alignment_delta IS NOT NULL"]

    if chamber:
        where.append("lower(chamber) LIKE lower(?)")
        params.append(f"%{chamber}%")
    if party:
        where.append("party = ?")
        params.append(party.upper())
    if sector:
        where.append("lower(top_donor_sector) LIKE lower(?)")
        params.append(f"%{sector}%")
    if min_delta is not None:
        where.append("ABS(alignment_delta) >= ?")
        params.append(min_delta)

    rows = conn.execute(f"""
        SELECT name, party, chamber,
               top_donor_sector          AS donor_sector,
               ROUND(top_sector_amt,0)   AS sector_donations_received,
               ROUND(sector_yes_rate,1)  AS yes_rate_on_sector_bills_pct,
               ROUND(other_yes_rate,1)   AS yes_rate_on_other_bills_pct,
               ROUND(alignment_delta,1)  AS alignment_delta_pp,
               sector_vote_count         AS contested_votes_in_sector,
               other_vote_count          AS contested_votes_other
        FROM donor_vote_alignment
        WHERE {' AND '.join(where)}
        ORDER BY ABS(alignment_delta) DESC
    """, params).fetchall()
    conn.close()

    return _respond([dict(r) for r in rows], "voteiq_donor_vote_alignment.csv", format)


# ── 2. Sponsor-donor correlation ───────────────────────────────────────────────

@router.get("/export/sponsor-correlation")
def export_sponsor_correlation(
    format:  str = Query("csv"),
    session: Optional[str] = Query(None, description="Session year e.g. 2025"),
    sector:  Optional[str] = Query(None),
    label:   Optional[str] = Query(None, description="strong | moderate | weak | none"),
):
    """
    Per-legislator bill sponsorship vs. donor sector funding.
    Each row = one (legislator, session, sector) combination.
    """
    conn = _conn()
    params: list = []
    where: list[str] = []

    if session:
        where.append("session = ?")
        params.append(session)
    if sector:
        where.append("lower(sector) LIKE lower(?)")
        params.append(f"%{sector}%")
    if label:
        where.append("alignment_label = ?")
        params.append(label)

    clause = f"WHERE {' AND '.join(where)}" if where else ""

    rows = conn.execute(f"""
        SELECT session,
               legislator_name            AS name,
               sector,
               bills_sponsored,
               bill_ids,
               ROUND(total_from_sector_donors,0) AS sector_donor_money_received,
               ROUND(total_raised_2021,0)         AS total_raised_that_cycle,
               ROUND(sector_pct,1)                AS sector_pct_of_total,
               top_sector_donor,
               ROUND(top_donor_amt,0)             AS top_donor_amount,
               alignment_label
        FROM sponsor_donor_correlation
        {clause}
        ORDER BY session DESC, sector, bills_sponsored DESC
    """, params).fetchall()
    conn.close()

    fname = f"voteiq_sponsor_correlation{'_'+session if session else ''}.csv"
    return _respond([dict(r) for r in rows], fname, format)


# ── 3. Donor cycle trends ──────────────────────────────────────────────────────

@router.get("/export/donor-cycle-trends")
def export_donor_cycle_trends(
    format:       str = Query("csv"),
    donor:        Optional[str] = Query(None, description="Partial donor name match"),
    spikes_only:  bool = Query(False, description="Only return spike cycles"),
    min_amount:   float = Query(0),
    institutional: bool = Query(True, description="Exclude individual donors"),
):
    """
    Per-donor per-cycle donation totals with z-scores and spike flags.
    Rows: one per (donor, election cycle).
    """
    conn = _conn()
    params: list = [min_amount]
    where = ["t.total_amount >= ?"]

    if donor:
        where.append("lower(t.canonical_name) LIKE lower(?)")
        params.append(f"%{donor}%")
    if spikes_only:
        where.append("t.is_spike = 1")
    if institutional:
        where.append("t.is_individual = 0")

    rows = conn.execute(f"""
        SELECT t.canonical_name            AS donor_name,
               e.variant_names,
               t.election_cycle,
               t.cycle_parity             AS year_type,
               ROUND(t.total_amount,0)    AS total_donated,
               t.gift_count               AS number_of_transactions,
               t.recipient_count          AS unique_legislators_funded,
               ROUND(t.zscore,2)          AS z_score_vs_own_baseline,
               ROUND(t.pct_change_prior,1) AS pct_change_vs_prior_same_type,
               ROUND(t.ratio_vs_mean,2)   AS ratio_vs_own_mean,
               t.is_spike                 AS notable_spike_flag
        FROM donor_cycle_trends t
        LEFT JOIN donor_entity_map e ON e.donor_key = t.donor_key
        WHERE {' AND '.join(where)}
        ORDER BY t.canonical_name, t.election_cycle
    """, params).fetchall()
    conn.close()

    return _respond([dict(r) for r in rows], "voteiq_donor_cycle_trends.csv", format)


# ── 4. Legislator profiles ─────────────────────────────────────────────────────

@router.get("/export/legislator-profiles")
def export_legislator_profiles(
    format:  str = Query("csv"),
    chamber: Optional[str] = Query(None),
    party:   Optional[str] = Query(None),
):
    """
    Pre-generated civic profiles: funding sector, alignment delta, sponsorship.
    One row per legislator — good for spreadsheet analysis or journalism.
    """
    conn = _conn()
    params: list = []
    where: list[str] = []

    if chamber:
        where.append("lower(n.chamber) LIKE lower(?)")
        params.append(f"%{chamber}%")
    if party:
        where.append("n.party = ?")
        params.append(party.upper())

    clause = f"WHERE {' AND '.join(where)}" if where else ""

    rows = conn.execute(f"""
        SELECT n.name,
               n.party,
               n.chamber,
               n.district,
               n.source                          AS finance_source,
               c.total_raised,
               c.latest_cycle,
               c.top_sector                      AS top_donor_sector,
               ROUND(c.top_sector_pct,1)         AS top_sector_pct,
               ROUND(d.sector_yes_rate,1)         AS yes_rate_on_sector_bills,
               ROUND(d.other_yes_rate,1)          AS yes_rate_on_other_bills,
               ROUND(d.alignment_delta,1)         AS alignment_delta_pp,
               n.narrative_short
        FROM legislator_narratives n
        LEFT JOIN campaign_finance_summary c
               ON c.legislator_id = n.legislator_id AND c.source = n.source
        LEFT JOIN donor_vote_alignment d
               ON d.legislator_id = n.legislator_id
        {clause}
        ORDER BY n.chamber, n.district
    """, params).fetchall()
    conn.close()

    return _respond([dict(r) for r in rows], "voteiq_legislator_profiles.csv", format)


# ── 5. Campaign finance summary ────────────────────────────────────────────────

@router.get("/export/campaign-finance")
def export_campaign_finance(
    format:  str = Query("csv"),
    chamber: Optional[str] = Query(None),
    party:   Optional[str] = Query(None),
    sector:  Optional[str] = Query(None, description="Filter by top_sector"),
    source:  str = Query("va_sbe", description="va_sbe or fec"),
):
    """
    Aggregated campaign finance summary per legislator.
    Includes top sector, total raised, top donors (JSON column).

    sector filter uses SBE taxonomy — use these exact values:
    Utilities, Fossil Fuels, Financial Services, Wine & Spirits, Tobacco,
    Commercial Real Estate, Agribusiness, Trial Lawyers, Software & IT,
    Ideological, Individual/Other, Education
    """
    conn = _conn()
    params: list = [source]
    where = ["source = ?"]

    if chamber:
        where.append("lower(chamber) LIKE lower(?)")
        params.append(f"%{chamber}%")
    if party:
        where.append("party = ?")
        params.append(party.upper())
    if sector:
        where.append("lower(top_sector) LIKE lower(?)")
        params.append(f"%{sector}%")

    rows = conn.execute(f"""
        SELECT name, party, chamber, district,
               latest_cycle,
               ROUND(total_raised,0) AS total_raised,
               top_sector,
               ROUND(top_sector_pct,1) AS top_sector_pct
        FROM campaign_finance_summary
        WHERE {' AND '.join(where)}
        ORDER BY total_raised DESC
    """, params).fetchall()
    conn.close()

    return _respond([dict(r) for r in rows], f"voteiq_campaign_finance_{source}.csv", format)


# ── 6. Lobbyist registrations ──────────────────────────────────────────────────

@router.get("/export/lobbyists")
def export_lobbyists(
    format:    str = Query("csv"),
    principal: Optional[str] = Query(None, description="Filter by principal name"),
    year:      Optional[str] = Query(None, description="Filter by year_range"),
):
    """
    All Virginia lobbyist registrations with principal and lobbyist names.
    """
    conn = _conn()
    params: list = []
    where: list[str] = []

    if principal:
        where.append("lower(principal_name) LIKE lower(?)")
        params.append(f"%{principal}%")
    if year:
        where.append("year_range LIKE ?")
        params.append(f"%{year}%")

    clause = f"WHERE {' AND '.join(where)}" if where else ""

    rows = conn.execute(f"""
        SELECT year_range, principal_name, lobbyist_name,
               authorizing_officer, form_type, submitted_date, status
        FROM lobbyist_registrations
        {clause}
        ORDER BY year_range DESC, principal_name, lobbyist_name
    """, params).fetchall()
    conn.close()

    return _respond([dict(r) for r in rows], "voteiq_lobbyist_registrations.csv", format)


# ── 7. Follow-the-money export for a bill ─────────────────────────────────────

@router.get("/export/follow-the-money/{bill_number}")
def export_follow_the_money(
    bill_number: str,
    session: Optional[str] = Query(None),
    format:  str = Query("csv"),
):
    """
    Flat CSV of the money chain for a specific bill:
    one row per (sponsor, donor, lobbyist_count).
    """
    conn = _conn()
    li   = sqlite3.connect(_LEG_DB, timeout=15)
    li.row_factory = sqlite3.Row

    bill_upper = bill_number.upper().replace(" ", "")
    q = "SELECT bill_id, session, title FROM legiscan_va_bills WHERE bill_number = ?"
    p: list = [bill_upper]
    if session:
        q += " AND session = ?"
        p.append(session)
    q += " ORDER BY session DESC LIMIT 1"
    bill = conn.execute(q, p).fetchone()

    if not bill:
        conn.close(); li.close()
        return Response(f"# Bill {bill_upper} not found\n", media_type="text/csv")

    sess    = bill["session"]
    title   = bill["title"] or ""

    sponsor_rows = li.execute("""
        SELECT bs.legislator_name FROM va_bill_sponsors bs
        JOIN va_bills vb ON bs.bill_id = vb.bill_id
        WHERE vb.bill_number = ? AND vb.session = ?
          AND bs.legislator_name != ''
        ORDER BY bs.sponsor_order LIMIT 6
    """, (bill_upper, sess)).fetchall()
    sponsor_names = [r["legislator_name"] for r in sponsor_rows]

    flat_rows: list[dict] = []
    for sp_name in sponsor_names:
        last = sp_name.split()[-1] if sp_name.split() else ""
        fin = conn.execute(
            "SELECT total_raised, top_sector, top_donors_json FROM campaign_finance_summary "
            "WHERE name LIKE ? AND source='va_sbe' ORDER BY total_raised DESC LIMIT 1",
            (f"%{last}%",),
        ).fetchone()
        donors = []
        if fin:
            try:
                donors = json.loads(fin["top_donors_json"] or "[]")
            except Exception:
                donors = []

        if not donors:
            flat_rows.append({
                "bill_number": bill_upper, "session": sess,
                "bill_title": title[:80],
                "sponsor": sp_name,
                "sponsor_total_raised": fin["total_raised"] if fin else 0,
                "sponsor_top_sector":   fin["top_sector"]   if fin else "",
                "donor_name": "", "donor_amount": 0,
                "donor_sector": "", "lobbyist_count": 0,
            })
        for d in donors[:6]:
            dname = d.get("contributor_name") or d.get("employer") or ""
            # Quick lobbyist count
            words = [w for w in dname.lower().split() if len(w) >= 4][:2]
            n_lob = 0
            if len(words) >= 2:
                cl = " AND ".join(f"lower(principal_name) LIKE ?" for _ in words)
                row = conn.execute(
                    f"SELECT COUNT(DISTINCT lobbyist_name) FROM lobbyist_registrations WHERE {cl}",
                    [f"%{w}%" for w in words],
                ).fetchone()
                n_lob = row[0] if row else 0
            flat_rows.append({
                "bill_number": bill_upper, "session": sess,
                "bill_title": title[:80],
                "sponsor": sp_name,
                "sponsor_total_raised": fin["total_raised"] if fin else 0,
                "sponsor_top_sector":   fin["top_sector"]   if fin else "",
                "donor_name":    dname,
                "donor_amount":  d.get("total", 0),
                "donor_sector":  d.get("sector", ""),
                "lobbyist_count": n_lob,
            })

    conn.close(); li.close()
    fname = f"voteiq_follow_the_money_{bill_upper}_{sess}.csv"
    return _respond(flat_rows, fname, format)


# ── 8. Committee testimony proxy ──────────────────────────────────────────────

@router.get("/export/testimony-proxy")
def export_testimony_proxy(
    format:     str           = Query("csv", description="csv | tsv | json"),
    session:    str           = Query("2026", description="Legislative session year"),
    sector:     Optional[str] = Query(None, description="e.g. Energy/Utilities, Healthcare"),
    bill:       Optional[str] = Query(None, description="Filter to one bill, e.g. HB628"),
    principal:  Optional[str] = Query(None, description="Partial principal name match"),
    position:   Optional[str] = Query(None, description="support | unknown"),
    confidence: Optional[str] = Query(None, description="high | medium | low"),
    min_lobbyists: Optional[int] = Query(None, description="Min registered lobbyists"),
    donated_only:  bool = Query(False, description="Only rows with donation-backed support"),
):
    """
    Committee testimony proxy — organizations likely present at hearings.

    Cross-references registered lobbyist principals with bills in the same
    sector and session. Donation records to bill sponsors indicate likely
    support position.

    Columns:
      session, bill_number, bill_title, bill_sector,
      principal_name, principal_sector, lobbyist_count,
      likely_position, position_basis, confidence,
      total_donated_to_sponsors, sponsor_names

    Note: This is a PROXY, not actual testimony. Virginia does not require
    lobbyists to file bill-specific position disclosures. Sector matching +
    donation records = best available public signal.
    """
    conn = _conn()

    # Check table exists
    tbl = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='committee_testimony_proxy'"
    ).fetchone()
    if not tbl:
        conn.close()
        return _respond([], "voteiq_testimony_proxy.csv", format)

    where: list[str] = ["session = ?"]
    params: list = [session]

    if sector:
        where.append("lower(bill_sector) LIKE lower(?)")
        params.append(f"%{sector}%")
    if bill and isinstance(bill, str):
        where.append("upper(bill_number) = upper(?)")
        params.append(bill.strip())
    if principal:
        where.append("lower(principal_name) LIKE lower(?)")
        params.append(f"%{principal}%")
    if position:
        where.append("likely_position = ?")
        params.append(position.lower())
    if confidence:
        where.append("confidence = ?")
        params.append(confidence.lower())
    if min_lobbyists:
        where.append("lobbyist_count >= ?")
        params.append(min_lobbyists)
    if donated_only:
        where.append("total_donated_to_sponsors > 0")

    sql = f"""
        SELECT
            session,
            bill_number,
            bill_title,
            bill_sector,
            principal_name,
            principal_sector,
            lobbyist_count,
            likely_position,
            position_basis,
            confidence,
            ROUND(total_donated_to_sponsors, 0)  AS total_donated_to_sponsors,
            sponsor_names
        FROM committee_testimony_proxy
        WHERE {" AND ".join(where)}
        ORDER BY bill_number, lobbyist_count DESC, likely_position
        LIMIT 5000
    """

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    out = []
    for r in rows:
        d = dict(r)
        # Parse sponsor_names JSON → readable string
        try:
            sponsors = json.loads(d.get("sponsor_names") or "[]")
            d["sponsor_names"] = "; ".join(sponsors[:3])
        except Exception:
            d["sponsor_names"] = ""
        out.append(d)

    fname = f"voteiq_testimony_proxy_{session}.csv"
    return _respond(out, fname, format)
