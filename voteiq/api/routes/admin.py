"""Admin and ingestion endpoints."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

from voteiq.api.dependencies import require_admin_token

_BASE_DIR = str(Path(__file__).resolve().parent.parent.parent.parent)
_POLLS_DB = os.path.join(os.getenv("DATA_DIR", _BASE_DIR), "polls.db")
_DATA_DIR = os.getenv("DATA_DIR", _BASE_DIR)
_FEC_DB   = os.path.join(_BASE_DIR, "fec_va.db")

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _run_script(script_name: str, args: list[str], timeout: int) -> dict:
    """Run a project-root script as a subprocess and return a result dict."""
    path = os.path.join(_BASE_DIR, script_name)
    if not os.path.exists(path):
        return {"ok": False, "error": f"{script_name} not found"}
    try:
        r = subprocess.run([sys.executable, path] + args,
                           capture_output=True, text=True, timeout=timeout)
        return {
            "ok": r.returncode == 0,
            "stdout": r.stdout[-2000:],
            "stderr": r.stderr[-500:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {timeout}s"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _reload_caches() -> None:
    """Bust in-process caches — lazy import avoids circular dependency."""
    import main as _m
    _m._load_pac_cache()
    _m._load_votes_cache()
    _m._federal_members_cache = None


# ── ingest endpoints ───────────────────────────────────────────────────────────

@router.post("/ingest-congress")
def admin_ingest_congress(
    congress: int = 119,
    house_limit: int = 300,
    fetch_text: bool = False,
    refresh_text: bool = False,
    bill: str | None = None,
    _: None = Depends(require_admin_token),
):
    """Populate polls.db congress_members, congress_bills, congress_votes tables."""
    results: dict = {}
    scripts: list[tuple[str, list[str]]] = []

    if os.getenv("CONGRESS_API_KEY"):
        args = ["--congress", str(congress)]
        if fetch_text:   args.append("--fetch-text")
        if refresh_text: args.append("--refresh-text")
        if bill:         args += ["--bill", bill]
        scripts.append(("ingest_congress.py", args))
    else:
        results["ingest_congress.py"] = {
            "ok": False, "skipped": "CONGRESS_API_KEY not set — members/bills not refreshed"
        }

    scripts.append(("ingest_congress_votes.py", ["--house-limit", str(house_limit)]))

    for script_name, args in scripts:
        results[script_name] = _run_script(script_name, args, timeout=600)

    # Reload in-memory PAC/votes caches so the currently-running process
    # picks up what this call just ingested — the old startup thread did this
    # unconditionally on every boot; now it's the ingest call's job instead.
    _reload_caches()
    return {"ok": all(v.get("ok") for v in results.values()), "scripts": results}


@router.post("/ingest-fec-2026")
def admin_ingest_fec_2026(
    background: BackgroundTasks,
    _: None = Depends(require_admin_token),
):
    """Seed/refresh fec_industry_totals for the 2026 cycle (non-blocking)."""
    script = os.path.join(_BASE_DIR, "scripts", "ingest_fec_2026.py")
    if not os.path.exists(script):
        return {"ok": False, "error": "scripts/ingest_fec_2026.py not found"}

    def _run():
        try:
            result = subprocess.run(
                [sys.executable, script, "--cycle", "2026"],
                capture_output=True, text=True, timeout=1800,
            )
            if result.returncode == 0:
                _reload_caches()
                print("[fec-2026] Admin ingestion complete — caches reloaded.")
            else:
                print(f"[fec-2026] Admin ingestion failed: {result.stderr[-500:]}")
        except subprocess.TimeoutExpired:
            print("[fec-2026] Admin ingestion timed out after 30 minutes.")
        except Exception as exc:
            print(f"[fec-2026] Admin ingestion error: {exc}")

    background.add_task(_run)
    return {"ok": True, "message": "FEC 2026 ingestion started in background."}


@router.post("/ingest-federal-alignment")
def admin_ingest_federal_alignment(
    background: BackgroundTasks,
    _: None = Depends(require_admin_token),
):
    """Rebuild federal vote-donor alignment rows (non-blocking)."""
    script = os.path.join(_BASE_DIR, "build_federal_vote_alignment.py")
    if not os.path.exists(script):
        return {"ok": False, "error": "build_federal_vote_alignment.py not found"}

    def _run():
        try:
            result = subprocess.run(
                [sys.executable, script],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode == 0:
                print("[fed-alignment] Admin ingestion complete.")
            else:
                print(f"[fed-alignment] Admin ingestion failed: {result.stderr[-400:]}")
        except subprocess.TimeoutExpired:
            print("[fed-alignment] Admin ingestion timed out.")
        except Exception as exc:
            print(f"[fed-alignment] Admin ingestion error: {exc}")

    background.add_task(_run)
    return {"ok": True, "message": "Federal vote-donor alignment build started in background."}


@router.post("/ingest-party-breakdown")
def admin_ingest_party_breakdown(
    background: BackgroundTasks,
    _: None = Depends(require_admin_token),
):
    """Refresh congressional party-breakdown / unity stats (non-blocking)."""
    script = os.path.join(_BASE_DIR, "ingest_party_breakdown.py")
    if not os.path.exists(script):
        return {"ok": False, "error": "ingest_party_breakdown.py not found"}

    def _run():
        try:
            result = subprocess.run(
                [sys.executable, script],
                capture_output=True, text=True, timeout=1800,
            )
            if result.returncode == 0:
                print("[party-breakdown] Admin ingestion complete.")
            else:
                print(f"[party-breakdown] Admin ingestion failed: {result.stderr[-400:]}")
        except subprocess.TimeoutExpired:
            print("[party-breakdown] Admin ingestion timed out after 30 minutes.")
        except Exception as exc:
            print(f"[party-breakdown] Admin ingestion error: {exc}")

    background.add_task(_run)
    return {"ok": True, "message": "Party breakdown ingestion started in background."}


@router.post("/ingest-fec-schedule-e")
def admin_ingest_fec_schedule_e(
    background: BackgroundTasks,
    _: None = Depends(require_admin_token),
):
    """Refresh FEC Schedule E independent-expenditure data (non-blocking)."""
    if not os.getenv("FEC_API_KEY"):
        return {"ok": False, "error": "FEC_API_KEY not set"}
    script = os.path.join(_BASE_DIR, "ingest_fec_schedule_e.py")
    if not os.path.exists(script):
        return {"ok": False, "error": "ingest_fec_schedule_e.py not found"}

    def _run():
        try:
            result = subprocess.run(
                [sys.executable, script],
                capture_output=True, text=True, timeout=1800,
            )
            if result.returncode == 0:
                print("[schedule-e] Admin ingestion complete.")
            else:
                print(f"[schedule-e] Admin ingestion failed: {result.stderr[-500:]}")
        except subprocess.TimeoutExpired:
            print("[schedule-e] Admin ingestion timed out after 30 minutes.")
        except Exception as exc:
            print(f"[schedule-e] Admin ingestion error: {exc}")

    background.add_task(_run)
    return {"ok": True, "message": "FEC Schedule E ingestion started in background."}


@router.post("/ingest-norfolk-agenda")
def admin_ingest_norfolk_agenda(
    days: int = 90,
    reset: bool = False,
    _: None = Depends(require_admin_token),
):
    """Scrape IQM2 for upcoming Norfolk council agendas and store in norfolk_upcoming_agenda."""
    args = ["--days", str(days)]
    if reset:
        args.append("--reset")
    result = _run_script("scrape_norfolk_agenda.py", args, timeout=120)
    return {"ok": result.get("ok"), "scripts": {"scrape_norfolk_agenda.py": result}}


@router.post("/ingest-vb-council")
def admin_ingest_vb_council(_: None = Depends(require_admin_token)):
    """Scrape VB City Council roster from clerk.virginiabeach.gov into vb_council_members."""
    result = _run_script("scrape_vb_council.py", [], timeout=60)
    return {"ok": result.get("ok"), "scripts": {"scrape_vb_council.py": result}}


@router.post("/ingest-vb-votes")
def admin_ingest_vb_votes(
    reset: bool = False,
    _: None = Depends(require_admin_token),
):
    """Pull VB vote history then rebuild blocs, finance, and adjacency tables."""
    results: dict = {}
    results["ingest_vb_votes.py"] = _run_script(
        "ingest_vb_votes.py", ["--reset"] if reset else [], timeout=300
    )
    for script in ("build_vb_blocs.py", "build_vb_finance.py",
                   "build_vb_dvadj.py", "build_vb_dissent.py", "build_vb_splits.py"):
        results[script] = _run_script(script, [], timeout=120)
    return {"ok": all(v.get("ok") for v in results.values()), "scripts": results}


@router.post("/ingest-sbe-local")
def admin_ingest_sbe_local(
    years: str = "",
    _: None = Depends(require_admin_token),
):
    """Download SBE local-race contribution CSVs and populate sbe_local_contributions."""
    args = ["--years"] + years.split() if years.strip() else []
    result = _run_script("download_sbe_finance.py", args, timeout=480)
    return {"ok": result.get("ok"), "scripts": {"download_sbe_finance.py": result}}


@router.post("/ingest-vb-agenda")
def admin_ingest_vb_agenda(
    days: int = 90,
    reset: bool = False,
    _: None = Depends(require_admin_token),
):
    """Scrape IQM2 for upcoming VB City Council agendas and store in vb_upcoming_agenda."""
    args = ["--days", str(days)]
    if reset:
        args.append("--reset")
    result = _run_script("scrape_vb_agenda.py", args, timeout=120)
    return {"ok": result.get("ok"), "scripts": {"scrape_vb_agenda.py": result}}


@router.post("/ingest-chesapeake-agenda")
def admin_ingest_chesapeake_agenda(
    reset: bool = False,
    _: None = Depends(require_admin_token),
):
    """Scrape Granicus for the upcoming Chesapeake council agenda into chesapeake_upcoming_agenda."""
    args = ["--reset"] if reset else []
    result = _run_script("scrape_chesapeake_agenda.py", args, timeout=120)
    return {"ok": result.get("ok"), "scripts": {"scrape_chesapeake_agenda.py": result}}


@router.post("/ingest-suffolk-agenda")
def admin_ingest_suffolk_agenda(
    reset: bool = False,
    _: None = Depends(require_admin_token),
):
    """Scrape AgendaCenter for the upcoming Suffolk council agenda into suffolk_upcoming_agenda."""
    args = ["--reset"] if reset else []
    result = _run_script("scrape_suffolk_agenda.py", args, timeout=120)
    return {"ok": result.get("ok"), "scripts": {"scrape_suffolk_agenda.py": result}}


@router.post("/ingest-hampton-agenda")
def admin_ingest_hampton_agenda(
    reset: bool = False,
    _: None = Depends(require_admin_token),
):
    """Scrape Legistar for the upcoming Hampton council agenda into hampton_upcoming_agenda."""
    args = ["--reset"] if reset else []
    result = _run_script("scrape_hampton_agenda.py", args, timeout=180)
    return {"ok": result.get("ok"), "scripts": {"scrape_hampton_agenda.py": result}}


@router.post("/ingest-newport-news-agenda")
def admin_ingest_newport_news_agenda(
    reset: bool = False,
    _: None = Depends(require_admin_token),
):
    """Scrape CivicWeb for the upcoming Newport News council agenda into newport_news_upcoming_agenda."""
    args = ["--reset"] if reset else []
    result = _run_script("scrape_newport_news_agenda.py", args, timeout=180)
    return {"ok": result.get("ok"), "scripts": {"scrape_newport_news_agenda.py": result}}


@router.post("/ingest-portsmouth-agenda")
def admin_ingest_portsmouth_agenda(
    reset: bool = False,
    _: None = Depends(require_admin_token),
):
    """Scrape Granicus for the upcoming Portsmouth council agenda into portsmouth_upcoming_agenda."""
    args = ["--reset"] if reset else []
    result = _run_script("scrape_portsmouth_agenda.py", args, timeout=120)
    return {"ok": result.get("ok"), "scripts": {"scrape_portsmouth_agenda.py": result}}


@router.post("/ingest-agenda-geocoding")
def admin_ingest_agenda_geocoding(
    city: str | None = None,
    _: None = Depends(require_admin_token),
):
    """Backfill lat/lng on upcoming-agenda items with a numbered street
    address (CUP/rezoning items) so chat can answer 'what's near me?'."""
    args = ["--city", city] if city else []
    result = _run_script("scripts/geocode_agenda_addresses.py", args, timeout=180)
    return {"ok": result.get("ok"), "scripts": {"geocode_agenda_addresses.py": result}}


# ── Historical council-vote refresh (weekly) ────────────────────────────────
# Each scraper already skips meetings it has already recorded (already_scraped
# checks / insert-if-not-exists), so these only need to look at a short recent
# window each run rather than re-walking full history -- keeps runtime bounded
# and avoids hammering each city's site every week.

@router.post("/ingest-chesapeake-votes")
def admin_ingest_chesapeake_votes(
    limit: int = 8,
    _: None = Depends(require_admin_token),
):
    """Scrape the N most recent Chesapeake council meetings for recorded votes."""
    result = _run_script("scrape_chesapeake_council.py", ["--limit", str(limit)], timeout=200)
    return {"ok": result.get("ok"), "scripts": {"scrape_chesapeake_council.py": result}}


@router.post("/ingest-suffolk-votes")
def admin_ingest_suffolk_votes(
    start_year: int | None = None,
    _: None = Depends(require_admin_token),
):
    """Scrape Suffolk AgendaCenter documents from start_year (default: this
    year) forward for recorded votes."""
    year = start_year or date.today().year
    args = ["--start-year", str(year), "--end-year", str(date.today().year)]
    # Suffolk re-sends every meeting in range through Gemini every run (no
    # per-meeting already-scraped short-circuit like the other 4 cities have,
    # just per-vote-item dedup on insert) -- confirmed a real run against the
    # full current year takes several minutes with occasional Gemini 504s.
    result = _run_script("scrape_suffolk_council.py", args, timeout=320)
    return {"ok": result.get("ok"), "scripts": {"scrape_suffolk_council.py": result}}


@router.post("/ingest-hampton-votes")
def admin_ingest_hampton_votes(
    start_year: int | None = None,
    _: None = Depends(require_admin_token),
):
    """Scrape Hampton Legistar meetings from start_year (default: this year)
    forward for recorded votes."""
    year = start_year or date.today().year
    args = ["--start-year", str(year), "--end-year", str(date.today().year)]
    result = _run_script("scrape_hampton_council.py", args, timeout=320)
    return {"ok": result.get("ok"), "scripts": {"scrape_hampton_council.py": result}}


@router.post("/ingest-newport-news-votes")
def admin_ingest_newport_news_votes(
    days: int = 60,
    _: None = Depends(require_admin_token),
):
    """Scrape Newport News CivicWeb voting records for all 12 members over
    the last `days` days (default 60)."""
    _start_d = date.today() - timedelta(days=days)
    start = f"{_start_d.strftime('%b')} {_start_d.day} {_start_d.year}"  # e.g. "Jun 5 2026", matching --start-date's expected format
    result = _run_script("scrape_newport_news_council.py", ["--start-date", start], timeout=420)
    return {"ok": result.get("ok"), "scripts": {"scrape_newport_news_council.py": result}}


@router.post("/ingest-portsmouth-votes")
def admin_ingest_portsmouth_votes(
    year: int | None = None,
    _: None = Depends(require_admin_token),
):
    """Scrape Portsmouth WebLink minutes for the given year (default: this
    year) for recorded votes."""
    y = year or date.today().year
    result = _run_script("scrape_portsmouth_council.py", ["--years", str(y)], timeout=320)
    return {"ok": result.get("ok"), "scripts": {"scrape_portsmouth_council.py": result}}


@router.post("/build-vb-blocs")
def admin_build_vb_blocs(
    reset: bool = False,
    _: None = Depends(require_admin_token),
):
    """Recompute pairwise VB council voting agreement into vb_voting_blocs."""
    args = ["--reset"] if reset else []
    result = _run_script("build_vb_blocs.py", args, timeout=120)
    return {"ok": result.get("ok"), "scripts": {"build_vb_blocs.py": result}}


@router.post("/build-vb-finance")
def admin_build_vb_finance(
    reset: bool = False,
    _: None = Depends(require_admin_token),
):
    """Classify VB council SBE contributions by sector into vb_finance_totals/summary."""
    args = ["--reset"] if reset else []
    result = _run_script("build_vb_finance.py", args, timeout=120)
    return {"ok": result.get("ok"), "scripts": {"build_vb_finance.py": result}}


@router.get("/vb-undisclosed-donors")
def admin_vb_undisclosed_donors(
    member: str | None = None,
    _: None = Depends(require_admin_token),
):
    """
    No params → all 11 members ranked by undisclosed_pct with summary stats.
    ?member=Hutcheson → full list of that member's undisclosed donor records.
    Undisclosed = both employer AND occupation filed as blank/unknown/n/a.
    """
    _UNDISCLOSED = {
        "unknown", "unkown", "unknow", "n/a", "na", "none",
        "", "not provided", "not available",
    }
    _SBE_FILTERS = {
        "Berlucchi":     {"first": "Michael",  "last": "Berlucchi",     "office": "Council"},
        "Dyer":          {"first": "Robert",   "last": "Dyer",          "office": "Mayor"},
        "Henley":        {"first": "Barbara",  "last": "Henley",        "office": "Council"},
        "Hutcheson":     {"first": "David",    "last": "Hutcheson",     "office": "Council"},
        "Wilson":        {"first": "Rosemary", "last": "Wilson",        "office": "Council"},
        "Remick":        {"first": "Robert",   "last": "Remick",        "office": "Council"},
        "Ross-Hammond":  {"first": "Amelia",   "last": "Ross-Hammond",  "office": "Council"},
        "Schulman":      {"first": "Joashua",  "last": "Schulman",      "office": "Council"},
        "Rouse":         {"first": "Jennifer", "last": "Rouse",         "office": "Council"},
        "Cummings":      {"first": "Stacy",    "last": "Cummings",      "office": "Council"},
        "Jackson-Green": {"first": "Calvern",  "last": "Jackson-Green", "office": "Council"},
    }

    conn = sqlite3.connect(_POLLS_DB)
    conn.row_factory = sqlite3.Row
    try:
        if not member:
            rows = conn.execute("""
                SELECT member_name, total_raised, undisclosed_count,
                       undisclosed_amt, undisclosed_pct
                FROM vb_finance_summary
                ORDER BY undisclosed_pct DESC
            """).fetchall()
            return {
                "members": [
                    {
                        "member": r["member_name"],
                        "total_raised": r["total_raised"],
                        "undisclosed_count": r["undisclosed_count"],
                        "undisclosed_amt": r["undisclosed_amt"],
                        "undisclosed_pct": r["undisclosed_pct"],
                    }
                    for r in rows
                ]
            }

        key = member.strip().title().replace(" ", "-")
        # Allow flexible match: "hutcheson", "Hutcheson", "David Hutcheson"
        matched = next(
            (k for k in _SBE_FILTERS if k.lower() == key.lower()
             or k.lower() in key.lower()),
            None,
        )
        if not matched:
            raise HTTPException(status_code=404,
                                detail=f"Member '{member}' not found. "
                                       f"Valid keys: {list(_SBE_FILTERS)}")

        f = _SBE_FILTERS[matched]
        rows = conn.execute("""
            SELECT contributor_first, contributor_last, employer, occupation,
                   amount, transaction_date, election_cycle
            FROM sbe_local_contributions
            WHERE locality = 'Virginia Beach'
              AND LOWER(candidate_name) LIKE LOWER(?)
              AND LOWER(candidate_name) LIKE LOWER(?)
              AND LOWER(office_sought) LIKE LOWER(?)
              AND amount > 0
            ORDER BY amount DESC, transaction_date DESC
        """, (f"%{f['first']}%", f"%{f['last']}%", f"%{f['office']}%")).fetchall()

        undisclosed = [
            {
                "name": f"{r['contributor_first'] or ''} {r['contributor_last'] or ''}".strip(),
                "employer": r["employer"],
                "occupation": r["occupation"],
                "amount": r["amount"],
                "date": r["transaction_date"],
                "cycle": r["election_cycle"],
            }
            for r in rows
            if (r["employer"] or "").lower().strip() in _UNDISCLOSED
            and (r["occupation"] or "").lower().strip() in _UNDISCLOSED
        ]
        total_amt = sum(d["amount"] for d in undisclosed)
        total_raised = conn.execute(
            "SELECT total_raised FROM vb_finance_summary WHERE member_name=?",
            (matched,)
        ).fetchone()
        return {
            "member": matched,
            "undisclosed_count": len(undisclosed),
            "undisclosed_amt": total_amt,
            "undisclosed_pct": round(100 * total_amt / total_raised["total_raised"], 1)
            if total_raised and total_raised["total_raised"] else 0,
            "donors": undisclosed,
        }
    finally:
        conn.close()


@router.post("/build-vb-dvadj")
def admin_build_vb_dvadj(
    reset: bool = False,
    _: None = Depends(require_admin_token),
):
    """Compute VB donor-sector ↔ vote-topic adjacency into vb_donor_vote_adjacency/summary."""
    args = ["--reset"] if reset else []
    result = _run_script("build_vb_dvadj.py", args, timeout=120)
    return {"ok": result.get("ok"), "scripts": {"build_vb_dvadj.py": result}}


@router.post("/build-vb-dissent")
def admin_build_vb_dissent(
    reset: bool = False,
    _: None = Depends(require_admin_token),
):
    """Compute per-member VB No-vote rates vs council avg by topic into vb_member_dissent."""
    args = ["--reset"] if reset else []
    result = _run_script("build_vb_dissent.py", args, timeout=120)
    return {"ok": result.get("ok"), "scripts": {"build_vb_dissent.py": result}}


@router.post("/build-norfolk-splits")
def admin_build_norfolk_splits(
    reset: bool = False,
    _: None = Depends(require_admin_token),
):
    """Precompute non-unanimous Norfolk council votes into norfolk_split_votes."""
    args = ["--reset"] if reset else []
    result = _run_script("build_norfolk_splits.py", args, timeout=120)
    return {"ok": result.get("ok"), "scripts": {"build_norfolk_splits.py": result}}


@router.post("/build-norfolk-blocs")
def admin_build_norfolk_blocs(
    reset: bool = False,
    _: None = Depends(require_admin_token),
):
    """Precompute pairwise Norfolk council voting agreement into norfolk_voting_blocs."""
    args = ["--reset"] if reset else []
    result = _run_script("build_norfolk_blocs.py", args, timeout=120)
    return {"ok": result.get("ok"), "scripts": {"build_norfolk_blocs.py": result}}


@router.post("/build-norfolk-dissent")
def admin_build_norfolk_dissent(
    reset: bool = False,
    _: None = Depends(require_admin_token),
):
    """Precompute per-member Norfolk No-vote rates by topic into norfolk_member_dissent."""
    args = ["--reset"] if reset else []
    result = _run_script("build_norfolk_dissent.py", args, timeout=120)
    return {"ok": result.get("ok"), "scripts": {"build_norfolk_dissent.py": result}}


@router.post("/build-norfolk-finance")
def admin_build_norfolk_finance(
    reset: bool = False,
    _: None = Depends(require_admin_token),
):
    """Classify Norfolk council SBE contributions by sector into norfolk_finance_totals."""
    args = ["--reset"] if reset else []
    result = _run_script("build_norfolk_finance.py", args, timeout=120)
    return {"ok": result.get("ok"), "scripts": {"build_norfolk_finance.py": result}}


@router.post("/build-norfolk-dvadj")
def admin_build_norfolk_dvadj(
    reset: bool = False,
    _: None = Depends(require_admin_token),
):
    """Compute Norfolk donor-sector ↔ vote-topic adjacency into norfolk_donor_vote_adjacency."""
    args = ["--reset"] if reset else []
    result = _run_script("build_norfolk_dvadj.py", args, timeout=120)
    return {"ok": result.get("ok"), "scripts": {"build_norfolk_dvadj.py": result}}


@router.post("/build-vb-splits")
def admin_build_vb_splits(
    reset: bool = False,
    _: None = Depends(require_admin_token),
):
    """Precompute non-unanimous VB council votes into vb_split_votes."""
    args = ["--reset"] if reset else []
    result = _run_script("build_vb_splits.py", args, timeout=120)
    return {"ok": result.get("ok"), "scripts": {"build_vb_splits.py": result}}


@router.get("/refresh-bill-text")
def admin_refresh_bill_text(refresh: bool = False, _: None = Depends(require_admin_token)):
    """Fetch full bill text from Congress.gov + govinfo.gov for all stored bills."""
    if not os.getenv("CONGRESS_API_KEY"):
        return {"ok": False, "error": "CONGRESS_API_KEY not set"}
    args = ["--fetch-text", "--bill-limit", "0"]
    if refresh:
        args.append("--refresh-text")
    path = os.path.join(_BASE_DIR, "ingest_congress.py")
    if not os.path.exists(path):
        return {"ok": False, "error": "ingest_congress.py not found"}
    try:
        r = subprocess.run([sys.executable, path] + args,
                           capture_output=True, text=True, timeout=900)
        lines = r.stdout.strip().splitlines()
        summary = next((l for l in reversed(lines) if "bill text" in l.lower()),
                       lines[-1] if lines else "")
        return {
            "ok": r.returncode == 0,
            "summary": summary,
            "stderr": r.stderr[-300:] if r.returncode != 0 else "",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timed out after 900s"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/ingest-polls")
def admin_ingest_polls(
    sources: str = "fivethirtyeight,votehub,news",
    use_gemini: bool = False,
    _: None = Depends(require_admin_token),
):
    """Manually trigger poll ingestion."""
    script = os.path.join(_BASE_DIR, "ingest_va_polls.py")
    if not os.path.exists(script):
        return {"ok": False, "error": "ingest_va_polls.py not found"}
    source_list = [s.strip() for s in sources.split(",") if s.strip()]
    cmd = [sys.executable, script, "--db", _POLLS_DB]
    for s in source_list:
        cmd += ["--source", s]
    if use_gemini and os.getenv("GEMINI_API_KEY"):
        cmd.append("--use-gemini")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return {
            "ok": r.returncode == 0,
            "returncode": r.returncode,
            "stdout": r.stdout[-2000:],
            "stderr": r.stderr[-1000:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ingestion timed out after 180s"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/reload-votes")
def admin_reload_votes(_: None = Depends(require_admin_token)):
    """Reload the in-memory vote cache from polls.db."""
    import main as _m
    _m._load_votes_cache()
    total = sum(len(v) for v in _m._VOTES_CACHE.values())
    return {"ok": True, "total_votes": total, "members": len(_m._VOTES_CACHE)}


@router.get("/reload-pacs")
def admin_reload_pacs(_: None = Depends(require_admin_token)):
    """Reload the in-memory PAC/industry cache from polls.db."""
    import main as _m
    _m._load_pac_cache()
    total = sum(len(v) for v in _m._PAC_CACHE.values())
    return {"ok": True, "total_industry_rows": total, "members": len(_m._PAC_CACHE)}


@router.get("/ingest-fec-pacs")
def admin_ingest_fec_pacs(
    background: BackgroundTasks,
    cycle: int | None = None,
    _: None = Depends(require_admin_token),
):
    """Trigger FEC PAC/industry ingestion in the background (non-blocking)."""
    script = os.path.join(_BASE_DIR, "ingest_fec_pacs.py")
    if not os.path.exists(script):
        return {"ok": False, "error": "ingest_fec_pacs.py not found"}

    def _run():
        cmd = [sys.executable, script]
        if cycle:
            cmd += ["--cycle", str(cycle)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if result.returncode == 0:
                import main as _m
                _m._load_pac_cache()
                print("[fec] Admin-triggered ingestion complete — cache reloaded.")
            else:
                print(f"[fec] Admin ingestion failed: {result.stderr[-300:]}")
        except subprocess.TimeoutExpired:
            print("[fec] Admin ingestion timed out after 30 minutes.")
        except Exception as exc:
            print(f"[fec] Admin ingestion error: {exc}")

    background.add_task(_run)
    label = f"cycle {cycle}" if cycle else "all cycles (2020/2022/2024)"
    return {"ok": True, "message": f"FEC ingestion started in background for {label}. Cache will reload when complete."}


@router.get("/ingest-committees")
def admin_ingest_committees(
    background: BackgroundTasks,
    _: None = Depends(require_admin_token),
):
    """Refresh congressional committee assignments (non-blocking)."""
    script = os.path.join(_BASE_DIR, "ingest_committees.py")
    if not os.path.exists(script):
        return {"ok": False, "error": "ingest_committees.py not found"}

    def _run():
        try:
            result = subprocess.run(
                [sys.executable, "-X", "utf8", script],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                print("[committees] Admin refresh complete.")
            else:
                print(f"[committees] Admin refresh failed: {result.stderr[-300:]}")
        except Exception as exc:
            print(f"[committees] Admin refresh error: {exc}")

    background.add_task(_run)
    return {"ok": True, "message": "Committee ingestion started — data refreshes within ~10 seconds."}


@router.post("/ingest-fec")
def admin_ingest_fec(cycle: str = "2026", _: None = Depends(require_admin_token)):
    """Trigger FEC campaign finance ingestion for Virginia candidates."""
    if not os.getenv("FEC_API_KEY"):
        return {"ok": False, "error": "FEC_API_KEY not set"}
    return _run_script("ingest_fec.py", ["--db", _FEC_DB, "--cycle", cycle], timeout=300)


@router.post("/ingest-fec-individuals")
async def admin_ingest_fec_individuals(_: None = Depends(require_admin_token)):
    """
    Run full FEC individual contributions pipeline
    for all Virginia House/Senate members into polls.db.
    """
    if not os.getenv("FEC_API_KEY"):
        return {"ok": False, "error": "FEC_API_KEY not set"}
    try:
        r = subprocess.run(
            [sys.executable, "-m", "voteiq.scripts.fix_fec_pipeline"],
            capture_output=True, text=True, timeout=900,
            env={**os.environ, "PYTHONPATH": _BASE_DIR},
        )
        return {
            "ok": r.returncode == 0,
            "returncode": r.returncode,
            "stdout": r.stdout[-3000:],
            "stderr": r.stderr[-1000:] if r.stderr else None,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timed out after 900s"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/upload-db")
async def upload_db(
    file: UploadFile = File(...),
    _: None = Depends(require_admin_token),
):
    """Upload a SQLite DB to the persistent DATA_DIR."""
    allowed = {"openstates_va.db", "polls.db"}
    if file.filename not in allowed:
        raise HTTPException(status_code=400, detail=f"Only {allowed} allowed")
    dest = os.path.join(_DATA_DIR, file.filename)
    data = await file.read()
    with open(dest, "wb") as f:
        f.write(data)
    size_mb = len(data) / (1024 * 1024)
    if file.filename == "polls.db":
        _reload_caches()
    return {"ok": True, "saved_to": dest, "size_mb": round(size_mb, 1)}


# ── debug endpoints ────────────────────────────────────────────────────────────

@router.get("/congress-debug")
def congress_debug(_: None = Depends(require_admin_token)):
    """Show what federal data is currently in polls.db on this server."""
    import main as _m
    info: dict = {}
    try:
        conn = sqlite3.connect(_POLLS_DB)
        for table in ("congress_members", "congress_votes", "congress_bills"):
            try:
                info[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except Exception as e:
                info[table] = f"missing: {e}"
        try:
            rows = conn.execute(
                "SELECT vote_date, bill, member_vote FROM congress_votes "
                "WHERE bioguide_id='K000399' ORDER BY vote_date DESC LIMIT 3"
            ).fetchall()
            info["kiggans_sample"] = [{"date": r[0], "bill": r[1], "vote": r[2]} for r in rows]
        except Exception as e:
            info["kiggans_sample"] = str(e)
        conn.close()
    except Exception as e:
        info["error"] = str(e)
    cache = _m._federal_members_cache
    info["_federal_members_cache_size"] = len(cache) if cache is not None else "not loaded"
    return info


# pac-summary and donors live in voteiq/api/routes/members.py


# ── bills debug / search ───────────────────────────────────────────────────────

@router.get("/bills-debug")
async def bills_debug(_ = Depends(require_admin_token)):
    conn = sqlite3.connect(_POLLS_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    tables = {}
    for table in ["va_bills", "va_votes", "donor_sector_totals", "va_sbe_contributions"]:
        try:
            cur.execute(f"SELECT COUNT(*) AS count FROM {table}")
            tables[table] = cur.fetchone()["count"]
        except Exception as e:
            tables[table] = f"error: {e}"
    conn.close()
    return {"status": "success", "tables": tables}


@router.get("/bill-descriptions/search")
def bill_descriptions_search(q: str, session: str | None = None, limit: int = 8):
    """Instant local bill-description search — no AI, Chroma, or Voyage calls."""
    import main as _m
    if not os.path.exists(_m._OPENSTATES_DB):
        return {"count": 0, "results": [], "source": "missing_openstates_db"}

    bill_numbers = _m._extract_bill_numbers(q)
    blocks = (
        _m._cached_bill_description_lookup(bill_numbers, session)
        if bill_numbers
        else _m._cached_bill_description_search(q, session, limit)
    )
    results = []
    for block in blocks.split("\n\n"):
        if not block.strip():
            continue
        header, _, body = block.partition("\n")
        match = re.search(r"([A-Z]+[0-9]+)\s+([0-9]{4})", header)
        results.append({
            "bill_id": match.group(1) if match else "",
            "session": match.group(2) if match else "",
            "description": body.strip(),
        })

    try:
        conn = sqlite3.connect(_m._OPENSTATES_DB)
        cache_count = conn.execute("SELECT COUNT(*) FROM bill_descriptions").fetchone()[0]
        conn.close()
    except Exception:
        cache_count = None
    return {
        "count":        len(results),
        "cache_count":  cache_count,
        "source":       "local_sqlite_bill_descriptions",
        "results":      results[:limit],
    }
