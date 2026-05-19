"""Admin and ingestion endpoints."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from voteiq.api.dependencies import require_admin_token

router = APIRouter(tags=["admin"])

_BASE_DIR = str(Path(__file__).resolve().parent.parent.parent.parent)
_POLLS_DB = os.path.join(os.getenv("DATA_DIR", _BASE_DIR), "polls.db")
_DATA_DIR = os.getenv("DATA_DIR", _BASE_DIR)
_FEC_DB   = os.path.join(_BASE_DIR, "fec_va.db")


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

@router.get("/api/admin/ingest-congress")
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

    import main as _m
    _m._federal_members_cache = None
    return {"ok": all(v.get("ok") for v in results.values()), "scripts": results}


@router.get("/api/admin/refresh-bill-text")
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


@router.get("/api/admin/ingest-polls")
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


@router.get("/api/admin/reload-votes")
def admin_reload_votes(_: None = Depends(require_admin_token)):
    """Reload the in-memory vote cache from polls.db."""
    import main as _m
    _m._load_votes_cache()
    total = sum(len(v) for v in _m._VOTES_CACHE.values())
    return {"ok": True, "total_votes": total, "members": len(_m._VOTES_CACHE)}


@router.get("/api/admin/reload-pacs")
def admin_reload_pacs(_: None = Depends(require_admin_token)):
    """Reload the in-memory PAC/industry cache from polls.db."""
    import main as _m
    _m._load_pac_cache()
    total = sum(len(v) for v in _m._PAC_CACHE.values())
    return {"ok": True, "total_industry_rows": total, "members": len(_m._PAC_CACHE)}


@router.get("/api/admin/ingest-fec-pacs")
def admin_ingest_fec_pacs(cycle: int | None = None, _: None = Depends(require_admin_token)):
    """Trigger FEC PAC/industry ingestion in a background thread (non-blocking)."""
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

    threading.Thread(target=_run, daemon=True).start()
    label = f"cycle {cycle}" if cycle else "all cycles (2020/2022/2024)"
    return {"ok": True, "message": f"FEC ingestion started in background for {label}. Cache will reload when complete."}


@router.get("/api/admin/ingest-committees")
def admin_ingest_committees(_: None = Depends(require_admin_token)):
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

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "message": "Committee ingestion started — data refreshes within ~10 seconds."}


@router.get("/api/admin/ingest-fec")
def admin_ingest_fec(cycle: str = "2026", _: None = Depends(require_admin_token)):
    """Trigger FEC campaign finance ingestion for Virginia candidates."""
    if not os.getenv("FEC_API_KEY"):
        return {"ok": False, "error": "FEC_API_KEY not set"}
    return _run_script("ingest_fec.py", ["--db", _FEC_DB, "--cycle", cycle], timeout=300)


@router.post("/api/admin/upload-db")
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

@router.get("/api/congress-debug")
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


# ── FEC donor detail (public, not admin-gated) ────────────────────────────────

@router.get("/api/congress/pac-summary/{bioguide_id}")
def pac_summary(bioguide_id: str):
    """Return FEC industry donation totals with per-cycle breakdown and trend %."""
    if not os.path.exists(_POLLS_DB):
        return {"bioguide_id": bioguide_id, "industries": [], "note": "polls.db missing"}
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fec_industry_totals'"
        ).fetchone()
        if not tbl:
            conn.close()
            return {"bioguide_id": bioguide_id, "industries": [], "note": "no data"}
        all_rows = conn.execute(
            "SELECT industry, cycle, total_amount, contributor_count, top_donors, member_name "
            "FROM fec_industry_totals WHERE bioguide_id=? ORDER BY industry, cycle",
            (bioguide_id,)
        ).fetchall()
        conn.close()
    except Exception as exc:
        return {"bioguide_id": bioguide_id, "industries": [], "note": str(exc)}

    if not all_rows:
        return {"bioguide_id": bioguide_id, "industries": [], "note": "no data — run ingest_fec_pacs.py"}

    member_name = all_rows[0]["member_name"]
    by_industry: dict = defaultdict(lambda: {"by_cycle": {}, "top_donors": [], "count": 0})
    for row in all_rows:
        ind, cyc = row["industry"], str(row["cycle"])
        by_industry[ind]["by_cycle"][cyc] = row["total_amount"]
        by_industry[ind]["count"] += row["contributor_count"]
        if not by_industry[ind]["top_donors"]:
            by_industry[ind]["top_donors"] = json.loads(row["top_donors"] or "[]")

    industries = []
    for ind, data in by_industry.items():
        by_cycle = data["by_cycle"]
        sorted_cycles = sorted(by_cycle.keys())
        latest, earliest = by_cycle[sorted_cycles[-1]], by_cycle[sorted_cycles[0]]
        if len(sorted_cycles) > 1 and earliest > 0:
            trend_pct = round((latest - earliest) / earliest * 100)
            trend_label = f"+{trend_pct}%" if trend_pct >= 0 else f"{trend_pct}%"
            is_new = False
        elif len(sorted_cycles) == 1 and sorted_cycles[0] != "2020":
            trend_pct, trend_label, is_new = None, "NEW", True
        else:
            trend_pct, trend_label, is_new = None, "—", False
        industries.append({
            "industry": ind, "total": latest, "count": data["count"],
            "top_donors": data["top_donors"], "by_cycle": by_cycle,
            "cycles": sorted_cycles, "trend_pct": trend_pct,
            "trend_label": trend_label, "is_new": is_new,
        })

    industries.sort(key=lambda r: r["total"], reverse=True)
    all_cycles = sorted({str(row["cycle"]) for row in all_rows})
    return {
        "bioguide_id": bioguide_id, "member_name": member_name,
        "cycles": all_cycles, "latest_cycle": all_cycles[-1] if all_cycles else "",
        "industries": industries,
    }


@router.get("/api/congress/donors/{candidate_id}")
def candidate_donors(
    candidate_id: str,
    cycle: int = 2024,
    sector: str = "",
    limit: int = 100,
    offset: int = 0,
    sort: str = "amount",
):
    """Paginated donor list for a FEC candidate ID."""
    if not os.path.exists(_POLLS_DB):
        return {"error": "polls.db missing"}
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fec_individual_contributions'"
        ).fetchone()
        if not tbl:
            conn.close()
            return {"candidate_id": candidate_id, "donors": [], "note": "no data — run fec_employer_pipeline.py"}
        sort_col = "amount" if sort == "amount" else "contribution_date"
        where = "candidate_id = ? AND cycle = ?"
        params: list = [candidate_id, cycle]
        if sector:
            where += " AND employer_sector = ?"
            params.append(sector)
        total = conn.execute(
            f"SELECT COUNT(*) FROM fec_individual_contributions WHERE {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"""SELECT contributor_name, contributor_employer, employer_sector,
                       contributor_occupation, amount, contribution_date, city, state
                FROM fec_individual_contributions
                WHERE {where} ORDER BY {sort_col} DESC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        sector_rows = conn.execute(
            """SELECT employer_sector, SUM(amount) AS total, COUNT(*) AS donors
               FROM fec_individual_contributions
               WHERE candidate_id = ? AND cycle = ?
               GROUP BY employer_sector ORDER BY total DESC""",
            [candidate_id, cycle],
        ).fetchall()
        conn.close()
        return {
            "candidate_id": candidate_id, "cycle": cycle,
            "total_donors": total, "limit": limit, "offset": offset,
            "sector_filter": sector or None,
            "sector_totals": [
                {"sector": r["employer_sector"], "total": r["total"], "donors": r["donors"]}
                for r in sector_rows
            ],
            "donors": [
                {"name": r["contributor_name"], "employer": r["contributor_employer"],
                 "sector": r["employer_sector"], "occupation": r["contributor_occupation"],
                 "amount": r["amount"], "date": r["contribution_date"],
                 "city": r["city"], "state": r["state"]}
                for r in rows
            ],
        }
    except Exception as exc:
        return {"error": str(exc)}
