from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware
import threading
import uvicorn
import folium
import geopandas as gpd
import os
import json
import re
import time
import hashlib
import sqlite3
import asyncio
from html import escape
import requests
import anthropic
import google.genai as genai
from address_lookup import find_district
import ingest_news

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI()
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

_OPENSTATES_DB = os.path.join(BASE_DIR, "openstates_va.db")
_POLLS_DB = os.path.join(BASE_DIR, "polls.db")
_FEC_DB = os.path.join(BASE_DIR, "fec_va.db")


import subprocess
import sys


def _polls_are_fresh() -> bool:
    """Return True if polls were ingested within the last 23 hours."""
    from datetime import datetime, timezone, timedelta
    try:
        conn = sqlite3.connect(_POLLS_DB)
        row = conn.execute("SELECT MAX(fetched_at) FROM polls").fetchone()
        conn.close()
        if row and row[0]:
            last = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
            return datetime.now(timezone.utc) - last < timedelta(hours=23)
    except Exception:
        pass
    return False


def _run_ingest_subprocess(sources: list[str] | None = None, extra_flags: list[str] | None = None) -> dict:
    """Run ingest_va_polls.py in a subprocess and return a result dict."""
    script = os.path.join(BASE_DIR, "ingest_va_polls.py")
    if not os.path.exists(script):
        return {"ok": False, "error": "ingest_va_polls.py not found"}
    cmd = [sys.executable, script, "--db", _POLLS_DB]
    for s in (sources or ["fivethirtyeight", "votehub", "news"]):
        cmd += ["--source", s]
    cmd += extra_flags or []
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-1000:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ingestion timed out after 180s"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _poll_ingest_background() -> None:
    if _polls_are_fresh():
        print("[polls] Data fresh — skipping startup ingestion.")
        return
    print("[polls] Starting background ingestion…")
    result = _run_ingest_subprocess()
    if result["ok"]:
        print("[polls] Startup ingestion complete.")
    else:
        print(f"[polls] Startup ingestion failed: {result.get('error') or result.get('stderr')}")


def _fec_pacs_are_fresh(max_age_days: int = 7) -> bool:
    """Return True if FEC PAC data was ingested within max_age_days."""
    from datetime import datetime, timezone, timedelta
    try:
        conn = sqlite3.connect(_POLLS_DB)
        row = conn.execute("SELECT MAX(fetched_at) FROM fec_industry_totals").fetchone()
        conn.close()
        if row and row[0]:
            last = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
            return datetime.now(timezone.utc) - last < timedelta(days=max_age_days)
    except Exception:
        pass
    return False


def _run_fec_ingest_background() -> None:
    """Run ingest_fec_pacs.py for all three cycles in a background thread."""
    if _fec_pacs_are_fresh():
        print("[fec] PAC data fresh — skipping startup ingestion.")
        return
    print("[fec] PAC data stale — starting background ingestion (all cycles)…")
    script = os.path.join(BASE_DIR, "ingest_fec_pacs.py")
    if not os.path.exists(script):
        print("[fec] ingest_fec_pacs.py not found — skipping.")
        return
    try:
        result = subprocess.run(
            [sys.executable, script],
            capture_output=True, text=True, timeout=1800,  # 30 min max
        )
        if result.returncode == 0:
            _load_pac_cache()
            print("[fec] Background ingestion complete — PAC cache reloaded.")
        else:
            print(f"[fec] Ingestion failed (rc={result.returncode}): {result.stderr[-500:]}")
    except subprocess.TimeoutExpired:
        print("[fec] Ingestion timed out after 30 minutes.")
    except Exception as exc:
        print(f"[fec] Ingestion error: {exc}")


def _congress_votes_are_fresh() -> bool:
    """Return True if congress_votes table has rows ingested in the last 30 days."""
    from datetime import datetime, timezone, timedelta
    try:
        conn = sqlite3.connect(_POLLS_DB)
        row = conn.execute("SELECT MAX(fetched_at) FROM congress_votes").fetchone()
        conn.close()
        if row and row[0]:
            last = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
            return datetime.now(timezone.utc) - last < timedelta(days=30)
    except Exception:
        pass
    return False


def _congress_ingest_background() -> None:
    """Run ingest_congress_votes.py (no API key needed) if data is missing or stale.
    Uses a small limit on startup (50 rolls) so it finishes in ~60s.
    Full refresh can be triggered via /api/admin/ingest-congress.
    """
    if _congress_votes_are_fresh():
        print("[congress] Vote data fresh — skipping startup ingestion.")
        return
    print("[congress] Vote data missing or stale — starting background ingestion…")
    # Members/bills first (needs API key, fast)
    api_key = os.getenv("CONGRESS_API_KEY", "")
    if api_key:
        members_script = os.path.join(BASE_DIR, "ingest_congress.py")
        if os.path.exists(members_script):
            try:
                r = subprocess.run(
                    [sys.executable, members_script],
                    capture_output=True, text=True, timeout=300,
                )
                print(f"[congress] Members/bills rc={r.returncode}")
            except Exception as exc:
                print(f"[congress] Members/bills error: {exc}")
    # Votes — small limit on startup so it completes quickly
    script = os.path.join(BASE_DIR, "ingest_congress_votes.py")
    if not os.path.exists(script):
        print("[congress] ingest_congress_votes.py not found — skipping.")
        return
    try:
        r = subprocess.run(
            [sys.executable, script, "--house-limit", "50", "--senate-limit", "50"],
            capture_output=True, text=True, timeout=180,
        )
        if r.returncode == 0:
            global _federal_members_cache
            _federal_members_cache = None
            print(f"[congress] Startup ingestion complete. {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ''}")
        else:
            print(f"[congress] Ingestion failed (rc={r.returncode}): {r.stderr[-300:]}")
    except subprocess.TimeoutExpired:
        print("[congress] Ingestion timed out after 180s.")
    except Exception as exc:
        print(f"[congress] Ingestion error: {exc}")


@app.on_event("startup")
async def startup_poll_ingest() -> None:
    threading.Thread(target=_poll_ingest_background, daemon=True).start()
    threading.Thread(target=_run_fec_ingest_background, daemon=True).start()
    threading.Thread(target=_congress_ingest_background, daemon=True).start()


@app.get("/api/va-news")
def va_news(limit: int = 30, topic: str | None = None, politician: str | None = None):
    """Return Virginia political news extracted by Gemini."""
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
            (limit * 3,),  # over-fetch so we can filter in Python
        ).fetchall()
        conn.close()
        results = []
        for row in rows:
            item = dict(row)
            gj = item.pop("gemini_json", None)
            item["data"] = json.loads(gj) if gj else {}
            # topic filter
            if topic:
                topics = item["data"].get("topics") or []
                if not any(topic.lower() in t.lower() for t in topics):
                    continue
            # politician filter
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


@app.get("/api/admin/ingest-news")
def admin_ingest_news(limit: int = 50):
    """Trigger Virginia political news ingestion via Gemini."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return {"ok": False, "error": "GEMINI_API_KEY not set"}
    script = os.path.join(BASE_DIR, "ingest_va_news.py")
    if not os.path.exists(script):
        return {"ok": False, "error": "ingest_va_news.py not found"}
    import subprocess
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


@app.get("/api/admin/ingest-congress")
def admin_ingest_congress(congress: int = 119, house_limit: int = 300):
    """Populate polls.db congress_members, congress_bills, congress_votes tables.
    ingest_congress.py requires CONGRESS_API_KEY; ingest_congress_votes.py runs always.
    """
    api_key = os.getenv("CONGRESS_API_KEY", "")
    results = {}
    scripts = []
    if api_key:
        scripts.append(("ingest_congress.py", ["--congress", str(congress)]))
    else:
        results["ingest_congress.py"] = {"ok": False, "skipped": "CONGRESS_API_KEY not set — members/bills not refreshed"}
    scripts.append(("ingest_congress_votes.py", ["--house-limit", str(house_limit)]))
    for script_name, extra in scripts:
        script = os.path.join(BASE_DIR, script_name)
        if not os.path.exists(script):
            results[script_name] = {"ok": False, "error": "script not found"}
            continue
        try:
            r = subprocess.run(
                [sys.executable, script] + extra,
                capture_output=True, text=True, timeout=600,
            )
            results[script_name] = {
                "ok": r.returncode == 0,
                "stdout": r.stdout[-2000:],
                "stderr": r.stderr[-500:],
            }
        except subprocess.TimeoutExpired:
            results[script_name] = {"ok": False, "error": "timed out after 600s"}
        except Exception as exc:
            results[script_name] = {"ok": False, "error": str(exc)}
    # Bust the in-process cache so new rows are picked up immediately
    global _federal_members_cache
    _federal_members_cache = None
    return {"ok": all(v.get("ok") for v in results.values()), "scripts": results}


@app.get("/api/admin/ingest-polls")
def admin_ingest_polls(sources: str = "fivethirtyeight,votehub,news", use_gemini: bool = False, token: str = ""):
    """Manually trigger poll ingestion. Pass use_gemini=true to enrich articles with Gemini."""
    expected = os.getenv("POLL_INGEST_ADMIN_TOKEN", "")
    if expected and token != expected:
        raise HTTPException(status_code=403, detail="Invalid poll ingest token")
    source_list = [s.strip() for s in sources.split(",") if s.strip()]
    extra_flags = ["--use-gemini"] if use_gemini and os.getenv("GEMINI_API_KEY") else []
    result = _run_ingest_subprocess(source_list, extra_flags=extra_flags)
    return result

@app.get("/api/admin/reload-votes")
def admin_reload_votes():
    """Reload the in-memory vote cache from polls.db (run after ingest_congress_votes.py)."""
    _load_votes_cache()
    total = sum(len(v) for v in _VOTES_CACHE.values())
    return {"ok": True, "total_votes": total, "members": len(_VOTES_CACHE)}


@app.get("/api/admin/reload-pacs")
def admin_reload_pacs():
    """Reload the in-memory PAC/industry cache from polls.db (run after ingest_fec_pacs.py)."""
    _load_pac_cache()
    total = sum(len(v) for v in _PAC_CACHE.values())
    return {"ok": True, "total_industry_rows": total, "members": len(_PAC_CACHE)}


@app.get("/api/admin/ingest-fec-pacs")
def admin_ingest_fec_pacs(cycle: int | None = None):
    """Trigger FEC PAC/industry ingestion in a background thread (non-blocking).
    Omit cycle to run all three cycles (2020, 2022, 2024).
    """
    script = os.path.join(BASE_DIR, "ingest_fec_pacs.py")
    if not os.path.exists(script):
        return {"ok": False, "error": "ingest_fec_pacs.py not found"}

    def _run():
        cmd = [sys.executable, script]
        if cycle:
            cmd += ["--cycle", str(cycle)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if result.returncode == 0:
                _load_pac_cache()
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


@app.get("/api/congress/pac-summary/{bioguide_id}")
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

        # Fetch all cycles for this member
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

    # Group by industry
    from collections import defaultdict
    by_industry: dict[str, dict] = defaultdict(lambda: {"by_cycle": {}, "top_donors": [], "count": 0})
    for row in all_rows:
        ind = row["industry"]
        cyc = str(row["cycle"])
        by_industry[ind]["by_cycle"][cyc] = row["total_amount"]
        by_industry[ind]["count"] += row["contributor_count"]
        if not by_industry[ind]["top_donors"]:
            by_industry[ind]["top_donors"] = json.loads(row["top_donors"] or "[]")

    industries = []
    for ind, data in by_industry.items():
        by_cycle = data["by_cycle"]
        sorted_cycles = sorted(by_cycle.keys())
        latest_total = by_cycle[sorted_cycles[-1]]
        earliest_total = by_cycle[sorted_cycles[0]]

        # Trend: % change from earliest to latest cycle
        if len(sorted_cycles) > 1 and earliest_total > 0:
            trend_pct = round((latest_total - earliest_total) / earliest_total * 100)
            trend_label = f"+{trend_pct}%" if trend_pct >= 0 else f"{trend_pct}%"
            is_new = False
        elif len(sorted_cycles) == 1 and sorted_cycles[0] != "2020":
            trend_pct = None
            trend_label = "NEW"
            is_new = True
        else:
            trend_pct = None
            trend_label = "—"
            is_new = False

        industries.append({
            "industry":    ind,
            "total":       latest_total,
            "count":       data["count"],
            "top_donors":  data["top_donors"],
            "by_cycle":    by_cycle,
            "cycles":      sorted_cycles,
            "trend_pct":   trend_pct,
            "trend_label": trend_label,
            "is_new":      is_new,
        })

    industries.sort(key=lambda r: r["total"], reverse=True)
    all_cycles = sorted({str(row["cycle"]) for row in all_rows})

    return {
        "bioguide_id":  bioguide_id,
        "member_name":  member_name,
        "cycles":       all_cycles,
        "latest_cycle": all_cycles[-1] if all_cycles else "",
        "industries":   industries,
    }


@app.get("/api/admin/ingest-fec")
def admin_ingest_fec(cycle: str = "2026"):
    """Trigger FEC campaign finance ingestion for Virginia candidates."""
    api_key = os.getenv("FEC_API_KEY", "")
    if not api_key:
        return {"ok": False, "error": "FEC_API_KEY not set"}
    script = os.path.join(BASE_DIR, "ingest_fec.py")
    if not os.path.exists(script):
        return {"ok": False, "error": "ingest_fec.py not found"}
    
    cmd = [sys.executable, script, "--db", _FEC_DB, "--cycle", cycle]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-500:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "FEC ingestion timed out after 300s"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

def _get_fec_summary(name: str) -> str:
    """Fetch a grounded FEC context string for a candidate name."""
    if not os.path.exists(_FEC_DB):
        return ""
    try:
        conn = sqlite3.connect(_FEC_DB)
        conn.row_factory = sqlite3.Row
        # Search for candidate by name in the finance table
        row = conn.execute(
            "SELECT * FROM va_candidate_finance WHERE name LIKE ? ORDER BY cycle DESC LIMIT 1",
            (f"%{name}%",)
        ).fetchone()
        conn.close()
        if row:
            return (
                f"\nFEC CAMPAIGN FINANCE GROUNDING ({row['cycle']}):\n"
                f"  - Total Receipts: ${row['total_receipts']:,.2f}\n"
                f"  - Total Disbursements: ${row['total_disbursements']:,.2f}\n"
                f"  - Cash on Hand: ${row['cash_on_hand']:,.2f}\n"
                f"  - Debt Owed: ${row['debts_owed']:,.2f}\n"
                f"  - Last Updated: {row['last_updated']}\n"
                f"  - Filing URL: {row['filing_url']}\n"
            )
    except Exception as e:
        print(f"FEC lookup error: {e}")
    return ""

_GEMINI_MODEL = "gemini-2.5-flash"

def _gemini_reply(system_prompt, messages, max_tokens):
    """Helper to call Gemini API with system instructions."""
    from google.genai import types as _gtypes
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    contents = [
        _gtypes.Content(
            role="user" if m.role == "user" else "model",
            parts=[_gtypes.Part(text=m.content)],
        )
        for m in messages
    ]
    response = client.models.generate_content(
        model=_GEMINI_MODEL,
        contents=contents,
        config=_gtypes.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_tokens,
        ),
    )
    return response.text

# ── News review queue ──────────────────────────────────────────────────────────

@app.get("/admin/review", response_class=HTMLResponse)
async def admin_review_page(request: Request):
    """Human review queue for flagged news articles."""
    with open(os.path.join(BASE_DIR, "templates", "review.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/api/admin/review-queue")
def api_review_queue(limit: int = 20, status: str = "pending"):
    """Return flagged articles awaiting review."""
    return {"items": ingest_news.get_review_queue(limit=limit, status=status), "stats": ingest_news.queue_stats()}


@app.post("/api/admin/review/{article_id}/approve")
def api_review_approve(article_id: str):
    found = ingest_news.set_review_status(article_id, "approved")
    if not found:
        raise HTTPException(status_code=404, detail="Article not found")
    return {"ok": True, "article_id": article_id, "status": "approved"}


@app.post("/api/admin/review/{article_id}/reject")
def api_review_reject(article_id: str):
    found = ingest_news.set_review_status(article_id, "rejected")
    if not found:
        raise HTTPException(status_code=404, detail="Article not found")
    return {"ok": True, "article_id": article_id, "status": "rejected"}


# ── Election maps — all four modes built once at startup ──────────────────────
import copy
import math
from shapely.geometry import shape

_election_maps: dict[str, str] = {}

_MODE_LABELS = {
    "total":   "All Votes",
    "early":   "Early Voting",
    "eday":    "Election Day",
    "mail":    "Mail-In (Absentee)",
    "density": "Vote Density",
}

# Parsed election data — loaded once on first map request, reused across modes
_election_data_cache: dict = {}

def _load_election_data():
    if _election_data_cache:
        return _election_data_cache
    with open(os.path.join(BASE_DIR, "data_2026_special.json"), encoding="utf-8") as f:
        data = json.load(f)
    county_data, city_data = {}, {}
    for j in data.get("localResults", []):
        raw = j["name"].strip().upper()
        yes_v = no_v = yes_ev = no_ev = yes_ed = no_ed = yes_ml = no_ml = 0
        for item in j.get("ballotItems", []):
            for opt in item.get("ballotOptions", []):
                grps = {g["groupName"]: g["voteCount"] for g in opt.get("groupResults", [])}
                if opt["name"].upper() == "YES":
                    yes_v  += opt["voteCount"]
                    yes_ev += grps.get("Early Voting", 0)
                    yes_ed += grps.get("Election Day", 0)
                    yes_ml += grps.get("Mailed Absentee", 0)
                elif opt["name"].upper() == "NO":
                    no_v   += opt["voteCount"]
                    no_ev  += grps.get("Early Voting", 0)
                    no_ed  += grps.get("Election Day", 0)
                    no_ml  += grps.get("Mailed Absentee", 0)
        info = {
            "yes": yes_v, "no": no_v,
            "early_yes": yes_ev, "early_no": no_ev,
            "eday_yes":  yes_ed, "eday_no":  no_ed,
            "mail_yes":  yes_ml, "mail_no":  no_ml,
            "display_name": j["name"].strip(),
        }
        key = raw[:-7].strip() if raw.endswith(" COUNTY") else \
              raw[:-5].strip() if raw.endswith(" CITY") else \
              raw.replace("&", "AND").replace("  ", " ").strip()
        (city_data if raw.endswith(" CITY") else county_data)[key] = info
    with open(os.path.join(BASE_DIR, "va_counties.json"), encoding="utf-8") as gf:
        base_features = json.load(gf)["features"]
    _election_data_cache["county"] = county_data
    _election_data_cache["city"]   = city_data
    _election_data_cache["features"] = base_features
    return _election_data_cache


def _build_election_map(mode: str) -> str:
    """Build a single election results map for the given mode."""
    ed = _load_election_data()
    county_data  = ed["county"]
    city_data    = ed["city"]
    base_features = ed["features"]

    features = copy.deepcopy(base_features)
    for feat in features:
        fips3   = int(feat["properties"]["GEO_ID"][-3:])
        name_up = feat["properties"]["NAME"].strip().upper()
        r = (city_data if fips3 > 199 else county_data).get(name_up)
        if r:
            y = r[f"{mode}_yes"] if mode not in ("total", "density") else r["yes"]
            n = r[f"{mode}_no"]  if mode not in ("total", "density") else r["no"]
            t = y + n
            feat["properties"].update({
                "display_name": r["display_name"],
                "yes": y, "no": n, "total": t,
                "pct_yes": round(y / t * 100, 1) if t else None,
                "winner": ("Yes" if y >= n else "No") if t else "No data",
            })
        else:
            feat["properties"].update({
                "display_name": feat["properties"]["NAME"],
                "pct_yes": None, "winner": "No data",
                "yes": 0, "no": 0, "total": 0,
            })

    m = folium.Map(location=[37.5, -79.0], zoom_start=7, tiles="CartoDB positron", min_zoom=6)
    map_var = m.get_name()

    if mode == "density":
        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=lambda f: {"fillColor": "#f5f5f5", "color": "#bbb", "weight": 0.7, "fillOpacity": 0.5},
        ).add_to(m)
        max_total = max((f["properties"]["total"] for f in features), default=1) or 1
        for feat in features:
            props = feat["properties"]
            total = props["total"]
            if total == 0:
                continue
            try:
                centroid = shape(feat["geometry"]).centroid
            except Exception:
                continue
            radius = 4 + math.sqrt(total / max_total) * 34
            fill_color = "#1a52c8" if props["winner"] == "Yes" else "#ff4444"
            pct = props["pct_yes"] or 50
            folium.CircleMarker(
                location=[centroid.y, centroid.x], radius=radius,
                color="white", weight=1, fill=True,
                fill_color=fill_color, fill_opacity=0.78,
                tooltip=(
                    f"<b style='font-family:Arial'>{props['display_name']}</b><br>"
                    f"Total votes: <b>{total:,}</b><br>"
                    f"Yes: {props['yes']:,} ({pct}%)<br>"
                    f"No: {props['no']:,} ({100-pct}%)<br>"
                    f"Winner: <b>{props['winner']}</b>"
                ),
            ).add_to(m)
        def _fmt_v(n):
            if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
            if n >= 1_000: return f"{round(n/1_000)}k"
            return str(n)
        size_key_html = ""
        for frac in [0.15, 0.50, 1.0]:
            ref_total = int(max_total * frac)
            r_px = 4 + math.sqrt(frac) * 34
            sz = int(r_px * 2 + 4)
            cx = cy = sz // 2
            lbl = _fmt_v(ref_total) if ref_total else "—"
            size_key_html += (
                f"<div style='display:flex;flex-direction:column;align-items:center;gap:2px'>"
                f"<svg width='{sz}' height='{sz}'>"
                f"<circle cx='{cx}' cy='{cy}' r='{r_px:.1f}' fill='#888' fill-opacity='0.45' stroke='white' stroke-width='1.5'/>"
                f"</svg>"
                f"<span style='font-size:10px;color:#555;white-space:nowrap'>≈ {lbl} votes</span>"
                f"</div>"
            )
        legend_html = (
            f"<b style='font-size:11px;letter-spacing:0.06em;text-transform:uppercase;color:#555'>Vote Density</b><br>"
            f"<span style='background:#1a52c8;color:white;padding:2px 8px;border-radius:3px;font-size:11px'>Yes won</span>"
            f"&nbsp;<span style='background:#ff4444;color:white;padding:2px 8px;border-radius:3px;font-size:11px'>No won</span>"
            f"<div style='margin-top:7px;font-weight:bold;color:#444;font-size:11px;letter-spacing:0.04em'>CIRCLE SIZE = VOTES CAST</div>"
            f"<div style='display:flex;align-items:flex-end;gap:12px;margin-top:4px'>{size_key_html}</div>"
        )
    else:
        def style_fn(feat):
            pct = feat["properties"].get("pct_yes")
            if pct is None:
                return {"fillColor": "#cccccc", "color": "#888", "weight": 0.8, "fillOpacity": 0.5}
            if pct >= 50:
                intensity = int(80 + (pct - 50) / 50 * 175)
                color = f"#{255-intensity:02x}{255-intensity:02x}ff"
            else:
                intensity = int(80 + (50 - pct) / 50 * 175)
                color = f"#ff{255-intensity:02x}{255-intensity:02x}"
            return {"fillColor": color, "color": "#444", "weight": 0.5, "fillOpacity": 0.85}
        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=style_fn,
            tooltip=folium.GeoJsonTooltip(
                fields=["display_name", "yes", "no", "pct_yes", "winner"],
                aliases=["Jurisdiction:", "Yes votes:", "No votes:", "Yes %:", "Winner:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        yes_rows = "".join(
            f"<div style='display:flex;align-items:center;gap:5px;margin:2px 0'>"
            f"<span style='display:inline-block;width:18px;height:13px;background:{c};border:1px solid #aaa'></span>"
            f"<span style='font-size:10px'>Yes {lo}–{hi}%</span></div>"
            for lo, hi, c in [(90,100,"#1212ff"),(80,90,"#3535ff"),(70,80,"#5858ff"),(60,70,"#7b7bff"),(50,60,"#9e9eff")]
        )
        no_rows = "".join(
            f"<div style='display:flex;align-items:center;gap:5px;margin:2px 0'>"
            f"<span style='display:inline-block;width:18px;height:13px;background:{c};border:1px solid #aaa'></span>"
            f"<span style='font-size:10px'>No {lo}–{hi}%</span></div>"
            for lo, hi, c in [(50,60,"#ff9e9e"),(60,70,"#ff7b7b"),(70,80,"#ff5858"),(80,90,"#ff3535"),(90,100,"#ff1212")]
        )
        legend_html = (
            f"<b style='font-size:11px;letter-spacing:0.06em;text-transform:uppercase;color:#555'>{_MODE_LABELS[mode]}</b>"
            f"<div style='font-size:10px;color:#777;margin:3px 0 5px;font-style:italic'>Vote share %</div>"
            f"<div style='display:flex;gap:12px'>"
            f"<div><div style='font-weight:bold;color:#1a52c8;font-size:11px;margin-bottom:3px'>Yes wins</div>{yes_rows}</div>"
            f"<div><div style='font-weight:bold;color:#e03030;font-size:11px;margin-bottom:3px'>No wins</div>{no_rows}</div>"
            f"</div>"
        )

    m.get_root().html.add_child(folium.Element(f"""
    <div style="position:fixed;bottom:40px;left:40px;z-index:1000;
         background:white;padding:12px 16px;border-radius:8px;
         box-shadow:2px 2px 8px rgba(0,0,0,.3);font-family:Arial;font-size:13px;line-height:1.8">
      {legend_html}
    </div>
    """))
    rendered = m.get_root().render()
    bounds_js = (
        f"<script>"
        f"{map_var}.setMaxBounds([[35.9,-84.8],[39.7,-74.9]]);"
        f"{map_var}.options.maxBoundsViscosity=1.0;"
        f"</script>"
    )
    return rendered.replace("</html>", bounds_js + "</html>")




# ── Election results cache (for chat context) ─────────────────────────────────
def _load_election_results():
    with open(os.path.join(BASE_DIR, "data_2026_special.json"), encoding="utf-8") as f:
        data = json.load(f)

    def groups(opts, name):
        o = next((x for x in opts if x["name"].upper() == name), {})
        g = {x["groupName"]: x["voteCount"] for x in o.get("groupResults", [])}
        return o.get("voteCount", 0), g.get("Early Voting", 0), g.get("Election Day", 0), g.get("Mailed Absentee", 0)

    sw = data.get("results", {}).get("ballotItems", [{}])[0].get("ballotOptions", [])
    yes_tot, yes_ev, yes_ed, yes_ml = groups(sw, "YES")
    no_tot,  no_ev,  no_ed,  no_ml  = groups(sw, "NO")
    grand = yes_tot + no_tot

    local = {}
    for j in data.get("localResults", []):
        raw = j["name"].strip().upper()
        y = n = 0
        for item in j.get("ballotItems", []):
            for opt in item.get("ballotOptions", []):
                if opt["name"].upper() == "YES": y += opt["voteCount"]
                elif opt["name"].upper() == "NO": n += opt["voteCount"]
        t = y + n
        key = raw[:-7].strip() if raw.endswith(" COUNTY") else raw[:-5].strip() if raw.endswith(" CITY") else raw
        local[key] = {"yes": y, "no": n, "total": t,
                      "pct_yes": round(y/t*100,1) if t else 50,
                      "winner": "Yes" if y >= n else "No",
                      "display": j["name"].strip()}

    return {
        "yes": yes_tot, "no": no_tot, "total": grand,
        "yes_pct": round(yes_tot/grand*100,1) if grand else 50,
        "no_pct":  round(no_tot /grand*100,1) if grand else 50,
        "winner": "YES (Approve)" if yes_tot >= no_tot else "NO (Reject)",
        "early": {"yes": yes_ev, "no": no_ev},
        "election_day": {"yes": yes_ed, "no": no_ed},
        "mail": {"yes": yes_ml, "no": no_ml},
        "local": local,
    }

try:
    _results = _load_election_results()
    print("Election results cache ready.")
except Exception as e:
    _results = None
    print(f"Warning: could not load election results: {e}")

# ── Vote cache — loaded once at startup, keyed by bioguide_id ─────────────────
_VOTES_CACHE: dict[str, list[dict]] = {}
_MEMBER_CACHE: dict[str, dict] = {}

def _load_votes_cache() -> None:
    """Load congress_votes and congress_members into memory as JSON-serialisable dicts."""
    global _VOTES_CACHE, _MEMBER_CACHE
    if not os.path.exists(_POLLS_DB):
        return
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row

        # Load members
        for row in conn.execute("SELECT * FROM congress_members"):
            _MEMBER_CACHE[row["bioguide_id"]] = dict(row)

        # Load votes grouped by member
        for row in conn.execute(
            "SELECT bioguide_id, chamber, vote_number, vote_date, bill, question, member_vote, result "
            "FROM congress_votes ORDER BY vote_date DESC, vote_number DESC"
        ):
            bgid = row["bioguide_id"]
            if bgid not in _VOTES_CACHE:
                _VOTES_CACHE[bgid] = []
            _VOTES_CACHE[bgid].append(dict(row))

        conn.close()
        total = sum(len(v) for v in _VOTES_CACHE.values())
        print(f"Vote cache ready: {total} votes across {len(_VOTES_CACHE)} members.")
    except Exception as exc:
        print(f"Vote cache skipped: {exc}")

_load_votes_cache()


# ── FEC industry PAC cache — loaded once at startup ───────────────────────────
_PAC_CACHE: dict[str, list[dict]] = {}  # bioguide_id -> [{industry, total_amount, top_donors}]

def _load_pac_cache() -> None:
    global _PAC_CACHE
    if not os.path.exists(_POLLS_DB):
        return
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fec_industry_totals'"
        ).fetchone()
        if not tbl:
            conn.close()
            return
        # Load the most-recent cycle per member×industry to avoid duplicates across reruns
        fresh: dict[str, list[dict]] = {}
        for row in conn.execute(
            """SELECT bioguide_id, member_name, industry,
                      total_amount, contributor_count, top_donors,
                      MAX(cycle) AS cycle
               FROM fec_industry_totals
               GROUP BY bioguide_id, industry
               ORDER BY total_amount DESC"""
        ):
            bgid = row["bioguide_id"]
            if bgid not in fresh:
                fresh[bgid] = []
            fresh[bgid].append({
                "industry":    row["industry"],
                "total":       row["total_amount"],
                "count":       row["contributor_count"],
                "top_donors":  json.loads(row["top_donors"] or "[]"),
                "member_name": row["member_name"],
                "cycle":       row["cycle"],
            })
        conn.close()
        _PAC_CACHE = fresh
        total_rows = sum(len(v) for v in _PAC_CACHE.values())
        print(f"PAC cache ready: {total_rows} industry rows across {len(_PAC_CACHE)} members.")
    except Exception as exc:
        print(f"PAC cache skipped: {exc}")

_load_pac_cache()


# ── 2025 General Election results — loaded once on first request ──────────────
import glob
import csv

_results_2025_cache: dict = {}
_statewide_locality_2025_cache: dict[str, dict] = {}

def _load_2025_results() -> dict:
    """Load 2025 election results from pre-built JSON (preferred) or raw CSV."""
    if _results_2025_cache:
        return _results_2025_cache

    # Fast path: pre-built JSON
    json_path = os.path.join(BASE_DIR, "election_results_2025.json")
    if os.path.exists(json_path):
        with open(json_path, encoding="utf-8") as _jf:
            raw = json.load(_jf)
        # JSON keys are strings; convert hod keys back to int
        _results_2025_cache["statewide"] = raw.get("statewide", [])
        _results_2025_cache["hod"] = {int(k): v for k, v in raw.get("hod", {}).items()}
        return _results_2025_cache

    # Fallback: parse raw CSV
    pattern = os.path.join(BASE_DIR, "Election Results_*.csv")
    matches = glob.glob(pattern)
    if not matches:
        return {"statewide": [], "hod": {}}
    csv_path = matches[0]

    # Aggregate: (DistrictType, DistrictName, OfficeTitle, CandidateName, Party) -> total votes
    agg: dict = {}

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Skip write-in rows
            if row.get("WriteInVote") == "1":
                continue
            if row.get("CandidateName", "").strip().upper() == "WRITE IN VOTES":
                continue

            d_type = row.get("DistrictType", "").strip()
            d_name = row.get("DistrictName", "").strip()
            office = row.get("OfficeTitle", "").strip()
            name   = row.get("CandidateName", "").strip()
            party  = row.get("Party", "").strip()

            raw_votes = row.get("TOTAL_VOTES", "").strip()
            try:
                votes = int(raw_votes)
            except (ValueError, TypeError):
                votes = 0

            key = (d_type, d_name, office, name, party)
            agg[key] = agg.get(key, 0) + votes

    # Build statewide races list
    OFFICE_ORDER = ["Governor", "Lieutenant Governor", "Attorney General"]

    statewide_races: dict = {}  # office -> {total, candidates: {name -> {party, votes}}}
    hod_races: dict = {}        # district_num (int) -> {total, candidates: {name -> {party, votes}}}

    for (d_type, d_name, office, name, party), votes in agg.items():
        if d_type == "state":
            if office not in statewide_races:
                statewide_races[office] = {"candidates": {}}
            cands = statewide_races[office]["candidates"]
            if name not in cands:
                cands[name] = {"party": party, "votes": 0}
            cands[name]["votes"] += votes

        elif d_type == "state-house":
            try:
                dist_num = int(d_name)
            except (ValueError, TypeError):
                continue
            if dist_num not in hod_races:
                hod_races[dist_num] = {"candidates": {}}
            cands = hod_races[dist_num]["candidates"]
            if name not in cands:
                cands[name] = {"party": party, "votes": 0}
            cands[name]["votes"] += votes

    def _build_race(office_or_dist, cands_dict):
        total = sum(c["votes"] for c in cands_dict.values())
        candidates = []
        for cname, cdata in cands_dict.items():
            votes = cdata["votes"]
            pct = round(votes / total * 100, 1) if total else 0.0
            candidates.append({"name": cname, "party": cdata["party"], "votes": votes, "pct": pct})
        candidates.sort(key=lambda c: c["votes"], reverse=True)
        return {"office": office_or_dist, "total": total, "candidates": candidates}

    # Sort statewide races: Governor, LG, AG first, then anything else alphabetically
    def _office_sort_key(office):
        try:
            return OFFICE_ORDER.index(office)
        except ValueError:
            return len(OFFICE_ORDER)

    statewide_list = []
    for office in sorted(statewide_races.keys(), key=_office_sort_key):
        race = _build_race(office, statewide_races[office]["candidates"])
        statewide_list.append(race)

    hod_dict = {}
    for dist_num in sorted(hod_races.keys()):
        race = _build_race(f"House of Delegates — District {dist_num}", hod_races[dist_num]["candidates"])
        # Remove the "office" key from hod entries to keep payload clean; add district number
        hod_dict[dist_num] = {"total": race["total"], "candidates": race["candidates"]}

    _results_2025_cache["statewide"] = statewide_list
    _results_2025_cache["hod"] = hod_dict
    return _results_2025_cache


def _load_2025_statewide_locality_results(office: str) -> dict:
    """Load 2025 statewide-office results by locality from pre-built JSON, or fall back to CSV."""
    if office in _statewide_locality_2025_cache:
        return _statewide_locality_2025_cache[office]

    # Fast path: pre-built combined JSON
    json_path = os.path.join(BASE_DIR, "statewide_locality_results_2025.json")
    if os.path.exists(json_path):
        with open(json_path, encoding="utf-8") as _jf:
            raw = json.load(_jf)
        for _office, _data in raw.items():
            _statewide_locality_2025_cache[_office] = _data
        if office in _statewide_locality_2025_cache:
            return _statewide_locality_2025_cache[office]

    # Fallback: parse raw CSV
    pattern = os.path.join(BASE_DIR, "Election Results_*.csv")
    matches = glob.glob(pattern)
    if not matches:
        raise RuntimeError("2025 election CSV not found")

    by_locality: dict = {}
    with open(matches[0], newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("WriteInVote") == "1":
                continue
            if row.get("CandidateName", "").strip().upper() == "WRITE IN VOTES":
                continue
            if row.get("DistrictType", "").strip() != "state":
                continue
            if row.get("OfficeTitle", "").strip() != office:
                continue

            locality = row.get("LocalityName", "").strip().upper()
            name = row.get("CandidateName", "").strip()
            party = row.get("Party", "").strip()
            try:
                votes = int(row.get("TOTAL_VOTES", "").strip())
            except (ValueError, TypeError):
                votes = 0

            locality_data = by_locality.setdefault(locality, {"candidates": {}})
            candidate = locality_data["candidates"].setdefault(
                name, {"name": name, "party": party, "votes": 0}
            )
            candidate["votes"] += votes

    results = {}
    for locality, data in by_locality.items():
        candidates = sorted(
            data["candidates"].values(),
            key=lambda candidate: candidate["votes"],
            reverse=True,
        )
        total = sum(candidate["votes"] for candidate in candidates)
        for candidate in candidates:
            candidate["pct"] = round(candidate["votes"] / total * 100, 1) if total else 0.0

        winner = candidates[0] if candidates else {}
        dem = next((candidate for candidate in candidates if "democrat" in candidate.get("party", "").lower()), {})
        rep = next((candidate for candidate in candidates if "republican" in candidate.get("party", "").lower()), {})
        results[locality] = {
            "total": total,
            "winner": winner,
            "winner_pct": winner.get("pct", 0.0),
            "dem": dem,
            "rep": rep,
            "candidates": candidates,
        }

    _statewide_locality_2025_cache[office] = results
    return results


def _build_2025_hod_flips(hod_data: dict) -> list[dict]:
    flips = []
    for district, race in sorted(hod_data.items()):
        candidates = race.get("candidates", [])
        if not candidates:
            continue
        winner = candidates[0]
        party_2023 = HOD_2023_PARTY.get(int(district), "Democrat")
        party_2025 = winner.get("party", "")
        if "democrat" in party_2025.lower():
            normalized_2025 = "Democrat"
        elif "republican" in party_2025.lower():
            normalized_2025 = "Republican"
        else:
            normalized_2025 = party_2025 or "Other"
        if party_2023 == normalized_2025:
            continue
        flips.append({
            "district": int(district),
            "winner": winner.get("name", "Unknown"),
            "from_party": party_2023,
            "to_party": normalized_2025,
            "winner_pct": winner.get("pct", 0.0),
            "votes": winner.get("votes", 0),
            "direction": (
                "Flipped Democratic"
                if normalized_2025 == "Democrat"
                else "Flipped Republican"
                if normalized_2025 == "Republican"
                else f"Flipped {normalized_2025}"
            ),
        })
    return flips


def _normalize_major_party(party: str) -> str:
    party_lower = str(party or "").lower()
    if "democrat" in party_lower:
        return "Democrat"
    if "republican" in party_lower:
        return "Republican"
    return party or "Other"


_DEM_BANDS = [(90,101,"#0000a0"),(80,90,"#3333cc"),(70,80,"#5555ee"),(60,70,"#7777ff"),(50,60,"#aaaaff")]
_REP_BANDS = [(90,101,"#990000"),(80,90,"#cc0000"),(70,80,"#ee4444"),(60,70,"#ff8888"),(50,60,"#ffbbbb")]

_bl_lookup_cache: dict = {}

def _get_bl_lookup() -> dict:
    """Return {normalized_geo_key: tpv_d_float} for baseline swing annotation."""
    global _bl_lookup_cache
    if _bl_lookup_cache:
        return _bl_lookup_cache
    gj = _build_locality_baseline_geojson()
    for bf in gj.get("features", []):
        bp = bf.get("properties", {})
        key = f"{bp.get('NAME', '')} {bp.get('LSAD', '')}".strip().upper().replace("&", "AND")
        try:
            _bl_lookup_cache[key] = float(str(bp.get("_bl_tpv_d", "50")).replace("%", ""))
        except Exception:
            _bl_lookup_cache[key] = 50.0
    return _bl_lookup_cache

def _partisan_lean(tpv_d: float) -> str:
    if tpv_d >= 65:   return "Solid D"
    if tpv_d >= 57:   return "Likely D"
    if tpv_d >= 52.5: return "Lean D"
    if tpv_d >= 47.5: return "Toss-up"
    if tpv_d >= 43:   return "Lean R"
    if tpv_d >= 35:   return "Likely R"
    return "Solid R"

def _annotate_baseline(props: dict, geo_key: str, dem_pct: float, rep_pct: float) -> None:
    """Inject _result, _baseline, _swing, _lean into a feature's properties dict."""
    bl_tpv_d = _get_bl_lookup().get(geo_key, 50.0)
    tp = dem_pct + rep_pct
    el_tpv_d = dem_pct / tp * 100 if tp > 0 else 50.0
    swing = el_tpv_d - bl_tpv_d
    props["_result"]   = f"D {el_tpv_d:.1f}% / R {100 - el_tpv_d:.1f}%"
    props["_baseline"] = f"D {bl_tpv_d:.1f}% avg (12 races)"
    props["_swing"]    = (
        "≈ on baseline" if abs(swing) < 0.5
        else f"▲ +{swing:.1f} pts D vs baseline" if swing > 0
        else f"▼ {abs(swing):.1f} pts R vs baseline"
    )
    props["_lean"] = _partisan_lean(el_tpv_d)

def _pct_to_band_color(pct: float, party: str) -> str:
    bands = _DEM_BANDS if "democrat" in str(party).lower() else _REP_BANDS if "republican" in str(party).lower() else None
    if not bands:
        return "#888888"
    for lo, hi, color in bands:
        if lo <= pct < hi:
            return color
    return bands[-1][2]  # lightest shade for anything below 50% (third-party effect)

def _color_from_tpv(d_tpv: float) -> str:
    """Color a locality by D two-party vote share — fixes dark-color bug on close races with 3rd parties."""
    if d_tpv >= 50:
        return _pct_to_band_color(d_tpv, "Democrat")
    else:
        return _pct_to_band_color(100 - d_tpv, "Republican")

def _vote_share_legend_inner() -> str:
    dem_rows = "".join(
        f"<div style='display:flex;align-items:center;gap:6px;margin:2px 0'>"
        f"<span style='display:inline-block;width:20px;height:14px;background:{c};border:1px solid #aaa'></span>"
        f"<span style='font-size:11px'>{lo}–{'100' if hi==101 else hi}%</span></div>"
        for lo, hi, c in _DEM_BANDS
    )
    rep_rows = "".join(
        f"<div style='display:flex;align-items:center;gap:6px;margin:2px 0'>"
        f"<span style='display:inline-block;width:20px;height:14px;background:{c};border:1px solid #aaa'></span>"
        f"<span style='font-size:11px'>{lo}–{'100' if hi==101 else hi}%</span></div>"
        for lo, hi, c in _REP_BANDS
    )
    return (
        f"<div style='font-size:10px;color:#666;margin-top:4px;margin-bottom:6px;font-style:italic'>Winner's vote share %</div>"
        f"<div style='display:flex;gap:18px'>"
        f"<div><div style='font-weight:bold;color:#3333cc;margin-bottom:3px'>Democrat</div>{dem_rows}</div>"
        f"<div><div style='font-weight:bold;color:#cc0000;margin-bottom:3px'>Republican</div>{rep_rows}</div>"
        f"</div>"
    )


_STATEWIDE_BASELINE_RACES = (
    ("2016", "President"),
    ("2017", "Governor"),
    ("2017", "Lieutenant Governor"),
    ("2017", "Attorney General"),
    ("2018", "U.S. Senate"),
    ("2020", "President"),
    ("2020", "U.S. Senate"),
    ("2021", "Governor"),
    ("2021", "Lieutenant Governor"),
    ("2021", "Attorney General"),
    ("2024", "President"),
    ("2024", "U.S. Senate"),
)

_statewide_baseline_d_share_cache = None


def _statewide_two_party_d_share(data: dict, office: str) -> float | None:
    for race in data.get("statewide", []):
        if (race.get("race") or race.get("office")) != office:
            continue
        dem = rep = 0.0
        for candidate in race.get("candidates", []):
            party = (candidate.get("party") or "").lower()
            value = candidate.get("votes")
            if value is None:
                value = candidate.get("pct")
            try:
                value = float(value or 0)
            except (TypeError, ValueError):
                value = 0.0
            if "dem" in party:
                dem += value
            elif "rep" in party:
                rep += value
        if dem + rep > 0:
            return dem / (dem + rep) * 100
    return None


def _statewide_baseline_d_share() -> float | None:
    global _statewide_baseline_d_share_cache
    if _statewide_baseline_d_share_cache is not None:
        return _statewide_baseline_d_share_cache
    shares = []
    for year, office in _STATEWIDE_BASELINE_RACES:
        share = _statewide_two_party_d_share(_load_results_for_year(year), office)
        if share is not None:
            shares.append(share)
    _statewide_baseline_d_share_cache = sum(shares) / len(shares) if shares else None
    return _statewide_baseline_d_share_cache


def _baseline_summary_for_statewide_race(year: str, office: str) -> str:
    current = _statewide_two_party_d_share(_load_results_for_year(year), office)
    baseline = _statewide_baseline_d_share()
    if current is None or baseline is None:
        return ""
    delta = current - baseline
    if abs(delta) < 0.05:
        return "Even with baseline"
    direction = "Democratic" if delta > 0 else "Republican"
    return f"{abs(delta):.1f} pts {direction} lean"


def _build_2023_state_leg_flips(chamber: str, results: dict) -> list[dict]:
    baseline = SD_PRE2023_PARTY if chamber == "senate" else HOD_PRE2023_PARTY
    flips = []
    for district, race in sorted(results.items()):
        candidates = race.get("candidates", [])
        if not candidates:
            continue
        winner = candidates[0]
        from_party = baseline.get(int(district), "Unknown")
        to_party = _normalize_major_party(winner.get("party", ""))
        if from_party == to_party:
            continue
        flips.append({
            "district": int(district),
            "winner": winner.get("name", "Unknown"),
            "from_party": from_party,
            "to_party": to_party,
            "winner_pct": winner.get("pct", 0.0),
            "votes": winner.get("votes", 0),
            "direction": (
                "Flipped Democratic"
                if to_party == "Democrat"
                else "Flipped Republican"
                if to_party == "Republican"
                else f"Flipped {to_party}"
            ),
        })
    return flips


# All district GDFs are lazy — borrowed from address_lookup on first use
_va_cd_gdf      = None
_va_hod_gdf     = None
_va_old_hod_gdf = None   # pre-2023 (2010-cycle) HOD boundaries — Census TIGER 2021
_va_sd_gdf      = None
_vb_council_map_gdf   = None
_nn_council_map_gdf   = None
_norfolk_ward_map_gdf      = None
_norfolk_superward_map_gdf = None

def _get_va_cd():
    global _va_cd_gdf
    if _va_cd_gdf is None:
        import address_lookup as _al
        if _al.va_cd is not None:
            _va_cd_gdf = _al.va_cd
        else:
            _va_cd_gdf = gpd.read_file(os.path.join(BASE_DIR, "tl_2023_51_cd118.shp"))
            _va_cd_gdf = _va_cd_gdf.to_crs(epsg=4326)
            _va_cd_gdf = _va_cd_gdf[['NAMELSAD', 'CD118FP', 'geometry']]
            _al.va_cd = _va_cd_gdf
    return _va_cd_gdf

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

_CLAUDE_TEMPORARY_MESSAGE = (
    "VoteIQ is getting a lot of AI traffic right now. Please try your question again in a moment."
)


def _is_retryable_claude_error(error):
    status_code = getattr(error, "status_code", None)
    text = str(error).lower()
    return (
        status_code in {429, 500, 502, 503, 504, 529}
        or "overloaded" in text
        or "rate_limit" in text
        or "temporarily unavailable" in text
    )


def _friendly_claude_error(error):
    if _is_retryable_claude_error(error):
        return _CLAUDE_TEMPORARY_MESSAGE
    return "VoteIQ's AI assistant is temporarily unavailable. Please try again shortly."


_CLAUDE_SONNET_MODEL = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-6")
_CLAUDE_HAIKU_MODEL = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")


def _claude_reply(system_prompt, messages, max_tokens, model: str | None = None):
    model_name = model or _CLAUDE_SONNET_MODEL
    last_error = None
    for attempt in range(3):
        try:
            response = client.messages.create(
                model=model_name,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": m.role, "content": m.content} for m in messages],
            )
            return response.content[0].text
        except Exception as error:
            last_error = error
            if not _is_retryable_claude_error(error) or attempt == 2:
                raise
            time.sleep(0.75 * (attempt + 1))
    raise last_error


def _simple_bill_lookup_question(user_query: str, mentioned: list[str], cached_bill_context: str) -> bool:
    """Route simple exact bill questions to Haiku when local cached context is enough."""
    if not mentioned or not cached_bill_context:
        return False
    q = str(user_query or "").lower()
    complex_terms = (
        "my rep", "my representative", "my delegate", "my senator",
        "vote", "voted", "voting record", "break", "party", "caucus",
        "sponsor network", "cosponsor", "co-sponsor", "compare",
        "campaign", "donor", "funding", "contribution",
    )
    if any(term in q for term in complex_terms):
        return False
    simple_terms = (
        "what is", "what's", "summarize", "summary", "explain",
        "describe", "tell me about", "status", "latest action",
    )
    return any(term in q for term in simple_terms) or len(q.split()) <= 8


_CACHE_TTL_SECONDS = 86400  # 24 hours for ad-hoc chat replies


def _init_query_cache():
    db = os.path.join(BASE_DIR, "openstates_va.db")
    if not os.path.exists(db):
        return
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS query_cache "
        "(cache_key TEXT PRIMARY KEY, reply TEXT, created_at INTEGER)"
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(query_cache)").fetchall()}
    if "cache_type" not in cols:
        conn.execute("ALTER TABLE query_cache ADD COLUMN cache_type TEXT DEFAULT 'ad_hoc'")
    conn.commit()
    conn.close()


def _cache_key(query: str, district_note: str) -> str:
    normalized = re.sub(r"\s+", " ", query.strip().lower())
    raw = f"{normalized}||{district_note}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _get_cached_reply(key: str, fallback_key: str | None = None) -> str | None:
    db = os.path.join(BASE_DIR, "openstates_va.db")
    if not os.path.exists(db):
        return None
    try:
        conn = sqlite3.connect(db)
        for lookup_key in ([key] + ([fallback_key] if fallback_key and fallback_key != key else [])):
            row = conn.execute(
                "SELECT reply, created_at, COALESCE(cache_type, 'ad_hoc') FROM query_cache WHERE cache_key = ?",
                (lookup_key,),
            ).fetchone()
            if row and (row[2] == "prewarm" or (time.time() - row[1]) < _CACHE_TTL_SECONDS):
                conn.close()
                return row[0]
        conn.close()
    except Exception:
        pass
    return None


def _set_cached_reply(key: str, reply: str, cache_type: str = "ad_hoc"):
    db = os.path.join(BASE_DIR, "openstates_va.db")
    if not os.path.exists(db):
        return
    try:
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT OR REPLACE INTO query_cache (cache_key, reply, created_at, cache_type) VALUES (?, ?, ?, ?)",
            (key, reply, int(time.time()), cache_type),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _init_rerank_cache():
    db = os.path.join(BASE_DIR, "openstates_va.db")
    if not os.path.exists(db):
        return
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cohere_rerank_cache "
        "(cache_key TEXT PRIMARY KEY, top_indexes TEXT, created_at INTEGER)"
    )
    conn.commit()
    conn.close()


def _rerank_cache_key(question: str, candidates: list[dict]) -> str:
    normalized = re.sub(r"\s+", " ", question.strip().lower())
    ids = [str(c.get("url") or c.get("title") or "") for c in candidates]
    raw = normalized + "||" + json.dumps(ids, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _get_cached_rerank(key: str) -> list[int] | None:
    db = os.path.join(BASE_DIR, "openstates_va.db")
    if not os.path.exists(db):
        return None
    try:
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT top_indexes, created_at FROM cohere_rerank_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
        conn.close()
        if row and (time.time() - row[1]) < _CACHE_TTL_SECONDS:
            return json.loads(row[0])
    except Exception:
        pass
    return None


def _set_cached_rerank(key: str, top_indexes: list[int]):
    db = os.path.join(BASE_DIR, "openstates_va.db")
    if not os.path.exists(db):
        return
    try:
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT OR REPLACE INTO cohere_rerank_cache (cache_key, top_indexes, created_at) VALUES (?, ?, ?)",
            (key, json.dumps(top_indexes), int(time.time())),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _init_feedback_table():
    db = os.path.join(BASE_DIR, "openstates_va.db")
    if not os.path.exists(db):
        return
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            rating TEXT NOT NULL,
            query TEXT,
            reply_hash TEXT,
            district TEXT
        )"""
    )
    conn.commit()
    conn.close()


_init_query_cache()
_init_rerank_cache()
_init_feedback_table()


DISTRICT_CONTEXT = {
    "VA-00": {"rep": None, "party": None, "region": "Statewide — Virginia", "url": None},
    "VA-01": {"rep": "Rob Wittman",       "party": "Republican", "region": "Western Chesapeake Bay / suburban Richmond",                          "url": "https://wittman.house.gov/contact"},
    "VA-02": {"rep": "Jen Kiggans",       "party": "Republican", "region": "Hampton Roads (Virginia Beach, Chesapeake, Suffolk)",                  "url": "https://kiggans.house.gov/contact"},
    "VA-03": {"rep": "Bobby Scott",       "party": "Democrat",   "region": "Inner Hampton Roads (Newport News, Hampton, Norfolk)",                 "url": "https://bobbyscott.house.gov/contact"},
    "VA-04": {"rep": "Jennifer McClellan","party": "Democrat",   "region": "Richmond city and Southside Virginia",                                "url": "https://mcclellan.house.gov/contact"},
    "VA-05": {"rep": "John McGuire",      "party": "Republican", "region": "Central and Southside Virginia",                                       "url": "https://mcguire.house.gov/contact"},
    "VA-06": {"rep": "Ben Cline",         "party": "Republican", "region": "Western Virginia / Shenandoah Valley",                                 "url": "https://cline.house.gov/contact"},
    "VA-07": {"rep": "Eugene Vindman",    "party": "Democrat",   "region": "Northern Virginia suburbs / central Virginia",                         "url": "https://vindman.house.gov/contact"},
    "VA-08": {"rep": "Don Beyer",         "party": "Democrat",   "region": "Northern Virginia inner suburbs (Arlington, Alexandria)",               "url": "https://beyer.house.gov/contact"},
    "VA-09": {"rep": "Morgan Griffith",   "party": "Republican", "region": "Southwest Virginia",                                                   "url": "https://morgangriffith.house.gov/contact"},
    "VA-10": {"rep": "Suhas Subramanyam", "party": "Democrat",   "region": "Northern Virginia outer suburbs (Loudoun, Prince William)",             "url": "https://subramanyam.house.gov/contact"},
    "VA-11": {"rep": "James Walkinshaw",  "party": "Democrat",   "region": "Northern Virginia outer suburbs (Fairfax County)",                     "url": "https://walkinshaw.house.gov/contact"},
}

# Virginia House of Delegates — 100 districts (2021 redistricting, 2026 session members)
HOD_CONTEXT = {
    1:   {"delegate": "Patrick A. Hope",                    "party": "Democrat",    "locality": "Arlington"},
    2:   {"delegate": "Adele Y. McClure",                   "party": "Democrat",    "locality": "Arlington"},
    3:   {"delegate": "Alfonso H. Lopez",                   "party": "Democrat",    "locality": "Arlington/Alexandria"},
    4:   {"delegate": "Charniele L. Herring",               "party": "Democrat",    "locality": "Fairfax/Alexandria"},
    5:   {"delegate": "Elizabeth B. Bennett-Parker",        "party": "Democrat",    "locality": "Alexandria"},
    6:   {"delegate": "Richard C. Sullivan, Jr.",           "party": "Democrat",    "locality": "Fairfax"},
    7:   {"delegate": "Karen A. Keys-Gamarra",              "party": "Democrat",    "locality": "Fairfax"},
    8:   {"delegate": "Irene Shin",                         "party": "Democrat",    "locality": "Fairfax/Herndon"},
    9:   {"delegate": "Karrie K. Delaney",                  "party": "Democrat",    "locality": "Fairfax"},
    10:  {"delegate": "Dan I. Helmer",                      "party": "Democrat",    "locality": "Fairfax"},
    11:  {"delegate": "David L. Bulova",                    "party": "Democrat",    "locality": "Fairfax/Fairfax City"},
    12:  {"delegate": "Holly M. Seibold",                   "party": "Democrat",    "locality": "Fairfax"},
    13:  {"delegate": "Marcus B. Simon",                    "party": "Democrat",    "locality": "Fairfax/Falls Church"},
    14:  {"delegate": "Vivian E. Watts",                    "party": "Democrat",    "locality": "Fairfax"},
    15:  {"delegate": "Laura Jane H. Cohen",                "party": "Democrat",    "locality": "Fairfax"},
    16:  {"delegate": "Paul E. Krizek",                     "party": "Democrat",    "locality": "Fairfax"},
    17:  {"delegate": "Mark D. Sickles",                    "party": "Democrat",    "locality": "Fairfax"},
    18:  {"delegate": "Kathy K. L. Tran",                   "party": "Democrat",    "locality": "Fairfax"},
    19:  {"delegate": "Rozia A. Henson, Jr.",               "party": "Democrat",    "locality": "Fairfax/Prince William"},
    20:  {"delegate": "Michelle-Ann E. Lopes Maldonado",    "party": "Democrat",    "locality": "Prince William"},
    21:  {"delegate": "Joshua E. Thomas",                   "party": "Democrat",    "locality": "Prince William"},
    22:  {"delegate": "Elizabeth R. Guzman",                "party": "Democrat",    "locality": "Prince William"},
    23:  {"delegate": "Candi Patrice Mundon King",          "party": "Democrat",    "locality": "Prince William/Stafford"},
    24:  {"delegate": "Luke E. Torian",                     "party": "Democrat",    "locality": "Prince William"},
    25:  {"delegate": "Briana D. Sewell",                   "party": "Democrat",    "locality": "Prince William"},
    26:  {"delegate": "JJ Singh",                           "party": "Democrat",    "locality": "Loudoun"},
    27:  {"delegate": "Atoosa R. Reaser",                   "party": "Democrat",    "locality": "Loudoun"},
    28:  {"delegate": "David A. Reid",                      "party": "Democrat",    "locality": "Loudoun"},
    29:  {"delegate": "Fernando J. Martinez",               "party": "Democrat",    "locality": "Loudoun"},
    30:  {"delegate": "John Chilton McAuliff",              "party": "Democrat",    "locality": "Fauquier/Loudoun"},
    31:  {"delegate": "Delores R. Oates",                   "party": "Republican",  "locality": "Clarke/Frederick/Warren"},
    32:  {"delegate": "William D. Wiley",                   "party": "Republican",  "locality": "Frederick/Winchester"},
    33:  {"delegate": "Justin L. Pence",                    "party": "Republican",  "locality": "Page/Rockingham/Shenandoah/Warren"},
    34:  {"delegate": "Tony O. Wilt",                       "party": "Republican",  "locality": "Rockingham/Harrisonburg"},
    35:  {"delegate": "Chris S. Runion",                    "party": "Republican",  "locality": "Augusta/Bath/Highland/Rockingham"},
    36:  {"delegate": "Ellen H. Campbell",                  "party": "Republican",  "locality": "Augusta/Rockbridge/Staunton/Waynesboro"},
    37:  {"delegate": "Terry L. Austin",                    "party": "Republican",  "locality": "Alleghany/Botetourt/Craig/Rockbridge"},
    38:  {"delegate": "S. Sam Rasoul",                      "party": "Democrat",    "locality": "Roanoke"},
    39:  {"delegate": "Will P. Davis",                      "party": "Republican",  "locality": "Franklin/Roanoke"},
    40:  {"delegate": "Joseph P. McNamara",                 "party": "Republican",  "locality": "Roanoke/Salem"},
    41:  {"delegate": "Lily V. Franklin",                   "party": "Democrat",    "locality": "Montgomery/Roanoke"},
    42:  {"delegate": "Jason S. Ballard",                   "party": "Republican",  "locality": "Giles/Montgomery/Pulaski/Radford"},
    43:  {"delegate": "James W. Morefield",                 "party": "Republican",  "locality": "Bland/Buchanan/Dickenson/Russell/Tazewell"},
    44:  {"delegate": "Israel D. O'Quinn",                  "party": "Republican",  "locality": "Russell/Washington/Bristol"},
    45:  {"delegate": "Terry G. Kilgore",                   "party": "Republican",  "locality": "Lee/Scott/Wise/Norton"},
    46:  {"delegate": "Mitchell D. Cornett",                "party": "Republican",  "locality": "Grayson/Pulaski/Smyth/Wythe"},
    47:  {"delegate": "Wren M. Williams",                   "party": "Republican",  "locality": "Carroll/Floyd/Henry/Patrick/Galax"},
    48:  {"delegate": "Eric J. Phillips",                   "party": "Republican",  "locality": "Henry/Pittsylvania/Martinsville"},
    49:  {"delegate": "Madison John R. Whittle",            "party": "Republican",  "locality": "Halifax/Pittsylvania/Danville"},
    50:  {"delegate": "Thomas C. Wright, Jr.",              "party": "Republican",  "locality": "Charlotte/Halifax/Lunenburg/Mecklenburg/Prince Edward"},
    51:  {"delegate": "Eric R. Zehr",                       "party": "Republican",  "locality": "Bedford/Campbell/Pittsylvania"},
    52:  {"delegate": "Wendell S. Walker",                  "party": "Republican",  "locality": "Campbell/Lynchburg"},
    53:  {"delegate": "Timothy P. Griffin",                 "party": "Republican",  "locality": "Amherst/Bedford/Nelson"},
    54:  {"delegate": "Katrina E. Callsen",                 "party": "Democrat",    "locality": "Albemarle/Charlottesville"},
    55:  {"delegate": "Amy J. Laufer",                      "party": "Democrat",    "locality": "Albemarle/Fluvanna/Louisa/Nelson"},
    56:  {"delegate": "Thomas A. Garrett, Jr.",             "party": "Republican",  "locality": "Appomattox/Buckingham/Cumberland/Fluvanna/Goochland/Louisa/Prince Edward"},
    57:  {"delegate": "May Nivar",                          "party": "Democrat",    "locality": "Goochland/Henrico"},
    58:  {"delegate": "Rodney T. Willett",                  "party": "Democrat",    "locality": "Henrico"},
    59:  {"delegate": "H. F. Fowler Jr.",                   "party": "Republican",  "locality": "Hanover/Henrico/Louisa"},
    60:  {"delegate": "Scott A. Wyatt",                     "party": "Republican",  "locality": "Hanover/New Kent"},
    61:  {"delegate": "Michael J. Webert",                  "party": "Republican",  "locality": "Culpeper/Fauquier/Rappahannock"},
    62:  {"delegate": "Karen F. Hamilton",                  "party": "Republican",  "locality": "Culpeper/Greene/Madison/Orange"},
    63:  {"delegate": "Phillip A. Scott",                   "party": "Republican",  "locality": "Orange/Spotsylvania"},
    64:  {"delegate": "Stacey A. Carroll",                  "party": "Democrat",    "locality": "Stafford"},
    65:  {"delegate": "Joshua G. Cole",                     "party": "Democrat",    "locality": "Spotsylvania/Stafford/Fredericksburg"},
    66:  {"delegate": "Nicole Tarlton Cole",                "party": "Democrat",    "locality": "Caroline/Spotsylvania"},
    67:  {"delegate": "Hillary Pugh Kent",                  "party": "Republican",  "locality": "Caroline/King George/Lancaster/Northumberland/Richmond/Westmoreland"},
    68:  {"delegate": "M. Keith Hodges",                    "party": "Republican",  "locality": "Essex/Gloucester/King and Queen/King William/Mathews/Middlesex"},
    69:  {"delegate": "Mark C. Downey",                     "party": "Democrat",    "locality": "Gloucester/James City/York/Newport News"},
    70:  {"delegate": "Shelly A. Simonds",                  "party": "Democrat",    "locality": "Newport News"},
    71:  {"delegate": "Jessica L. Anderson",                "party": "Democrat",    "locality": "James City/New Kent/Williamsburg"},
    72:  {"delegate": "R. Lee Ware Jr.",                    "party": "Republican",  "locality": "Amelia/Chesterfield/Nottoway/Powhatan"},
    73:  {"delegate": "Leslie C. Mehta",                    "party": "Democrat",    "locality": "Chesterfield"},
    74:  {"delegate": "Mike A. Cherry",                     "party": "Republican",  "locality": "Chesterfield/Colonial Heights"},
    75:  {"delegate": "Lindsey M. Dougherty",               "party": "Democrat",    "locality": "Chesterfield/Prince George/Hopewell"},
    76:  {"delegate": "Debra D. Gardner",                   "party": "Democrat",    "locality": "Chesterfield"},
    77:  {"delegate": "Michael J. Jones",                   "party": "Democrat",    "locality": "Chesterfield/Richmond"},
    78:  {"delegate": "Betsy B. Carr",                      "party": "Democrat",    "locality": "Richmond"},
    79:  {"delegate": "Rae C. Cousins",                     "party": "Democrat",    "locality": "Richmond"},
    80:  {"delegate": "Destiny L. LeVere Bolling",          "party": "Democrat",    "locality": "Henrico"},
    81:  {"delegate": "Delores L. McQuinn",                 "party": "Democrat",    "locality": "Charles City/Chesterfield/Henrico"},
    82:  {"delegate": "Kimberly Pope Adams",                "party": "Democrat",    "locality": "Dinwiddie/Prince George/Surry/Petersburg"},
    83:  {"delegate": "H. Otto Wachsmann, Jr.",             "party": "Republican",  "locality": "Brunswick/Dinwiddie/Greensville/Isle of Wight/Southampton/Sussex/Emporia"},
    84:  {"delegate": "Nadarius E. Clark",                  "party": "Democrat",    "locality": "Chesapeake/Isle of Wight/Franklin/Suffolk"},
    85:  {"delegate": "Marcia S. Price",                    "party": "Democrat",    "locality": "Newport News"},
    86:  {"delegate": "Virgil G. Thornton, Sr.",            "party": "Democrat",    "locality": "York/Hampton/Poquoson"},
    87:  {"delegate": "Jeion A. Ward",                      "party": "Democrat",    "locality": "Hampton"},
    88:  {"delegate": "Don L. Scott Jr.",                   "party": "Democrat",    "locality": "Portsmouth"},
    89:  {"delegate": "Karen Robins Carnegie",              "party": "Democrat",    "locality": "Chesapeake/Suffolk"},
    90:  {"delegate": "James A. Leftwich, Jr.",             "party": "Republican",  "locality": "Chesapeake"},
    91:  {"delegate": "C. E. Hayes Jr.",                    "party": "Democrat",    "locality": "Chesapeake/Portsmouth"},
    92:  {"delegate": "Bonita Grace Anthony",               "party": "Democrat",    "locality": "Chesapeake/Norfolk"},
    93:  {"delegate": "Jackie Hope Glass",                  "party": "Democrat",    "locality": "Norfolk"},
    94:  {"delegate": "Phil M. Hernandez",                  "party": "Democrat",    "locality": "Norfolk"},
    95:  {"delegate": "Alex Q. Askew",                      "party": "Democrat",    "locality": "Norfolk/Virginia Beach"},
    96:  {"delegate": "Kelly K. Convirs-Fowler",            "party": "Democrat",    "locality": "Virginia Beach"},
    97:  {"delegate": "Michael B. Feggans",                 "party": "Democrat",    "locality": "Virginia Beach"},
    98:  {"delegate": "Barry D. Knight",                    "party": "Republican",  "locality": "Virginia Beach"},
    99:  {"delegate": "Anne Ferrell Tata",                  "party": "Republican",  "locality": "Virginia Beach"},
    100: {"delegate": "Robert S. Bloxom Jr.",               "party": "Republican",  "locality": "Accomack/Northampton/Virginia Beach"},
}

# 2023-2025 session composition — 51 R, 49 D (used for flip map comparison)
HOD_2023_PARTY = {d: "Republican" for d in [
    21, 22, 30, 31, 32, 33, 34, 35, 36, 37, 39, 40, 41,
    42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53,
    56, 57, 59, 60, 61, 62, 63, 64, 66, 67, 68, 69,
    71, 72, 73, 74, 75, 82, 83, 86, 89, 90, 97, 98, 99, 100,
]}

# Prior-cycle numbered district baselines. These were elected under pre-2021
# redistricting boundaries, so 2023 flip views label them as numbered-district
# comparisons rather than same-boundary seat flips.
# HOD_2021_PARTY: old pre-redistricting district numbers (2011 maps, used only for pre-2023 HOD flip reference)
HOD_2021_PARTY = {d: "Republican" for d in [
    1, 3, 4, 5, 6, 7, 8, 9, 12, 14, 15, 16, 17, 18, 19, 20,
    22, 23, 24, 25, 26, 27, 28, 29, 30, 33, 54, 55, 56, 58,
    59, 60, 61, 62, 63, 64, 65, 66, 75, 78, 81, 82, 83, 84,
    85, 88, 91, 96, 97, 98, 99, 100,
]}
HOD_2021_PARTY.update({
    d: "Democrat" for d in range(1, 101) if d not in HOD_2021_PARTY
})

SD_2019_PARTY = {d: "Republican" for d in [
    3, 4, 7, 8, 11, 12, 14, 15, 17, 19, 20, 22, 23, 24, 26,
    27, 28, 38, 40,
]}
SD_2019_PARTY.update({
    d: "Democrat" for d in range(1, 41) if d not in SD_2019_PARTY
})

# HOD_PRE2021_PARTY: pre-election party for each NEW 2021-redistricted HOD district (used for 2021 flip map)
# Derived by matching 2019 HOD winners (by name) to 2021 candidates; unmatched open seats default to 2021 winner.
# Pre-election composition: 46 R, 54 D → result 50 R, 50 D (net +4 R)
HOD_PRE2021_PARTY = {d: "Republican" for d in [
    1, 3, 4, 5, 6, 7, 8, 9, 14, 15, 16, 17, 18, 19, 20,
    22, 23, 24, 25, 26, 27, 29, 30, 33, 51, 54, 55, 56, 58,
    59, 60, 61, 62, 64, 65, 66, 78, 81, 82, 84, 88, 96, 97, 98, 99, 100,
]}
HOD_PRE2021_PARTY.update({
    d: "Democrat" for d in range(1, 101) if d not in HOD_PRE2021_PARTY
})

# HOD_PRE2023_PARTY: pre-election party for each NEW 2021-redistricted HOD district (used for 2023 flip map)
# Derived from 2021 incumbent name matching + winner defaults for open seats.
# Pre-election composition: 50 R, 50 D → result 49 R, 51 D (net +1 D)
HOD_PRE2023_PARTY = {d: "Republican" for d in [
    22, 30, 31, 32, 33, 34, 35, 36, 37, 39, 40, 41, 42, 43, 44, 45, 46, 47,
    48, 49, 50, 51, 52, 53, 56, 57, 59, 60, 61, 62, 63, 64, 66, 67, 68, 69,
    71, 72, 73, 74, 75, 82, 83, 86, 89, 90, 97, 98, 99, 100,
]}
HOD_PRE2023_PARTY.update({
    d: "Democrat" for d in range(1, 101) if d not in HOD_PRE2023_PARTY
})

# SD_PRE2023_PARTY: pre-election party for each NEW 2021-redistricted Senate district (used for 2023 flip map)
# Pre-election composition: 19 R, 21 D → result 18 R, 22 D (net +1 D, SD16 flipped D)
# SD16: Siobhan Dunnavant (R incumbent) ran and lost to VanValkenburg (D) — confirmed flip
SD_PRE2023_PARTY = {d: "Republican" for d in [
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 16, 17, 19, 20, 25, 26, 27, 28,
]}
SD_PRE2023_PARTY.update({
    d: "Democrat" for d in range(1, 41) if d not in SD_PRE2023_PARTY
})

# Virginia State Senate — 40 districts (2021 redistricting, 2026 session members)
_HOD_BASE = "https://house.vga.virginia.gov/members/{}"
_HOD_MEMBER_URL = {
    1:"H0219",2:"H0375",3:"H0239",4:"H0208",5:"H0406",6:"H0269",7:"H0370",8:"H0344",9:"H0294",10:"H0317",
    11:"H0403",12:"H0351",13:"H0264",14:"H0108",15:"H0355",16:"H0281",17:"H0405",18:"H0305",19:"H0365",20:"H0340",
    21:"H0382",22:"H0297",23:"H0404",24:"H0227",25:"H0343",26:"H0385",27:"H0380",28:"H0301",29:"H0374",30:"H0395",
    31:"H0377",32:"H0329",33:"H0398",34:"H0231",35:"H0321",36:"H0350",37:"H0253",38:"H0266",39:"H0357",40:"H0308",
    41:"H0393",42:"H0333",43:"H0224",44:"H0242",45:"H0056",46:"H0390",47:"H0348",48:"H0384",49:"H0401",50:"H0136",
    51:"H0383",52:"H0325",53:"H0364",54:"H0354",55:"H0371",56:"H0362",57:"H0397",58:"H0327",59:"H0259",60:"H0328",
    61:"H0247",62:"H0394",63:"H0342",64:"H0388",65:"H0314",66:"H0389",67:"H0369",68:"H0238",69:"H0392",70:"H0323",
    71:"H0386",72:"H0124",73:"H0396",74:"H0335",75:"H0391",76:"H0361",77:"H0402",78:"H0212",79:"H0356",80:"H0372",
    81:"H0207",82:"H0399",83:"H0347",84:"H0336",85:"H0284",86:"H0400",87:"H0173",88:"H0322",89:"H0387",90:"H0262",
    91:"H0285",92:"H0353",93:"H0349",94:"H0366",95:"H0311",96:"H0295",97:"H0360",98:"H0407",99:"H0345",100:"H0267",
}
_HOD_MEMBER_URL = {d: _HOD_BASE.format(mid) for d, mid in _HOD_MEMBER_URL.items()}

_SD_URL = "https://apps.senate.virginia.gov/Senator/memberpage.php?id={}"
SD_CONTEXT = {
    1:  {"senator": "Timmy French",             "party": "Republican", "region": "Clarke, Frederick, Shenandoah, Warren; Winchester",                                                                           "url": _SD_URL.format("S121")},
    2:  {"senator": "Mark Obenshain",           "party": "Republican", "region": "Augusta, Bath, Highland, Page, Rockingham; Harrisonburg",                                                                    "url": _SD_URL.format("S68")},
    3:  {"senator": "Chris Head",               "party": "Republican", "region": "Alleghany, Augusta, Bedford, Botetourt, Craig, Roanoke, Rockbridge",                                                         "url": _SD_URL.format("S122")},
    4:  {"senator": "Dave Suetterlein",         "party": "Republican", "region": "Montgomery, Roanoke; Roanoke, Salem",                                                                                        "url": _SD_URL.format("S101")},
    5:  {"senator": "Travis Hackworth",         "party": "Republican", "region": "Bland, Giles, Montgomery, Pulaski, Smyth, Tazewell, Wythe; Radford",                                                        "url": _SD_URL.format("S112")},
    6:  {"senator": "Todd Pillion",             "party": "Republican", "region": "Buchanan, Dickenson, Lee, Russell, Scott, Washington, Wise; Bristol, Norton",                                               "url": _SD_URL.format("S111")},
    7:  {"senator": "Bill Stanley",             "party": "Republican", "region": "Carroll, Floyd, Franklin, Grayson, Henry, Patrick, Wythe; Martinsville, Galax",                                             "url": _SD_URL.format("S82")},
    8:  {"senator": "Mark Peake",               "party": "Republican", "region": "Bedford, Campbell; Lynchburg",                                                                                               "url": _SD_URL.format("S105")},
    9:  {"senator": "Tammy Brankley Mulchi",    "party": "Republican", "region": "Charlotte, Halifax, Lunenburg, Mecklenburg, Nottoway, Pittsylvania, Prince Edward; Danville",                               "url": _SD_URL.format("S131")},
    10: {"senator": "Luther Cifers",            "party": "Republican", "region": "Amelia, Appomattox, Buckingham, Cumberland, Fluvanna, Goochland, Hanover, Henrico, Louisa, Powhatan, Prince Edward",        "url": _SD_URL.format("S132")},
    11: {"senator": "Creigh Deeds",             "party": "Democrat",   "region": "Albemarle, Amherst, Louisa, Nelson; Charlottesville",                                                                       "url": _SD_URL.format("S62")},
    12: {"senator": "Glen Sturtevant",          "party": "Republican", "region": "Chesterfield; Colonial Heights",                                                                                             "url": _SD_URL.format("S99")},
    13: {"senator": "Lashrecse Aird",           "party": "Democrat",   "region": "Charles City, Dinwiddie, Henrico, Prince George, Surry, Sussex; Hopewell, Petersburg",                                      "url": _SD_URL.format("S115")},
    14: {"senator": "Lamont Bagby",             "party": "Democrat",   "region": "Henrico; Richmond",                                                                                                          "url": _SD_URL.format("S114")},
    15: {"senator": "Michael Jones",            "party": "Democrat",   "region": "Chesterfield; Richmond",                                                                                                     "url": _SD_URL.format("S134")},
    16: {"senator": "Schuyler VanValkenburg",   "party": "Democrat",   "region": "Henrico",                                                                                                                    "url": _SD_URL.format("S129")},
    17: {"senator": "Emily Jordan",             "party": "Republican", "region": "Brunswick, Dinwiddie, Greensville, Isle of Wight, Southampton; Chesapeake, Emporia, Franklin, Portsmouth, Suffolk",         "url": _SD_URL.format("S116")},
    18: {"senator": "L. Louise Lucas",          "party": "Democrat",   "region": "Chesapeake, Portsmouth",                                                                                                     "url": _SD_URL.format("S19")},
    19: {"senator": "Christie New Craig",       "party": "Republican", "region": "Chesapeake, Virginia Beach",                                                                                                 "url": _SD_URL.format("S118")},
    20: {"senator": "Bill DeSteph",             "party": "Republican", "region": "Accomack, Northampton; Norfolk, Virginia Beach",                                                                             "url": _SD_URL.format("S96")},
    21: {"senator": "Angelia Williams Graves",  "party": "Democrat",   "region": "Norfolk",                                                                                                                    "url": _SD_URL.format("S130")},
    22: {"senator": "Aaron Rouse",              "party": "Democrat",   "region": "Virginia Beach",                                                                                                             "url": _SD_URL.format("S113")},
    23: {"senator": "Mamie Locke",              "party": "Democrat",   "region": "Hampton; Newport News",                                                                                                      "url": _SD_URL.format("S67")},
    24: {"senator": "Danny Diggs",              "party": "Republican", "region": "James City, York; Newport News, Poquoson, Williamsburg",                                                                     "url": _SD_URL.format("S119")},
    25: {"senator": "Richard Stuart",           "party": "Republican", "region": "Caroline, Essex, King George, King William, Lancaster, Middlesex, Northumberland, Richmond, Spotsylvania, Westmoreland",    "url": _SD_URL.format("S78")},
    26: {"senator": "Ryan McDougle",            "party": "Republican", "region": "Gloucester, Hanover, James City, Mathews, New Kent",                                                                        "url": _SD_URL.format("S69")},
    27: {"senator": "Tara Durant",              "party": "Republican", "region": "Spotsylvania, Stafford; Fredericksburg",                                                                                     "url": _SD_URL.format("S120")},
    28: {"senator": "Bryce Reeves",             "party": "Republican", "region": "Culpeper, Fauquier, Greene, Madison, Orange, Rappahannock, Spotsylvania",                                                   "url": _SD_URL.format("S88")},
    29: {"senator": "Jeremy McPike",            "party": "Democrat",   "region": "Prince William, Stafford",                                                                                                   "url": _SD_URL.format("S98")},
    30: {"senator": "Danica Roem",              "party": "Democrat",   "region": "Prince William; Manassas, Manassas Park",                                                                                    "url": _SD_URL.format("S126")},
    31: {"senator": "Russet Perry",             "party": "Democrat",   "region": "Fauquier, Loudoun",                                                                                                          "url": _SD_URL.format("S125")},
    32: {"senator": "Kannan Srinivasan",        "party": "Democrat",   "region": "Loudoun",                                                                                                                    "url": _SD_URL.format("S133")},
    33: {"senator": "Jennifer Carroll Foy",     "party": "Democrat",   "region": "Fairfax, Prince William",                                                                                                    "url": _SD_URL.format("S117")},
    34: {"senator": "Scott Surovell",           "party": "Democrat",   "region": "Fairfax",                                                                                                                    "url": _SD_URL.format("S100")},
    35: {"senator": "Dave Marsden",             "party": "Democrat",   "region": "Fairfax",                                                                                                                    "url": _SD_URL.format("S80")},
    36: {"senator": "Stella Pekarsky",          "party": "Democrat",   "region": "Fairfax",                                                                                                                    "url": _SD_URL.format("S124")},
    37: {"senator": "Saddam Azlan Salim",       "party": "Democrat",   "region": "Fairfax; Fairfax City, Falls Church",                                                                                       "url": _SD_URL.format("S127")},
    38: {"senator": "Jennifer Boysko",          "party": "Democrat",   "region": "Fairfax",                                                                                                                    "url": _SD_URL.format("S106")},
    39: {"senator": "Elizabeth Bennett-Parker", "party": "Democrat",   "region": "Arlington, Fairfax; Alexandria",                                                                                             "url": _SD_URL.format("S135")},
    40: {"senator": "Barbara Favola",           "party": "Democrat",   "region": "Arlington",                                                                                                                  "url": _SD_URL.format("S86")},
}

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    district: str
    messages: list[ChatMessage]
    locality: str = ""
    hod_district: int | None = None
    sd_district: int | None = None

    @field_validator('hod_district', 'sd_district', mode='before')
    @classmethod
    def empty_str_to_none(cls, v):
        if v == '' or v is None:
            return None
        return int(v)

class ChatResponse(BaseModel):
    reply: str

class ElectionChatRequest(BaseModel):
    year: str
    messages: list[ChatMessage]

@app.get("/election-map", response_class=HTMLResponse)
@app.get("/results-map", response_class=HTMLResponse)
def election_map(mode: str = "total"):
    if mode not in _election_maps:
        try:
            _election_maps[mode] = _build_election_map(mode)
        except Exception as e:
            print(f"Warning: could not build election map ({mode}): {e}")
            return f"<p style='font-family:sans-serif;padding:40px'>Map unavailable: {e}</p>"
    return _election_maps.get(mode, "")

@app.get("/results", response_class=HTMLResponse)
def results_page():
    with open(os.path.join(BASE_DIR, "templates", "results.html"), "r", encoding="utf-8") as f:
        return f.read()

@app.get("/api/results-data")
def results_data():
    with open(os.path.join(BASE_DIR, "data_2026_special.json"), encoding="utf-8") as f:
        data = json.load(f)

    def _group_counts(ballot_options, vote_name):
        opt = next((o for o in ballot_options if o["name"].upper() == vote_name), None)
        if not opt:
            return 0, 0, 0, 0
        groups = {g["groupName"]: g["voteCount"] for g in opt.get("groupResults", [])}
        return (
            opt["voteCount"],
            groups.get("Early Voting", 0),
            groups.get("Election Day", 0),
            groups.get("Mailed Absentee", 0),
        )

    statewide_opts = data.get("results", {}).get("ballotItems", [{}])[0].get("ballotOptions", [])
    yes_state, yes_early_s, yes_eday_s, yes_mail_s = _group_counts(statewide_opts, "YES")
    no_state,  no_early_s,  no_eday_s,  no_mail_s  = _group_counts(statewide_opts, "NO")

    local = []
    for j in data.get("localResults", []):
        yes_v = no_v = 0
        yes_early = no_early = yes_eday = no_eday = yes_mail = no_mail = 0
        for item in j.get("ballotItems", []):
            opts = item.get("ballotOptions", [])
            yv, ye, yd, ym = _group_counts(opts, "YES")
            nv, ne, nd, nm = _group_counts(opts, "NO")
            yes_v += yv; yes_early += ye; yes_eday += yd; yes_mail += ym
            no_v  += nv; no_early  += ne; no_eday  += nd; no_mail  += nm
        total = yes_v + no_v
        local.append({
            "name": j["name"].strip(),
            "yes": yes_v, "no": no_v, "total": total,
            "pct_yes": round(yes_v / total * 100, 1) if total else 50.0,
            "winner": "Yes" if yes_v >= no_v else "No",
            "early_yes": yes_early, "early_no": no_early,
            "eday_yes": yes_eday,   "eday_no": no_eday,
            "mail_yes": yes_mail,   "mail_no": no_mail,
        })

    return {
        "statewide": {
            "yes": yes_state, "no": no_state,
            "early": {"yes": yes_early_s, "no": no_early_s},
            "election_day": {"yes": yes_eday_s, "no": no_eday_s},
            "mail": {"yes": yes_mail_s, "no": no_mail_s},
        },
        "local": local,
    }

@app.get("/", response_class=HTMLResponse)
def home():
    with open(os.path.join(BASE_DIR, "templates", "index.html"), "r", encoding="utf-8") as f:
        return f.read()

@app.get("/api/lookup")
def lookup(address: str):
    result = find_district(address)
    if "error" in result:
        return {"district": result}

    hod_num = result.get("hod_district")
    sd_num  = result.get("sd_district")
    hod_info = HOD_CONTEXT.get(hod_num) if hod_num else None
    sd_info  = SD_CONTEXT.get(sd_num)  if sd_num  else None

    cd_num_raw = result.get("district_number", "")
    try:
        cd_key = f"VA-{int(cd_num_raw):02d}"
    except (ValueError, TypeError):
        cd_key = "VA-00"
    cd_info = DISTRICT_CONTEXT.get(cd_key, {})

    from address_lookup import _vb_council, _vb_school_board, _norfolk_officials, _chesapeake_officials, _portsmouth_officials, _hampton_officials, _newport_news_officials, _suffolk_officials
    vb_num = result.get("vb_council_district")
    vb_info = _vb_council.get(vb_num) if vb_num is not None else None
    mayor_info = _vb_council.get(0)

    def _officer(key):
        o = _vb_council.get(key)
        return {"name": o["name"], "email": o["email"], "party": o.get("party", ""), "url": o.get("url", "")} if o else None

    polling_place = (
        result.get("vb_polling_place") or
        result.get("hampton_polling_place") or
        result.get("portsmouth_polling_place") or
        result.get("suffolk_polling_place") or
        result.get("chesapeake_polling_place") or
        result.get("norfolk_polling_place")
    )
    precinct_number = (
        result.get("hampton_precinct_number") or
        result.get("portsmouth_precinct_number") or
        result.get("suffolk_precinct_number") or
        result.get("chesapeake_precinct_number") or
        result.get("norfolk_precinct_number") or
        result.get("newport_news_precinct_number") or
        (polling_place or {}).get("precinct_number")
    )
    precinct_name = result.get("precinct")
    precinct_info = {
        "name": precinct_name,
        "number": precinct_number,
        "location": (polling_place or {}).get("location") or result.get("hampton_polling_location") or result.get("portsmouth_polling_location") or result.get("suffolk_polling_location") or result.get("chesapeake_polling_location") or result.get("norfolk_polling_location") or result.get("newport_news_polling_location"),
        "address": (polling_place or {}).get("full_address") or result.get("newport_news_polling_address"),
        "address_line_1": (polling_place or {}).get("address_line_1"),
        "address_line_2": (polling_place or {}).get("address_line_2"),
        "city": (polling_place or {}).get("city"),
        "state": (polling_place or {}).get("state"),
        "zip_code": (polling_place or {}).get("zip_code"),
        "room": (polling_place or {}).get("room"),
    } if precinct_name and precinct_name != "Not found" else None

    norfolk_council_urls = {
        "martin a thomas jr": "https://www.norfolk.gov/542/Vice-Mayor-Martin-A-Thomas-Jr",
        "courtney doyle": "https://www.norfolk.gov/4111/Courtney-R-Doyle",
        "courtney r doyle": "https://www.norfolk.gov/4111/Courtney-R-Doyle",
        "mamie johnson": "https://www.norfolk.gov/2932/Mamie-B-Johnson",
        "mamie b johnson": "https://www.norfolk.gov/2932/Mamie-B-Johnson",
        "john e jp paige": "https://www.norfolk.gov/538/John-E-JP-Paige",
        "tommy r smigiel jr": "https://www.norfolk.gov/539/Thomas-R-Smigiel-Jr",
        "thomas r smigiel jr": "https://www.norfolk.gov/539/Thomas-R-Smigiel-Jr",
        "jeremy d mcgee": "https://www.norfolk.gov/6429/Jeremy-D-McGee",
        "carlos j clanton": "https://www.norfolk.gov/6428/Carlos-J-Clanton",
    }

    def _norfolk_council_url(name):
        key = re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()
        return norfolk_council_urls.get(key, "")

    norfolk_school_board_urls = {
        "dr adale m martin": "https://www.npsk12.com/our-division/school-board/school-board-members/dr-adale-m-martin-member",
        "adale m martin": "https://www.npsk12.com/our-division/school-board/school-board-members/dr-adale-m-martin-member",
        "tanya k bhasin": "https://www.npsk12.com/our-division/school-board/school-board-members/ms-tanya-k-bhasin-member",
        "tiffany moore buffaloe": "https://www.npsk12.com/our-division/school-board/school-board-members/mrs-tiffany-moore-buffaloe-member",
        "ken d paulson": "https://www.npsk12.com/our-division/school-board/school-board-members/mr-kenneth-paulson-member",
        "kenneth d paulson": "https://www.npsk12.com/our-division/school-board/school-board-members/mr-kenneth-paulson-member",
        "kenneth paulson": "https://www.npsk12.com/our-division/school-board/school-board-members/mr-kenneth-paulson-member",
        "jason inge": "https://www.npsk12.com/our-division/school-board/school-board-members/mr-jason-inge-member",
        "jodi m slaughter": "https://www.npsk12.com/our-division/school-board/school-board-members/mr-jason-inge-member",
        "sarah e dicalogero": "https://www.npsk12.com/our-division/school-board/school-board-members/ms-sarah-e-dicalogero-chair",
        "alfreda a thomas": "https://www.npsk12.com/our-division/school-board/school-board-members/ms-alfreda-a-thomas-vice-chair",
        "alfreda thomas": "https://www.npsk12.com/our-division/school-board/school-board-members/ms-alfreda-a-thomas-vice-chair",
        "carlos j clanton": "https://www.norfolk.gov/6428/Carlos-J-Clanton",
    }

    def _norfolk_school_board_url(name):
        key = re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()
        return norfolk_school_board_urls.get(key, "")

    return {
        "district": result,
        "precinct_info": precinct_info,
        "vb_council": {
            "district_number": vb_num,
            "name": vb_info["name"],
            "email": vb_info["email"],
            "district": vb_info["district"],
            "party": vb_info.get("party", ""),
            "mayor": {"name": mayor_info["name"], "email": mayor_info["email"], "party": mayor_info.get("party", "")} if mayor_info else None,
            "sheriff": _officer("Sheriff"),
            "commonwealths_attorney": _officer("Commonwealth's Attorney"),
            "commissioner": _officer("Commissioner of the Revenue"),
            "treasurer": _officer("City Treasurer"),
            "clerk": _officer("Clerk of the Circuit Court"),
        } if vb_info else None,
        "norfolk": {
            "precinct": result.get("norfolk_precinct") or result.get("precinct"),
            "precinct_number": result.get("norfolk_precinct_number"),
            "polling_location": result.get("norfolk_polling_location"),
            "polling_address": (result.get("norfolk_polling_place") or {}).get("full_address"),
            "mayor": (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_norfolk_officials.get("mayor")),
            "sheriff": (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_norfolk_officials.get("sheriff")),
            "commonwealths_attorney": (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_norfolk_officials.get("commonwealths_attorney")),
            "treasurer": (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_norfolk_officials.get("treasurer")),
            "commissioner": (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_norfolk_officials.get("commissioner")),
            "ward": result.get("norfolk_ward"),
            "ward_rep": result.get("norfolk_ward_rep"),
            "ward_rep_url": result.get("norfolk_ward_rep_url") or _norfolk_council_url(result.get("norfolk_ward_rep")),
            "ward_sbm": result.get("norfolk_ward_sbm"),
            "ward_sbm_url": result.get("norfolk_ward_sbm_url") or _norfolk_school_board_url(result.get("norfolk_ward_sbm")),
            "superward": result.get("norfolk_superward"),
            "superward_rep": result.get("norfolk_superward_rep"),
            "superward_rep_url": result.get("norfolk_superward_rep_url") or _norfolk_council_url(result.get("norfolk_superward_rep")),
            "superward_sbm": result.get("norfolk_superward_sbm"),
            "superward_sbm_url": result.get("norfolk_superward_sbm_url") or _norfolk_school_board_url(result.get("norfolk_superward_sbm")),
        } if "norfolk" in result.get("locality", "").lower() else None,
        "chesapeake": {
            "precinct": result.get("chesapeake_precinct") or result.get("precinct"),
            "precinct_number": result.get("chesapeake_precinct_number"),
            "polling_location": result.get("chesapeake_polling_location"),
            "polling_address": (result.get("chesapeake_polling_place") or {}).get("full_address"),
            "polling_room": (result.get("chesapeake_polling_place") or {}).get("room"),
            "mayor":                  (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_chesapeake_officials.get("mayor")),
            "vice_mayor":             (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_chesapeake_officials.get("vice_mayor")),
            "sheriff":                (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_chesapeake_officials.get("sheriff")),
            "commonwealths_attorney": (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_chesapeake_officials.get("commonwealths_attorney")),
            "commissioner":           (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_chesapeake_officials.get("commissioner")),
            "treasurer":              (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_chesapeake_officials.get("treasurer")),
            "clerk":                  (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_chesapeake_officials.get("clerk")),
            "council": [{"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} for m in _chesapeake_officials.get("council", [])],
            "school_board": [{"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} for m in _chesapeake_officials.get("school_board", [])],
        } if "chesapeake" in result.get("locality", "").lower() else None,
        "portsmouth": {
            "precinct": result.get("portsmouth_precinct") or result.get("precinct"),
            "precinct_number": result.get("portsmouth_precinct_number"),
            "polling_location": result.get("portsmouth_polling_location"),
            "polling_address": (result.get("portsmouth_polling_place") or {}).get("full_address"),
            "polling_room": (result.get("portsmouth_polling_place") or {}).get("room"),
            "mayor":                  (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_portsmouth_officials.get("mayor")),
            "vice_mayor":             (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_portsmouth_officials.get("vice_mayor")),
            "sheriff":                (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_portsmouth_officials.get("sheriff")),
            "commonwealths_attorney": (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_portsmouth_officials.get("commonwealths_attorney")),
            "commissioner":           (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_portsmouth_officials.get("commissioner")),
            "treasurer":              (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_portsmouth_officials.get("treasurer")),
            "clerk":                  (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_portsmouth_officials.get("clerk")),
            "council": [{"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} for m in _portsmouth_officials.get("council", [])],
            "school_board": [{"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} for m in _portsmouth_officials.get("school_board", [])],
        } if "portsmouth" in result.get("locality", "").lower() else None,
        "hampton": {
            "precinct": result.get("hampton_precinct") or result.get("precinct"),
            "precinct_number": result.get("hampton_precinct_number"),
            "polling_location": result.get("hampton_polling_location"),
            "polling_address": (result.get("hampton_polling_place") or {}).get("full_address"),
            "polling_room": (result.get("hampton_polling_place") or {}).get("room"),
            "mayor":                  (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_hampton_officials.get("mayor")),
            "vice_mayor":             (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_hampton_officials.get("vice_mayor")),
            "sheriff":                (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_hampton_officials.get("sheriff")),
            "commonwealths_attorney": (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_hampton_officials.get("commonwealths_attorney")),
            "commissioner":           (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_hampton_officials.get("commissioner")),
            "treasurer":              (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_hampton_officials.get("treasurer")),
            "clerk":                  (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_hampton_officials.get("clerk")),
            "council": [{"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} for m in _hampton_officials.get("council", [])],
            "school_board": [{"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} for m in _hampton_officials.get("school_board", [])],
        } if "hampton" in result.get("locality", "").lower() else None,
        "newport_news": {
            "precinct": result.get("newport_news_precinct") or result.get("precinct"),
            "precinct_number": result.get("newport_news_precinct_number"),
            "polling_location": result.get("newport_news_polling_location"),
            "polling_address": result.get("newport_news_polling_address"),
            "mayor":                  (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_newport_news_officials.get("mayor")),
            "vice_mayor":             (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_newport_news_officials.get("vice_mayor")),
            "sheriff":                (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_newport_news_officials.get("sheriff")),
            "commonwealths_attorney": (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_newport_news_officials.get("commonwealths_attorney")),
            "commissioner":           (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_newport_news_officials.get("commissioner")),
            "treasurer":              (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_newport_news_officials.get("treasurer")),
            "clerk":                  (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_newport_news_officials.get("clerk")),
            "council_district": result.get("nn_council_district"),
            "council_district_name": result.get("nn_council_district_name"),
            "council": [{"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} for m in _newport_news_officials.get(f"council_{result.get('nn_council_district')}", [])],
            "school_board": (
                [{"name": m["name"], "party": m.get("party", ""), "url": m.get("url", ""), "district": m.get("district", "")} for m in _newport_news_officials.get(f"school_board_{result.get('nn_council_district')}", [])] +
                [{"name": m["name"], "party": m.get("party", ""), "url": m.get("url", ""), "district": m.get("district", "")} for m in _newport_news_officials.get("school_board_at_large", [])]
            ),
        } if "newport news" in result.get("locality", "").lower() else None,
        "suffolk": {
            "precinct": result.get("suffolk_precinct") or result.get("precinct"),
            "precinct_number": result.get("suffolk_precinct_number"),
            "polling_location": result.get("suffolk_polling_location"),
            "polling_address": (result.get("suffolk_polling_place") or {}).get("full_address"),
            "polling_room": (result.get("suffolk_polling_place") or {}).get("room"),
            "mayor":                  (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_suffolk_officials.get("mayor")),
            "vice_mayor":             (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_suffolk_officials.get("vice_mayor")),
            "sheriff":                (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_suffolk_officials.get("sheriff")),
            "commonwealths_attorney": (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_suffolk_officials.get("commonwealths_attorney")),
            "commissioner":           (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_suffolk_officials.get("commissioner")),
            "treasurer":              (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_suffolk_officials.get("treasurer")),
            "clerk":                  (lambda m: {"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} if m else None)(_suffolk_officials.get("clerk")),
            "council": [{"name": m["name"], "party": m.get("party", "")} for m in _suffolk_officials.get("council", [])],
        } if "suffolk" in result.get("locality", "").lower() else None,
        "vb_school_board": {
            "member": (lambda o: {"name": o["name"], "party": o.get("party", ""), "url": o.get("url", "")} if o else None)(_vb_school_board.get(vb_num)),
            "at_large": (lambda o: {"name": o["name"], "party": o.get("party", ""), "url": o.get("url", "")} if o else None)(_vb_school_board.get("at_large")),
        } if vb_num is not None else None,
        "us_rep": {
            "district_number": int(cd_num_raw) if cd_num_raw not in ("", "N/A") else None,
            "rep": cd_info.get("rep"),
            "party": cd_info.get("party"),
            "region": cd_info.get("region"),
            "url": cd_info.get("url"),
        } if cd_info.get("rep") else None,
        "state_delegate": {
            "district_number": hod_num,
            "delegate": hod_info["delegate"],
            "party": hod_info["party"],
            "locality": hod_info["locality"],
            "url": _HOD_MEMBER_URL.get(hod_num),
        } if hod_info else None,
        "state_senator": {
            "district_number": sd_num,
            "senator": sd_info["senator"],
            "party": sd_info["party"],
            "region": sd_info["region"],
            "url": sd_info.get("url"),
        } if sd_info else None,
        "us_senators": [
            {"name": "Mark Warner", "party": "Democrat", "title": "Virginia · Senior Senator", "url": "https://www.warner.senate.gov/public/index.cfm/contact"},
            {"name": "Tim Kaine",   "party": "Democrat", "title": "Virginia · Junior Senator", "url": "https://www.kaine.senate.gov/contact"},
        ],
        "us_president": {"name": "Donald J. Trump", "party": "Republican", "title": "United States", "url": "https://www.whitehouse.gov/contact/"},
        "va_statewide": [
            {"name": "Abigail D. Spanberger", "party": "Democrat", "title": "Governor",            "url": "https://www.governor.virginia.gov/contact/"},
            {"name": "Ghazala F. Hashmi",     "party": "Democrat", "title": "Lieutenant Governor", "url": "https://www.ltgov.virginia.gov/contact/"},
            {"name": "Jay C. Jones",          "party": "Democrat", "title": "Attorney General",    "url": "https://www.ag.virginia.gov/about/contact/"},
        ],
    }

@app.get("/map", response_class=HTMLResponse)
def get_map(address: str):
    result = find_district(address)
    if "error" in result:
        return "<p>Address not found</p>"
    m = folium.Map(location=[result["lat"], result["lng"]], zoom_start=12)
    folium.Marker(location=[result["lat"], result["lng"]], popup=f"{result['district']}", icon=folium.Icon(color="red")).add_to(m)
    folium.GeoJson(_get_va_cd()).add_to(m)
    return m.get_root().render()


def _build_district_map(layer: str, user_lat: float = None, user_lng: float = None, district: int = None) -> str:
    """Build a party-shaded folium map for congressional, HOD, SD, or city council districts."""
    global _va_hod_gdf, _va_sd_gdf, _vb_council_map_gdf, _nn_council_map_gdf, _norfolk_ward_map_gdf, _norfolk_superward_map_gdf

    import address_lookup as _al
    if layer == "hod":
        if _va_hod_gdf is None:
            # Reuse already-loaded GDF if address lookup already ran; otherwise load only HOD
            if _al.va_hod is not None:
                _va_hod_gdf = _al.va_hod
            else:
                _va_hod_gdf = gpd.read_file(os.path.join(BASE_DIR, "SCV Final 2021 Redistricting Plans", "SCV FINAL HOD.shp"))
                _va_hod_gdf = _va_hod_gdf.to_crs(epsg=4326)
                _al.va_hod = _va_hod_gdf  # share back so address_lookup can use it too
        if _va_hod_gdf is None:
            raise RuntimeError("HOD shapefile could not be loaded")
    if layer == "sd":
        if _va_sd_gdf is None:
            # Reuse already-loaded GDF if address lookup already ran; otherwise load only SD
            if _al.va_sd is not None:
                _va_sd_gdf = _al.va_sd
            else:
                _va_sd_gdf = gpd.read_file(os.path.join(BASE_DIR, "SCV Final 2021 Redistricting Plans", "SCV FINAL SD.shp"))
                _va_sd_gdf = _va_sd_gdf.to_crs(epsg=4326)
                _al.va_sd = _va_sd_gdf  # share back so address_lookup can use it too
        if _va_sd_gdf is None:
            raise RuntimeError("SD shapefile could not be loaded")
    if layer == "vb_council":
        if _vb_council_map_gdf is None:
            if _al.vb_council_gdf is not None:
                _vb_council_map_gdf = _al.vb_council_gdf
            else:
                _vb_council_map_gdf = gpd.read_file(os.path.join(BASE_DIR, "City_Council_Districts.shp")).to_crs(epsg=4326)
                _al.vb_council_gdf = _vb_council_map_gdf
        if _vb_council_map_gdf is None:
            raise RuntimeError("VB council shapefile could not be loaded")
    if layer == "nn_council":
        if _nn_council_map_gdf is None:
            if _al.nn_council_gdf is not None:
                _nn_council_map_gdf = _al.nn_council_gdf
            else:
                _nn_council_map_gdf = gpd.read_file(os.path.join(BASE_DIR, "City_Council_District.shp")).to_crs(epsg=4326)
                _nn_council_map_gdf = _nn_council_map_gdf[['DISTRICT', 'LONGNAME', 'geometry']]
                _al.nn_council_gdf = _nn_council_map_gdf
        if _nn_council_map_gdf is None:
            raise RuntimeError("Newport News council shapefile could not be loaded")
    if layer == "norfolk_ward":
        if _norfolk_ward_map_gdf is None:
            _norfolk_ward_map_gdf = gpd.read_file(os.path.join(BASE_DIR, "Wards.shp")).to_crs(epsg=4326)
        if _norfolk_ward_map_gdf is None:
            raise RuntimeError("Norfolk wards shapefile could not be loaded")
    if layer == "norfolk_superward":
        if _norfolk_superward_map_gdf is None:
            _norfolk_superward_map_gdf = gpd.read_file(os.path.join(BASE_DIR, "Superwards.geojson")).to_crs(epsg=4326)
        if _norfolk_superward_map_gdf is None:
            raise RuntimeError("Norfolk superwards file could not be loaded")

    features = []
    legend_type = "simple"
    m = folium.Map(location=[37.5, -79.0], zoom_start=7, tiles="CartoDB positron", min_zoom=6)
    map_var = m.get_name()

    if layer == "congressional":
        from shapely.geometry import mapping as _mapping
        va_cd = _get_va_cd()
        features = []
        for _, row in va_cd.iterrows():
            try:
                fp = str(int(row["CD118FP"]))
                district_key = f"VA-{fp.zfill(2)}"
                ctx = DISTRICT_CONTEXT.get(district_key, {})
                features.append({"type": "Feature",
                    "geometry": _mapping(row.geometry.simplify(0.005, preserve_topology=True)),
                    "properties": {
                        "_party": ctx.get("party", ""),
                        "_rep": ctx.get("rep", "Unknown"),
                        "_district": district_key,
                        "_region": ctx.get("region", ""),
                    }})
            except Exception:
                continue

        def cd_style(feat):
            party = feat["properties"].get("_party", "")
            fill = "#1a52c8" if party == "Democrat" else "#e03030" if party == "Republican" else "#aaaaaa"
            return {"fillColor": fill, "color": "#555", "weight": 1.0, "fillOpacity": 0.3}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=cd_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_rep", "_party", "_region"],
                aliases=["District:", "U.S. Representative:", "Party:", "Region:"],
                localize=True, sticky=True,
                style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        title = "Current Congressional Map — Virginia"

    elif layer == "hod":
        from shapely.geometry import mapping as _mapping
        features = []
        for _, row in _va_hod_gdf.iterrows():
            try:
                d = int(row["DISTRICT"])
                ctx = HOD_CONTEXT.get(d, {})
                features.append({"type": "Feature",
                    "geometry": _mapping(row.geometry.simplify(0.01, preserve_topology=True)),
                    "properties": {"DISTRICT": d,
                        "_district": f"HOD District {d}",
                        "_delegate": ctx.get("delegate", "Unknown"),
                        "_party": ctx.get("party", ""),
                        "_locality": ctx.get("locality", ""),
                        "_highlight": (d == district) if district else False}})
            except Exception:
                continue

        def hod_style(feat):
            party = feat["properties"].get("_party", "")
            hi = feat["properties"].get("_highlight", False)
            fill = "#0a3a9e" if party == "Democrat" else "#b01020" if party == "Republican" else "#888"
            return {"fillColor": fill, "color": "#333", "weight": 2 if hi else 0.6,
                    "fillOpacity": 0.85 if hi else 0.3}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=hod_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_delegate", "_party", "_locality"],
                aliases=["District:", "Delegate:", "Party:", "Area:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        ctx = HOD_CONTEXT.get(district, {}) if district else {}
        title = f"HOD District {district} — {ctx.get('delegate','')}" if district else "VA House of Delegates"

    elif layer == "sd":
        from shapely.geometry import mapping as _mapping
        for _, row in _va_sd_gdf.iterrows():
            try:
                d = int(row["DISTRICT"])
                ctx = SD_CONTEXT.get(d, {})
                features.append({"type": "Feature",
                    "geometry": _mapping(row.geometry.simplify(0.01, preserve_topology=True)),
                    "properties": {"DISTRICT": d,
                        "_district": f"Senate District {d}",
                        "_senator": ctx.get("senator", "Unknown"),
                        "_party": ctx.get("party", ""),
                        "_region": ctx.get("region", ""),
                        "_highlight": (d == district) if district else False}})
            except Exception:
                continue

        def sd_style(feat):
            party = feat["properties"].get("_party", "")
            hi = feat["properties"].get("_highlight", False)
            fill = "#0a3a9e" if party == "Democrat" else "#b01020" if party == "Republican" else "#888"
            return {"fillColor": fill, "color": "#333", "weight": 2 if hi else 0.6,
                    "fillOpacity": 0.85 if hi else 0.3}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=sd_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_senator", "_party", "_region"],
                aliases=["District:", "Senator:", "Party:", "Region:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        ctx = SD_CONTEXT.get(district, {}) if district else {}
        title = f"Senate District {district} — {ctx.get('senator','')}" if district else "VA State Senate"

    elif layer == "vb_council":
        from shapely.geometry import mapping as _mapping
        from address_lookup import _vb_council as _vb_ctx
        for _, row in _vb_council_map_gdf.iterrows():
            try:
                d = int(row["District"])
                ctx = _vb_ctx.get(d, {})
                features.append({"type": "Feature",
                    "geometry": _mapping(row.geometry.simplify(0.002, preserve_topology=True)),
                    "properties": {"DISTRICT": d,
                        "_district": f"District {d}",
                        "_rep": ctx.get("name", "Unknown"),
                        "_party": ctx.get("party", ""),
                        "_highlight": (d == district) if district else False}})
            except Exception:
                continue

        def vb_style(feat):
            party = feat["properties"].get("_party", "")
            hi = feat["properties"].get("_highlight", False)
            fill = "#0a3a9e" if party == "Democrat" else "#b01020" if party == "Republican" else "#888"
            return {"fillColor": fill, "color": "#333", "weight": 2 if hi else 0.6,
                    "fillOpacity": 0.85 if hi else 0.3}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=vb_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_rep", "_party"],
                aliases=["District:", "Council Member:", "Party:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        ctx = _vb_ctx.get(district, {}) if district else {}
        title = f"VB Council District {district} — {ctx.get('name','')}" if district else "Virginia Beach City Council Districts"
        m.location = [36.852, -76.085]
        m.zoom_start = 11

    elif layer == "nn_council":
        from shapely.geometry import mapping as _mapping
        from address_lookup import _newport_news_officials as _nn_ctx
        for _, row in _nn_council_map_gdf.iterrows():
            try:
                d = int(row["DISTRICT"])
                long_name = str(row.get("LONGNAME", f"District {d}"))
                members = _nn_ctx.get(f"council_{d}", [])
                member_name = members[0]["name"] if members else "Unknown"
                member_party = members[0].get("party", "") if members else ""
                features.append({"type": "Feature",
                    "geometry": _mapping(row.geometry.simplify(0.002, preserve_topology=True)),
                    "properties": {"DISTRICT": d,
                        "_district": long_name,
                        "_rep": member_name,
                        "_party": member_party,
                        "_highlight": (d == district) if district else False}})
            except Exception:
                continue

        def nn_style(feat):
            party = feat["properties"].get("_party", "")
            hi = feat["properties"].get("_highlight", False)
            fill = "#0a3a9e" if party == "Democrat" else "#b01020" if party == "Republican" else "#888"
            return {"fillColor": fill, "color": "#333", "weight": 2 if hi else 0.6,
                    "fillOpacity": 0.85 if hi else 0.3}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=nn_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_rep", "_party"],
                aliases=["District:", "Council Member:", "Party:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        if district:
            matched = [f for f in features if f["properties"]["DISTRICT"] == district]
            d_name = matched[0]["properties"]["_district"] if matched else f"District {district}"
            d_rep  = matched[0]["properties"]["_rep"]      if matched else ""
            title = f"Newport News {d_name} — {d_rep}"
        else:
            title = "Newport News City Council Districts"
        m.location = [37.085, -76.493]
        m.zoom_start = 11

    elif layer == "norfolk_ward":
        from shapely.geometry import mapping as _mapping
        for _, row in _norfolk_ward_map_gdf.iterrows():
            try:
                w = int(row["WARD"])
                rep = str(row.get("WARD_REP", "Unknown"))
                features.append({"type": "Feature",
                    "geometry": _mapping(row.geometry.simplify(0.002, preserve_topology=True)),
                    "properties": {"WARD": w,
                        "_district": f"Ward {w}",
                        "_rep": rep,
                        "_party": "",
                        "_highlight": (w == district) if district else False}})
            except Exception:
                continue

        def norfolk_style(feat):
            hi = feat["properties"].get("_highlight", False)
            return {"fillColor": "#2a6496", "color": "#333", "weight": 2 if hi else 0.6,
                    "fillOpacity": 0.85 if hi else 0.3}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=norfolk_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_rep"],
                aliases=["Ward:", "Council Member:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        if district:
            matched = [f for f in features if f["properties"]["WARD"] == district]
            rep = matched[0]["properties"]["_rep"] if matched else ""
            title = f"Norfolk Ward {district} — {rep}"
        else:
            title = "Norfolk City Council Wards"
        m.location = [36.851, -76.286]
        m.zoom_start = 12

    elif layer == "vb_school_board":
        from shapely.geometry import mapping as _mapping
        from address_lookup import _vb_school_board as _vb_sb_ctx
        if _vb_council_map_gdf is None:
            import address_lookup as _al2
            if _al2.vb_council_gdf is not None:
                _vb_council_map_gdf = _al2.vb_council_gdf
            else:
                _vb_council_map_gdf = gpd.read_file(os.path.join(BASE_DIR, "City_Council_Districts.shp")).to_crs(epsg=4326)
                _al2.vb_council_gdf = _vb_council_map_gdf
        for _, row in _vb_council_map_gdf.iterrows():
            try:
                d = int(row["District"])
                ctx = _vb_sb_ctx.get(d, {})
                features.append({"type": "Feature",
                    "geometry": _mapping(row.geometry.simplify(0.002, preserve_topology=True)),
                    "properties": {"DISTRICT": d,
                        "_district": f"District {d}",
                        "_rep": ctx.get("name", "Unknown"),
                        "_party": ctx.get("party", ""),
                        "_highlight": (d == district) if district else False}})
            except Exception:
                continue

        def vb_sb_style(feat):
            party = feat["properties"].get("_party", "")
            hi = feat["properties"].get("_highlight", False)
            fill = "#0a3a9e" if party == "Democrat" else "#b01020" if party == "Republican" else "#888"
            return {"fillColor": fill, "color": "#333", "weight": 2 if hi else 0.6,
                    "fillOpacity": 0.85 if hi else 0.3}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=vb_sb_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_rep", "_party"],
                aliases=["District:", "School Board Member:", "Party:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        ctx = _vb_sb_ctx.get(district, {}) if district else {}
        title = f"VB School Board District {district} — {ctx.get('name','')}" if district else "Virginia Beach School Board Districts"
        m.location = [36.852, -76.085]
        m.zoom_start = 11

    elif layer == "nn_school_board":
        from shapely.geometry import mapping as _mapping
        from address_lookup import _newport_news_officials as _nn_sb_ctx
        if _nn_council_map_gdf is None:
            import address_lookup as _al2
            if _al2.nn_council_gdf is not None:
                _nn_council_map_gdf = _al2.nn_council_gdf
            else:
                _nn_council_map_gdf = gpd.read_file(os.path.join(BASE_DIR, "City_Council_District.shp")).to_crs(epsg=4326)
                _nn_council_map_gdf = _nn_council_map_gdf[['DISTRICT', 'LONGNAME', 'geometry']]
                _al2.nn_council_gdf = _nn_council_map_gdf
        for _, row in _nn_council_map_gdf.iterrows():
            try:
                d = int(row["DISTRICT"])
                long_name = str(row.get("LONGNAME", f"District {d}"))
                members = _nn_sb_ctx.get(f"school_board_{d}", [])
                member_name = members[0]["name"] if members else "Unknown"
                member_party = members[0].get("party", "") if members else ""
                features.append({"type": "Feature",
                    "geometry": _mapping(row.geometry.simplify(0.002, preserve_topology=True)),
                    "properties": {"DISTRICT": d,
                        "_district": long_name,
                        "_rep": member_name,
                        "_party": member_party,
                        "_highlight": (d == district) if district else False}})
            except Exception:
                continue

        def nn_sb_style(feat):
            party = feat["properties"].get("_party", "")
            hi = feat["properties"].get("_highlight", False)
            fill = "#0a3a9e" if party == "Democrat" else "#b01020" if party == "Republican" else "#888"
            return {"fillColor": fill, "color": "#333", "weight": 2 if hi else 0.6,
                    "fillOpacity": 0.85 if hi else 0.3}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=nn_sb_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_rep", "_party"],
                aliases=["District:", "School Board Member:", "Party:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        if district:
            matched = [f for f in features if f["properties"]["DISTRICT"] == district]
            d_name = matched[0]["properties"]["_district"] if matched else f"District {district}"
            d_rep  = matched[0]["properties"]["_rep"]      if matched else ""
            title = f"Newport News School Board {d_name} — {d_rep}"
        else:
            title = "Newport News School Board Districts"
        m.location = [37.085, -76.493]
        m.zoom_start = 11

    elif layer == "norfolk_ward_sb":
        from shapely.geometry import mapping as _mapping
        if _norfolk_ward_map_gdf is None:
            _norfolk_ward_map_gdf = gpd.read_file(os.path.join(BASE_DIR, "Wards.shp")).to_crs(epsg=4326)
        for _, row in _norfolk_ward_map_gdf.iterrows():
            try:
                w = int(row["WARD"])
                sbm = str(row.get("WARD_SBM", "Unknown"))
                features.append({"type": "Feature",
                    "geometry": _mapping(row.geometry.simplify(0.002, preserve_topology=True)),
                    "properties": {"WARD": w,
                        "_district": f"Ward {w}",
                        "_rep": sbm,
                        "_party": "",
                        "_highlight": (w == district) if district else False}})
            except Exception:
                continue

        def norfolk_sb_style(feat):
            hi = feat["properties"].get("_highlight", False)
            return {"fillColor": "#2a6496", "color": "#333", "weight": 2 if hi else 0.6,
                    "fillOpacity": 0.85 if hi else 0.3}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=norfolk_sb_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_rep"],
                aliases=["Ward:", "School Board Member:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        if district:
            matched = [f for f in features if f["properties"]["WARD"] == district]
            sbm = matched[0]["properties"]["_rep"] if matched else ""
            title = f"Norfolk School Board Ward {district} — {sbm}"
        else:
            title = "Norfolk School Board Wards"
        m.location = [36.851, -76.286]
        m.zoom_start = 12

    elif layer == "norfolk_superward":
        from shapely.geometry import mapping as _mapping
        for _, row in _norfolk_superward_map_gdf.iterrows():
            try:
                sw = int(row["SUPWARD"])
                rep = str(row.get("SWARD_REP", "Unknown"))
                features.append({"type": "Feature",
                    "geometry": _mapping(row.geometry.simplify(0.002, preserve_topology=True)),
                    "properties": {"SUPWARD": sw,
                        "_district": f"Superward {sw}",
                        "_rep": rep,
                        "_party": "",
                        "_highlight": (sw == district) if district else False}})
            except Exception:
                continue

        def norfolk_sw_style(feat):
            hi = feat["properties"].get("_highlight", False)
            return {"fillColor": "#1a6b3c", "color": "#333", "weight": 2 if hi else 0.6,
                    "fillOpacity": 0.85 if hi else 0.3}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=norfolk_sw_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_rep"],
                aliases=["Superward:", "Council Member:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        if district:
            matched = [f for f in features if f["properties"]["SUPWARD"] == district]
            rep = matched[0]["properties"]["_rep"] if matched else ""
            title = f"Norfolk Superward {district} — {rep}"
        else:
            title = "Norfolk City Council Superwards"
        m.location = [36.851, -76.286]
        m.zoom_start = 12

    elif layer == "hod_flip":
        from shapely.geometry import mapping as _mapping
        if _va_hod_gdf is None:
            import address_lookup as _al2
            if _al2.va_hod is not None:
                _va_hod_gdf = _al2.va_hod
            else:
                _va_hod_gdf = gpd.read_file(os.path.join(BASE_DIR, "SCV Final 2021 Redistricting Plans", "SCV FINAL HOD.shp"))
                _va_hod_gdf = _va_hod_gdf.to_crs(epsg=4326)
                _al2.va_hod = _va_hod_gdf
        results_2025 = _load_2025_results()
        hod_data_2025 = results_2025.get("hod", {})
        for _, row in _va_hod_gdf.iterrows():
            try:
                d = int(row["DISTRICT"])
                party_2023 = HOD_2023_PARTY.get(d, "Democrat")
                party_2025 = HOD_CONTEXT.get(d, {}).get("party", "")
                race = hod_data_2025.get(d, {})
                winner_name = race.get("candidates", [{}])[0].get("name", HOD_CONTEXT.get(d, {}).get("delegate", "Unknown"))
                flipped = (party_2023 != party_2025)
                if flipped and "democrat" in party_2025.lower():
                    status = "Flipped Democratic"
                elif flipped and "republican" in party_2025.lower():
                    status = "Flipped Republican"
                elif "democrat" in party_2025.lower():
                    status = "Held Democratic"
                else:
                    status = "Held Republican"
                features.append({"type": "Feature",
                    "geometry": _mapping(row.geometry.simplify(0.01, preserve_topology=True)),
                    "properties": {
                        "_district": f"HOD District {d}",
                        "_winner": winner_name,
                        "_status": status,
                        "_2023": party_2023,
                        "_2025": party_2025,
                    }})
            except Exception:
                continue

        def hod_flip_style(feat):
            status = feat["properties"].get("_status", "")
            if status == "Flipped Democratic":
                return {"fillColor": "#1a52c8", "color": "#111", "weight": 1.5, "fillOpacity": 0.85}
            elif status == "Flipped Republican":
                return {"fillColor": "#c8102e", "color": "#111", "weight": 1.5, "fillOpacity": 0.85}
            elif status == "Held Democratic":
                return {"fillColor": "#6b9fd4", "color": "#555", "weight": 0.4, "fillOpacity": 0.35}
            else:
                return {"fillColor": "#e8807a", "color": "#555", "weight": 0.4, "fillOpacity": 0.35}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=hod_flip_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_winner", "_status", "_2023", "_2025"],
                aliases=["District:", "2025 Winner:", "Result:", "2023 Party:", "2025 Party:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        title = "2025 Virginia HOD — District Flips vs 2023"
        legend_type = "flip"

    elif layer in ("gov_results", "ltgov_results", "ag_results"):
        office_config = {
            "gov_results": {
                "office": "Governor",
                "title": "2025 Virginia Governor's Race - by Locality",
            },
            "ltgov_results": {
                "office": "Lieutenant Governor",
                "title": "2025 Virginia Lieutenant Governor's Race - by Locality",
            },
            "ag_results": {
                "office": "Attorney General",
                "title": "2025 Virginia Attorney General's Race - by Locality",
            },
        }[layer]
        race_data = _load_2025_statewide_locality_results(office_config["office"])

        # Normalize locality names for matching
        def _nloc(n):
            return n.upper().replace("&", "AND").strip()

        race_lookup = {_nloc(k): v for k, v in race_data.items()}

        counties_path = os.path.join(BASE_DIR, "va_counties.json")
        with open(counties_path, encoding="utf-8") as _cf:
            counties_geojson = json.load(_cf)
        features = counties_geojson.get("features", [])

        for feat in features:
            props = feat.get("properties", {})
            name = props.get("NAME", "")
            lsad = props.get("LSAD", "")
            geo_key = _nloc(f"{name} {lsad}")
            result = race_lookup.get(geo_key, {})
            winner = result.get("winner", {})
            winner_party = winner.get("party", "")
            dem = result.get("dem", {})
            rep = result.get("rep", {})
            dem_name = dem.get("name", "Democratic")
            rep_name = rep.get("name", "Republican")
            dem_pct = float(dem.get("pct", 0.0))
            rep_pct = float(rep.get("pct", 0.0))
            winner_pct = result.get("winner_pct", 0.0)
            margin = max(0.0, winner_pct - 50.0)
            opacity = round(0.2 + min(margin / 35.0, 1.0) * 0.6, 3)
            props["_locality"] = f"{name} {lsad}".title()
            props["_democrat"] = f"{dem_name} ({dem_pct:.1f}%)"
            props["_republican"] = f"{rep_name} ({rep_pct:.1f}%)"
            props["_winner"] = (
                f"{winner.get('name', 'N/A')} ({'D' if 'democrat' in winner_party.lower() else 'R' if 'republican' in winner_party.lower() else winner_party})"
                if winner else "N/A"
            )
            props["_party"] = winner_party
            _tp25 = dem_pct + rep_pct
            _d_tpv25 = dem_pct / _tp25 * 100 if _tp25 > 0 else 50.0
            props["_color"] = _color_from_tpv(_d_tpv25) if result else "#cccccc"
            _annotate_baseline(props, geo_key, dem_pct, rep_pct)

        def statewide_results_style(feat):
            return {"fillColor": feat["properties"].get("_color", "#cccccc"), "color": "#555", "weight": 0.5, "fillOpacity": 0.9}

        folium.GeoJson(
            counties_geojson,
            style_function=statewide_results_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_locality", "_winner", "_result", "_baseline", "_swing", "_lean"],
                aliases=["County:", "Winner:", "Result:", "Baseline:", "Swing:", "Lean:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        title = office_config["title"]
        baseline_summary = _baseline_summary_for_statewide_race("2025", office_config["office"])
        legend_type = "vote_share"

    elif layer in ("pres_2024", "senate_2024"):
        office_label = "President" if layer == "pres_2024" else "U.S. Senate"
        map_title = (
            "2024 Virginia — President by Locality" if layer == "pres_2024"
            else "2024 Virginia — U.S. Senate by Locality"
        )
        raw = _load_2024_results()
        race_data = raw.get("locality_results", {}).get(office_label, {})

        def _nloc24(n):
            return n.upper().replace("&", "AND").strip()

        race_lookup24 = {_nloc24(k): v for k, v in race_data.items()}

        counties_path = os.path.join(BASE_DIR, "va_counties.json")
        with open(counties_path, encoding="utf-8") as _cf:
            counties_geojson = json.load(_cf)

        for feat in counties_geojson.get("features", []):
            props = feat.get("properties", {})
            name = props.get("NAME", "")
            lsad = props.get("LSAD", "")
            geo_key = _nloc24(f"{name} {lsad}")
            result = race_lookup24.get(geo_key, {})
            winner = result.get("winner", {})
            winner_party = winner.get("party", "")
            dem = result.get("dem", {})
            rep = result.get("rep", {})
            dem_pct = float(dem.get("pct") or 0.0)
            rep_pct = float(rep.get("pct") or 0.0)
            margin = max(0.0, float(winner.get("pct") or 0.0) - 50.0)
            opacity = round(0.2 + min(margin / 35.0, 1.0) * 0.6, 3)
            props["_locality"] = f"{name} {lsad}".title()
            props["_democrat"] = f"{dem.get('name','Dem')} ({dem_pct:.1f}%)"
            props["_republican"] = f"{rep.get('name','Rep')} ({rep_pct:.1f}%)"
            props["_winner"] = (
                f"{winner.get('name','N/A')} ({'D' if 'democrat' in winner_party.lower() else 'R' if 'republican' in winner_party.lower() else winner_party})"
                if winner else "N/A"
            )
            props["_party"] = winner_party
            _tp24 = dem_pct + rep_pct
            _d_tpv24 = dem_pct / _tp24 * 100 if _tp24 > 0 else 50.0
            props["_color"] = _color_from_tpv(_d_tpv24) if result else "#cccccc"
            _annotate_baseline(props, geo_key, dem_pct, rep_pct)

        def _2024_locality_style(feat):
            return {"fillColor": feat["properties"].get("_color", "#cccccc"), "color": "#555", "weight": 0.5, "fillOpacity": 0.9}

        folium.GeoJson(
            counties_geojson,
            style_function=_2024_locality_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_locality", "_winner", "_result", "_baseline", "_swing", "_lean"],
                aliases=["County:", "Winner:", "Result:", "Baseline:", "Swing:", "Lean:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        title = map_title
        baseline_summary = _baseline_summary_for_statewide_race("2024", office_label)
        legend_type = "vote_share"

    elif layer == "congress_2024":
        from shapely.geometry import mapping as _mapping
        raw = _load_2024_results()
        congress_data = raw.get("congress", {})
        _ORDINALS = {1:"1st",2:"2nd",3:"3rd",4:"4th",5:"5th",6:"6th",7:"7th",8:"8th",9:"9th",10:"10th",11:"11th"}
        va_cd = _get_va_cd()
        features = []
        for _, row in va_cd.iterrows():
            try:
                dist_num = int(row["CD118FP"])
                dist_key = _ORDINALS.get(dist_num, f"{dist_num}th")
                race = congress_data.get(dist_key, {})
                cands = race.get("candidates", [])
                winner = cands[0] if cands else {}
                runner = cands[1] if len(cands) > 1 else {}
                w_name = winner.get("name", "Unknown")
                w_party = winner.get("party", "")
                w_pct = float(winner.get("pct") or 0.0)
                r_label = f"{runner.get('name','—')} ({float(runner.get('pct') or 0.0):.1f}%)" if runner else "Uncontested"
                features.append({"type": "Feature",
                    "geometry": _mapping(row.geometry.simplify(0.005, preserve_topology=True)),
                    "properties": {
                        "_district": f"VA-{dist_num}",
                        "_winner": w_name,
                        "_party": w_party,
                        "_winner_pct": f"{w_pct:.1f}%",
                        "_runner": r_label,
                        "_color": _pct_to_band_color(w_pct, w_party) if race else "#cccccc",
                    }})
            except Exception:
                continue

        def _congress_2024_style(feat):
            return {"fillColor": feat["properties"].get("_color", "#cccccc"), "color": "#555", "weight": 1.0, "fillOpacity": 0.9}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=_congress_2024_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_winner", "_party", "_winner_pct", "_runner"],
                aliases=["District:", "Winner:", "Party:", "Winner %:", "Runner-Up:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        title = "2024 Virginia — Congressional District Results"
        legend_type = "vote_share"

    elif layer in ("gov_2021", "ltgov_2021", "ag_2021", "gov_2017", "ltgov_2017", "ag_2017", "pres_2016", "pres_2020", "senate_2020", "senate_2018"):
        year = layer.rsplit("_", 1)[-1]
        race_map = {
            "gov_2021": "Governor",
            "ltgov_2021": "Lieutenant Governor",
            "ag_2021": "Attorney General",
            "gov_2017": "Governor",
            "ltgov_2017": "Lieutenant Governor",
            "ag_2017": "Attorney General",
            "pres_2016": "President",
            "pres_2020": "President",
            "senate_2020": "U.S. Senate",
            "senate_2018": "U.S. Senate",
        }
        title_map = {
            "gov_2021": "2021 Virginia — Governor by Locality",
            "ltgov_2021": "2021 Virginia — Lt. Governor by Locality",
            "ag_2021": "2021 Virginia — Attorney General by Locality",
        }
        title_map.update({
            "gov_2017": "2017 Virginia Governor by Locality",
            "ltgov_2017": "2017 Virginia Lt. Governor by Locality",
            "ag_2017": "2017 Virginia Attorney General by Locality",
            "pres_2016": "2016 Virginia President by Locality",
            "pres_2020": "2020 Virginia — President by Locality",
            "senate_2020": "2020 Virginia — U.S. Senate by Locality",
            "senate_2018": "2018 Virginia — U.S. Senate by Locality",
        })
        race_name = race_map[layer]
        raw21 = (
            _load_2021_results() if year == "2021" else
            _load_2020_results() if year == "2020" else
            _load_2018_results() if year == "2018" else
            _load_historical_results(year)
        )
        cand_party = {}
        for _race in raw21.get("statewide", []):
            if _race["race"] == race_name:
                for _c in _race["candidates"]:
                    cand_party[_c["name"]] = _c["party"]
        race_data = raw21.get("locality_results", {}).get(race_name, {})

        def _nloc21(n): return n.upper().replace("&", "AND").strip()
        race_lookup21 = {_nloc21(k): v for k, v in race_data.items()}

        with open(os.path.join(BASE_DIR, "va_counties.json"), encoding="utf-8") as _f:
            counties_geojson21 = json.load(_f)

        for feat in counties_geojson21["features"]:
            props = feat.get("properties", {})
            name = props.get("NAME", "")
            lsad = props.get("LSAD", "")
            geo_key = _nloc21(f"{name} {lsad}")
            result = race_lookup21.get(geo_key, {})
            if result:
                if isinstance(result, dict) and "candidates" in result:
                    for candidate in result.get("candidates", []):
                        cand_party[candidate.get("name", "")] = candidate.get("party", "")
                    result = {
                        candidate.get("name", ""): float(candidate.get("pct") or 0.0)
                        for candidate in result.get("candidates", [])
                    }
                sorted_cands = sorted(result.items(), key=lambda x: -x[1])
                w_name, w_pct = sorted_cands[0]
                r_name, r_pct = sorted_cands[1] if len(sorted_cands) > 1 else ("—", 0)
                w_party = cand_party.get(w_name, "")
                margin = max(0.0, w_pct - 50.0)
                opacity = round(0.2 + min(margin / 35.0, 1.0) * 0.6, 3)
            else:
                w_name, w_pct, w_party, r_name, r_pct = "—", 0.0, "", "—", 0.0
            r_party = cand_party.get(r_name, "")
            if "democrat" in w_party.lower():
                _dem_p, _rep_p = w_pct, (r_pct if "republican" in r_party.lower() else 0.0)
            elif "republican" in w_party.lower():
                _dem_p, _rep_p = (r_pct if "democrat" in r_party.lower() else 0.0), w_pct
            else:
                _dem_p = next((pct for n, pct in sorted_cands if "democrat" in cand_party.get(n, "").lower()), 0.0)
                _rep_p = next((pct for n, pct in sorted_cands if "republican" in cand_party.get(n, "").lower()), 0.0)
            props["_locality"] = f"{name} {lsad}".title()
            props["_winner"] = f"{w_name} ({w_pct:.1f}%)"
            props["_runner"] = f"{r_name} ({r_pct:.1f}%)"
            props["_party"] = w_party
            _tp_old = _dem_p + _rep_p
            _d_tpv_old = _dem_p / _tp_old * 100 if _tp_old > 0 else 50.0
            props["_color"] = _color_from_tpv(_d_tpv_old) if w_party else "#cccccc"
            _annotate_baseline(props, geo_key, _dem_p, _rep_p)

        def _2021_locality_style(feat):
            return {"fillColor": feat["properties"].get("_color", "#cccccc"), "color": "#555", "weight": 0.5, "fillOpacity": 0.9}

        folium.GeoJson(
            counties_geojson21,
            style_function=_2021_locality_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_locality", "_winner", "_result", "_baseline", "_swing", "_lean"],
                aliases=["County:", "Winner:", "Result:", "Baseline:", "Swing:", "Lean:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        title = title_map[layer]
        baseline_summary = _baseline_summary_for_statewide_race(year, race_name)
        legend_type = "vote_share"

    elif layer in ("senate_2019_results", "hod_2019_results"):
        from shapely.geometry import mapping as _mapping
        chamber = "senate" if layer == "senate_2019_results" else "hod"
        chamber_label = "State Senate" if chamber == "senate" else "House of Delegates"
        if chamber == "senate":
            if _va_sd_gdf is None:
                import address_lookup as _al2
                if _al2.va_sd is not None:
                    _va_sd_gdf = _al2.va_sd
                else:
                    _va_sd_gdf = gpd.read_file(os.path.join(BASE_DIR, "SCV Final 2021 Redistricting Plans", "SCV FINAL SD.shp"))
                    _va_sd_gdf = _va_sd_gdf.to_crs(epsg=4326)
                    _al2.va_sd = _va_sd_gdf
            gdf = _va_sd_gdf
        else:
            if _va_hod_gdf is None:
                import address_lookup as _al2
                if _al2.va_hod is not None:
                    _va_hod_gdf = _al2.va_hod
                else:
                    _va_hod_gdf = gpd.read_file(os.path.join(BASE_DIR, "SCV Final 2021 Redistricting Plans", "SCV FINAL HOD.shp"))
                    _va_hod_gdf = _va_hod_gdf.to_crs(epsg=4326)
                    _al2.va_hod = _va_hod_gdf
            gdf = _va_hod_gdf

        results_2019 = _load_historical_results("2019").get(chamber, {})
        for _, row in gdf.iterrows():
            d = int(row["DISTRICT"])
            race = results_2019.get(str(d), results_2019.get(d, {}))
            cands = race.get("candidates", [])
            winner = cands[0] if cands else {}
            runner = cands[1] if len(cands) > 1 else {}
            w_name = winner.get("name", "No data")
            w_party = winner.get("party", "")
            w_pct = float(winner.get("pct", 0.0))
            r_label = f"{runner.get('name','-')} ({float(runner.get('pct') or 0.0):.1f}%)" if runner else "Uncontested"
            features.append({"type": "Feature",
                "geometry": _mapping(row.geometry.simplify(0.01, preserve_topology=True)),
                "properties": {
                    "_district": f"{chamber_label} District {d}",
                    "_winner": w_name,
                    "_party": w_party,
                    "_pct": f"{w_pct:.1f}%",
                    "_runner": r_label,
                    "_color": _pct_to_band_color(w_pct, w_party) if cands else "#cccccc",
                }})

        def _state_leg_2019_style(feat):
            return {"fillColor": feat["properties"].get("_color", "#cccccc"), "color": "#555", "weight": 0.5, "fillOpacity": 0.9}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=_state_leg_2019_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_winner", "_party", "_pct", "_runner"],
                aliases=["District:", "Winner:", "Party:", "Vote Share:", "Runner-Up:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        title = f"2019 Virginia {chamber_label} - Numbered District Results"
        legend_type = "vote_share"

    elif layer in ("congress_2022", "congress_2016", "congress_2020", "congress_2018"):
        from shapely.geometry import mapping as _mapping
        year = layer.split("_")[1]
        raw22 = (
            _load_historical_results("2016") if year == "2016" else
            _load_2022_results() if year == "2022" else
            _load_2020_results() if year == "2020" else
            _load_2018_results()
        )
        congress_data22 = raw22.get("congress", {})
        _ORDINALS22 = {1:"1st",2:"2nd",3:"3rd",4:"4th",5:"5th",6:"6th",7:"7th",8:"8th",9:"9th",10:"10th",11:"11th"}
        va_cd = _get_va_cd()
        features22 = []
        for _, row in va_cd.iterrows():
            try:
                dist_num = int(row["CD118FP"])
                dist_key = _ORDINALS22.get(dist_num, f"{dist_num}th")
                race = congress_data22.get(dist_key, {})
                cands = race.get("candidates", [])
                winner = cands[0] if cands else {}
                runner = cands[1] if len(cands) > 1 else {}
                w_name = winner.get("name", "Unknown")
                w_party = winner.get("party", "")
                w_pct = float(winner.get("pct") or 0.0)
                r_label = f"{runner.get('name','—')} ({float(runner.get('pct') or 0.0):.1f}%)" if runner else "Uncontested"
                features22.append({"type": "Feature",
                    "geometry": _mapping(row.geometry.simplify(0.005, preserve_topology=True)),
                    "properties": {
                        "_district": f"VA-{dist_num}",
                        "_winner": w_name,
                        "_party": w_party,
                        "_winner_pct": f"{w_pct:.1f}%",
                        "_runner": r_label,
                        "_color": _pct_to_band_color(w_pct, w_party) if race else "#cccccc",
                    }})
            except Exception:
                continue

        def _congress_2022_style(feat):
            return {"fillColor": feat["properties"].get("_color", "#cccccc"), "color": "#555", "weight": 1.0, "fillOpacity": 0.9}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features22},
            style_function=_congress_2022_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_winner", "_party", "_winner_pct", "_runner"],
                aliases=["District:", "Winner:", "Party:", "Winner %:", "Runner-Up:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        title = f"{year} Virginia - Congressional District Results"
        legend_type = "vote_share"

    elif layer in ("senate_2023_flip_2019", "hod_2023_flip_2021"):
        from shapely.geometry import mapping as _mapping
        chamber = "senate" if layer == "senate_2023_flip_2019" else "hod"
        baseline = SD_PRE2023_PARTY if chamber == "senate" else HOD_PRE2023_PARTY
        baseline_year = "Pre-2023"
        chamber_label = "State Senate" if chamber == "senate" else "House of Delegates"

        if chamber == "senate":
            if _va_sd_gdf is None:
                import address_lookup as _al2
                if _al2.va_sd is not None:
                    _va_sd_gdf = _al2.va_sd
                else:
                    _va_sd_gdf = gpd.read_file(os.path.join(BASE_DIR, "SCV Final 2021 Redistricting Plans", "SCV FINAL SD.shp"))
                    _va_sd_gdf = _va_sd_gdf.to_crs(epsg=4326)
                    _al2.va_sd = _va_sd_gdf
            gdf = _va_sd_gdf
        else:
            if _va_hod_gdf is None:
                import address_lookup as _al2
                if _al2.va_hod is not None:
                    _va_hod_gdf = _al2.va_hod
                else:
                    _va_hod_gdf = gpd.read_file(os.path.join(BASE_DIR, "SCV Final 2021 Redistricting Plans", "SCV FINAL HOD.shp"))
                    _va_hod_gdf = _va_hod_gdf.to_crs(epsg=4326)
                    _al2.va_hod = _va_hod_gdf
            gdf = _va_hod_gdf

        results_2023 = _load_2023_results().get(chamber, {})
        for _, row in gdf.iterrows():
            d = int(row["DISTRICT"])
            race = results_2023.get(d, {})
            candidates = race.get("candidates", [])
            winner = candidates[0] if candidates else {}
            winner_name = winner.get("name", "No data")
            party_2023 = _normalize_major_party(winner.get("party", ""))
            party_prior = baseline.get(d, "Unknown")
            flipped = party_prior != party_2023 and party_prior != "Unknown"
            if flipped and party_2023 == "Democrat":
                status = "Flipped Democratic"
            elif flipped and party_2023 == "Republican":
                status = "Flipped Republican"
            elif party_2023 == "Democrat":
                status = "Held Democratic"
            elif party_2023 == "Republican":
                status = "Held Republican"
            else:
                status = "No data"

            features.append({"type": "Feature",
                "geometry": _mapping(row.geometry.simplify(0.01, preserve_topology=True)),
                "properties": {
                    "_district": f"{chamber_label} District {d}",
                    "_winner": winner_name,
                    "_status": status,
                    "_prior": party_prior,
                    "_2023": party_2023,
                }})

        def state_leg_2023_flip_style(feat):
            status = feat["properties"].get("_status", "")
            if status == "Flipped Democratic":
                return {"fillColor": "#1a52c8", "color": "#111", "weight": 1.5, "fillOpacity": 0.85}
            if status == "Flipped Republican":
                return {"fillColor": "#c8102e", "color": "#111", "weight": 1.5, "fillOpacity": 0.85}
            if status == "Held Democratic":
                return {"fillColor": "#6b9fd4", "color": "#555", "weight": 0.4, "fillOpacity": 0.35}
            if status == "Held Republican":
                return {"fillColor": "#e8807a", "color": "#555", "weight": 0.4, "fillOpacity": 0.35}
            return {"fillColor": "#999999", "color": "#555", "weight": 0.4, "fillOpacity": 0.2}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=state_leg_2023_flip_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_winner", "_status", "_prior", "_2023"],
                aliases=["District:", "2023 Winner:", "Result:", f"{baseline_year} Party:", "2023 Party:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        title = f"2023 Virginia {chamber_label} — Seat Flips vs Pre-Election Party"
        legend_type = "flip"

    elif layer in ("pres_flip", "gov_flip", "gov_2025_flip"):
        if layer == "pres_flip":
            gj = _build_pres_2016_2020_flip_geojson()
            title = "2016 → 2020 Virginia President — Locality Flips"
            tip_fields = ["NAME", "_flip_status", "_winner_2016", "_winner_2020", "_party_2016", "_party_2020"]
            tip_aliases = ["Locality:", "Result:", "2016 Winner:", "2020 Winner:", "2016 Party:", "2020 Party:"]
        elif layer == "gov_flip":
            gj = _build_locality_office_flip_geojson("2017", "2021", "Governor")
            title = "2017 → 2021 Virginia Governor — Locality Flips"
            tip_fields = ["NAME", "_flip_status", "_start_winner", "_end_winner", "_start_party", "_end_party"]
            tip_aliases = ["Locality:", "Result:", "2017 Winner:", "2021 Winner:", "2017 Party:", "2021 Party:"]
        else:
            gj = _build_locality_office_flip_geojson("2021", "2025", "Governor")
            title = "2021 → 2025 Virginia Governor — Locality Flips"
            tip_fields = ["NAME", "_flip_status", "_start_winner", "_end_winner", "_start_party", "_end_party"]
            tip_aliases = ["Locality:", "Result:", "2021 Winner:", "2025 Winner:", "2021 Party:", "2025 Party:"]

        def _locality_flip_style(feat):
            s = feat["properties"].get("_flip_status", "")
            if s == "Flipped Democratic":   return {"fillColor": "#1a52c8", "color": "#111", "weight": 1.0, "fillOpacity": 0.85}
            elif s == "Flipped Republican": return {"fillColor": "#c8102e", "color": "#111", "weight": 1.0, "fillOpacity": 0.85}
            elif s == "Held Democratic":    return {"fillColor": "#6b9fd4", "color": "#555", "weight": 0.4, "fillOpacity": 0.4}
            elif s == "Held Republican":    return {"fillColor": "#e8807a", "color": "#555", "weight": 0.4, "fillOpacity": 0.4}
            return {"fillColor": "#cccccc", "color": "#555", "weight": 0.4, "fillOpacity": 0.3}

        folium.GeoJson(
            gj,
            style_function=_locality_flip_style,
            tooltip=folium.GeoJsonTooltip(
                fields=tip_fields, aliases=tip_aliases,
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        legend_type = "flip"

    elif layer == "locality_baseline":
        gj = _build_locality_baseline_geojson()

        def _baseline_style(feat):
            return {"fillColor": feat["properties"].get("_bl_color", "#cccccc"),
                    "color": "#666", "weight": 0.5, "fillOpacity": 0.88}

        folium.GeoJson(
            gj,
            style_function=_baseline_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_bl_locality", "_bl_lean", "_bl_tpv_d", "_bl_d_pct", "_bl_r_pct", "_bl_n"],
                aliases=["Locality:", "Partisan Lean:", "Avg D Two-Party %:", "Avg D Raw %:", "Avg R Raw %:", "Races:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        title = "Virginia Partisan Baseline — Average of 12 Statewide Races (2016–2024)"
        legend_type = "baseline"

    elif layer in ("congress_midterm_flip", "hod_state_flip", "hod_2019_flip_2017"):
        if layer == "congress_midterm_flip":
            gj = _build_congress_flip_geojson("2018", "2022")
            title = "2018 → 2022 Virginia U.S. House — District Flips"
            tip_fields = ["DISTRICTN", "_flip_status", "_start_winner", "_end_winner", "_start_party", "_end_party"]
            tip_aliases = ["District:", "Result:", "2018 Winner:", "2022 Winner:", "2018 Party:", "2022 Party:"]
        elif layer == "hod_2019_flip_2017":
            gj = _build_hod_flip_geojson("2017", "2019")
            title = "2017 → 2019 Virginia HOD — District Flips"
            tip_fields = ["DISTRICTN", "_flip_status", "_start_winner", "_end_winner", "_start_party", "_end_party"]
            tip_aliases = ["District:", "Result:", "2017 Winner:", "2019 Winner:", "2017 Party:", "2019 Party:"]
        else:
            gj = _build_hod_2017_2021_flip_geojson()
            title = "2019 → 2021 Virginia HOD — District Flips"
            tip_fields = ["DISTRICTN", "_flip_status", "_start_winner", "_end_winner", "_start_party", "_end_party"]
            tip_aliases = ["District:", "Result:", "2019 Winner:", "2021 Winner:", "2019 Party:", "2021 Party:"]

        def _district_flip_style(feat):
            s = feat["properties"].get("_flip_status", "")
            if s == "Flipped Democratic":   return {"fillColor": "#1a52c8", "color": "#111", "weight": 1.5, "fillOpacity": 0.85}
            elif s == "Flipped Republican": return {"fillColor": "#c8102e", "color": "#111", "weight": 1.5, "fillOpacity": 0.85}
            elif s == "Held Democratic":    return {"fillColor": "#6b9fd4", "color": "#555", "weight": 0.5, "fillOpacity": 0.4}
            elif s == "Held Republican":    return {"fillColor": "#e8807a", "color": "#555", "weight": 0.5, "fillOpacity": 0.4}
            return {"fillColor": "#cccccc", "color": "#555", "weight": 0.4, "fillOpacity": 0.3}

        folium.GeoJson(
            gj,
            style_function=_district_flip_style,
            tooltip=folium.GeoJsonTooltip(
                fields=tip_fields, aliases=tip_aliases,
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        legend_type = "flip"

    elif layer in ("pres_2020_2024_flip", "congress_2024_flip"):
        if layer == "pres_2020_2024_flip":
            gj = _build_pres_2020_2024_flip_geojson()
            title = "2020 → 2024 Virginia President — Locality Flips"
            tip_fields = ["NAME", "_flip_status", "_winner_2020", "_winner_2024", "_party_2020", "_party_2024"]
            tip_aliases = ["Locality:", "Result:", "2020 Winner:", "2024 Winner:", "2020 Party:", "2024 Party:"]
        else:
            gj = _build_congress_flip_geojson("2022", "2024")
            title = "2022 → 2024 Virginia U.S. House — District Flips"
            tip_fields = ["DISTRICTN", "_flip_status", "_start_winner", "_end_winner", "_start_party", "_end_party"]
            tip_aliases = ["District:", "Result:", "2022 Winner:", "2024 Winner:", "2022 Party:", "2024 Party:"]

        def _flip_2024_style(feat):
            s = feat["properties"].get("_flip_status", "")
            if s == "Flipped Democratic":   return {"fillColor": "#1a52c8", "color": "#111", "weight": 1.5, "fillOpacity": 0.85}
            elif s == "Flipped Republican": return {"fillColor": "#c8102e", "color": "#111", "weight": 1.5, "fillOpacity": 0.85}
            elif s == "Held Democratic":    return {"fillColor": "#6b9fd4", "color": "#555", "weight": 0.5, "fillOpacity": 0.4}
            elif s == "Held Republican":    return {"fillColor": "#e8807a", "color": "#555", "weight": 0.5, "fillOpacity": 0.4}
            return {"fillColor": "#cccccc", "color": "#555", "weight": 0.4, "fillOpacity": 0.3}

        folium.GeoJson(
            gj,
            style_function=_flip_2024_style,
            tooltip=folium.GeoJsonTooltip(
                fields=tip_fields, aliases=tip_aliases,
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        legend_type = "flip"

    elif layer in ("senate_2023_results", "hod_2023_results"):
        from shapely.geometry import mapping as _mapping
        chamber = "senate" if layer == "senate_2023_results" else "hod"
        gdf = None
        if chamber == "senate":
            if _va_sd_gdf is None:
                import address_lookup as _al2
                if _al2.va_sd is not None:
                    _va_sd_gdf = _al2.va_sd
                else:
                    _va_sd_gdf = gpd.read_file(os.path.join(BASE_DIR, "SCV Final 2021 Redistricting Plans", "SCV FINAL SD.shp"))
                    _va_sd_gdf = _va_sd_gdf.to_crs(epsg=4326)
                    _al2.va_sd = _va_sd_gdf
            gdf = _va_sd_gdf
        else:
            if _va_hod_gdf is None:
                import address_lookup as _al2
                if _al2.va_hod is not None:
                    _va_hod_gdf = _al2.va_hod
                else:
                    _va_hod_gdf = gpd.read_file(os.path.join(BASE_DIR, "SCV Final 2021 Redistricting Plans", "SCV FINAL HOD.shp"))
                    _va_hod_gdf = _va_hod_gdf.to_crs(epsg=4326)
                    _al2.va_hod = _va_hod_gdf
            gdf = _va_hod_gdf

        results_2023 = _load_2023_results().get(chamber, {})
        for _, row in gdf.iterrows():
            try:
                d = int(row["DISTRICT"])
                race = results_2023.get(d, {})
                cands = race.get("candidates", [])
                winner = cands[0] if cands else {}
                runner = cands[1] if len(cands) > 1 else {}
                w_name = winner.get("name", "No data")
                w_party = winner.get("party", "")
                w_pct = float(winner.get("pct", 0.0))
                r_name = runner.get("name", "") if runner else ""
                r_pct = float(runner.get("pct", 0.0)) if runner else 0.0
                features.append({"type": "Feature",
                    "geometry": _mapping(row.geometry.simplify(0.01, preserve_topology=True)),
                    "properties": {
                        "DISTRICT": d,
                        "_district": f"{'Senate' if chamber == 'senate' else 'HOD'} District {d}",
                        "_winner": w_name,
                        "_party": w_party,
                        "_pct": f"{w_pct:.1f}%",
                        "_runner": f"{r_name} ({r_pct:.1f}%)" if r_name else "Uncontested",
                        "_color": _pct_to_band_color(w_pct, w_party) if cands else "#cccccc",
                    }})
            except Exception:
                continue

        def state_leg_2023_style(feat):
            return {"fillColor": feat["properties"].get("_color", "#cccccc"), "color": "#555", "weight": 0.5, "fillOpacity": 0.9}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=state_leg_2023_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_winner", "_party", "_pct", "_runner"],
                aliases=["District:", "Winner:", "Party:", "Vote Share:", "Runner-Up:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        title = (
            "2023 Virginia State Senate — Election Results"
            if chamber == "senate"
            else "2023 Virginia House of Delegates — Election Results"
        )
        legend_type = "vote_share"

    elif layer == "hod_2021_results":
        from shapely.geometry import mapping as _mapping
        global _va_old_hod_gdf
        if _va_old_hod_gdf is None:
            _va_old_hod_gdf = gpd.read_file(os.path.join(BASE_DIR, "tl_2021_51_sldl", "tl_2021_51_sldl.shp"))
            _va_old_hod_gdf = _va_old_hod_gdf.to_crs(epsg=4326)

        hod_data = _load_2021_results().get("hod", {})
        for _, row in _va_old_hod_gdf.iterrows():
            try:
                d = int(row["SLDLST"])
                race = hod_data.get(str(d), hod_data.get(d, {}))
                cands = race.get("candidates", [])
                winner = cands[0] if cands else {}
                runner = cands[1] if len(cands) > 1 else {}
                w_name = winner.get("name", "No data")
                w_party = winner.get("party", "")
                w_pct = float(winner.get("pct", 0.0))
                r_name = runner.get("name", "—") if runner else "—"
                r_pct = float(runner.get("pct", 0.0)) if runner else 0.0
                runner_label = f"{r_name} ({r_pct:.1f}%)" if r_name != "—" else "Uncontested"
                features.append({"type": "Feature",
                    "geometry": _mapping(row.geometry.simplify(0.01, preserve_topology=True)),
                    "properties": {
                        "DISTRICT": d,
                        "_district": f"HOD District {d}",
                        "_winner": w_name,
                        "_party": w_party,
                        "_pct": f"{w_pct:.1f}%",
                        "_runner": runner_label,
                        "_color": _pct_to_band_color(w_pct, w_party) if cands else "#cccccc",
                    }})
            except Exception:
                continue

        def hod_2021_results_style(feat):
            return {"fillColor": feat["properties"].get("_color", "#cccccc"), "color": "#555", "weight": 0.5, "fillOpacity": 0.9}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=hod_2021_results_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_winner", "_party", "_pct", "_runner"],
                aliases=["District:", "Winner:", "Party:", "Vote Share:", "Runner-Up:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        title = "2021 Virginia House of Delegates — Election Results"
        legend_type = "vote_share"

    elif layer == "hod_results":
        from shapely.geometry import mapping as _mapping
        if _va_hod_gdf is None:
            import address_lookup as _al2
            if _al2.va_hod is not None:
                _va_hod_gdf = _al2.va_hod
            else:
                _va_hod_gdf = gpd.read_file(os.path.join(BASE_DIR, "SCV Final 2021 Redistricting Plans", "SCV FINAL HOD.shp"))
                _va_hod_gdf = _va_hod_gdf.to_crs(epsg=4326)
                _al2.va_hod = _va_hod_gdf
        results = _load_2025_results()
        hod_data = results.get("hod", {})
        for _, row in _va_hod_gdf.iterrows():
            try:
                d = int(row["DISTRICT"])
                race = hod_data.get(d, {})
                cands = race.get("candidates", [])
                winner = cands[0] if cands else {}
                runner = cands[1] if len(cands) > 1 else {}
                w_name = winner.get("name", "Uncontested")
                w_party = winner.get("party", "")
                w_pct = float(winner.get("pct", 0.0))
                r_name = runner.get("name", "—") if runner else "—"
                r_pct = float(runner.get("pct", 0.0)) if runner else 0.0
                runner_label = f"{r_name} ({r_pct:.1f}%)" if r_name != "—" else "Uncontested"
                features.append({"type": "Feature",
                    "geometry": _mapping(row.geometry.simplify(0.01, preserve_topology=True)),
                    "properties": {"DISTRICT": d,
                        "_district": f"HOD District {d}",
                        "_winner": w_name,
                        "_party": w_party,
                        "_pct": f"{w_pct:.1f}%",
                        "_runner": runner_label,
                        "_color": _pct_to_band_color(w_pct, w_party) if cands else "#cccccc"}})
            except Exception:
                continue

        def hod_results_style(feat):
            return {"fillColor": feat["properties"].get("_color", "#cccccc"), "color": "#555", "weight": 0.5, "fillOpacity": 0.9}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=hod_results_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_winner", "_party", "_pct", "_runner"],
                aliases=["District:", "Winner:", "Party:", "Vote Share:", "Runner-Up:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        title = "2025 Virginia House of Delegates — Election Results"
        legend_type = "vote_share"

    # Rep name label at centroid of the user's highlighted district
    highlighted = [f for f in features if f["properties"].get("_highlight")] if district else []
    if highlighted:
        from shapely.geometry import shape as _shape
        try:
            centroid = _shape(highlighted[0]["geometry"]).centroid
            name_field = "_delegate" if layer == "hod" else "_senator" if layer == "sd" else "_rep"
            rep_name = highlighted[0]["properties"].get(name_field, "")
            party = features[0]["properties"].get("_party", "")
            bg = "#1a52c8" if party == "Democrat" else "#e03030" if party == "Republican" else "#555"
            folium.Marker(
                location=[centroid.y, centroid.x],
                icon=folium.DivIcon(
                    html=f'<div style="font-family:Arial;font-size:12px;font-weight:700;color:#fff;'
                         f'background:{bg};padding:4px 10px;border-radius:4px;'
                         f'white-space:nowrap;box-shadow:1px 1px 4px rgba(0,0,0,0.3);">'
                         f'{rep_name}</div>',
                    icon_size=(200, 30),
                    icon_anchor=(100, 15),
                ),
            ).add_to(m)
        except Exception:
            pass

    # Fit map to the user's highlighted district (or all of Virginia if no district)
    target_features = [f for f in features if f["properties"].get("_highlight")] if district else features
    if target_features:
        from shapely.geometry import shape as _shape
        from shapely.ops import unary_union
        try:
            geoms = [_shape(f["geometry"]) for f in target_features]
            bounds = unary_union(geoms).bounds  # (minx, miny, maxx, maxy)
            m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])
        except Exception:
            pass

    # User location pin — use CircleMarker to avoid Font Awesome dependency
    if user_lat is not None and user_lng is not None:
        folium.CircleMarker(
            location=[user_lat, user_lng],
            radius=10,
            color="white",
            weight=3,
            fill=True,
            fill_color="#c8102e",
            fill_opacity=1.0,
            popup="Your Location",
            tooltip="📍 Your Location",
        ).add_to(m)

    if legend_type == "vote_share":
        legend_box = (
            f"<div style='position:fixed;bottom:30px;left:30px;z-index:1000;"
            f"background:white;padding:12px 16px;border-radius:8px;"
            f"box-shadow:2px 2px 8px rgba(0,0,0,.3);font-family:Arial;font-size:12px'>"
            f"<b style='font-size:13px'>{title}</b>"
            f"{_vote_share_legend_inner()}"
            f"</div>"
        )
    elif legend_type == "baseline":
        legend_box = """
        <div style='position:fixed;bottom:30px;left:30px;z-index:1000;
             background:white;padding:12px 16px;border-radius:8px;
             box-shadow:2px 2px 8px rgba(0,0,0,.3);font-family:Arial;font-size:11px;min-width:180px'>
          <b style='font-size:12px;display:block;margin-bottom:8px'>Avg D Two-Party Vote Share</b>
          <div style='display:flex;flex-direction:column;gap:3px'>
            <div style='display:flex;align-items:center;gap:6px'>
              <span style='display:inline-block;width:20px;height:13px;background:#1545ff;border:1px solid #aaa'></span>
              <span>D+25% or more</span>
            </div>
            <div style='display:flex;align-items:center;gap:6px'>
              <span style='display:inline-block;width:20px;height:13px;background:#5f8aff;border:1px solid #aaa'></span>
              <span>D+15% to D+25%</span>
            </div>
            <div style='display:flex;align-items:center;gap:6px'>
              <span style='display:inline-block;width:20px;height:13px;background:#b0c8ff;border:1px solid #aaa'></span>
              <span>D+5% to D+15%</span>
            </div>
            <div style='display:flex;align-items:center;gap:6px'>
              <span style='display:inline-block;width:20px;height:13px;background:#e0ebff;border:1px solid #aaa'></span>
              <span>D+0% to D+5%</span>
            </div>
            <div style='display:flex;align-items:center;gap:6px'>
              <span style='display:inline-block;width:20px;height:13px;background:#ffe0e0;border:1px solid #aaa'></span>
              <span>R+0% to R+5%</span>
            </div>
            <div style='display:flex;align-items:center;gap:6px'>
              <span style='display:inline-block;width:20px;height:13px;background:#ffb0b0;border:1px solid #aaa'></span>
              <span>R+5% to R+15%</span>
            </div>
            <div style='display:flex;align-items:center;gap:6px'>
              <span style='display:inline-block;width:20px;height:13px;background:#ff6060;border:1px solid #aaa'></span>
              <span>R+15% to R+25%</span>
            </div>
            <div style='display:flex;align-items:center;gap:6px'>
              <span style='display:inline-block;width:20px;height:13px;background:#ff1515;border:1px solid #aaa'></span>
              <span>R+25% or more</span>
            </div>
          </div>
          <div style='margin-top:6px;font-size:10px;color:#666'>Avg of 12 races, 2016–2024</div>
        </div>"""
    elif legend_type == "flip":
        legend_box = """
        <div style='position:fixed;bottom:30px;left:30px;z-index:1000;
             background:white;padding:12px 16px;border-radius:8px;
             box-shadow:2px 2px 8px rgba(0,0,0,.3);font-family:Arial;font-size:12px'>
          <div style='display:flex;flex-direction:column;gap:4px;margin-top:4px'>
            <div style='display:flex;align-items:center;gap:8px'>
              <span style='display:inline-block;width:20px;height:14px;background:#1a52c8;border:1px solid #aaa'></span>
              <span style='font-size:11px'>Flipped Democratic</span>
            </div>
            <div style='display:flex;align-items:center;gap:8px'>
              <span style='display:inline-block;width:20px;height:14px;background:#c8102e;border:1px solid #aaa'></span>
              <span style='font-size:11px'>Flipped Republican</span>
            </div>
            <div style='display:flex;align-items:center;gap:8px'>
              <span style='display:inline-block;width:20px;height:14px;background:#6b9fd4;border:1px solid #aaa'></span>
              <span style='font-size:11px'>Held Democratic</span>
            </div>
            <div style='display:flex;align-items:center;gap:8px'>
              <span style='display:inline-block;width:20px;height:14px;background:#e8807a;border:1px solid #aaa'></span>
              <span style='font-size:11px'>Held Republican</span>
            </div>
          </div>
        </div>"""
    else:
        legend_box = """
        <div style='position:fixed;bottom:40px;left:40px;z-index:1000;
             background:white;padding:12px 16px;border-radius:8px;
             box-shadow:2px 2px 8px rgba(0,0,0,.3);font-family:Arial;font-size:13px;line-height:1.8'>
          <span style='background:#1a52c8;color:white;padding:2px 10px;border-radius:3px'>Democrat</span>
          &nbsp;
          <span style='background:#e03030;color:white;padding:2px 10px;border-radius:3px'>Republican</span>
          <br><small style='color:#666'>Hover for details</small>
        </div>"""

    baseline_summary_html = ""
    baseline_summary = locals().get("baseline_summary", "")
    if baseline_summary:
        baseline_summary_html = (
            f"<div style=\"font-size:11px;font-weight:600;letter-spacing:0.02em;"
            f"opacity:0.9;margin-top:3px;text-transform:none;\">"
            f"{escape(baseline_summary)}"
            f"</div>"
        )

    flip_summary_html = ""
    if legend_type == "flip":
        dem_flips = 0
        rep_flips = 0
        summary_features = features or (gj.get("features", []) if isinstance(locals().get("gj"), dict) else [])
        for feat in summary_features:
            status = feat.get("properties", {}).get("_flip_status") or feat.get("properties", {}).get("_status", "")
            if status == "Flipped Democratic":
                dem_flips += 1
            elif status == "Flipped Republican":
                rep_flips += 1
        net = dem_flips - rep_flips
        if net > 0:
            summary = f"Net Democratic gain +{net}"
        elif net < 0:
            summary = f"Net Republican gain +{abs(net)}"
        else:
            summary = "No net party change"
        flip_summary_html = (
            f"<div style=\"font-size:11px;font-weight:600;letter-spacing:0.02em;"
            f"opacity:0.9;margin-top:3px;text-transform:none;\">"
            f"{escape(summary)} &nbsp;|&nbsp; D flips: {dem_flips} &nbsp;|&nbsp; R flips: {rep_flips}"
            f"</div>"
        )

    m.get_root().html.add_child(folium.Element(f"""
    <div style="position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:1000;
         background:#0d1b2a;color:white;padding:8px 20px;border-radius:6px;
         box-shadow:2px 2px 8px rgba(0,0,0,.4);font-family:Arial;font-size:14px;
         font-weight:700;letter-spacing:0.05em;white-space:nowrap;pointer-events:none;text-align:center;">
      {title}
      {baseline_summary_html}
      {flip_summary_html}
    </div>
    {legend_box}
    """))
    rendered = m.get_root().render()
    bounds_js = (
        f"<script>"
        f"{map_var}.setMaxBounds([[35.9,-84.8],[39.7,-74.9]]);"
        f"{map_var}.options.maxBoundsViscosity=1.0;"
        f"</script>"
    )
    return rendered.replace("</html>", bounds_js + "</html>")


# District maps are built lazily on first request to keep startup memory low
_district_maps: dict[str, str] = {}
_statewide_bubble_maps: dict[str, str] = {}


def _statewide_results_for_bubbles(year: str, office: str) -> dict:
    year = str(year)
    if year == "2025":
        return _load_2025_statewide_locality_results(office)
    data = _load_results_for_year(year)
    return data.get("locality_results", {}).get(office, {})


def _build_statewide_bubble_map(year: str, office: str) -> str:
    year = str(year)
    race_data = _statewide_results_for_bubbles(year, office)
    data = _load_results_for_year(year)
    party_by_candidate = {}
    for race in data.get("statewide", []):
        if (race.get("race") or race.get("office")) == office:
            for candidate in race.get("candidates", []):
                party_by_candidate[candidate.get("name", "")] = candidate.get("party", "")

    def _race_entry(result):
        if isinstance(result, dict) and "winner" in result:
            candidates = result.get("candidates", [])
            if not candidates:
                candidates = [c for c in (result.get("dem", {}), result.get("rep", {})) if c]
            winner = result.get("winner", {})
            if not isinstance(winner, dict):
                winner_name = str(winner or "")
                winner = next((c for c in candidates if c.get("name") == winner_name), {})
            return {
                "total": int(result.get("total") or sum(int(c.get("votes") or 0) for c in candidates)),
                "winner": winner.get("name", "No data"),
                "party": _normalize_major_party(winner.get("party", "")),
                "pct": float(winner.get("pct") or result.get("winner_pct") or 0.0),
            }
        if isinstance(result, dict) and "candidates" in result:
            candidates = result.get("candidates", [])
            winner = candidates[0] if candidates else {}
            return {
                "total": int(result.get("total") or sum(int(c.get("votes") or 0) for c in candidates)),
                "winner": winner.get("name", "No data"),
                "party": _normalize_major_party(winner.get("party", "")),
                "pct": float(winner.get("pct") or 0.0),
            }
        if isinstance(result, dict):
            sorted_cands = sorted(result.items(), key=lambda item: -float(item[1] or 0))
            winner_name, winner_pct = sorted_cands[0] if sorted_cands else ("No data", 0.0)
            return {
                "total": 0,
                "winner": winner_name,
                "party": _normalize_major_party(party_by_candidate.get(winner_name, "")),
                "pct": float(winner_pct or 0.0),
            }
        return {"total": 0, "winner": "No data", "party": "Unknown", "pct": 0.0}

    lookup = {_normalize_locality_key(k): _race_entry(v) for k, v in race_data.items()}
    counties = json.loads(json.dumps(_load_va_counties_geojson()))
    features = counties.get("features", [])
    max_total = 0
    for feat in features:
        props = feat.setdefault("properties", {})
        locality = f"{props.get('NAME', '')} {props.get('LSAD', '')}".strip()
        result = lookup.get(_normalize_locality_key(locality), {})
        total = int(result.get("total") or 0)
        max_total = max(max_total, total)
        props["_locality"] = locality.title()
        props["_bubble_total"] = total
        props["_bubble_winner"] = result.get("winner", "No data")
        props["_bubble_party"] = result.get("party", "Unknown")
        props["_bubble_pct"] = float(result.get("pct") or 0.0)

    m = folium.Map(location=[37.5, -79.0], zoom_start=7, tiles="CartoDB positron", min_zoom=6)
    map_var = m.get_name()
    folium.GeoJson(
        counties,
        style_function=lambda _f: {"fillColor": "#f4f1eb", "color": "#9a9488", "weight": 0.6, "fillOpacity": 0.22},
    ).add_to(m)

    for feat in features:
        props = feat.get("properties", {})
        total = int(props.get("_bubble_total") or 0)
        if not props.get("_bubble_winner") or props.get("_bubble_winner") == "No data":
            continue
        try:
            centroid = shape(feat.get("geometry", {})).centroid
        except Exception:
            continue
        party = props.get("_bubble_party", "")
        color = "#1a52c8" if party == "Democrat" else "#c8102e" if party == "Republican" else "#7a7468"
        radius = 11 if not max_total else 4 + math.sqrt(total / max_total) * 34
        total_label = f"{total:,}" if total else "Total unavailable"
        folium.CircleMarker(
            location=[centroid.y, centroid.x],
            radius=radius,
            color="white",
            weight=1.5,
            fill=True,
            fill_color=color,
            fill_opacity=0.76,
            tooltip=(
                f"<b>{escape(props.get('_locality', ''))}</b><br>"
                f"Winner: <b>{escape(props.get('_bubble_winner', 'No data'))}</b> ({escape(party)})<br>"
                f"Winner share: {props.get('_bubble_pct', 0):.1f}%<br>"
                f"Votes: <b>{total_label}</b>"
            ),
        ).add_to(m)

    # Build size-key bubbles at 15%, 50%, 100% of max_total
    def _fmt_votes(n):
        if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
        if n >= 1_000:     return f"{round(n/1_000)}k"
        return str(n)
    _key_fracs = [0.15, 0.50, 1.0]
    _key_items_html = ""
    for frac in _key_fracs:
        ref_total = int(max_total * frac)
        r = 4 + math.sqrt(frac) * 34
        sz = int(r * 2 + 4)
        cx = cy = sz // 2
        label = _fmt_votes(ref_total) if ref_total else "—"
        _key_items_html += (
            f"<div style='display:flex;flex-direction:column;align-items:center;gap:2px'>"
            f"<svg width='{sz}' height='{sz}'>"
            f"<circle cx='{cx}' cy='{cy}' r='{r:.1f}' fill='#888' fill-opacity='0.45' stroke='white' stroke-width='1.5'/>"
            f"</svg>"
            f"<span style='font-size:10px;color:#555;white-space:nowrap'>≈ {label} votes</span>"
            f"</div>"
        )

    title = f"{year} Virginia {office} - Statewide Vote Bubbles"
    m.get_root().html.add_child(folium.Element(f"""
    <div style="position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:1000;
         background:#0d1b2a;color:white;padding:8px 20px;border-radius:6px;
         box-shadow:2px 2px 8px rgba(0,0,0,.4);font-family:Arial;font-size:14px;
         font-weight:700;letter-spacing:0.05em;white-space:nowrap;pointer-events:none;">{escape(title)}</div>
    <div style="position:fixed;bottom:36px;left:36px;z-index:1000;background:white;padding:12px 16px;border-radius:8px;
         box-shadow:2px 2px 8px rgba(0,0,0,.3);font-family:Arial;font-size:12px">
      <span style="background:#1a52c8;color:white;padding:2px 8px;border-radius:3px;font-size:11px">Democratic winner</span>
      &nbsp;<span style="background:#c8102e;color:white;padding:2px 8px;border-radius:3px;font-size:11px">Republican winner</span>
      <div style="margin-top:8px;font-weight:bold;color:#444;font-size:11px;letter-spacing:0.04em">CIRCLE SIZE = VOTES CAST</div>
      <div style="display:flex;align-items:flex-end;gap:14px;margin-top:4px">
        {_key_items_html}
      </div>
    </div>
    """))
    rendered = m.get_root().render()
    bounds_js = f"<script>{map_var}.setMaxBounds([[35.9,-84.8],[39.7,-74.9]]);{map_var}.options.maxBoundsViscosity=1.0;</script>"
    return rendered.replace("</html>", bounds_js + "</html>")


@app.get("/statewide-bubble-map", response_class=HTMLResponse)
def statewide_bubble_map(year: str, office: str, v: str = None):
    key = f"{year}:{office}"
    if key not in _statewide_bubble_maps:
        _statewide_bubble_maps[key] = _build_statewide_bubble_map(year, office)
    return _statewide_bubble_maps[key]


@app.get("/district-map", response_class=HTMLResponse)
def district_map(layer: str = "congressional", lat: float = None, lng: float = None, district: int = None):
    if layer in ("congressional", "hod_results", "hod_flip", "gov_results", "ltgov_results", "ag_results", "pres_2024", "senate_2024", "congress_2024", "senate_2023_results", "hod_2023_results", "senate_2023_flip_2019", "hod_2023_flip_2021", "gov_2021", "ltgov_2021", "ag_2021", "hod_2021_results", "congress_2022", "gov_2017", "ltgov_2017", "ag_2017", "senate_2019_results", "hod_2019_results", "pres_2016", "congress_2016", "pres_2020", "senate_2020", "congress_2020", "senate_2018", "congress_2018", "pres_flip", "gov_flip", "gov_2025_flip", "congress_midterm_flip", "hod_state_flip", "pres_2020_2024_flip", "congress_2024_flip") and lat is None:
        if layer not in _district_maps:
            try:
                _district_maps[layer] = _build_district_map(layer)
            except Exception as e:
                return f"<p style='font-family:sans-serif;padding:40px'>Could not build map: {escape(str(e))}</p>"
        return _district_maps[layer]
    else:
        try:
            return _build_district_map(layer, user_lat=lat, user_lng=lng, district=district)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return f"<p style='font-family:sans-serif;padding:40px'>Could not build {escape(layer)} map: {escape(str(e))}</p>"


def _fetch_vote_context(question: str, bioguide_ids: list[str], limit: int = 8) -> str:
    """Return a formatted block of relevant votes for the chat system prompt."""
    if not _VOTES_CACHE or not bioguide_ids:
        return ""

    q_lower = question.lower()

    # Topic keywords → vote question substrings to match
    topic_hints = {
        "gun": ["weapon", "firearm", "gun", "assault"],
        "health": ["health", "medicaid", "medicare", "aca", "obamacare"],
        "budget": ["budget", "appropriat", "spending", "debt", "fiscal"],
        "immigration": ["immigr", "border", "asylum", "dhs"],
        "tax": ["tax", "revenue", "irs"],
        "education": ["education", "school", "student", "pell"],
        "environment": ["environment", "climate", "energy", "epa", "clean"],
        "defense": ["defense", "military", "ndaa", "armed"],
        "infrastructure": ["infrastructure", "transpor", "highway", "broadband"],
        "social security": ["social security", "medicare", "retirement"],
    }
    matched_hints: list[str] = []
    for kw, hints in topic_hints.items():
        if kw in q_lower:
            matched_hints.extend(hints)

    lines: list[str] = []
    for bgid in bioguide_ids:
        votes = _VOTES_CACHE.get(bgid, [])
        member = _MEMBER_CACHE.get(bgid, {})
        name = member.get("name", bgid)

        # Score each vote by relevance to question
        scored: list[tuple[int, dict]] = []
        for v in votes:
            q_text = (v.get("question") or "").lower()
            bill   = (v.get("bill") or "").lower()
            score  = 0

            # Direct keyword match in question text
            for w in re.findall(r"\b\w{4,}\b", q_lower):
                if w in q_text or w in bill:
                    score += 2

            # Topic hint match
            for hint in matched_hints:
                if hint in q_text:
                    score += 3

            # Boost if user explicitly asked about this member
            name_lower = name.lower()
            last = name_lower.split()[-1] if name_lower.split() else ""
            if last and last in q_lower:
                score += 1

            if score > 0:
                scored.append((score, v))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:limit]

        if not top:
            # No topic match — include 3 most recent votes anyway if member is relevant
            top = [(0, v) for v in votes[:3]]

        if top:
            lines.append(f"\n{name} recent votes:")
            for _, v in top:
                yea_nay = v.get("member_vote", "")
                bill    = v.get("bill", "")
                q_text  = (v.get("question") or "")[:80]
                date    = (v.get("vote_date") or "")[:10]
                result  = v.get("result", "")
                lines.append(f"  {date} | {bill} | {q_text} | Voted: {yea_nay} | Outcome: {result}")

    if not lines:
        return ""
    return "VOTING RECORD (official roll-call data from congress.gov):\n" + "\n".join(lines)


# Industry keyword → topic keywords that suggest the user is asking about money in that area
_INDUSTRY_TOPIC_HINTS: dict[str, list[str]] = {
    "Defense":       ["defense", "military", "ndaa", "weapon", "pentagon", "war"],
    "Healthcare":    ["health", "medicaid", "medicare", "aca", "obamacare", "pharma", "drug"],
    "Energy":        ["energy", "oil", "gas", "coal", "climate", "fossil", "epa", "pipeline"],
    "Finance":       ["bank", "wall street", "financial", "investment", "securities", "credit"],
    "Technology":    ["tech", "cyber", "data", "privacy", "ai ", "internet", "broadband"],
    "Guns/NRA":      ["gun", "nra", "firearm", "weapon", "second amendment", "rifle"],
    "Labor":         ["union", "labor", "worker", "wage", "collective bargaining"],
    "Agriculture":   ["farm", "agri", "food", "crop", "usda"],
    "Real Estate":   ["real estate", "housing", "mortgage", "rent", "build"],
    "Transportation":["airline", "transport", "highway", "rail", "shipping"],
    "Telecom":       ["telecom", "cable", "internet", "spectrum", "broadband"],
}


def _fetch_finance_context(bioguide_ids: list[str], question: str) -> str:
    """Return a formatted PAC/industry donation block correlated with voting patterns."""
    if not _PAC_CACHE or not bioguide_ids:
        return ""

    q_lower = question.lower()
    money_keywords = ["fund", "donor", "pac", "money", "contribut", "financ", "pay", "sponsor", "lobbying"]
    is_money_question = any(kw in q_lower for kw in money_keywords)

    # Determine which industries the question touches
    relevant_industries: set[str] = set()
    for industry, hints in _INDUSTRY_TOPIC_HINTS.items():
        if any(h in q_lower for h in hints):
            relevant_industries.add(industry)

    lines: list[str] = []
    for bgid in bioguide_ids:
        pac_rows = _PAC_CACHE.get(bgid, [])
        if not pac_rows:
            continue
        member_name = pac_rows[0].get("member_name", bgid)
        cycle       = pac_rows[0].get("cycle", "")

        # Filter to relevant industries, or show top 5 if it's a money question
        if relevant_industries:
            filtered = [r for r in pac_rows if r["industry"] in relevant_industries]
        elif is_money_question:
            filtered = sorted(pac_rows, key=lambda r: r["total"], reverse=True)[:5]
        else:
            continue  # no finance question, no industry match — skip

        if not filtered:
            if is_money_question:
                filtered = sorted(pac_rows, key=lambda r: r["total"], reverse=True)[:5]
            else:
                continue

        lines.append(f"\n{member_name} campaign contributions ({cycle} cycle):")
        for r in filtered:
            donors = ", ".join(d["name"] for d in r["top_donors"][:3]) if r["top_donors"] else ""
            donor_str = f"  (top: {donors})" if donors else ""
            lines.append(f"  {r['industry']:20s}: ${r['total']:>10,.0f}{donor_str}")

        # Cross-reference: does voting record align with top donor industries?
        votes = _VOTES_CACHE.get(bgid, [])
        if votes and relevant_industries:
            corr_lines: list[str] = []
            for industry in relevant_industries:
                if not any(r["industry"] == industry for r in filtered):
                    continue
                hints = _INDUSTRY_TOPIC_HINTS.get(industry, [])
                related_votes = [
                    v for v in votes
                    if any(h in (v.get("question") or "").lower() for h in hints)
                ][:6]
                if related_votes:
                    yeas = sum(1 for v in related_votes if "yea" in (v.get("member_vote") or "").lower())
                    nays = len(related_votes) - yeas
                    corr_lines.append(
                        f"  {industry} votes: {yeas} Yea / {nays} Nay on {len(related_votes)} related bills"
                    )
            if corr_lines:
                lines.append(f"  Voting alignment:")
                lines.extend(corr_lines)

    if not lines:
        return ""
    return "CAMPAIGN FINANCE & INDUSTRY CORRELATION (source: FEC.gov, fec.gov/data):\n" + "\n".join(lines)


try:
    import cohere as _cohere
    _COHERE_CLIENT = _cohere.ClientV2(api_key=os.getenv("COHERE_API_KEY", ""))
    _COHERE_AVAILABLE = bool(os.getenv("COHERE_API_KEY"))
except Exception:
    _COHERE_CLIENT = None
    _COHERE_AVAILABLE = False


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _ensure_embeddings_column() -> None:
    """Add embedding column to va_news if missing."""
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.execute("ALTER TABLE va_news ADD COLUMN embedding TEXT")
        conn.commit()
        conn.close()
    except Exception:
        pass  # column already exists


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts using Cohere embed-english-v3.0."""
    if not _COHERE_AVAILABLE or not texts:
        return []
    try:
        resp = _COHERE_CLIENT.embed(
            texts=texts,
            model="embed-english-v3.0",
            input_type="search_document",
            embedding_types=["float"],
        )
        return resp.embeddings.float_
    except Exception:
        return []


def _embed_query(question: str) -> list[float]:
    """Embed a search query using Cohere."""
    if not _COHERE_AVAILABLE:
        return []
    try:
        resp = _COHERE_CLIENT.embed(
            texts=[question],
            model="embed-english-v3.0",
            input_type="search_query",
            embedding_types=["float"],
        )
        return resp.embeddings.float_[0]
    except Exception:
        return []


def backfill_news_embeddings(batch_size: int = 48) -> int:
    """Embed all va_news rows that don't have an embedding yet. Returns count embedded."""
    if not _COHERE_AVAILABLE or not os.path.exists(_POLLS_DB):
        return 0
    _ensure_embeddings_column()
    conn = sqlite3.connect(_POLLS_DB)
    rows = conn.execute(
        "SELECT article_id, title, gemini_json FROM va_news "
        "WHERE embedding IS NULL AND gemini_json IS NOT NULL"
    ).fetchall()

    embedded = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        texts = []
        for _, title, gjson in batch:
            try:
                d = json.loads(gjson)
                summary = d.get("summary") or ""
                headline = d.get("headline") or title or ""
                pols = " ".join(p.get("name", "") for p in (d.get("politicians") or []))
                topics = " ".join(d.get("topics") or [])
                texts.append(f"{headline}. {summary} {pols} {topics}".strip())
            except Exception:
                texts.append(title or "")

        vecs = _embed_texts(texts)
        if not vecs:
            break
        for (article_id, _, _), vec in zip(batch, vecs):
            conn.execute(
                "UPDATE va_news SET embedding=? WHERE article_id=?",
                (json.dumps(vec), article_id)
            )
        conn.commit()
        embedded += len(vecs)

    conn.close()
    return embedded


def _semantic_news_candidates(question: str, limit: int = 20) -> list[dict]:
    """Return articles ranked by semantic similarity using Cohere embeddings."""
    q_vec = _embed_query(question)
    if not q_vec:
        return []
    conn = sqlite3.connect(_POLLS_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT article_id, url, title, gemini_json, embedding FROM va_news "
        "WHERE embedding IS NOT NULL AND gemini_json IS NOT NULL "
        "ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT 200"
    ).fetchall()
    conn.close()

    scored = []
    for row in rows:
        try:
            vec = json.loads(row["embedding"])
            sim = _cosine_sim(q_vec, vec)
            d = json.loads(row["gemini_json"])
            scored.append((sim, d, row["url"], row["title"]))
        except Exception:
            continue

    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"score": s, "data": d, "url": u, "title": t} for s, d, u, t in scored[:limit]]


def _rerank_articles(question: str, candidates: list[dict], limit: int = 4) -> list[dict]:
    """Re-rank candidate articles using Cohere Rerank."""
    if not _COHERE_AVAILABLE or not candidates:
        return candidates[:limit]

    cache_key = _rerank_cache_key(question, candidates)
    cached_indexes = _get_cached_rerank(cache_key)
    if cached_indexes is not None:
        return [candidates[i] for i in cached_indexes if 0 <= i < len(candidates)]

    try:
        docs = []
        for c in candidates:
            d = c["data"]
            headline = d.get("headline") or c["title"] or ""
            summary = d.get("summary") or ""
            docs.append(f"{headline}. {summary}")

        resp = _COHERE_CLIENT.rerank(
            model="rerank-english-v3.0",
            query=question,
            documents=docs,
            top_n=limit,
        )
        top_indexes = [r.index for r in resp.results]
        _set_cached_rerank(cache_key, top_indexes)
        return [candidates[i] for i in top_indexes]
    except Exception:
        return candidates[:limit]


def _fetch_relevant_news(question: str, politician_names: list[str] | None = None, limit: int = 4) -> str:
    """Find relevant news using Cohere semantic search + rerank, falling back to keyword scoring."""
    if not os.path.exists(_POLLS_DB):
        return ""
    try:
        conn = sqlite3.connect(_POLLS_DB)
        tbl = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='va_news'").fetchone()
        conn.close()
        if not tbl:
            return ""

        if _COHERE_AVAILABLE:
            # Step 1: semantic search — finds conceptually related articles
            candidates = _semantic_news_candidates(question, limit=20)

            # Boost articles mentioning the user's known politicians
            if politician_names:
                pol_lower = [p.lower() for p in politician_names]
                for c in candidates:
                    pols = [p.get("name", "").lower() for p in (c["data"].get("politicians") or [])]
                    if any(any(pl in ap or ap in pl for ap in pols) for pl in pol_lower):
                        c["score"] = c.get("score", 0) + 0.15
                candidates.sort(key=lambda x: x.get("score", 0), reverse=True)

            # Step 2: rerank — picks the best from candidates
            top = _rerank_articles(question, candidates, limit=limit)
        else:
            # Keyword fallback
            conn = sqlite3.connect(_POLLS_DB)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT url, title, gemini_json FROM va_news "
                "WHERE gemini_json IS NOT NULL "
                "ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT 120"
            ).fetchall()
            conn.close()
            q_lower = question.lower()
            pol_lower = [p.lower() for p in (politician_names or [])]
            scored = []
            for row in rows:
                try:
                    d = json.loads(row["gemini_json"])
                except Exception:
                    continue
                score = 0
                article_pols = [p.get("name", "").lower() for p in (d.get("politicians") or [])]
                for pol in pol_lower:
                    if any(pol in ap or ap in pol for ap in article_pols):
                        score += 3
                words = re.findall(r'\b\w{5,}\b', q_lower)
                title = (d.get("headline") or row["title"] or "").lower()
                summary = (d.get("summary") or "").lower()
                for w in words:
                    if w in title: score += 1
                    if w in summary: score += 1
                if score > 0:
                    scored.append({"score": score, "data": d, "url": row["url"], "title": row["title"]})
            scored.sort(key=lambda x: x["score"], reverse=True)
            top = scored[:limit]

        if not top:
            return ""

        lines = ["RECENT VIRGINIA NEWS (use these to answer current-events questions):"]
        for c in top:
            d = c["data"]
            url = c.get("url") or ""
            raw_title = c.get("title") or ""
            headline = d.get("headline") or raw_title or "Article"
            outlet = d.get("outlet") or ""
            author = d.get("author") or ""
            summary = d.get("summary") or ""
            pub = (d.get("published") or "")[:10]
            byline = outlet + (f" — {author}" if author else "") + (f" ({pub})" if pub else "")
            lines.append(f"- {headline}" + (f" [{byline}]" if byline else ""))
            if summary:
                lines.append(f"  Summary: {summary}")
            if url:
                lines.append(f"  Source: {url}")
        return "\n".join(lines)
    except Exception:
        return ""


@app.post("/chat", response_model=ChatResponse)
@limiter.limit("10/minute")
async def chat(request: Request, req: ChatRequest):
    ctx = DISTRICT_CONTEXT.get(req.district)
    if not ctx:
        return ChatResponse(reply="Unknown district.")

    # Build election results context
    if _results:
        r = _results
        statewide_block = (
            f"STATEWIDE RESULT — April 21, 2026 Special Election:\n"
            f"  Outcome: {r['winner']}\n"
            f"  Yes (Approve): {r['yes']:,} votes ({r['yes_pct']}%)\n"
            f"  No  (Reject):  {r['no']:,} votes ({r['no_pct']}%)\n"
            f"  Early Voting:  Yes {r['early']['yes']:,} / No {r['early']['no']:,}\n"
            f"  Election Day:  Yes {r['election_day']['yes']:,} / No {r['election_day']['no']:,}\n"
            f"  Mail-In:       Yes {r['mail']['yes']:,} / No {r['mail']['no']:,}"
        )
        # Try to match the user's locality to a result
        locality_block = ""
        if req.locality:
            raw_key = req.locality.strip().upper()
            raw_key = raw_key[:-7].strip() if raw_key.endswith(" COUNTY") else \
                      raw_key[:-5].strip() if raw_key.endswith(" CITY") else raw_key
            loc = r["local"].get(raw_key)
            if loc:
                locality_block = (
                    f"\nUSER'S LOCALITY — {loc['display']}:\n"
                    f"  Yes: {loc['yes']:,} ({loc['pct_yes']}%)  |  "
                    f"No: {loc['no']:,}  |  Winner: {loc['winner']}"
                )
        election_context = statewide_block + locality_block
    else:
        election_context = "Election results data is currently unavailable."

    district_block = (
        f"USER'S CONGRESSIONAL DISTRICT: {req.district}\n"
        f"U.S. Representative: {ctx['rep']} ({ctx['party']})\n"
        f"Region: {ctx['region']}"
        if ctx["rep"] else
        "The user has not yet looked up their specific district. Answer statewide questions."
    )

    # Add state House of Delegates context
    hod_info = HOD_CONTEXT.get(req.hod_district) if req.hod_district else None
    if hod_info:
        district_block += (
            f"\nVA HOUSE OF DELEGATES DISTRICT: {req.hod_district}\n"
            f"Delegate: {hod_info['delegate']} ({hod_info['party']})\n"
            f"Locality: {hod_info['locality']}"
        )

    # Add state Senate context
    sd_info = SD_CONTEXT.get(req.sd_district) if req.sd_district else None
    if sd_info:
        district_block += (
            f"\nVA STATE SENATE DISTRICT: {req.sd_district}\n"
            f"Senator: {sd_info['senator']} ({sd_info['party']})\n"
            f"Region: {sd_info['region']}"
        )
    # Pull relevant news and votes for the user's question
    last_question = req.messages[-1].content if req.messages else ""
    pol_names = []
    bioguide_ids = []
    if ctx and ctx.get("rep"):
        pol_names.append(ctx["rep"])
    if hod_info:
        pol_names.append(hod_info["delegate"])
    if sd_info:
        pol_names.append(sd_info["senator"])

    # Resolve bioguide IDs for the user's federal reps
    for bgid, m in _MEMBER_CACHE.items():
        mname = m.get("name", "").lower()
        if any(pol.lower() in mname or mname in pol.lower() for pol in pol_names if pol):
            bioguide_ids.append(bgid)

    news_context    = _fetch_relevant_news(last_question, pol_names)
    vote_context    = _fetch_vote_context(last_question, bioguide_ids)
    finance_context = _fetch_finance_context(bioguide_ids, last_question)

    system_prompt = f"""You are VoteIQ, a nonpartisan civic assistant helping Virginia voters learn about their elected representatives.

{district_block}

Your job is to help voters understand who represents them and what those officials do. Answer questions about:
- The representative's role, responsibilities, and committee assignments
- How to contact or reach the representative
- Recent legislation they have sponsored or voted on
- How they voted on specific bills or issues
- Campaign donors and which industries fund their campaigns
- General information about the office (U.S. House, state legislature, etc.)
- Voter registration and civic participation

{vote_context if vote_context else ""}

{finance_context if finance_context else ""}

{f'''
{news_context}

When answering questions about recent news or current events, cite the article source and author.
Format citations as: [Outlet — Author](URL) at the end of the relevant sentence.
If no relevant news is listed above, answer from your training knowledge.
''' if news_context else ''}

Keep answers 2-4 sentences. Be factual and nonpartisan. When citing a vote, include the bill name and Yea/Nay. When citing donors or industry contributions, state the dollar amount and add "(FEC data, fec.gov)" so users know the source. For official contact info direct users to house.gov, senate.gov, or virginiageneralassembly.gov. Never express opinions on representatives or tell people how to vote."""
    try:
        return ChatResponse(reply=_claude_reply(system_prompt, req.messages, max_tokens=1000))
    except Exception as e:
        return ChatResponse(reply=_friendly_claude_error(e))

@app.post("/api/gemini-chat", response_model=ChatResponse)
@limiter.limit("10/minute")
async def gemini_chat(request: Request, req: ChatRequest):
    """Chat with VoteIQ using Gemini, grounded in FEC data and district context."""
    ctx = DISTRICT_CONTEXT.get(req.district)
    if not ctx:
        return ChatResponse(reply="Unknown district.")

    # Build grounded context
    fec_context = ""
    if ctx["rep"]:
        fec_context = _get_fec_summary(ctx["rep"])

    district_block = (
        f"USER'S CONGRESSIONAL DISTRICT: {req.district}\n"
        f"U.S. Representative: {ctx['rep']} ({ctx['party']})\n"
        f"Region: {ctx['region']}\n"
        f"{fec_context}"
    )

    # State level context
    hod_info = HOD_CONTEXT.get(req.hod_district) if req.hod_district else None
    if hod_info:
        district_block += (
            f"\nVA HOUSE OF DELEGATES DISTRICT: {req.hod_district}\n"
            f"Delegate: {hod_info['delegate']} ({hod_info['party']})\n"
        )

    system_prompt = f"""You are VoteIQ, a nonpartisan civic assistant for Virginia. 
You are grounded in local election data and federal campaign finance (FEC) data.

{district_block}

Rules:
1. Use the FEC finance totals provided in the context if the user asks about money, donors, or campaign spending.
2. Be factual and nonpartisan.
3. Keep answers concise (2-4 sentences).
4. If you don't have specific finance data for a state official, clarify that FEC data only covers federal offices.
5. Direct users to FEC.gov or house.gov for official records.

Never tell people how to vote or express opinions on the spending habits of representatives."""

    try:
        # Filter messages for valid types
        reply = _gemini_reply(system_prompt, req.messages, max_tokens=1000)
        if not reply.rstrip().endswith("public datasets.*"):
            reply = reply.rstrip() + "\n\n---\n*Sources: FEC.gov, OpenStates, and VoteIQ Local Data.*"
        return ChatResponse(reply=reply)
    except Exception as e:
        return ChatResponse(reply=f"Gemini Error: {str(e)}")

@app.get("/past-elections", response_class=HTMLResponse)
def election_results_page():
    results = _load_2025_results()
    locality_results = {}
    for _office in ["Governor", "Lieutenant Governor", "Attorney General"]:
        try:
            locality_results[_office] = _load_2025_statewide_locality_results(_office)
        except Exception:
            locality_results[_office] = {}
    all_data = {
        **results,
        "locality_results": locality_results,
        "hod_flips": _build_2025_hod_flips(results.get("hod", {})),
    }
    safe_json = json.dumps(all_data, default=str).replace("</script>", "<\\/script>")
    with open(os.path.join(BASE_DIR, "templates", "election_results.html"), "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("</head>", f"<script>window._ELECTION_DATA={safe_json};</script></head>", 1)
    return html


@app.get("/api/election-results-2025")
def election_results_2025_api():
    return _load_2025_results()


@app.get("/past-elections/2023", response_class=HTMLResponse)
def election_results_2023_page():
    data = _load_2023_results()
    data = {
        **data,
        "senate_flips": _build_2023_state_leg_flips("senate", data.get("senate", {})),
        "hod_flips": _build_2023_state_leg_flips("hod", data.get("hod", {})),
    }
    safe_json = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    with open(os.path.join(BASE_DIR, "templates", "election_results_2023.html"), "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("</head>", f"<script>window._ELECTION_DATA={safe_json};</script></head>", 1)
    return html


_2024_data_cache = None
_2023_data_cache = None
_2022_data_cache = None
_2021_data_cache = None
_2020_data_cache = None
_2018_data_cache = None
_historical_data_cache = {}


def _build_2023_race(cands_dict):
    total = sum(c["votes"] for c in cands_dict.values())
    candidates = []
    for cname, cdata in cands_dict.items():
        votes = cdata["votes"]
        pct = round(votes / total * 100, 1) if total else 0.0
        candidates.append({"name": cname, "party": cdata["party"], "votes": votes, "pct": pct})
    candidates.sort(key=lambda c: c["votes"], reverse=True)
    return {"total": total, "candidates": candidates}


def _load_2023_results():
    global _2023_data_cache
    if _2023_data_cache is not None:
        return _2023_data_cache

    json_path = os.path.join(BASE_DIR, "election_results_2023.json")
    if os.path.exists(json_path):
        with open(json_path, encoding="utf-8") as f:
            raw = json.load(f)
        _2023_data_cache = {
            "senate": {int(k): v for k, v in raw.get("senate", {}).items()},
            "hod": {int(k): v for k, v in raw.get("hod", {}).items()},
        }
        return _2023_data_cache

    path = os.path.join(BASE_DIR, "Election Results_a6f500ae-6aed-4237-b29a-8a94a22dfbbb.csv")
    senate_races: dict = {}
    hod_races: dict = {}

    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("WriteInVote") == "1":
                    continue
                if row.get("CandidateName", "").strip().upper() == "WRITE IN VOTES":
                    continue

                district_type = row.get("DistrictType", "").strip()
                if district_type not in ("state-senate", "state-house"):
                    continue

                try:
                    district = int(row.get("DistrictName", "").strip())
                except (TypeError, ValueError):
                    continue

                name = row.get("CandidateName", "").strip()
                party = row.get("Party", "").strip()
                try:
                    votes = int(row.get("TOTAL_VOTES", "").strip())
                except (TypeError, ValueError):
                    votes = 0

                races = senate_races if district_type == "state-senate" else hod_races
                district_data = races.setdefault(district, {"candidates": {}})
                candidate = district_data["candidates"].setdefault(
                    name, {"name": name, "party": party, "votes": 0}
                )
                candidate["votes"] += votes
    except Exception as exc:
        print(f"_load_2023_results: {exc}")
        _2023_data_cache = {"senate": {}, "hod": {}}
        return _2023_data_cache

    _2023_data_cache = {
        "senate": {
            district: _build_2023_race(data["candidates"])
            for district, data in sorted(senate_races.items())
        },
        "hod": {
            district: _build_2023_race(data["candidates"])
            for district, data in sorted(hod_races.items())
        },
    }
    return _2023_data_cache

def _load_2024_results():
    global _2024_data_cache
    if _2024_data_cache is not None:
        return _2024_data_cache
    path = os.path.join(BASE_DIR, "election_results_2024.json")
    try:
        with open(path, encoding="utf-8") as f:
            _2024_data_cache = json.load(f)
    except Exception as e:
        print(f"_load_2024_results: {e}")
        _2024_data_cache = {"statewide": [], "locality_results": {}, "congress": {}}
    return _2024_data_cache


@app.get("/past-elections/2024", response_class=HTMLResponse)
def election_results_2024_page():
    data = _load_2024_results()
    safe_json = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    with open(os.path.join(BASE_DIR, "templates", "election_results_2024.html"), "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("</head>", f"<script>window._ELECTION_DATA={safe_json};</script></head>", 1)
    return html


def _load_2022_results():
    global _2022_data_cache
    if _2022_data_cache is not None:
        return _2022_data_cache
    path = os.path.join(BASE_DIR, "election_results_2022.json")
    try:
        with open(path, encoding="utf-8") as f:
            _2022_data_cache = json.load(f)
    except Exception as e:
        print(f"_load_2022_results: {e}")
        _2022_data_cache = {"congress": {}}
    return _2022_data_cache


def _load_2021_results():
    global _2021_data_cache
    if _2021_data_cache is not None:
        return _2021_data_cache
    path = os.path.join(BASE_DIR, "election_results_2021.json")
    try:
        with open(path, encoding="utf-8") as f:
            _2021_data_cache = json.load(f)
    except Exception as e:
        print(f"_load_2021_results: {e}")
        _2021_data_cache = {"statewide": [], "locality_results": {}, "hod": {}}
    return _2021_data_cache


def _load_2020_results():
    global _2020_data_cache
    if _2020_data_cache is not None:
        return _2020_data_cache
    path = os.path.join(BASE_DIR, "election_results_2020.json")
    try:
        with open(path, encoding="utf-8") as f:
            _2020_data_cache = json.load(f)
    except Exception as e:
        print(f"_load_2020_results: {e}")
        _2020_data_cache = {"statewide": [], "locality_results": {}, "congress": {}}
    return _2020_data_cache


def _load_2018_results():
    global _2018_data_cache
    if _2018_data_cache is not None:
        return _2018_data_cache
    path = os.path.join(BASE_DIR, "election_results_2018.json")
    try:
        with open(path, encoding="utf-8") as f:
            _2018_data_cache = json.load(f)
    except Exception as e:
        print(f"_load_2018_results: {e}")
        _2018_data_cache = {"statewide": [], "locality_results": {}, "congress": {}}
    return _2018_data_cache


HISTORICAL_ELECTION_META = {
    "2016": {
        "title": "2016 Virginia Federal Election",
        "subtitle": "President and U.S. House results from the November 8, 2016 general election.",
        "date": "November 8, 2016",
        "kind": "federal",
        "maps": [
            {"tab": "president-map", "label": "President Map", "title": "President - Locality Map", "layer": "pres_2016"},
            {"tab": "congress-map", "label": "Congress Map", "title": "U.S. House - District Results Map", "url": "/maps/congress/2016"},
        ],
        "bubble_maps": [
            {"tab": "president-bubbles", "label": "President Bubbles", "title": "President - Statewide Vote Bubbles", "year": "2016", "office": "President"},
        ],
        "notes": ["Congressional district map shows 2010-cycle (113th Congress) boundaries with 2016 election results."],
    },
    "2017": {
        "title": "2017 Virginia State Elections",
        "subtitle": "Governor, Lieutenant Governor, Attorney General, and House of Delegates results from the November 7, 2017 general election.",
        "date": "November 7, 2017",
        "kind": "statewide",
        "maps": [
            {"tab": "governor-map",  "label": "Governor Map",  "title": "Governor - Locality Map",            "layer": "gov_2017"},
            {"tab": "ltgov-map",     "label": "Lt. Gov Map",   "title": "Lieutenant Governor - Locality Map", "layer": "ltgov_2017"},
            {"tab": "ag-map",        "label": "AG Map",        "title": "Attorney General - Locality Map",    "layer": "ag_2017"},
            {"tab": "hod-map",       "label": "HOD Map",       "title": "House of Delegates - District Results Map", "url": "/maps/hod/2017"},
        ],
        "bubble_maps": [
            {"tab": "governor-bubbles", "label": "Gov Bubbles",    "title": "Governor - Statewide Vote Bubbles",            "year": "2017", "office": "Governor"},
            {"tab": "ltgov-bubbles",    "label": "Lt. Gov Bubbles","title": "Lieutenant Governor - Statewide Vote Bubbles",  "year": "2017", "office": "Lieutenant Governor"},
            {"tab": "ag-bubbles",       "label": "AG Bubbles",     "title": "Attorney General - Statewide Vote Bubbles",    "year": "2017", "office": "Attorney General"},
        ],
        "notes": [],
    },
    "2018": {
        "title": "2018 Virginia Midterm Election",
        "subtitle": "U.S. Senate and U.S. House results from the November 6, 2018 general election.",
        "date": "November 6, 2018",
        "kind": "federal",
        "maps": [
            {"tab": "congress-map", "label": "Congress Map", "title": "U.S. House - District Results Map", "url": "/maps/congress/2018"},
        ],
        "notes": ["Congressional district map shows 2010-cycle (113th Congress) boundaries with 2018 election results."],
    },
    "2019": {
        "title": "2019 Virginia State Legislative Elections",
        "subtitle": "State Senate and House of Delegates results from the November 5, 2019 general election.",
        "date": "November 5, 2019",
        "kind": "district",
        "maps": [
            {"tab": "senate-map", "label": "Senate Map", "title": "State Senate - District Results Map",        "url": "/maps/senate/2019"},
            {"tab": "senate-flip-map", "label": "Senate Flip Map", "title": "State Senate - 2019 to 2023 Flip Map", "layer": "senate_2023_flip_2019"},
            {"tab": "hod-map",    "label": "HOD Map",    "title": "House of Delegates - District Results Map", "url": "/maps/hod/2019"},
            {"tab": "hod-flip-map", "label": "HOD Flip Map", "title": "House of Delegates - 2017 to 2019 Flip Map", "layer": "hod_2019_flip_2017"},
        ],
        "notes": ["Maps show 2010-cycle district boundaries with 2019 election results."],
    },
    "2020": {
        "title": "2020 Virginia Local Elections",
        "subtitle": "June 4 town election results from the uploaded CSV.",
        "date": "June 4, 2020",
        "kind": "local",
        "maps": [],
        "notes": ["This CSV contains local town races only, so no statewide choropleth map is available."],
    },
}


def _load_historical_results(year: str):
    year = str(year)
    if year in _historical_data_cache:
        return _historical_data_cache[year]
    path = os.path.join(BASE_DIR, f"election_results_{year}.json")
    try:
        with open(path, encoding="utf-8") as f:
            _historical_data_cache[year] = json.load(f)
    except Exception as e:
        print(f"_load_historical_results {year}: {e}")
        _historical_data_cache[year] = {}
    return _historical_data_cache[year]


@app.get("/past-elections/2022", response_class=HTMLResponse)
def election_results_2022_page():
    data = _load_2022_results()
    safe_json = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    with open(os.path.join(BASE_DIR, "templates", "election_results_2022.html"), "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("</head>", f"<script>window._ELECTION_DATA={safe_json};</script></head>", 1)
    return html


@app.get("/past-elections/2021", response_class=HTMLResponse)
def election_results_2021_page():
    data = _load_2021_results()
    safe_json = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    with open(os.path.join(BASE_DIR, "templates", "election_results_2021.html"), "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("</head>", f"<script>window._ELECTION_DATA={safe_json};</script></head>", 1)
    return html


@app.get("/past-elections/2020", response_class=HTMLResponse)
def election_results_2020_page():
    data = _load_2020_results()
    safe_json = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    with open(os.path.join(BASE_DIR, "templates", "election_results_2020.html"), "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("</head>", f"<script>window._ELECTION_DATA={safe_json};</script></head>", 1)
    return html


@app.get("/past-elections/2018", response_class=HTMLResponse)
def election_results_2018_page():
    data = _load_2018_results()
    safe_json = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    with open(os.path.join(BASE_DIR, "templates", "election_results_2018.html"), "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("</head>", f"<script>window._ELECTION_DATA={safe_json};</script></head>", 1)
    return html


@app.get("/past-elections/{year}", response_class=HTMLResponse)
def election_results_historical_page(year: str):
    year = str(year)
    if year not in HISTORICAL_ELECTION_META:
        raise HTTPException(status_code=404, detail="Election year not found")
    data = _load_historical_results(year)
    meta = HISTORICAL_ELECTION_META[year]
    safe_json = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    safe_meta = json.dumps(meta, default=str).replace("</script>", "<\\/script>")
    with open(os.path.join(BASE_DIR, "templates", "election_results_historical.html"), "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace(
        "</head>",
        f"<script>window._ELECTION_DATA={safe_json};window._ELECTION_META={safe_meta};</script></head>",
        1,
    )
    return html


# ── Pre-built district result maps (2016-2019) ────────────────────────────────

_DISTRICT_MAP_FILES = {
    ("congress", "2016"): "va_congress_2016.html",
    ("congress", "2018"): "va_congress_2018.html",
    ("hod",      "2017"): "va_house_delegates_2017.html",
    ("senate",   "2019"): "va_senate_2019.html",
    ("hod",      "2019"): "va_house_delegates_2019.html",
}

_district_map_cache: dict[tuple, str] = {}


@app.get("/maps/{chamber}/{year}", response_class=HTMLResponse)
def district_result_map(chamber: str, year: str):
    from fastapi import HTTPException
    key = (chamber.lower(), year)
    if key not in _DISTRICT_MAP_FILES:
        raise HTTPException(status_code=404, detail=f"No map for {chamber}/{year}")
    if key not in _district_map_cache:
        path = os.path.join(BASE_DIR, "templates", _DISTRICT_MAP_FILES[key])
        with open(path, "r", encoding="utf-8") as f:
            _district_map_cache[key] = f.read()
    return _district_map_cache[key]


def _build_election_summary(year: str) -> str:
    try:
        if year == "2025":
            data = _load_2025_results()
            lines = ["2025 Virginia State Election results:"]
            for race in data.get("statewide", []):
                cands = race.get("candidates", [])
                if cands:
                    w  = cands[0]
                    r  = cands[1] if len(cands) > 1 else {}
                    label = race.get("race") or race.get("office", "?")
                    lines.append(f"  {label}: {w['name']} ({w['party']}) {w['pct']}% vs {r.get('name','—')} ({r.get('party','')}) {r.get('pct',0)}%")
            hod = data.get("hod", {})
            dem_w = sum(1 for d in hod.values() if (d.get("candidates") or [{}])[0].get("party","").lower().startswith("d"))
            rep_w = len(hod) - dem_w
            lines.append(f"  House of Delegates: Democrats {dem_w} seats, Republicans {rep_w} seats (100 total)")
            lines.append(f"  State Senate: Democrats 21 seats, Republicans 19 seats (40 total)")
            return "\n".join(lines)
        elif year == "2024":
            data = _load_2024_results()
            lines = ["2024 Virginia Federal Election results:"]
            for race in data.get("statewide", []):
                cands = [c for c in race.get("candidates", []) if c.get("name","").upper() != "WRITE IN VOTES"]
                if cands:
                    w, r = cands[0], cands[1] if len(cands) > 1 else {}
                    label = race.get("race") or race.get("office", "?")
                    lines.append(f"  {label}: {w['name']} ({w['party']}) {w['pct']}% vs {r.get('name','—')} ({r.get('party','')}) {r.get('pct',0)}%")
            for dist, race in sorted(data.get("congress", {}).items()):
                cands = race.get("candidates", [])
                if cands:
                    w, r = cands[0], cands[1] if len(cands) > 1 else {}
                    lines.append(f"  VA-{dist}: {w['name']} ({w['party']}) {w['pct']}% vs {r.get('name','—')} {r.get('pct',0)}%")
            return "\n".join(lines)
        elif year == "2023":
            data = _load_2023_results()
            lines = ["2023 Virginia State Election results:"]
            for dist, race in sorted(data.get("senate", {}).items(), key=lambda x: int(x[0])):
                cands = race.get("candidates", [])
                if cands:
                    w = cands[0]
                    lines.append(f"  Senate District {dist}: {w['name']} ({w['party']}) {w['pct']}%")
            for dist, race in sorted(data.get("hod", {}).items(), key=lambda x: int(x[0])):
                cands = race.get("candidates", [])
                if cands:
                    w = cands[0]
                    lines.append(f"  HOD District {dist}: {w['name']} ({w['party']}) {w['pct']}%")
            return "\n".join(lines)
        elif year == "2022":
            data = _load_2022_results()
            lines = ["2022 Virginia Midterm Election (U.S. House) results:"]
            for dist, race in data.get("congress", {}).items():
                cands = race.get("candidates", [])
                if cands:
                    w, r = cands[0], cands[1] if len(cands) > 1 else {}
                    lines.append(f"  VA-{dist}: {w['name']} ({w['party']}) {w['pct']}% vs {r.get('name','—')} {r.get('pct',0)}%")
            return "\n".join(lines)
        elif year == "2021":
            data = _load_2021_results()
            lines = ["2021 Virginia State Election results:"]
            for race in data.get("statewide", []):
                cands = race.get("candidates", [])
                if cands:
                    w, r = cands[0], cands[1] if len(cands) > 1 else {}
                    lines.append(f"  {race['race']}: {w['name']} ({w['party']}) {w['pct']}% vs {r.get('name','—')} {r.get('pct',0)}%")
            hod = data.get("hod", {})
            dem_w = sum(1 for d in hod.values() if (d.get("candidates") or [{}])[0].get("party","").lower().startswith("d"))
            lines.append(f"  House of Delegates: Democrats {dem_w} seats, Republicans {len(hod)-dem_w} seats")
            return "\n".join(lines)
        elif year == "2020":
            data = _load_2020_results()
            lines = ["2020 Virginia Federal Election results:"]
            for race in data.get("statewide", []):
                cands = race.get("candidates", [])
                if cands:
                    w, r = cands[0], cands[1] if len(cands) > 1 else {}
                    lines.append(f"  {race['race']}: {w['name']} ({w['party']}) {w['pct']}% vs {r.get('name','—')} {r.get('pct',0)}%")
            for dist, race in sorted(data.get("congress", {}).items()):
                cands = race.get("candidates", [])
                if cands:
                    w, r = cands[0], cands[1] if len(cands) > 1 else {}
                    lines.append(f"  VA-{dist}: {w['name']} ({w['party']}) {w['pct']}% vs {r.get('name','—')} {r.get('pct',0)}%")
            return "\n".join(lines)
        elif year == "2018":
            data = _load_2018_results()
            lines = ["2018 Virginia Federal Election (Midterm) results:"]
            for race in data.get("statewide", []):
                cands = race.get("candidates", [])
                if cands:
                    w, r = cands[0], cands[1] if len(cands) > 1 else {}
                    lines.append(f"  {race['race']}: {w['name']} ({w['party']}) {w['pct']}% vs {r.get('name','—')} {r.get('pct',0)}%")
            for dist, race in sorted(data.get("congress", {}).items()):
                cands = race.get("candidates", [])
                if cands:
                    w, r = cands[0], cands[1] if len(cands) > 1 else {}
                    lines.append(f"  VA-{dist}: {w['name']} ({w['party']}) {w['pct']}% vs {r.get('name','—')} {r.get('pct',0)}%")
            return "\n".join(lines)
    except Exception as e:
        print(f"_build_election_summary: {e}")
    return f"{year} Virginia election results."


def _candidate_list(race: dict) -> list[dict]:
    return [
        c for c in race.get("candidates", [])
        if str(c.get("name", "")).strip().upper() != "WRITE IN VOTES"
    ]


def _race_label(group: str, key, race: dict) -> str:
    if group == "statewide":
        return race.get("race") or race.get("office") or "Statewide race"
    if group == "locality":
        return str(key)
    if group == "congress":
        dist = race.get("district") or key
        return f"U.S. House District {dist}"
    if group == "senate":
        return f"State Senate District {key}"
    if group == "hod":
        return f"House District {key}"
    return str(key)


def _format_race_line(group: str, key, race: dict, prefix: str = "") -> str:
    cands = _candidate_list(race)
    if not cands:
        return ""
    winner = cands[0]
    runner = cands[1] if len(cands) > 1 else None
    label = _race_label(group, key, race)
    total = race.get("total") or sum(int(c.get("votes") or 0) for c in cands)
    line = (
        f"{prefix}{label}: {winner.get('name', 'Unknown')} "
        f"({winner.get('party', '')}) won with {int(winner.get('votes') or 0):,} votes "
        f"({winner.get('pct', 0)}%)"
    )
    if runner:
        margin_votes = int(winner.get("votes") or 0) - int(runner.get("votes") or 0)
        margin_pct = round(float(winner.get("pct") or 0) - float(runner.get("pct") or 0), 1)
        line += (
            f" over {runner.get('name', 'Unknown')} ({runner.get('party', '')}), "
            f"{int(runner.get('votes') or 0):,} votes ({runner.get('pct', 0)}%). "
            f"Margin: {margin_votes:,} votes / {margin_pct} points"
        )
    else:
        line += " in an uncontested or single-candidate listed race"
    if total:
        line += f". Total votes: {int(total):,}"
    return line + "."


def _sort_race_items(items):
    def key_fn(item):
        nums = re.findall(r"\d+", str(item[0]))
        return (0, int(nums[0])) if nums else (1, str(item[0]))
    return sorted(items, key=key_fn)


def _party_counts(races: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for race in races.values():
        cands = _candidate_list(race)
        if not cands:
            continue
        party = cands[0].get("party") or "Other"
        counts[party] = counts.get(party, 0) + 1
    return counts


def _district_keys_by_group(question: str) -> dict[str, set[str]]:
    groups = {"congress": set(), "senate": set(), "hod": set(), "all": set()}
    pattern_groups = [
        ("congress", r"\b(?:va|cd|congress(?:ional)?(?:\s+district)?)\s*-?\s*(\d{1,3})(?:st|nd|rd|th)?\b"),
        ("hod", r"\b(?:hd|hod|house(?:\s+district)?)\s*-?\s*(\d{1,3})(?:st|nd|rd|th)?\b"),
        ("senate", r"\b(?:sd|senate(?:\s+district)?)\s*-?\s*(\d{1,3})(?:st|nd|rd|th)?\b"),
        ("all", r"\bdistrict\s+(\d{1,3})(?:st|nd|rd|th)?\b"),
    ]
    for group, pattern in pattern_groups:
        for m in re.finditer(pattern, question, re.I):
            groups[group].add(m.group(1))
    return groups


def _district_key_matches(key, targets: set[str]) -> bool:
    nums = re.findall(r"\d+", str(key))
    return bool(nums and nums[0] in targets)


def _norm_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _candidate_mentioned(candidate_name: str, question_norm: str) -> bool:
    name_norm = _norm_text(candidate_name)
    if not name_norm:
        return False
    if name_norm in question_norm:
        return True
    parts = [p for p in name_norm.split() if len(p) > 3]
    return bool(parts and parts[-1] in question_norm)


def _closest_races(data: dict, limit: int = 8) -> list[str]:
    close = []
    for group in ("congress", "senate", "hod"):
        for key, race in data.get(group, {}).items():
            cands = _candidate_list(race)
            if len(cands) < 2:
                continue
            margin = abs(float(cands[0].get("pct") or 0) - float(cands[1].get("pct") or 0))
            close.append((margin, group, key, race))
    close.sort(key=lambda item: item[0])
    return [_format_race_line(group, key, race) for _, group, key, race in close[:limit]]


def _build_election_chat_context(year: str, question: str = "") -> str:
    try:
        data = _load_results_for_year(str(year))
        if str(year) == "2025" and not data.get("locality_results"):
            locality_results = {}
            for office in ["Governor", "Lieutenant Governor", "Attorney General"]:
                try:
                    locality_results[office] = _load_2025_statewide_locality_results(office)
                except Exception:
                    locality_results[office] = {}
            data = {**data, "locality_results": locality_results}
        q_norm = _norm_text(question)
        district_targets = _district_keys_by_group(question)
        wants_close = any(word in q_norm.split() for word in ("close", "closest", "tight", "tightest", "margin"))
        lines = [f"{year} Virginia election results from VoteIQ JSON data:"]

        statewide = data.get("statewide", [])
        if statewide:
            lines.append("Statewide races:")
            for race in statewide:
                line = _format_race_line("statewide", race.get("race") or race.get("office"), race, "  ")
                if line:
                    lines.append(line)

        for group, label in (("congress", "U.S. House"), ("senate", "State Senate"), ("hod", "House of Delegates")):
            races = data.get(group, {})
            if not races:
                continue
            counts = _party_counts(races)
            count_text = ", ".join(f"{party}: {count}" for party, count in sorted(counts.items()))
            lines.append(f"{label} races listed: {len(races)}. Winners by party: {count_text}.")

        targeted = []
        for group in ("congress", "senate", "hod"):
            group_targets = district_targets[group] | district_targets["all"]
            for key, race in _sort_race_items(data.get(group, {}).items()):
                include = bool(group_targets and _district_key_matches(key, group_targets))
                include = include or any(_candidate_mentioned(c.get("name", ""), q_norm) for c in _candidate_list(race))
                if include:
                    line = _format_race_line(group, key, race)
                    if line:
                        targeted.append(line)
        if targeted:
            lines.append("Question-matched district or candidate races:")
            lines.extend(f"  {line}" for line in targeted[:20])

        locality_hits = []
        for office, localities in data.get("locality_results", {}).items():
            for locality, race in localities.items():
                loc_norm = _norm_text(locality)
                short_loc = re.sub(r"\b(county|city)\b", "", loc_norm).strip()
                if loc_norm and (loc_norm in q_norm or (len(short_loc) > 3 and short_loc in q_norm)):
                    line = _format_race_line("locality", f"{locality} - {office}", race)
                    if line:
                        locality_hits.append(line)
        if locality_hits:
            lines.append("Question-matched locality results:")
            lines.extend(f"  {line}" for line in locality_hits[:18])

        if wants_close:
            close_lines = _closest_races(data)
            if close_lines:
                lines.append("Closest district races:")
                lines.extend(f"  {line}" for line in close_lines)

        if not targeted and not locality_hits:
            for group, label in (("congress", "U.S. House district winners"), ("senate", "State Senate district winners"), ("hod", "House of Delegates district winners")):
                races = data.get(group, {})
                if not races:
                    continue
                lines.append(f"{label}:")
                for key, race in _sort_race_items(races.items())[:120]:
                    cands = _candidate_list(race)
                    if cands:
                        w = cands[0]
                        lines.append(f"  {_race_label(group, key, race)}: {w.get('name')} ({w.get('party')}) {w.get('pct')}%")

        return "\n".join(lines)[:30000]
    except Exception as e:
        print(f"_build_election_chat_context: {e}")
    return f"{year} Virginia election results."


class ElectionChatRequest(BaseModel):
    year: str
    messages: list[ChatMessage]


@app.post("/api/election-chat", response_model=ChatResponse)
@limiter.limit("10/minute")
async def election_chat(request: Request, req: ElectionChatRequest):
    user_query = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    summary = _build_election_chat_context(req.year, user_query)
    system_prompt = f"""You are a friendly Virginia election results assistant on the VoteIQ platform.

Here are the official {req.year} Virginia election results:

{summary}

Answer questions about these results clearly and concisely (2-4 sentences). Be factual and nonpartisan. Give specific numbers when asked about candidates, margins, or localities. If you don't have the data, say so honestly. Never express opinions on candidates or tell users how to vote."""
    try:
        return ChatResponse(reply=_claude_reply(system_prompt, req.messages, max_tokens=400))
    except Exception as e:
        return ChatResponse(reply=_friendly_claude_error(e))


# ── Bills RAG chat ────────────────────────────────────────────────────────────

_BILLS_MODEL      = "voyage-law-2"
_BILLS_COLLECTION = "voteiq_bills"
_vo               = None
_chroma_collection_id = None

def _get_voyage_client():
    global _vo
    if _vo is None:
        import voyageai
        _vo = voyageai.Client(api_key=os.getenv("VOYAGE_API_KEY"))
    return _vo

def _chroma_headers():
    return {
        "x-chroma-token": os.getenv("CHROMA_API_KEY", ""),
        "Content-Type": "application/json",
    }

def _chroma_base():
    tenant = os.getenv("CHROMA_TENANT", "")
    database = os.getenv("CHROMA_DATABASE", "")
    return f"https://api.trychroma.com/api/v2/tenants/{tenant}/databases/{database}"

def _get_chroma_collection_id():
    global _chroma_collection_id
    if _chroma_collection_id is None:
        import httpx
        r = httpx.get(
            f"{_chroma_base()}/collections/{_BILLS_COLLECTION}",
            headers=_chroma_headers(),
            timeout=10,
        )
        r.raise_for_status()
        _chroma_collection_id = r.json()["id"]
    return _chroma_collection_id

def _session_year(value) -> str:
    match = _SESSION_YEAR_RE.search(str(value or ""))
    return match.group(0) if match else ""

def _fetch_bills_by_id(bill_ids: list[str], session_year: str | None = None) -> list[tuple[str, dict]]:
    """Exact lookup by bill_id metadata. If session_year given, filters to that year only.
    Otherwise returns results from every embedded session."""
    import httpx
    col_id = _get_chroma_collection_id()
    results = []
    for bid in bill_ids:
        bid = re.sub(r"\s+", "", bid).upper()
        where: dict = {"bill_id": {"$eq": bid}}
        try:
            r = httpx.post(
                f"{_chroma_base()}/collections/{col_id}/get",
                headers=_chroma_headers(),
                json={"where": where, "include": ["documents", "metadatas"], "limit": 200},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            for doc, meta in zip(data.get("documents", []), data.get("metadatas", [])):
                found_year = _session_year(meta.get("session")) or _session_year(meta.get("session_id")) or _session_year(doc)
                if session_year and found_year != session_year:
                    continue
                results.append((doc, meta))
        except Exception:
            pass
    return results


def _fetch_bills_by_session(session_year: str, limit: int = 80) -> list[tuple[str, dict]]:
    """Fetch representative bill excerpts for a whole embedded session/year."""
    import httpx
    col_id = _get_chroma_collection_id()
    results = []
    seen_docs: set[str] = set()
    seen_bills: set[str] = set()

    def add_items(documents, metadatas):
        fallback: list[tuple[str, dict]] = []
        for doc, meta in zip(documents, metadatas):
            if doc in seen_docs:
                continue
            found_year = _session_year(meta.get("session")) or _session_year(meta.get("session_id")) or _session_year(doc)
            if found_year != session_year:
                continue
            seen_docs.add(doc)
            bill_id = str(meta.get("bill_id") or "").upper()
            chunk_type = meta.get("chunk_type", "")
            item = (doc, meta)
            if chunk_type == "bill_summary" and bill_id and bill_id not in seen_bills:
                seen_bills.add(bill_id)
                results.append(item)
            else:
                fallback.append(item)
            if len(results) >= limit:
                return
        if len(results) < limit:
            for item in fallback:
                results.append(item)
                if len(results) >= limit:
                    return

    try:
        r = httpx.post(
            f"{_chroma_base()}/collections/{col_id}/get",
            headers=_chroma_headers(),
            json={
                "where": {"session": {"$eq": session_year}},
                "include": ["documents", "metadatas"],
                "limit": limit * 3,
            },
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        add_items(data.get("documents", []), data.get("metadatas", []))
    except Exception:
        pass

    try:
        r = httpx.post(
            f"{_chroma_base()}/collections/{col_id}/get",
            headers=_chroma_headers(),
            json={"include": ["documents", "metadatas"], "limit": 1000},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        add_items(data.get("documents", []), data.get("metadatas", []))
    except Exception:
        pass
    return results


_BILL_NUMBER_RE = re.compile(r"\b(HB|SB|HJ|SJ|HR|SR)\s*(\d+)\b", re.IGNORECASE)
_SESSION_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_LEGISLATOR_NAME_RE = re.compile(r"\b(delegate|senator|legislator|delegate|rep\.?|sen\.?)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)", re.IGNORECASE)
_EDUCATION_KEYWORDS = ("education", "school", "teacher", "student", "curriculum", "tuition",
                       "higher education", "community college", "university", "literacy", "library")

_VA_LEGIS_DB = os.path.join(BASE_DIR, "virginia_legislature.db")
_REP_PROFILES_JSONL = os.path.join(BASE_DIR, "va_rep_profiles.jsonl")
_rep_profiles_cache = None


def _extract_bill_numbers(text: str) -> list[str]:
    return [f"{m.group(1).upper()}{m.group(2)}" for m in _BILL_NUMBER_RE.finditer(text)]


def _normalize_person_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def _load_rep_profiles() -> list[dict]:
    global _rep_profiles_cache
    if _rep_profiles_cache is not None:
        return _rep_profiles_cache
    profiles = []
    if os.path.exists(_REP_PROFILES_JSONL):
        try:
            with open(_REP_PROFILES_JSONL, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        profiles.append(json.loads(line))
        except Exception as e:
            print(f"main: could not load rep profiles: {e}")
    _rep_profiles_cache = profiles
    return profiles


def _rep_profile_by_name(name: str, session: str = "2026") -> str:
    target = _normalize_person_name(name)
    if not target:
        return ""
    target_parts = set(target.split())
    best = None
    for profile in _load_rep_profiles():
        meta = profile.get("metadata") or {}
        if str(meta.get("session") or profile.get("session_id") or "") != session:
            continue
        profile_name = _normalize_person_name(meta.get("name"))
        if profile_name == target:
            best = profile
            break
        if target_parts and target_parts.issubset(set(profile_name.split())):
            best = profile
    if not best:
        return ""
    meta = best.get("metadata") or {}
    return f"[Representative Profile — {meta.get('name', name)} {session}]\n{best.get('text', '')}"


def _profile_question(text: str) -> bool:
    text = str(text or "").lower()
    return any(
        phrase in text
        for phrase in (
            "my rep", "my representative", "my delegate", "my senator",
            "what has", "what did", "what does", "what do",
            "sponsored", "voting record", "vote record",
            "priorities", "accomplished", "fought for", "working on",
            "tell me about", "who is", "about my", "profile",
            "bills", "committee", "support", "oppose", "position",
            "failed", "passed", "stance", "record",
        )
    )


def _request_rep_profiles(req, user_query: str) -> str:
    blocks = []
    leg_name = _extract_legislator_name(user_query)
    if leg_name:
        profile = _rep_profile_by_name(leg_name)
        if profile:
            blocks.append(profile)

    # Always include the user's own reps when district context is available
    hod_info = HOD_CONTEXT.get(req.hod_district) if req.hod_district else None
    sd_info  = SD_CONTEXT.get(req.sd_district)  if req.sd_district  else None
    if hod_info:
        profile = _rep_profile_by_name(hod_info.get("delegate"))
        if profile and profile not in blocks:
            blocks.append(profile)
    if sd_info:
        profile = _rep_profile_by_name(sd_info.get("senator"))
        if profile and profile not in blocks:
            blocks.append(profile)
    return "\n\n".join(blocks)


# ── Federal (congressional) member context ────────────────────────────────────

_federal_members_cache: list[dict] | None = None

# Hardcoded VA federal delegation — used when polls.db hasn't been ingested yet.
# Keep in sync with ingest_congress.py results.
_VA_FEDERAL_MEMBERS_FALLBACK: list[dict] = [
    {"bioguide_id": "W000804", "name": "Wittman, Robert J.",              "party": "Republican", "chamber": "House of Representatives", "district": "1"},
    {"bioguide_id": "K000399", "name": "Kiggans, Jennifer A.",             "party": "Republican", "chamber": "House of Representatives", "district": "2"},
    {"bioguide_id": "S000185", "name": 'Scott, Robert C. "Bobby"',         "party": "Democratic", "chamber": "House of Representatives", "district": "3"},
    {"bioguide_id": "M001227", "name": "McClellan, Jennifer L.",           "party": "Democratic", "chamber": "House of Representatives", "district": "4"},
    {"bioguide_id": "M001239", "name": "McGuire, John J.",                 "party": "Republican", "chamber": "House of Representatives", "district": "5"},
    {"bioguide_id": "C001118", "name": "Cline, Ben",                       "party": "Republican", "chamber": "House of Representatives", "district": "6"},
    {"bioguide_id": "V000138", "name": "Vindman, Eugene Simon",            "party": "Democratic", "chamber": "House of Representatives", "district": "7"},
    {"bioguide_id": "B001292", "name": "Beyer, Donald S.",                 "party": "Democratic", "chamber": "House of Representatives", "district": "8"},
    {"bioguide_id": "G000568", "name": "Griffith, H. Morgan",              "party": "Republican", "chamber": "House of Representatives", "district": "9"},
    {"bioguide_id": "S001230", "name": "Subramanyam, Suhas",               "party": "Democratic", "chamber": "House of Representatives", "district": "10"},
    {"bioguide_id": "W000831", "name": "Walkinshaw, James R.",             "party": "Democratic", "chamber": "House of Representatives", "district": "11"},
    {"bioguide_id": "W000805", "name": "Warner, Mark R.",                  "party": "Democratic", "chamber": "Senate",                   "district": "S"},
    {"bioguide_id": "K000384", "name": "Kaine, Tim",                       "party": "Democratic", "chamber": "Senate",                   "district": "S"},
]


def _load_federal_members() -> list[dict]:
    global _federal_members_cache
    if _federal_members_cache is not None:
        return _federal_members_cache
    try:
        conn = sqlite3.connect(_POLLS_DB)
        rows = conn.execute(
            "SELECT bioguide_id, name, party, chamber, district FROM congress_members"
        ).fetchall()
        conn.close()
        if rows:
            _federal_members_cache = [
                {"bioguide_id": r[0], "name": r[1], "party": r[2], "chamber": r[3], "district": r[4]}
                for r in rows
            ]
            return _federal_members_cache
    except Exception:
        pass
    # DB not yet ingested — use hardcoded list for name detection
    _federal_members_cache = _VA_FEDERAL_MEMBERS_FALLBACK
    return _federal_members_cache


def _federal_member_by_name(text: str) -> dict | None:
    """Return the VA federal member whose name appears in text, or None."""
    text_norm = _normalize_person_name(text)
    for member in _load_federal_members():
        # "Last, First M." → check last name and full normalized form
        parts = member["name"].split(",")
        last = _normalize_person_name(parts[0]) if parts else ""
        if last and last in text_norm:
            return member
        full = _normalize_person_name(member["name"])
        if full and full in text_norm:
            return member
    return None


def _fetch_federal_context(member: dict) -> str:
    """Build a plain-text context block for a VA federal member: votes + sponsored bills."""
    bio = member["bioguide_id"]
    name = member["name"]
    party = member["party"]
    district = member["district"]
    chamber = member["chamber"]
    seat = (
        f"U.S. Senator from Virginia"
        if district == "S"
        else f"U.S. Representative, Virginia's {district}th Congressional District"
    )
    votes, bills = [], []
    db_available = False
    try:
        conn = sqlite3.connect(_POLLS_DB)
        votes = conn.execute(
            """SELECT vote_date, bill, question, member_vote, result
               FROM congress_votes WHERE bioguide_id = ?
               ORDER BY vote_date DESC LIMIT 40""",
            (bio,),
        ).fetchall()
        bills = conn.execute(
            """SELECT bill_type, bill_number, title, policy_area,
                      latest_action, latest_action_date, role
               FROM congress_bills WHERE sponsor_id = ?
               ORDER BY latest_action_date DESC LIMIT 25""",
            (bio,),
        ).fetchall()
        conn.close()
        db_available = True
    except Exception:
        db_available = False

    lines = [
        f"[Federal Member — {name} ({party})]",
        f"Office: {seat} | Chamber: {chamber} | 119th Congress",
        f"Source: congress.gov / clerk.house.gov",
    ]
    if not db_available:
        lines.append(
            "\nNOTE: Federal vote/bill database not yet loaded on this server. "
            "The member is confirmed as Virginia's federal representative but specific "
            "roll-call votes and sponsored bills are not available yet. "
            "Tell the user this person IS a Virginia federal representative and direct them "
            "to congress.gov for their full voting record."
        )
        return "\n".join(lines)

    if votes:
        lines.append(f"\nRoll-Call Votes (119th Congress, most recent first — {len(votes)} shown):")
        for vote_date, bill, question, member_vote, result in votes:
            lines.append(f"  {vote_date} | {bill} | {question} | Voted: {member_vote} | Result: {result}")
    else:
        lines.append("\nNo roll-call votes in dataset.")

    if bills:
        lines.append(f"\nSponsored / Co-Sponsored Bills ({len(bills)} shown):")
        for bill_type, bill_number, title, policy_area, latest_action, latest_action_date, role in bills:
            bid = f"{bill_type.upper()} {bill_number}"
            area = f" [{policy_area}]" if policy_area else ""
            lines.append(f"  {bid}: {title}{area} | {role} | Last action: {latest_action_date} — {latest_action}")
    else:
        lines.append("\nNo sponsored bills in dataset.")

    return "\n".join(lines)


# ── SQLite bill lookup ─────────────────────────────────────────────────────────

def _sqlite_bill_lookup(bill_numbers: list[str]) -> str:
    """Return a formatted context block for the given bill numbers from the local SQLite DB."""
    if not bill_numbers or not os.path.exists(_VA_LEGIS_DB):
        return ""
    try:
        conn = sqlite3.connect(_VA_LEGIS_DB)
        cur = conn.cursor()
        lines = []
        for bid in bill_numbers:
            row = cur.execute(
                "SELECT Bill_id, Bill_description, Patron_name, Passed, Failed, Approved, Vetoed, "
                "Last_governor_action, Last_house_action, Last_senate_action "
                "FROM bills WHERE Bill_id = ?", (bid,)
            ).fetchone()
            if not row:
                continue
            bill_id, desc, patron, passed, failed, approved, vetoed, gov_action, house_action, senate_action = row
            if approved == "Y":
                status = "Signed into law"
            elif vetoed == "Y":
                status = "Vetoed by Governor"
            elif passed == "Y":
                status = "Passed both chambers"
            elif failed == "Y":
                status = "Failed"
            else:
                status = "Did not pass / continued"
            lines.append(
                f"[SQLite — {bill_id} 2026 Virginia Session]\n"
                f"Bill {bill_id}: {desc}\n"
                f"Primary patron: {patron}\n"
                f"Status: {status}"
                + (f"\nLast House action: {house_action}" if house_action else "")
                + (f"\nLast Senate action: {senate_action}" if senate_action else "")
                + (f"\nGovernor action: {gov_action}" if gov_action else "")
            )
        conn.close()
        return "\n\n".join(lines)
    except Exception:
        return ""


def _cached_bill_description_lookup(bill_numbers: list[str], session: str | None = None) -> str:
    """Return cached bill descriptions from openstates_va.db with no external API calls."""
    if not bill_numbers or not os.path.exists(_OPENSTATES_DB):
        return ""
    try:
        conn = sqlite3.connect(_OPENSTATES_DB)
        cur = conn.cursor()
        lines = []
        for bid in bill_numbers:
            if session:
                rows = cur.execute(
                    "SELECT bill_id, session, title, description, source_url "
                    "FROM bill_descriptions WHERE bill_id=? AND session=?",
                    (bid, session),
                ).fetchall()
            else:
                rows = cur.execute(
                    "SELECT bill_id, session, title, description, source_url "
                    "FROM bill_descriptions WHERE bill_id=? ORDER BY session DESC",
                    (bid,),
                ).fetchall()
            for bill_id, sess, title, desc, url in rows:
                lines.append(
                    f"[Cached Bill Description — {bill_id} {sess}]\n"
                    f"{desc}"
                    + (f"\nSource: {url}" if url and url not in desc else "")
                )
        conn.close()
        return "\n\n".join(lines)
    except Exception:
        return ""


def _cached_bill_description_search(query: str, session: str | None = None, limit: int = 8) -> str:
    """Search cached bill descriptions locally. Uses FTS5 when available, LIKE fallback otherwise."""
    if not query or not os.path.exists(_OPENSTATES_DB):
        return ""

    terms = [
        t for t in re.findall(r"[A-Za-z0-9]+", query.lower())
        if len(t) > 2 and t not in {"what", "who", "how", "did", "the", "for", "and", "are", "bill", "bills"}
    ][:8]
    if not terms:
        return ""

    def _fts_rows(cur, match_q, sess, lim):
        base = """
            SELECT bd.bill_id, bd.session, bd.title, bd.description, bd.source_url
            FROM bill_descriptions_fts f
            JOIN bill_descriptions bd ON bd.bill_id=f.bill_id AND bd.session=f.session
            WHERE bill_descriptions_fts MATCH ?
        """
        if sess:
            return cur.execute(base + " AND bd.session=? LIMIT ?", (match_q, sess, lim)).fetchall()
        return cur.execute(base + " LIMIT ?", (match_q, lim)).fetchall()

    try:
        conn = sqlite3.connect(_OPENSTATES_DB)
        cur = conn.cursor()
        rows = []
        try:
            # Try AND first (precise), fall back to OR if nothing found
            and_query = " AND ".join(terms)
            rows = _fts_rows(cur, and_query, session, limit)
            if not rows and len(terms) > 1:
                rows = _fts_rows(cur, " OR ".join(terms), session, limit)
        except Exception:
            # FTS5 not available — LIKE fallback with individual terms ANDed via WHERE clauses
            where_parts = " AND ".join("search_text LIKE ?" for _ in terms[:4])
            like_args = [f"%{t}%" for t in terms[:4]]
            if session:
                rows = cur.execute(
                    f"SELECT bill_id, session, title, description, source_url FROM bill_descriptions "
                    f"WHERE session=? AND {where_parts} LIMIT ?",
                    [session] + like_args + [limit],
                ).fetchall()
            else:
                rows = cur.execute(
                    f"SELECT bill_id, session, title, description, source_url FROM bill_descriptions "
                    f"WHERE {where_parts} LIMIT ?",
                    like_args + [limit],
                ).fetchall()
        conn.close()
        if not rows:
            return ""
        blocks = []
        for bill_id, sess, title, desc, url in rows:
            blocks.append(
                f"[Cached Bill Search Result — {bill_id} {sess}]\n"
                f"{desc}"
                + (f"\nSource: {url}" if url and url not in desc else "")
            )
        return "\n\n".join(blocks)
    except Exception:
        return ""


def _sqlite_legislator_votes(name: str) -> str:
    """Return voting summary and education bill info for a legislator by last name."""
    if not os.path.exists(_VA_LEGIS_DB):
        return ""
    try:
        conn = sqlite3.connect(_VA_LEGIS_DB)
        cur = conn.cursor()
        # Find member_id by last name (partial match)
        name_clean = name.strip().split()[-1]  # use last word as last name
        member = cur.execute(
            "SELECT member_id, member_name FROM members WHERE member_name LIKE ?",
            (f"%{name_clean}%",)
        ).fetchone()
        if not member:
            conn.close()
            return ""
        member_id, member_name = member
        # Overall vote counts
        counts = cur.execute(
            "SELECT vote, COUNT(*) FROM votes WHERE patron_id = ? GROUP BY vote",
            (member_id,)
        ).fetchall()
        vote_map = {r[0]: r[1] for r in counts}
        total = sum(vote_map.values())
        y, n = vote_map.get("Y", 0), vote_map.get("N", 0)
        # Education bills they sponsored
        edu_filter = " OR ".join(["LOWER(Bill_description) LIKE ?" for _ in _EDUCATION_KEYWORDS])
        edu_params = [f"%{k}%" for k in _EDUCATION_KEYWORDS] + [member_id]
        edu_bills = cur.execute(
            f"SELECT Bill_id, Bill_description, Passed, Approved, Vetoed FROM bills "
            f"WHERE ({edu_filter}) AND Patron_id = ? ORDER BY Bill_id",
            edu_params
        ).fetchall()
        conn.close()
        lines = [
            f"[SQLite — Legislator: {member_name} ({member_id})]",
            f"Overall voting record across {total} roll calls: {y} Yes, {n} No, "
            f"{vote_map.get('X', 0)} Not voting, {vote_map.get('P', 0)} Present",
            f"Yes rate: {round(y/total*100, 1) if total else 0}%",
        ]
        if edu_bills:
            lines.append(f"\nEducation bills sponsored by {member_name}:")
            for bill_id, desc, passed, approved, vetoed in edu_bills:
                outcome = "✓ Signed" if approved == "Y" else ("✗ Vetoed" if vetoed == "Y" else ("Passed" if passed == "Y" else "Did not pass"))
                lines.append(f"  {bill_id}: {desc} — {outcome}")
        else:
            lines.append(f"\n{member_name} did not sponsor any education-related bills in this session.")
        return "\n".join(lines)
    except Exception:
        return ""


def _extract_legislator_name(text: str) -> str | None:
    """Extract a legislator name from a query like 'how did McNamara vote on education'."""
    # Check for title + name pattern
    m = _LEGISLATOR_NAME_RE.search(text)
    if m:
        return m.group(2)
    # Check for "how did X vote" pattern
    m2 = re.search(r"how\s+did\s+([A-Z][a-zA-Z\s,\.]+?)\s+vote", text, re.IGNORECASE)
    if m2:
        return m2.group(1).strip()
    # Check for "[name] voting" or "[name] vote"
    m3 = re.search(r"([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\s+vot(?:e|ing|ed)", text, re.IGNORECASE)
    if m3:
        return m3.group(1).strip()
    return None


def _openstates_vote_lookup(bill_numbers: list[str], session: str | None = None) -> str:
    """Return named vote breakdown for bills from openstates_va.db."""
    if not bill_numbers or not os.path.exists(_OPENSTATES_DB):
        return ""
    try:
        conn = sqlite3.connect(_OPENSTATES_DB)
        cur = conn.cursor()
        lines = []
        for bid in bill_numbers:
            # Determine which sessions to search
            if session:
                sessions_to_try = [session]
            else:
                sessions_to_try = cur.execute(
                    "SELECT DISTINCT session FROM bills WHERE bill_id = ? ORDER BY session DESC",
                    (bid,)
                ).fetchall()
                sessions_to_try = [r[0] for r in sessions_to_try] or ["2026"]

            for sess in sessions_to_try:
                bill_row = cur.execute(
                    "SELECT title, sponsors, latest_action, result, openstates_url FROM bills WHERE bill_id=? AND session=?",
                    (bid, sess)
                ).fetchone()
                if not bill_row:
                    continue
                title, sponsors, latest_action, result, url = bill_row

                # Use each voter's most recent recorded option to avoid duplicates across vote events
                vote_rows = cur.execute(
                    """
                    SELECT option, voter_name, party
                    FROM votes
                    WHERE bill_id=? AND session=?
                      AND (voter_name, vote_date) IN (
                          SELECT voter_name, MAX(vote_date)
                          FROM votes WHERE bill_id=? AND session=?
                          GROUP BY voter_name
                      )
                    ORDER BY option, voter_name
                    """,
                    (bid, sess, bid, sess)
                ).fetchall()
                if not vote_rows:
                    continue

                yes_voters = [f"{r[1]} ({r[2]})" for r in vote_rows if r[0] == "yes"]
                no_voters  = [f"{r[1]} ({r[2]})" for r in vote_rows if r[0] == "no"]
                abs_voters = [f"{r[1]} ({r[2]})" for r in vote_rows if r[0] not in ("yes", "no")]

                block = [f"[OpenStates — {bid} {sess} Virginia]"]
                if title:
                    block.append(f"Title: {title}")
                if sponsors:
                    block.append(f"Sponsors: {sponsors}")
                if latest_action:
                    block.append(f"Latest action: {latest_action}")
                if result:
                    block.append(f"Overall result: {result}")
                if yes_voters:
                    block.append(f"YES ({len(yes_voters)}): {', '.join(yes_voters[:30])}"
                                 + (" …" if len(yes_voters) > 30 else ""))
                if no_voters:
                    block.append(f"NO ({len(no_voters)}): {', '.join(no_voters[:30])}"
                                 + (" …" if len(no_voters) > 30 else ""))
                if abs_voters:
                    block.append(f"ABSTAIN/OTHER ({len(abs_voters)}): {', '.join(abs_voters[:20])}")
                if url:
                    block.append(f"Source: {url}")
                lines.append("\n".join(block))

        conn.close()
        return "\n\n".join(lines)
    except Exception:
        return ""


def _openstates_legislator_lookup(name: str) -> str:
    """Return bills and vote positions for a legislator from openstates_va.db."""
    if not os.path.exists(_OPENSTATES_DB):
        return ""
    try:
        conn = sqlite3.connect(_OPENSTATES_DB)
        cur = conn.cursor()
        last = name.strip().split()[-1]
        rows = cur.execute(
            "SELECT DISTINCT voter_name, party, district FROM votes WHERE voter_name LIKE ? LIMIT 5",
            (f"%{last}%",)
        ).fetchall()
        if not rows:
            conn.close()
            return ""
        voter_name, party, district = rows[0]
        ambiguous_note = (
            f" [Note: '{last}' matched {len(rows)} legislators; showing {voter_name}. "
            f"Others: {', '.join(r[0] for r in rows[1:])}]"
            if len(rows) > 1 else ""
        )
        # Vote counts per option
        counts = cur.execute(
            "SELECT option, COUNT(*) FROM votes WHERE voter_name=? GROUP BY option",
            (voter_name,)
        ).fetchall()
        count_map = {r[0]: r[1] for r in counts}
        total = sum(count_map.values())
        yes_n = count_map.get("yes", 0)
        no_n  = count_map.get("no", 0)
        # Bills they voted no on (interesting for questions like "how did X vote on education")
        no_bills = cur.execute(
            "SELECT v.bill_id, v.session, b.title FROM votes v "
            "LEFT JOIN bills b ON b.bill_id=v.bill_id AND b.session=v.session "
            "WHERE v.voter_name=? AND v.option='no' ORDER BY v.session DESC LIMIT 10",
            (voter_name,)
        ).fetchall()
        lines = [
            f"[OpenStates — Legislator: {voter_name} ({party}, District {district}){ambiguous_note}]",
            f"Votes across all tracked sessions: {yes_n} YES, {no_n} NO, {total - yes_n - no_n} other",
            f"Yes rate: {round(yes_n/total*100,1) if total else 0}%",
        ]
        if no_bills:
            lines.append("Recent NO votes:")
            for bill_id, sess, title in no_bills:
                lines.append(f"  {bill_id} ({sess}): {title or '—'}")
        conn.close()
        return "\n".join(lines)
    except Exception:
        return ""


def _extract_session_year(text: str) -> str | None:
    m = _SESSION_YEAR_RE.search(text)
    return m.group(0) if m else None


def _query_chroma(query_embedding: list, n_results: int = 6):
    import httpx
    col_id = _get_chroma_collection_id()
    r = httpx.post(
        f"{_chroma_base()}/collections/{col_id}/query",
        headers=_chroma_headers(),
        json={"query_embeddings": [query_embedding], "n_results": n_results,
              "include": ["documents", "metadatas"]},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


class BillsChatRequest(BaseModel):
    messages: list[ChatMessage]
    district: str = ""
    locality: str = ""
    hod_district: int | None = None
    sd_district: int | None = None

    @field_validator('hod_district', 'sd_district', mode='before')
    @classmethod
    def empty_str_to_none(cls, v):
        if v == '' or v is None:
            return None
        return int(v)


class FeedbackRequest(BaseModel):
    rating: str
    query: str = ""
    reply_hash: str = ""
    district: str = ""


@app.post("/api/feedback")
async def submit_feedback(request: Request, data: FeedbackRequest):
    if data.rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="rating must be 'up' or 'down'")
    db = os.path.join(BASE_DIR, "openstates_va.db")
    try:
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO feedback (created_at, rating, query, reply_hash, district) VALUES (?, ?, ?, ?, ?)",
            (int(time.time()), data.rating, data.query[:500], data.reply_hash[:64], data.district[:100]),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    return {"ok": True}


@app.post("/api/pdf-chat")
async def pdf_chat(
    file: UploadFile = File(...),
    question: str = Form(...),
):
    """
    Accept a PDF upload and answer the user's question grounded strictly in
    the document text. pdfplumber extracts text deterministically so the model
    can only cite what was actually parsed — keeping hallucination near-zero.
    """
    import pdfplumber, io

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    if not question.strip():
        raise HTTPException(status_code=400, detail="question is required")

    pdf_bytes = await file.read()
    if len(pdf_bytes) > 20 * 1024 * 1024:  # 20 MB cap
        raise HTTPException(status_code=413, detail="PDF too large (20 MB max)")

    try:
        pages_text = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            total_pages = len(pdf.pages)
            for page in pdf.pages[:40]:  # cap at 40 pages
                text = page.extract_text() or ""
                if text.strip():
                    pages_text.append(text)
        doc_text = "\n\n".join(pages_text).strip()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not read PDF: {exc}")

    if not doc_text:
        raise HTTPException(status_code=422, detail="No readable text found in PDF")

    system_prompt = (
        "You are a Virginia civic assistant. The user has uploaded a document. "
        "Answer ONLY using the document text provided below. "
        "Do not add facts, figures, names, or dates that are not explicitly in the document. "
        "If the answer is not in the document say: \"I don't see that in this document.\" "
        "Be concise and cite the relevant section when helpful."
    )

    # Trim to ~14 000 chars to stay well within context while leaving room for reply
    grounded_prompt = (
        f"DOCUMENT ({total_pages} pages, filename: {file.filename}):\n\n"
        f"{doc_text[:14000]}\n\n"
        f"---\nQUESTION: {question.strip()}"
    )

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    c = anthropic.Anthropic(api_key=api_key)
    msg = c.messages.create(
        model=_CLAUDE_SONNET_MODEL,
        max_tokens=900,
        system=system_prompt,
        messages=[{"role": "user", "content": grounded_prompt}],
    )
    answer = msg.content[0].text

    return {
        "answer": answer,
        "pages_read": len(pages_text),
        "total_pages": total_pages,
        "chars_used": min(len(doc_text), 14000),
    }


@app.get("/api/congress-debug")
def congress_debug():
    """Show what federal data is currently in polls.db on this server."""
    info = {}
    try:
        conn = sqlite3.connect(_POLLS_DB)
        for table in ("congress_members", "congress_votes", "congress_bills"):
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                info[table] = n
            except Exception as e:
                info[table] = f"missing: {e}"
        # Sample Kiggans votes
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
    info["_federal_members_cache_size"] = len(_federal_members_cache) if _federal_members_cache is not None else "not loaded"
    return info


@app.get("/api/bills-debug")
async def bills_debug():
    chroma_key = os.getenv("CHROMA_API_KEY", "")
    voyage_key = os.getenv("VOYAGE_API_KEY", "")
    env_info = {
        "CHROMA_API_KEY":  chroma_key[:8]  + "..." if chroma_key  else "MISSING",
        "CHROMA_TENANT":   os.getenv("CHROMA_TENANT",   "MISSING"),
        "CHROMA_DATABASE": os.getenv("CHROMA_DATABASE", "MISSING"),
        "VOYAGE_API_KEY":  voyage_key[:8]  + "..." if voyage_key  else "MISSING",
        "version": "v4",
    }
    try:
        col_id   = _get_chroma_collection_id()
        test_vec = _get_voyage_client().embed(
            ["HB4 affordable housing"], model=_BILLS_MODEL, input_type="query"
        ).embeddings[0]
        res = _query_chroma(test_vec, n_results=2)
        return {"status": "ok", "collection_id": col_id, "env": env_info,
                "sample": res["documents"][0]}
    except Exception as e:
        return {"status": "error", "error": str(e), "type": type(e).__name__, "env": env_info}


@app.get("/api/bill-descriptions/search")
def bill_descriptions_search(q: str, session: str | None = None, limit: int = 8):
    """Instant local bill-description search. No AI, embedding, Chroma, or OpenStates calls."""
    if not os.path.exists(_OPENSTATES_DB):
        return {"count": 0, "results": [], "source": "missing_openstates_db"}

    bill_numbers = _extract_bill_numbers(q)
    blocks = (
        _cached_bill_description_lookup(bill_numbers, session)
        if bill_numbers
        else _cached_bill_description_search(q, session, limit)
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
        conn = sqlite3.connect(_OPENSTATES_DB)
        cache_count = conn.execute("SELECT COUNT(*) FROM bill_descriptions").fetchone()[0]
        conn.close()
    except Exception:
        cache_count = None
    return {
        "count": len(results),
        "cache_count": cache_count,
        "source": "local_sqlite_bill_descriptions",
        "results": results[:limit],
    }


@app.get("/api/polls")
def polls_search(
    office: str | None = None,
    cycle: str | None = None,
    race_id: str | None = None,
    limit: int = 25,
):
    """Read Virginia poll rows ingested by ingest_va_polls.py."""
    if not os.path.exists(_POLLS_DB):
        return {"count": 0, "results": [], "source": "missing_polls_db"}
    limit = max(1, min(int(limit or 25), 100))
    where = ["LOWER(COALESCE(state, '')) = 'virginia'"]
    params: list[object] = []
    if office:
        where.append("LOWER(COALESCE(office_type, '')) LIKE ?")
        params.append(f"%{office.lower()}%")
    if cycle:
        where.append("cycle = ?")
        params.append(cycle)
    if race_id:
        where.append("race_id = ?")
        params.append(race_id)

    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='polls'"
        ).fetchone()
        if not table_exists:
            conn.close()
            return {"count": 0, "results": [], "source": "polls_table_not_initialized"}
        rows = conn.execute(
            f"""
            SELECT *
            FROM polls
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(end_date, start_date, created_at, fetched_at) DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        records = []
        for row in rows:
            results = conn.execute(
                """
                SELECT answer, candidate_name, candidate_party, pct
                FROM poll_results
                WHERE source_record_id = ?
                ORDER BY pct DESC
                """,
                (row["source_record_id"],),
            ).fetchall()
            item = dict(row)
            item["results"] = [dict(r) for r in results]
            item.pop("raw_json", None)
            records.append(item)
        conn.close()
        return {"count": len(records), "source": "local_sqlite_polls", "results": records}
    except Exception as exc:
        return {"count": 0, "results": [], "source": "polls_error", "error": str(exc)}


@app.get("/api/poll-articles")
def poll_articles(limit: int = 25):
    """Read poll-related news/RSS mentions ingested by ingest_va_polls.py."""
    if not os.path.exists(_POLLS_DB):
        return {"count": 0, "results": [], "source": "missing_polls_db"}
    limit = max(1, min(int(limit or 25), 100))
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='poll_articles'"
        ).fetchone()
        if not table_exists:
            conn.close()
            return {"count": 0, "results": [], "source": "poll_articles_table_not_initialized"}
        rows = conn.execute(
            """
            SELECT source, title, summary, published_at, url, matched_terms, extracted_numbers, fetched_at
            FROM poll_articles
            ORDER BY COALESCE(published_at, fetched_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
        return {"count": len(rows), "source": "local_sqlite_poll_articles", "results": [dict(r) for r in rows]}
    except Exception as exc:
        return {"count": 0, "results": [], "source": "poll_articles_error", "error": str(exc)}


@app.get("/api/polls-debug")
def polls_debug():
    """Diagnostic: DB path, file existence, row counts, last ingest time."""
    db_path = _POLLS_DB
    info: dict = {"db_path": db_path, "db_exists": os.path.exists(db_path)}
    if info["db_exists"]:
        info["db_size_bytes"] = os.path.getsize(db_path)
    try:
        conn = sqlite3.connect(db_path)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        info["tables"] = tables
        for tbl in ("polls", "poll_results", "poll_articles", "poll_ingest_runs"):
            if tbl in tables:
                info[f"{tbl}_count"] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                if tbl == "polls":
                    row = conn.execute(
                        "SELECT state, COUNT(*) FROM polls GROUP BY state ORDER BY COUNT(*) DESC LIMIT 5"
                    ).fetchall()
                    info["polls_by_state"] = [{"state": r[0], "count": r[1]} for r in row]
                    last = conn.execute("SELECT MAX(fetched_at) FROM polls").fetchone()[0]
                    info["polls_last_fetched"] = last
                if tbl == "poll_ingest_runs":
                    runs = conn.execute(
                        "SELECT source, status, rows_written, started_at FROM poll_ingest_runs ORDER BY id DESC LIMIT 10"
                    ).fetchall()
                    info["recent_ingest_runs"] = [dict(zip(["source","status","rows_written","started_at"], r)) for r in runs]
        conn.close()
    except Exception as exc:
        info["db_error"] = str(exc)
    return info


@app.get("/api/polls-feed")
def polls_feed(
    office: str | None = None,
    limit: int = 60,
):
    """Return Virginia polls with nested candidate results for the polls page."""
    if not os.path.exists(_POLLS_DB):
        return {"count": 0, "results": [], "source": "missing_polls_db"}
    limit = max(1, min(int(limit or 60), 200))
    where = ["state = 'Virginia'"]
    params: list = []
    if office:
        where.append("LOWER(office_type) LIKE LOWER(?)")
        params.append(f"%{office}%")
    where_sql = " AND ".join(where)
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        for tbl in ("polls", "poll_results"):
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
            ).fetchone()
            if not exists:
                conn.close()
                return {"count": 0, "results": [], "source": f"{tbl}_not_initialized"}
        rows = conn.execute(
            f"""
            SELECT p.source_record_id, p.source, p.cycle, p.office_type, p.seat_name,
                   p.stage, p.pollster, p.sponsor, p.fte_grade, p.sample_size,
                   p.population, p.methodology, p.start_date, p.end_date,
                   p.election_date, p.url, p.notes, p.internal, p.partisan,
                   pr.answer, pr.candidate_name, pr.candidate_party, pr.pct
            FROM (
                SELECT * FROM polls
                WHERE {where_sql}
                ORDER BY COALESCE(end_date, start_date, created_at, fetched_at) DESC
                LIMIT ?
            ) p
            LEFT JOIN poll_results pr ON pr.source_record_id = p.source_record_id
            ORDER BY COALESCE(p.end_date, p.start_date, p.created_at) DESC, p.source_record_id
            """,
            params + [limit],
        ).fetchall()
        conn.close()
        polls: dict = {}
        order: list = []
        for row in rows:
            rid = row["source_record_id"]
            if rid not in polls:
                order.append(rid)
                polls[rid] = {
                    "source_record_id": rid,
                    "source": row["source"],
                    "cycle": row["cycle"],
                    "office_type": row["office_type"],
                    "seat_name": row["seat_name"],
                    "stage": row["stage"],
                    "pollster": row["pollster"],
                    "sponsor": row["sponsor"],
                    "fte_grade": row["fte_grade"],
                    "sample_size": row["sample_size"],
                    "population": row["population"],
                    "methodology": row["methodology"],
                    "start_date": row["start_date"],
                    "end_date": row["end_date"],
                    "election_date": row["election_date"],
                    "url": row["url"],
                    "notes": row["notes"],
                    "internal": row["internal"],
                    "partisan": row["partisan"],
                    "results": [],
                }
            if row["candidate_name"] or row["answer"]:
                polls[rid]["results"].append({
                    "answer": row["answer"],
                    "candidate_name": row["candidate_name"],
                    "candidate_party": row["candidate_party"],
                    "pct": row["pct"],
                })
        result_list = [polls[rid] for rid in order]
        return {"count": len(result_list), "source": "local_sqlite_polls_feed", "results": result_list}
    except Exception as exc:
        return {"count": 0, "results": [], "source": "polls_feed_error", "error": str(exc)}


@app.get("/api/spanberger-approval")
def spanberger_approval():
    """Return Spanberger poll numbers over time for the approval tracker chart."""
    if not os.path.exists(_POLLS_DB):
        return {"count": 0, "data": []}
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT p.end_date, p.start_date, p.pollster, p.sample_size,
                   p.population, p.source, p.url,
                   pr.pct, pr.candidate_name, pr.candidate_party
            FROM poll_results pr
            JOIN polls p ON p.source_record_id = pr.source_record_id
            WHERE LOWER(pr.candidate_name) LIKE '%spanberger%'
              AND pr.pct >= 30
              AND (p.cycle >= '2024' OR COALESCE(p.end_date, p.start_date, '') >= '2024')
            ORDER BY COALESCE(p.end_date, p.start_date) ASC
            """
        ).fetchall()
        conn.close()
        data = [
            {
                "date": row["end_date"] or row["start_date"],
                "pollster": row["pollster"],
                "pct": row["pct"],
                "candidate_name": row["candidate_name"],
                "sample_size": row["sample_size"],
                "population": row["population"],
                "source": row["source"],
                "url": row["url"],
            }
            for row in rows
        ]
        return {"count": len(data), "data": data}
    except Exception as exc:
        return {"count": 0, "data": [], "error": str(exc)}


@app.get("/api/congress/members")
def congress_members():
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("""
            SELECT bioguide_id, name, party, chamber, district, website
            FROM congress_members
            ORDER BY chamber DESC, CAST(district AS INTEGER)
        """).fetchall()
        conn.close()
        return {"members": [
            {"bioguide_id": r[0], "name": r[1], "party": r[2],
             "chamber": r[3], "district": r[4], "website": r[5]}
            for r in rows
        ]}
    except Exception as exc:
        return {"members": [], "error": str(exc)}


@app.get("/api/congress/bills/{bioguide_id}")
def congress_member_bills(bioguide_id: str, role: str = "sponsored", limit: int = 20):
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("""
            SELECT b.bill_type, b.bill_number, b.title, b.introduced_date,
                   b.policy_area, b.latest_action, b.latest_action_date, b.role
            FROM congress_bills b
            WHERE b.sponsor_id = ? AND (? = 'all' OR b.role = ?)
            ORDER BY b.introduced_date DESC
            LIMIT ?
        """, (bioguide_id, role, role, limit)).fetchall()
        conn.close()
        return {"bills": [
            {"type": r[0], "number": r[1], "title": r[2], "introduced": r[3],
             "policy_area": r[4], "latest_action": r[5],
             "latest_action_date": r[6], "role": r[7]}
            for r in rows
        ]}
    except Exception as exc:
        return {"bills": [], "error": str(exc)}


@app.get("/api/congress/votes/{bioguide_id}")
def congress_member_votes(bioguide_id: str, limit: int = 50):
    """Return recent roll-call votes for a VA member."""
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("""
            SELECT vote_number, chamber, vote_date, bill, question, member_vote, result
            FROM congress_votes
            WHERE bioguide_id = ?
            ORDER BY vote_date DESC, vote_number DESC
            LIMIT ?
        """, (bioguide_id, limit)).fetchall()
        conn.close()
        return {"votes": [
            {"vote_number": r[0], "chamber": r[1], "date": r[2], "bill": r[3],
             "question": r[4], "vote": r[5], "result": r[6]}
            for r in rows
        ]}
    except Exception as exc:
        return {"votes": [], "error": str(exc)}


@app.get("/api/congress/votes/{bioguide_id}/summary")
def congress_member_vote_summary(bioguide_id: str):
    """Return Yea/Nay/Not Voting counts and party-line stats for a member."""
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("""
            SELECT member_vote, COUNT(*) as cnt
            FROM congress_votes
            WHERE bioguide_id = ?
            GROUP BY member_vote
        """, (bioguide_id,)).fetchall()
        conn.close()
        summary = {r[0]: r[1] for r in rows}
        total = sum(summary.values())
        return {
            "total": total,
            "yea": summary.get("Yea", 0) + summary.get("Aye", 0),
            "nay": summary.get("Nay", 0) + summary.get("No", 0),
            "not_voting": summary.get("Not Voting", 0),
            "present": summary.get("Present", 0),
        }
    except Exception as exc:
        return {"total": 0, "error": str(exc)}


@app.get("/api/congress/bills")
def congress_bills_search(policy_area: str = "", limit: int = 50):
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("""
            SELECT b.bill_type, b.bill_number, b.title, b.introduced_date,
                   b.policy_area, b.latest_action, b.latest_action_date,
                   b.role, m.name, m.party, m.district
            FROM congress_bills b
            JOIN congress_members m ON m.bioguide_id = b.sponsor_id
            WHERE b.role = 'sponsored'
              AND (? = '' OR LOWER(b.policy_area) LIKE '%' || LOWER(?) || '%')
            ORDER BY b.introduced_date DESC
            LIMIT ?
        """, (policy_area, policy_area, limit)).fetchall()
        conn.close()
        return {"bills": [
            {"type": r[0], "number": r[1], "title": r[2], "introduced": r[3],
             "policy_area": r[4], "latest_action": r[5], "latest_action_date": r[6],
             "role": r[7], "sponsor": r[8], "party": r[9], "district": r[10]}
            for r in rows
        ]}
    except Exception as exc:
        return {"bills": [], "error": str(exc)}


@app.get("/api/va-officials")
def va_officials(office: str = "", limit: int = 200):
    try:
        conn = sqlite3.connect(DB_PATH)
        query = """
            SELECT person_name, office, district, party, role,
                   incumbent, finance_url, source_url, data_confidence
            FROM va_finance_people
            WHERE (? = '' OR office = ?)
            ORDER BY
                CASE office
                    WHEN 'Governor' THEN 1
                    WHEN 'Lieutenant Governor' THEN 2
                    WHEN 'Attorney General' THEN 3
                    WHEN 'State Senate' THEN 4
                    WHEN 'House of Delegates' THEN 5
                    ELSE 6
                END,
                CAST(district AS INTEGER)
            LIMIT ?
        """
        rows = conn.execute(query, (office, office, limit)).fetchall()
        conn.close()
        return {"officials": [
            {"name": r[0], "office": r[1], "district": r[2], "party": r[3],
             "role": r[4], "incumbent": bool(r[5]), "finance_url": r[6],
             "source_url": r[7], "confidence": r[8]}
            for r in rows
        ]}
    except Exception as exc:
        return {"officials": [], "error": str(exc)}


@app.get("/representatives", response_class=HTMLResponse)
def representatives_page():
    with open(os.path.join(BASE_DIR, "templates", "representatives.html"), "r", encoding="utf-8") as f:
        return f.read()


# ── 2026 Races ────────────────────────────────────────────────────────────────

_races_2026_cache: dict | None = None
_races_2026_cache_ts: float = 0.0
_RACES_2026_TTL = 3600  # 1 hour


_ALL_RACES_2026 = {
    "S":   {"race_id": "S",   "label": "U.S. Senate — Kaine Seat (Class II)", "office": "S", "district": "00", "sort_key": (0, 0)},
    "H1":  {"race_id": "H1",  "label": "House — District 1",  "office": "H", "district": "01", "sort_key": (1, 1)},
    "H2":  {"race_id": "H2",  "label": "House — District 2",  "office": "H", "district": "02", "sort_key": (1, 2)},
    "H3":  {"race_id": "H3",  "label": "House — District 3",  "office": "H", "district": "03", "sort_key": (1, 3)},
    "H4":  {"race_id": "H4",  "label": "House — District 4",  "office": "H", "district": "04", "sort_key": (1, 4)},
    "H5":  {"race_id": "H5",  "label": "House — District 5",  "office": "H", "district": "05", "sort_key": (1, 5)},
    "H6":  {"race_id": "H6",  "label": "House — District 6",  "office": "H", "district": "06", "sort_key": (1, 6)},
    "H7":  {"race_id": "H7",  "label": "House — District 7",  "office": "H", "district": "07", "sort_key": (1, 7)},
    "H8":  {"race_id": "H8",  "label": "House — District 8",  "office": "H", "district": "08", "sort_key": (1, 8)},
    "H9":  {"race_id": "H9",  "label": "House — District 9",  "office": "H", "district": "09", "sort_key": (1, 9)},
    "H10": {"race_id": "H10", "label": "House — District 10", "office": "H", "district": "10", "sort_key": (1, 10)},
    "H11": {"race_id": "H11", "label": "House — District 11", "office": "H", "district": "11", "sort_key": (1, 11)},
}


def _load_current_holders() -> dict[str, dict]:
    """Return dict of race_key -> current holder info from congress_members table."""
    holders: dict[str, dict] = {}
    try:
        db = sqlite3.connect(_POLLS_DB)
        db.row_factory = sqlite3.Row
        for row in db.execute(
            "SELECT bioguide_id, name, party, chamber, district FROM congress_members WHERE state='Virginia'"
        ):
            name_raw = row["name"]
            if "," in name_raw:
                last, first = name_raw.split(",", 1)
                first_word = first.strip().split()[0]
                display = f"{first_word} {last.strip()}"
            else:
                display = name_raw
            party_map = {"Democratic": "DEM", "Republican": "REP", "Independent": "IND"}
            party = party_map.get(row["party"], (row["party"] or "")[:3].upper())
            if row["chamber"] == "Senate":
                if row["bioguide_id"] == "K000384":  # only Kaine's seat up in 2026
                    key = "S"
                else:
                    continue
            else:
                key = f"H{row['district']}"
            holders[key] = {
                "bioguide_id": row["bioguide_id"],
                "name": display,
                "party": party,
                "party_label": {"DEM": "Democrat", "REP": "Republican"}.get(party, party),
            }
        db.close()
    except Exception:
        pass
    return holders


def _fetch_races_2026() -> dict:
    global _races_2026_cache, _races_2026_cache_ts
    now = time.time()
    if _races_2026_cache and now - _races_2026_cache_ts < _RACES_2026_TTL:
        return _races_2026_cache

    current_holders = _load_current_holders()
    api_key = os.getenv("FEC_API_KEY", "DEMO_KEY")
    base = "https://api.open.fec.gov/v1"

    candidates: list[dict] = []
    page = 1
    while True:
        try:
            resp = requests.get(
                f"{base}/candidates/totals/",
                params={"api_key": api_key, "state": "VA", "election_year": 2026,
                        "per_page": 100, "page": page, "sort": "name"},
                timeout=20,
            )
            resp.raise_for_status()
            body = resp.json()
            results = body.get("results", [])
            candidates.extend(results)
            if len(results) < 100:
                break
            page += 1
        except Exception as exc:
            return {"races": [], "total_candidates": 0, "cycle": 2026, "error": str(exc)}

    _PARTY_LABELS = {"REP": "Republican", "DEM": "Democrat", "IND": "Independent",
                     "LIB": "Libertarian", "GRE": "Green"}

    enriched: list[dict] = []
    for c in candidates:
        name_raw = c.get("name", "")
        if "," in name_raw:
            last, first = name_raw.split(",", 1)
            display_name = f"{first.strip().title()} {last.strip().title()}"
        else:
            display_name = name_raw.title()
        party = c.get("party", "")
        enriched.append({
            "candidate_id": c.get("candidate_id", ""),
            "name": display_name,
            "party": party,
            "party_label": _PARTY_LABELS.get(party, party),
            "office": c.get("office", ""),
            "district": c.get("district", ""),
            "incumbent_challenge": c.get("incumbent_challenge_full", ""),
            "raised": c.get("receipts"),
            "spent": c.get("disbursements"),
            "cash": c.get("last_cash_on_hand_end_period"),
            "fec_url": f"https://www.fec.gov/data/candidate/{c.get('candidate_id', '')}/",
            "is_fec_filed": True,
            "bioguide_id": None,
        })

    # Build all 12 races upfront so every district shows even with zero filings
    races: dict[str, dict] = {}
    for key, meta in _ALL_RACES_2026.items():
        races[key] = {**meta, "candidates": [],
                      "current_holder": current_holders.get(key),
                      "no_filings": False}

    for c in enriched:
        key = "S" if c["office"] == "S" else f"H{int(c['district'].lstrip('0') or 0)}"
        if key not in races:
            continue
        # Try to link FEC candidate to known incumbent by last name + party
        holder = races[key].get("current_holder")
        if holder and not c.get("bioguide_id"):
            holder_last = holder["name"].split()[-1].upper()
            cand_last = c["name"].split()[-1].upper()
            if holder_last == cand_last and holder["party"] == c["party"]:
                c["bioguide_id"] = holder["bioguide_id"]
        races[key]["candidates"].append(c)

    from collections import Counter
    for race in races.values():
        race["candidates"].sort(key=lambda x: (
            0 if x.get("incumbent_challenge") == "Incumbent" else 1,
            -(x.get("raised") or 0),
        ))
        pc = Counter(c["party"] for c in race["candidates"])
        race["has_dem_primary"] = pc.get("DEM", 0) > 1
        race["has_rep_primary"] = pc.get("REP", 0) > 1
        race["no_filings"] = len(race["candidates"]) == 0

    sorted_races = sorted(races.values(), key=lambda r: r["sort_key"])
    for r in sorted_races:
        r.pop("sort_key", None)

    result = {"races": sorted_races, "total_candidates": len(enriched), "cycle": 2026}
    _races_2026_cache = result
    _races_2026_cache_ts = now
    return result


@app.get("/api/races/2026")
def api_races_2026():
    return _fetch_races_2026()


@app.get("/api/races/2026/refresh")
def api_races_2026_refresh():
    global _races_2026_cache, _races_2026_cache_ts
    _races_2026_cache = None
    _races_2026_cache_ts = 0.0
    return _fetch_races_2026()


@app.get("/races-2026", response_class=HTMLResponse)
def races_2026_page():
    with open(os.path.join(BASE_DIR, "templates", "races2026.html"), "r", encoding="utf-8") as f:
        return f.read()


@app.get("/news", response_class=HTMLResponse)
def news_page():
    with open(os.path.join(BASE_DIR, "templates", "news.html"), "r", encoding="utf-8") as f:
        return f.read()


@app.get("/polls", response_class=HTMLResponse)
def polls_page():
    with open(os.path.join(BASE_DIR, "templates", "polls.html"), "r", encoding="utf-8") as f:
        return f.read()


@app.post("/api/bills-chat", response_model=ChatResponse)
@limiter.limit("10/minute")
async def bills_chat(request: Request, req: BillsChatRequest):
    user_query = next(
        (m.content for m in reversed(req.messages) if m.role == "user"), ""
    )
    mentioned = _extract_bill_numbers(user_query)
    session_year = _extract_session_year(user_query)
    cached_bill_context = (
        _cached_bill_description_lookup(mentioned, session_year)
        if mentioned
        else _cached_bill_description_search(user_query, session_year)
    )
    use_haiku = _simple_bill_lookup_question(user_query, mentioned, cached_bill_context)

    query_vec = None
    results = {"documents": [[]], "metadatas": [[]]}
    chroma_error = None

    if not cached_bill_context:
        try:
            query_vec = _get_voyage_client().embed([user_query], model=_BILLS_MODEL, input_type="query").embeddings[0]
        except Exception as e:
            chroma_error = f"Voyage AI unavailable: {e}"

    if query_vec is not None:
        try:
            results = _query_chroma(query_vec, n_results=10)
        except Exception as e:
            chroma_error = f"ChromaDB unavailable: {e}"

    exact_lookup_note = ""
    try:
        context_blocks = []
        seen_docs: set[str] = set()

        # SQLite bill lookup — supplements ChromaDB with local 2026 bill data
        if cached_bill_context:
            for block in cached_bill_context.split("\n\n"):
                if block and block not in seen_docs:
                    seen_docs.add(block)
                    context_blocks.insert(0, block)

        if mentioned:
            sqlite_bill = _sqlite_bill_lookup(mentioned)
            if sqlite_bill:
                for block in sqlite_bill.split("\n\n"):
                    if block and block not in seen_docs:
                        seen_docs.add(block)
                        context_blocks.insert(0, block)

        # OpenStates vote lookup — named votes (who voted yes/no) per bill
        if mentioned:
            os_votes = _openstates_vote_lookup(mentioned, session_year)
            if os_votes:
                for block in os_votes.split("\n\n"):
                    if block and block not in seen_docs:
                        seen_docs.add(block)
                        context_blocks.insert(0, block)

        # SQLite legislator vote lookup
        leg_name = _extract_legislator_name(user_query)
        if leg_name:
            sqlite_leg = _sqlite_legislator_votes(leg_name)
            if sqlite_leg and sqlite_leg not in seen_docs:
                seen_docs.add(sqlite_leg)
                context_blocks.insert(0, sqlite_leg)
            # Also check OpenStates for named vote history
            os_leg = _openstates_legislator_lookup(leg_name)
            if os_leg and os_leg not in seen_docs:
                seen_docs.add(os_leg)
                context_blocks.insert(0, os_leg)

        # Representative profile chunks — powers "what has my rep done?" prompts
        rep_profiles = _request_rep_profiles(req, user_query)
        if rep_profiles:
            for block in rep_profiles.split("\n\n[Representative Profile"):
                if block.startswith("[Representative Profile"):
                    profile_block = block
                else:
                    profile_block = "[Representative Profile" + block if block.strip() else ""
                if profile_block and profile_block not in seen_docs:
                    seen_docs.add(profile_block)
                    context_blocks.insert(0, profile_block)

        # Exact bill-number lookup — always runs when query names a specific bill
        if mentioned:
            exact_docs = _fetch_bills_by_id(mentioned, session_year)
            if session_year:
                if exact_docs:
                    exact_lookup_note = (
                        f"\nEXACT LOOKUP: Found excerpts for {', '.join(mentioned)} "
                        f"from the {session_year} session.\n"
                    )
                else:
                    exact_lookup_note = (
                        f"\nEXACT LOOKUP: No excerpts were found for {', '.join(mentioned)} "
                        f"from the {session_year} session. If the excerpts below include the same "
                        "bill number from another session, describe it only as related data from a "
                        "different session.\n"
                    )
            else:
                years_found = sorted(
                    {
                        _session_year(meta.get("session"))
                        or _session_year(meta.get("session_id"))
                        or _session_year(doc)
                        for doc, meta in exact_docs
                    }
                )
                years_found = [year for year in years_found if year]
                if years_found:
                    exact_lookup_note = (
                        f"\nEXACT LOOKUP: Found excerpts for {', '.join(mentioned)} "
                        f"from embedded sessions: {', '.join(years_found)}.\n"
                    )
            for doc, meta in exact_docs:
                if doc not in seen_docs:
                    seen_docs.add(doc)
                    label = f"[{meta.get('chunk_type','?')} — {meta.get('bill_id','?')} {meta.get('session','?')}]"
                    context_blocks.append(f"{label}\n{doc}")
        elif session_year:
            session_docs = _fetch_bills_by_session(session_year)
            if session_docs:
                exact_lookup_note = (
                    f"\nSESSION LOOKUP: Found {len(session_docs)} representative bill excerpts "
                    f"from the embedded {session_year} session. Answer using these {session_year} "
                    "excerpts first; mention other years only if the user asks for comparison.\n"
                )
            else:
                exact_lookup_note = (
                    f"\nSESSION LOOKUP: No embedded bill excerpts were found for the {session_year} session.\n"
                )
            for doc, meta in session_docs:
                if doc not in seen_docs:
                    seen_docs.add(doc)
                    label = f"[{meta.get('chunk_type','?')} — {meta.get('bill_id','?')} {meta.get('session','?')}]"
                    context_blocks.append(f"{label}\n{doc}")

        # Semantic search results
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            if session_year:
                found_year = _session_year(meta.get("session")) or _session_year(meta.get("session_id")) or _session_year(doc)
                if found_year and found_year != session_year:
                    continue
            if doc not in seen_docs:
                seen_docs.add(doc)
                label = f"[{meta.get('chunk_type','?')} — {meta.get('bill_id','?')} {meta.get('session','?')}]"
                context_blocks.append(f"{label}\n{doc}")

        context = "\n\n---\n\n".join(context_blocks)
    except Exception as e:
        # Even if parsing fails, try to answer from SQLite alone
        context = ""
        chroma_error = f"Result parsing error: {e}"

    # If AI stack failed but SQLite has something useful, still answer
    if not context and chroma_error:
        sqlite_fallback = ""
        try:
            mentioned = _extract_bill_numbers(user_query)
            leg_name = _extract_legislator_name(user_query)
            if mentioned:
                sqlite_fallback = _sqlite_bill_lookup(mentioned)
                os_fb = _openstates_vote_lookup(mentioned)
                if os_fb:
                    sqlite_fallback = (sqlite_fallback + "\n\n" + os_fb).strip()
            elif leg_name:
                sqlite_fallback = _sqlite_legislator_votes(leg_name)
                os_fb = _openstates_legislator_lookup(leg_name)
                if os_fb:
                    sqlite_fallback = (sqlite_fallback + "\n\n" + os_fb).strip()
            profiles_fb = _request_rep_profiles(req, user_query)
            if profiles_fb:
                sqlite_fallback = (sqlite_fallback + "\n\n" + profiles_fb).strip()
        except Exception:
            pass
        if sqlite_fallback:
            context = sqlite_fallback
        else:
            return ChatResponse(reply="I'm having trouble connecting to the knowledge base right now. Try asking about a specific bill number (e.g. HB9) or a legislator's name and I'll look it up from local data.")

    district_parts = []
    if req.district:
        district_parts.append(f"Congressional district: {req.district}")
    if req.locality:
        district_parts.append(f"locality: {req.locality}")
    if req.hod_district:
        district_parts.append(f"HOD district: {req.hod_district}")
    if req.sd_district:
        district_parts.append(f"Senate district: {req.sd_district}")
    district_note = f"\nUSER'S DISTRICT CONTEXT: {', '.join(district_parts)}\n" if district_parts else ""

    # Response cache — skip on multi-turn conversations (only cache single-question queries)
    _ck = _cache_key(user_query, district_note) if len(req.messages) == 1 else None
    # Fallback key using only HOD/SD district — matches prewarm entries which omit congressional district
    _ck_fallback = None
    if len(req.messages) == 1 and (req.hod_district or req.sd_district):
        _state_parts = []
        if req.hod_district: _state_parts.append(f"HOD district: {req.hod_district}")
        if req.sd_district: _state_parts.append(f"Senate district: {req.sd_district}")
        _ck_fallback = _cache_key(user_query, f"\nUSER'S DISTRICT CONTEXT: {', '.join(_state_parts)}\n")
    if _ck:
        cached = _get_cached_reply(_ck, _ck_fallback)
        if cached:
            if not cached.rstrip().endswith("public datasets.*"):
                cached = cached.rstrip() + _SOURCE_LINE
            return ChatResponse(reply=cached)

    chroma_note = f"\nNOTE: AI knowledge base unavailable ({chroma_error}). Answering from local database only.\n" if chroma_error else ""
    model_note = "\nMODEL ROUTING: Simple exact bill lookup using cached local bill context; answer briefly.\n" if use_haiku else ""

    system_prompt = f"""You are VoteIQ, a nonpartisan Virginia civic assistant. Today is May 2026. \
You have access to the retrieved excerpts below, which may include Virginia General Assembly bills, \
election results, legislator voting records, representative profile summaries from the local 2026 session database, \
AND roll-call votes and sponsored bills for Virginia's 13 federal representatives (119th Congress) from congress.gov. \
Answer the user's question using ONLY the excerpts below — do not rely on your training data. \
Be factual and cite bill numbers when relevant. \
For federal members (U.S. House/Senate), cite bill type and number (e.g. H.R. 23) and note the source as congress.gov.{district_note}{chroma_note}{model_note}

VOTE INTERPRETATION — apply these rules when reading vote records:
- If a legislator votes YES on passage but NO on concurrence/conference substitute, they likely objected to the amended version, not the bill itself. Say: "voted against the House-amended version; accepted final compromise."
- If a legislator votes YES in committee but NO on floor, they may have had ideological concerns or constituent pressure. Do not assume — say "voted NO on floor passage after supporting it in committee; dataset does not explain the change."
- Always show the SEQUENCE of votes when available, not just the final result. A bill can have 4-8 votes — the pattern matters.
- Flag when a NO vote is on a substitute or amendment vs. the original bill. These are different positions.
- "Concur House Substitute", "Concur House Amendment", "Adopt Conference Committee Report" are amendment/concurrence votes — not original passage votes.
- "Reported from [Committee]" = committee vote. "Passage R" or "Passage H" = floor vote.

HALLUCINATION PREVENTION — follow these rules strictly:
1. Never say a legislator "prioritized", "championed", "focused on", or "made X a priority" unless the excerpt explicitly states it. Sponsoring a bill does not imply it was a priority.
2. Never infer a legislator's role beyond what the data shows. If they are listed as sponsor, say "sponsored". If their exact role is unclear, say "co-sponsored or listed as patron — exact role unclear."
3. Always distinguish:
   - CONFIRMED: data directly states it — cite bill ID and source ("According to OpenStates, Rouse sponsored SB3")
   - INFERRED: your interpretation of a pattern — label it ("Based on vote pattern, though not a stated position...")
   - UNCERTAIN: data not available — say so ("I don't have vote data for this bill" / "Primary patron unclear")
4. Never fill data gaps with assumptions. If you don't have it, say so.
5. Always cite source inline: "According to OpenStates (openstates.org)" for votes/sponsorships, "According to LIS" for bill text/status.

WHEN TO SAY "I DON'T KNOW":
- Vote data missing → "I don't have vote data for this bill in the current dataset"
- Bill text not in DB → "I don't have the full bill text — check lis.virginia.gov"
- Patron unclear → "Primary patron unclear from available data; listed as co-sponsor"
- DB build incomplete → "Based on partial 2026 session data — full dataset loads tomorrow"

CITATION FORMAT — always include:
- Bill ID as a clickable markdown link if the excerpt provides a URL — use the exact URL from the excerpt, never construct your own
- Short title after the link
- Vote count if available (e.g. "passed 32-8 according to OpenStates")
- Source database (LIS or OpenStates)
- Date if relevant
- Legislator names as clickable links if the profile excerpt provides a URL for them

RESPONSE FORMAT — use this exact structure for legislator questions:

Your [chamber] representative is **[Full Name] ([Party], District [N])**.

**[YEAR] Session Voting Record:**
- Overall vote rate: [CONFIRMED — OpenStates] [Y] YES ([X]%), [N] NO out of [N] floor votes
- Party alignment: [CONFIRMED — calculated from vote records] voted with [Party] party majority on [X]% of floor votes
- Caucus read: [copy exactly from excerpt if present; use the plain-language wording, not the old technical faction label]
- Committee votes: [CONFIRMED — OpenStates] [N] total — [Y] YES, [N] NO

**Key Votes** (if present in excerpt; put this before aggregate issue stats):
- [markdown bill link from excerpt]: [YES/NO note exactly from excerpt] — [one-line plain-English issue summary]

**Dissenting Votes — voted NO but bill passed ([N] total):**
[CONFIRMED — OpenStates, INFERRED significance]:
- [Policy area]: [markdown bill links from excerpt] — [one-line description]
Pattern: [INFERRED] [one sentence. Always end with: "Dataset does not include stated reasons for these votes."]

**Vote Breakdown by Issue Area** (if present in excerpt):
- [topic]: [N] votes — [Y] YES, [N] NO | party alignment: [X]%
  - Breaks from party: [copy the NO-against-party-YES-majority bill links from excerpt]

**Legislative Partnerships** (if present in excerpt):
- [Name] ([Party]) — [N] bills co-sponsored [cross-party if flagged]
- Bipartisan: [names if present]

**Bills Sponsored ([N] total, [X] passed):**
- [CONFIRMED — OpenStates]: [bill link] — [one plain-English sentence: what does this bill actually do for a Virginia resident?]

PLAIN-ENGLISH BILL HOOKS — for every bill you mention, add a plain-English parenthetical after the link:
- Bad: [HB665 — Virtual currency kiosk operators...](url)
- Good: [HB665 — Virtual currency kiosk operators...](url) — Requires crypto ATM operators to get a state license and follow consumer protection rules.
Keep it to one sentence. Use everyday language, not legislative jargon.

LEGISLATIVE FOCUS — if the excerpt contains a "Legislative focus" line, lead with it as a one-liner:
"[Name] focuses mainly on [topics] legislation — [N] bills sponsored this session, [X] passed."

COMMITTEE VOTE CONTEXT — if the excerpt contains a [CONTEXT] note about committee votes, include it in plain language:
- 0 NO votes in hundreds of committee votes → "Voted YES on every committee bill — typical for majority-party members who control committee assignments."
- Near-zero NO rate → explain it's committee culture, not rubber-stamping, and that floor votes show the real contested positions.

IMPORTANT: Always copy bill links exactly as they appear in the excerpts — format is [BILLID — title](url). Never shorten or reformat them. List ALL sponsored bills found in the excerpt, do not truncate the list.
When a bill excerpt contains a "Companion bill(s)" line, always render those as clickable markdown links in your response. If a companion bill ID is mentioned in text but has no URL in the excerpt, construct its link as [BILLID — title](https://openstates.org/va/bills/{{year}}/{{bill_id}}/) using the session year from the excerpt.

**Methodology note** (always include when answering about caucus labels or party alignment):
Caucus read is plain-language shorthand derived from OpenStates roll-call data (confirmed votes only). Split-vote threshold: 15–85% of party voting YES, minimum 5 members voting. "Breaks from party" means actual recorded NO votes against the party YES majority. Issue-area tags are keyword-based, not official legislative categories.

CALL TO ACTION — always end every legislator response with:
**Want to dig deeper?** Ask me how [Name] voted on [topic1], [topic2], or [topic3]. Or ask about a specific bill by number.
Use the legislator's actual name and their real top 3 issue areas from the excerpt.

If any section has no data, write: "No [section] data available in current dataset."{exact_lookup_note}

EXCERPTS:
{context}"""

    try:
        reply = _claude_reply(
            system_prompt,
            req.messages,
            max_tokens=700 if use_haiku else 1800,
            model=_CLAUDE_HAIKU_MODEL if use_haiku else _CLAUDE_SONNET_MODEL,
        )
        if not reply.rstrip().endswith("public datasets.*"):
            reply = reply.rstrip() + _SOURCE_LINE
        if _ck:
            _set_cached_reply(_ck, reply)
        return ChatResponse(reply=reply)
    except Exception as e:
        return ChatResponse(reply=_friendly_claude_error(e))


@app.post("/api/bills-chat-stream")
@limiter.limit("10/minute")
async def bills_chat_stream(request: Request, req: BillsChatRequest):
    """Streaming version of bills-chat — tokens appear as Claude generates them."""
    # Reuse the same context/prompt/cache logic as bills_chat
    user_query = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    mentioned = _extract_bill_numbers(user_query)
    session_year = _extract_session_year(user_query)
    cached_bill_context = (
        _cached_bill_description_lookup(mentioned, session_year)
        if mentioned
        else _cached_bill_description_search(user_query, session_year)
    )
    use_haiku = _simple_bill_lookup_question(user_query, mentioned, cached_bill_context)

    query_vec = None
    results = {"documents": [[]], "metadatas": [[]]}
    chroma_error = None

    if not cached_bill_context:
        try:
            query_vec = _get_voyage_client().embed([user_query], model=_BILLS_MODEL, input_type="query").embeddings[0]
        except Exception as e:
            chroma_error = f"Voyage AI unavailable: {e}"

    if query_vec is not None:
        try:
            results = _query_chroma(query_vec, n_results=10)
        except Exception as e:
            chroma_error = f"ChromaDB unavailable: {e}"

    exact_lookup_note = ""
    try:
        context_blocks: list[str] = []
        seen_docs: set[str] = set()
        if cached_bill_context:
            for block in cached_bill_context.split("\n\n"):
                if block and block not in seen_docs:
                    seen_docs.add(block); context_blocks.insert(0, block)
        if mentioned:
            sqlite_bill = _sqlite_bill_lookup(mentioned)
            if sqlite_bill:
                for block in sqlite_bill.split("\n\n"):
                    if block and block not in seen_docs:
                        seen_docs.add(block); context_blocks.insert(0, block)
        if mentioned:
            os_votes = _openstates_vote_lookup(mentioned, session_year)
            if os_votes:
                for block in os_votes.split("\n\n"):
                    if block and block not in seen_docs:
                        seen_docs.add(block); context_blocks.insert(0, block)
        leg_name = _extract_legislator_name(user_query)
        if leg_name:
            for fn in (_sqlite_legislator_votes, _openstates_legislator_lookup):
                result = fn(leg_name)
                if result and result not in seen_docs:
                    seen_docs.add(result); context_blocks.insert(0, result)
        rep_profiles = _request_rep_profiles(req, user_query)
        if rep_profiles:
            for block in rep_profiles.split("\n\n[Representative Profile"):
                pb = block if block.startswith("[Representative Profile") else (
                    "[Representative Profile" + block if block.strip() else ""
                )
                if pb and pb not in seen_docs:
                    seen_docs.add(pb); context_blocks.insert(0, pb)
        fed_member = _federal_member_by_name(user_query)
        if fed_member:
            fed_ctx = _fetch_federal_context(fed_member)
            if fed_ctx and fed_ctx not in seen_docs:
                seen_docs.add(fed_ctx); context_blocks.insert(0, fed_ctx)
        if mentioned:
            exact_docs = _fetch_bills_by_id(mentioned, session_year)
            if session_year:
                exact_lookup_note = (
                    f"\nEXACT LOOKUP: Found excerpts for {', '.join(mentioned)} from the {session_year} session.\n"
                    if exact_docs else
                    f"\nEXACT LOOKUP: No excerpts were found for {', '.join(mentioned)} from the {session_year} session.\n"
                )
            for doc, meta in exact_docs:
                if doc not in seen_docs:
                    seen_docs.add(doc)
                    context_blocks.append(f"[{meta.get('chunk_type','?')} — {meta.get('bill_id','?')} {meta.get('session','?')}]\n{doc}")
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            if session_year:
                fy = _session_year(meta.get("session")) or _session_year(meta.get("session_id")) or _session_year(doc)
                if fy and fy != session_year:
                    continue
            if doc not in seen_docs:
                seen_docs.add(doc)
                context_blocks.append(f"[{meta.get('chunk_type','?')} — {meta.get('bill_id','?')} {meta.get('session','?')}]\n{doc}")
        context = "\n\n---\n\n".join(context_blocks)
    except Exception as e:
        context = ""; chroma_error = f"Result parsing error: {e}"

    if not context and chroma_error:
        fallback_msg = "I'm having trouble connecting to the knowledge base right now. Try asking about a specific bill number (e.g. HB9) or a legislator's name."
        async def _err():
            yield f"data: {json.dumps({'token': fallback_msg})}\n\ndata: [DONE]\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    district_note = ""
    if req.district:
        parts = [f"Congressional district: {req.district}"]
        if req.locality: parts.append(f"locality: {req.locality}")
        if req.hod_district: parts.append(f"HOD district: {req.hod_district}")
        if req.sd_district: parts.append(f"Senate district: {req.sd_district}")
        district_note = f"\nUSER'S DISTRICT CONTEXT: {', '.join(parts)}\n"

    _ck = _cache_key(user_query, district_note) if len(req.messages) == 1 else None
    _ck_fb = None
    if len(req.messages) == 1 and (req.hod_district or req.sd_district):
        _sp = []
        if req.hod_district: _sp.append(f"HOD district: {req.hod_district}")
        if req.sd_district: _sp.append(f"Senate district: {req.sd_district}")
        _ck_fb = _cache_key(user_query, f"\nUSER'S DISTRICT CONTEXT: {', '.join(_sp)}\n")
    if _ck:
        cached = _get_cached_reply(_ck, _ck_fb)
        if cached:
            if not cached.rstrip().endswith("public datasets.*"):
                cached = cached.rstrip() + _SOURCE_LINE
            async def _cached_gen(text=cached):
                chunk = 24
                for i in range(0, len(text), chunk):
                    yield f"data: {json.dumps({'token': text[i:i+chunk]})}\n\n"
                    await asyncio.sleep(0)
                yield "data: [DONE]\n\n"
            return StreamingResponse(_cached_gen(), media_type="text/event-stream")

    chroma_note = (
        f"\nNOTE: AI knowledge base unavailable ({chroma_error}). Answering from local database only.\n"
        if chroma_error else ""
    )
    model_note = "\nMODEL ROUTING: Simple exact bill lookup using cached local bill context; answer briefly.\n" if use_haiku else ""
    system_prompt = f"""You are VoteIQ, a nonpartisan Virginia civic assistant. Today is May 2026. \
You have access to the retrieved excerpts below, which may include Virginia General Assembly bills, \
election results, legislator voting records, representative profile summaries from the local 2026 session database, \
AND roll-call votes and sponsored bills for Virginia's 13 federal representatives (119th Congress) from congress.gov. \
Answer the user's question using ONLY the excerpts below — do not rely on your training data. \
Be factual and cite bill numbers when relevant. \
For federal members (U.S. House/Senate), cite bill type and number (e.g. H.R. 23) and note the source as congress.gov.{district_note}{chroma_note}{model_note}

VOTE INTERPRETATION — apply these rules when reading vote records:
- If a legislator votes YES on passage but NO on concurrence/conference substitute, they likely objected to the amended version, not the bill itself. Say: "voted against the House-amended version; accepted final compromise."
- If a legislator votes YES in committee but NO on floor, they may have had ideological concerns or constituent pressure. Do not assume — say "voted NO on floor passage after supporting it in committee; dataset does not explain the change."
- Always show the SEQUENCE of votes when available, not just the final result. A bill can have 4-8 votes — the pattern matters.
- Flag when a NO vote is on a substitute or amendment vs. the original bill. These are different positions.
- "Concur House Substitute", "Concur House Amendment", "Adopt Conference Committee Report" are amendment/concurrence votes — not original passage votes.
- "Reported from [Committee]" = committee vote. "Passage R" or "Passage H" = floor vote.

HALLUCINATION PREVENTION — follow these rules strictly:
1. Never say a legislator "prioritized", "championed", "focused on", or "made X a priority" unless the excerpt explicitly states it. Sponsoring a bill does not imply it was a priority.
2. Never infer a legislator's role beyond what the data shows. If they are listed as sponsor, say "sponsored". If their exact role is unclear, say "co-sponsored or listed as patron — exact role unclear."
3. Always distinguish:
   - CONFIRMED: data directly states it
   - INFERRED: your interpretation — label it
   - UNCERTAIN: data not available — say so
4. Never fill data gaps with assumptions. If you don't have it, say so.
5. Always cite source inline: "According to OpenStates (openstates.org)" for votes/sponsorships, "According to LIS" for bill text/status.

WHEN TO SAY "I DON'T KNOW":
- Vote data missing → "I don't have vote data for this bill in the current dataset"
- Bill text not in DB → "I don't have the full bill text — check lis.virginia.gov"
- Patron unclear → "Primary patron unclear from available data; listed as co-sponsor"

CITATION FORMAT — always include bill ID as a clickable markdown link, vote counts, source database, legislator links when available.

RESPONSE FORMAT — use this exact structure for legislator questions:

Your [chamber] representative is **[Full Name] ([Party], District [N])**.

**[YEAR] Session Voting Record:**
- Overall vote rate: [CONFIRMED — OpenStates] [Y] YES ([X]%), [N] NO out of [N] floor votes
- Party alignment: [CONFIRMED — calculated from vote records] voted with [Party] party majority on [X]% of floor votes
- Caucus read: [copy exactly from excerpt if present]
- Committee votes: [CONFIRMED — OpenStates] [N] total — [Y] YES, [N] NO

**Key Votes** (if present in excerpt):
- [markdown bill link]: [YES/NO] — [one-line plain-English issue summary]

**Dissenting Votes — voted NO but bill passed ([N] total):**
- [Policy area]: [bill links] — [one-line description]
Pattern: [INFERRED] [one sentence ending with "Dataset does not include stated reasons for these votes."]

**Vote Breakdown by Issue Area** (if present in excerpt):
- [topic]: [N] votes — [Y] YES, [N] NO | party alignment: [X]%

**Legislative Partnerships** (if present in excerpt):
- [Name] ([Party]) — [N] bills co-sponsored

**Bills Sponsored ([N] total, [X] passed):**
- [bill link] — [one plain-English sentence]

PLAIN-ENGLISH BILL HOOKS — for every bill, add a plain-English one-sentence summary after the link.
LEGISLATIVE FOCUS — if excerpt contains "Legislative focus" line, lead with it.
COMMITTEE VOTE CONTEXT — if excerpt contains [CONTEXT] note, include it in plain language.
IMPORTANT: Always copy bill links exactly. List ALL sponsored bills. Render Companion bill lines as links.

**Methodology note** (always include for caucus/party alignment questions):
Caucus read is plain-language shorthand derived from OpenStates roll-call data. Split-vote threshold: 15–85% YES, minimum 5 members. "Breaks from party" = actual NO votes against party majority. Issue-area tags are keyword-based.

CALL TO ACTION — always end legislator responses with:
**Want to dig deeper?** Ask me how [Name] voted on [topic1], [topic2], or [topic3]. Or ask about a specific bill by number.
Use the legislator's actual name and their real top 3 issue areas from the excerpt.

If any section has no data, write: "No [section] data available in current dataset."{exact_lookup_note}

EXCERPTS:
{context}"""

    msgs = [{"role": m.role, "content": m.content} for m in req.messages]

    async def _stream_gen():
        full_reply = ""
        try:
            with client.messages.stream(
                model=_CLAUDE_HAIKU_MODEL if use_haiku else _CLAUDE_SONNET_MODEL,
                max_tokens=700 if use_haiku else 1800,
                system=system_prompt,
                messages=msgs,
            ) as stream:
                for text in stream.text_stream:
                    full_reply += text
                    yield f"data: {json.dumps({'token': text})}\n\n"
            if not full_reply.rstrip().endswith("public datasets.*"):
                full_reply = full_reply.rstrip() + _SOURCE_LINE
                yield f"data: {json.dumps({'token': _SOURCE_LINE})}\n\n"
            if _ck:
                _set_cached_reply(_ck, full_reply)
        except Exception as e:
            yield f"data: {json.dumps({'error': _friendly_claude_error(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _stream_gen(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


_SOURCE_LINE = (
    "\n\n---\n"
    "*Sources: [OpenStates](https://openstates.org/va/) · "
    "[LIS](https://lis.virginia.gov) · "
    "Data current through May 16, 2026. "
    "Vote reasons/statements are not available in public datasets.*"
)


_va_counties_geojson_cache = None
_locality_baseline_geojson_cache = None
_va_hod_geojson_cache = None
_va_old_hod_geojson_cache = None   # pre-2023 (2010-cycle) HOD boundaries
_va_sd_geojson_cache = None
_pres_2016_2020_flip_geojson_cache = None
_pres_2020_2024_flip_geojson_cache = None
_state_leg_2023_flip_geojson_cache = {}
_locality_flip_geojson_cache = {}
_congress_flip_geojson_cache = {}
_hod_flip_geojson_cache = {}
_hod_2017_2021_flip_geojson_cache = None
_hod_density_geojson_cache = {}
_hod_density_points_geojson_cache = {}


def _load_va_counties_geojson():
    global _va_counties_geojson_cache
    if _va_counties_geojson_cache is not None:
        return _va_counties_geojson_cache
    with open(os.path.join(BASE_DIR, "va_counties.json"), encoding="utf-8") as f:
        _va_counties_geojson_cache = json.load(f)
    return _va_counties_geojson_cache


def _normalize_locality_key(name: str) -> str:
    return str(name or "").upper().replace("&", "AND").strip()


def _build_locality_baseline_geojson():
    """Average D two-party vote share across all 12 statewide races (2016-2024)."""
    global _locality_baseline_geojson_cache
    if _locality_baseline_geojson_cache is not None:
        return _locality_baseline_geojson_cache

    RACES = [
        ("2016", "President"),
        ("2017", "Governor"),
        ("2017", "Lieutenant Governor"),
        ("2017", "Attorney General"),
        ("2018", "U.S. Senate"),
        ("2020", "President"),
        ("2020", "U.S. Senate"),
        ("2021", "Governor"),
        ("2021", "Lieutenant Governor"),
        ("2021", "Attorney General"),
        ("2024", "President"),
        ("2024", "U.S. Senate"),
    ]

    from collections import defaultdict
    locality_data = defaultdict(lambda: {"d": [], "r": []})

    for year, office in RACES:
        data = _load_results_for_year(year)
        race_results = data.get("locality_results", {}).get(office, {})
        for loc_name, result in race_results.items():
            if not isinstance(result, dict):
                continue
            norm = _normalize_locality_key(loc_name)
            d_pct = r_pct = None
            if "candidates" in result:
                for c in result["candidates"]:
                    p = (c.get("party") or "").lower()
                    pct = float(c.get("pct") or 0)
                    if "dem" in p and d_pct is None:
                        d_pct = pct
                    elif "rep" in p and r_pct is None:
                        r_pct = pct
            elif "dem" in result and "rep" in result:
                d_pct = float((result["dem"] or {}).get("pct") or 0)
                r_pct = float((result["rep"] or {}).get("pct") or 0)
            if d_pct is not None and r_pct is not None and (d_pct + r_pct) > 0:
                locality_data[norm]["d"].append(d_pct)
                locality_data[norm]["r"].append(r_pct)

    def _lean_color(d_lean):
        intensity = min(abs(d_lean) / 30.0, 1.0)
        if d_lean >= 0:
            r = int(255 - intensity * 210)
            g = int(255 - intensity * 160)
            b = 255
        else:
            r = 255
            g = int(255 - intensity * 160)
            b = int(255 - intensity * 210)
        return f"#{r:02x}{g:02x}{b:02x}"

    counties = json.loads(json.dumps(_load_va_counties_geojson()))
    for feat in counties.get("features", []):
        props = feat.setdefault("properties", {})
        locality = f"{props.get('NAME', '')} {props.get('LSAD', '')}".strip()
        norm = _normalize_locality_key(locality)
        ld = locality_data.get(norm, {})
        d_list = ld.get("d", [])
        r_list = ld.get("r", [])
        n = len(d_list)
        if n:
            avg_d = sum(d_list) / n
            avg_r = sum(r_list) / n
            tpv_d = avg_d / (avg_d + avg_r) * 100
        else:
            avg_d = avg_r = tpv_d = 50.0
        d_lean = tpv_d - 50.0
        props["_bl_locality"] = locality
        props["_bl_d_pct"] = f"{avg_d:.1f}%"
        props["_bl_r_pct"] = f"{avg_r:.1f}%"
        props["_bl_tpv_d"] = f"{tpv_d:.1f}%"
        props["_bl_lean"] = f"+{d_lean:.1f} D" if d_lean >= 0 else f"+{abs(d_lean):.1f} R"
        props["_bl_n"] = n
        props["_bl_color"] = _lean_color(d_lean)

    _locality_baseline_geojson_cache = counties
    return _locality_baseline_geojson_cache


def _locality_winner_lookup(results: dict, office: str) -> dict:
    lookup = {}
    party_by_candidate = {}
    for race in results.get("statewide", []):
        if race.get("race") == office:
            for candidate in race.get("candidates", []):
                party_by_candidate[candidate.get("name", "")] = candidate.get("party", "")
    for locality, race in results.get("locality_results", {}).get(office, {}).items():
        if isinstance(race, dict) and "candidates" in race:
            candidates = race.get("candidates", [])
            winner = candidates[0] if candidates else {}
        elif isinstance(race, dict):
            sorted_cands = sorted(race.items(), key=lambda item: -float(item[1] or 0))
            winner = {
                "name": sorted_cands[0][0],
                "party": party_by_candidate.get(sorted_cands[0][0], ""),
                "pct": sorted_cands[0][1],
            } if sorted_cands else {}
        else:
            winner = {}
        party = _normalize_major_party(winner.get("party", ""))
        lookup[_normalize_locality_key(locality)] = {
            "name": winner.get("name", "No data"),
            "party": party,
            "pct": float(winner.get("pct") or 0.0),
        }
    return lookup


def _locality_winner_lookup_2025(office: str) -> dict:
    lookup = {}
    for locality, race in _load_2025_statewide_locality_results(office).items():
        winner = race.get("winner", {})
        lookup[_normalize_locality_key(locality)] = {
            "name": winner.get("name", "No data"),
            "party": _normalize_major_party(winner.get("party", "")),
            "pct": float(winner.get("pct") or race.get("winner_pct") or 0.0),
        }
    return lookup


def _build_pres_2016_2020_flip_geojson() -> dict:
    global _pres_2016_2020_flip_geojson_cache
    if _pres_2016_2020_flip_geojson_cache is not None:
        return _pres_2016_2020_flip_geojson_cache

    counties = json.loads(json.dumps(_load_va_counties_geojson()))
    winners_2016 = _locality_winner_lookup(_load_historical_results("2016"), "President")
    winners_2020 = _locality_winner_lookup(_load_2020_results(), "President")

    for feat in counties.get("features", []):
        props = feat.setdefault("properties", {})
        locality = f"{props.get('NAME', '')} {props.get('LSAD', '')}".strip()
        key = _normalize_locality_key(locality)
        winner_2016 = winners_2016.get(key, {})
        winner_2020 = winners_2020.get(key, {})
        party_2016 = winner_2016.get("party", "Unknown")
        party_2020 = winner_2020.get("party", "Unknown")
        flipped = party_2016 != party_2020 and "Unknown" not in (party_2016, party_2020)
        if flipped and party_2020 == "Democrat":
            status = "Flipped Democratic"
        elif flipped and party_2020 == "Republican":
            status = "Flipped Republican"
        elif party_2020 == "Democrat":
            status = "Held Democratic"
        elif party_2020 == "Republican":
            status = "Held Republican"
        else:
            status = "No data"
        props["_flip_status"] = status
        props["_party_2016"] = party_2016
        props["_party_2020"] = party_2020
        props["_winner_2016"] = winner_2016.get("name", "No data")
        props["_winner_2020"] = winner_2020.get("name", "No data")
        props["_pct_2016"] = winner_2016.get("pct", 0.0)
        props["_pct_2020"] = winner_2020.get("pct", 0.0)
        props["_flip_label"] = f"{party_2016} to {party_2020}" if flipped else status

    _pres_2016_2020_flip_geojson_cache = counties
    return _pres_2016_2020_flip_geojson_cache


def _locality_winner_lookup_2024(office: str) -> dict:
    lookup = {}
    data = _load_2024_results()
    for locality, race in data.get("locality_results", {}).get(office, {}).items():
        winner = race.get("winner", {})
        lookup[_normalize_locality_key(locality)] = {
            "name": winner.get("name", "No data"),
            "party": _normalize_major_party(winner.get("party", "")),
            "pct": float(winner.get("pct") or 0.0),
        }
    return lookup


def _build_pres_2020_2024_flip_geojson() -> dict:
    global _pres_2020_2024_flip_geojson_cache
    if _pres_2020_2024_flip_geojson_cache is not None:
        return _pres_2020_2024_flip_geojson_cache

    counties = json.loads(json.dumps(_load_va_counties_geojson()))
    winners_2020 = _locality_winner_lookup(_load_2020_results(), "President")
    winners_2024 = _locality_winner_lookup_2024("President")

    for feat in counties.get("features", []):
        props = feat.setdefault("properties", {})
        locality = f"{props.get('NAME', '')} {props.get('LSAD', '')}".strip()
        key = _normalize_locality_key(locality)
        winner_2020 = winners_2020.get(key, {})
        winner_2024 = winners_2024.get(key, {})
        party_2020 = winner_2020.get("party", "Unknown")
        party_2024 = winner_2024.get("party", "Unknown")
        flipped = party_2020 != party_2024 and "Unknown" not in (party_2020, party_2024)
        if flipped and party_2024 == "Democrat":
            status = "Flipped Democratic"
        elif flipped and party_2024 == "Republican":
            status = "Flipped Republican"
        elif party_2024 == "Democrat":
            status = "Held Democratic"
        elif party_2024 == "Republican":
            status = "Held Republican"
        else:
            status = "No data"
        props["_flip_status"] = status
        props["_party_2020"] = party_2020
        props["_party_2024"] = party_2024
        props["_winner_2020"] = winner_2020.get("name", "No data")
        props["_winner_2024"] = winner_2024.get("name", "No data")
        props["_pct_2020"] = winner_2020.get("pct", 0.0)
        props["_pct_2024"] = winner_2024.get("pct", 0.0)
        props["_flip_label"] = f"{party_2020} to {party_2024}" if flipped else status

    _pres_2020_2024_flip_geojson_cache = counties
    return _pres_2020_2024_flip_geojson_cache


def _build_locality_office_flip_geojson(start_year: str, end_year: str, office: str) -> dict:
    cache_key = f"{start_year}_{end_year}_{office}"
    if cache_key in _locality_flip_geojson_cache:
        return _locality_flip_geojson_cache[cache_key]

    counties = json.loads(json.dumps(_load_va_counties_geojson()))
    start_loader = _load_historical_results if start_year not in ("2020", "2021", "2024", "2025") else {
        "2020": _load_2020_results,
        "2021": _load_2021_results,
        "2024": _load_2024_results,
        "2025": _load_2025_results,
    }[start_year]
    end_loader = _load_historical_results if end_year not in ("2020", "2021", "2024", "2025") else {
        "2020": _load_2020_results,
        "2021": _load_2021_results,
        "2024": _load_2024_results,
        "2025": _load_2025_results,
    }[end_year]
    start_results = start_loader(start_year) if start_loader == _load_historical_results else start_loader()
    end_results = end_loader(end_year) if end_loader == _load_historical_results else end_loader()
    start_winners = (
        _locality_winner_lookup_2025(office)
        if start_year == "2025"
        else _locality_winner_lookup(start_results, office)
    )
    end_winners = (
        _locality_winner_lookup_2025(office)
        if end_year == "2025"
        else _locality_winner_lookup(end_results, office)
    )

    for feat in counties.get("features", []):
        props = feat.setdefault("properties", {})
        locality = f"{props.get('NAME', '')} {props.get('LSAD', '')}".strip()
        key = _normalize_locality_key(locality)
        start_winner = start_winners.get(key, {})
        end_winner = end_winners.get(key, {})
        start_party = start_winner.get("party", "Unknown")
        end_party = end_winner.get("party", "Unknown")
        flipped = start_party != end_party and "Unknown" not in (start_party, end_party)
        if flipped and end_party == "Democrat":
            status = "Flipped Democratic"
        elif flipped and end_party == "Republican":
            status = "Flipped Republican"
        elif end_party == "Democrat":
            status = "Held Democratic"
        elif end_party == "Republican":
            status = "Held Republican"
        else:
            status = "No data"
        props["_flip_status"] = status
        props["_start_party"] = start_party
        props["_end_party"] = end_party
        props["_start_year"] = start_year
        props["_end_year"] = end_year
        props["_start_winner"] = start_winner.get("name", "No data")
        props["_end_winner"] = end_winner.get("name", "No data")
        props["_start_pct"] = start_winner.get("pct", 0.0)
        props["_end_pct"] = end_winner.get("pct", 0.0)
        props["_flip_label"] = f"{start_party} to {end_party}" if flipped else status

    _locality_flip_geojson_cache[cache_key] = counties
    return _locality_flip_geojson_cache[cache_key]


def _congress_winner_lookup(results: dict) -> dict:
    lookup = {}
    for district, race in results.get("congress", {}).items():
        candidates = race.get("candidates", [])
        winner = candidates[0] if candidates else {}
        try:
            district_num = int(re.search(r"\d+", str(district)).group())
        except Exception:
            continue
        lookup[district_num] = {
            "name": winner.get("name", "No data"),
            "party": _normalize_major_party(winner.get("party", "")),
            "pct": float(winner.get("pct") or 0.0),
        }
    return lookup


def _build_congress_flip_geojson(start_year: str, end_year: str) -> dict:
    cache_key = f"{start_year}_{end_year}"
    if cache_key in _congress_flip_geojson_cache:
        return _congress_flip_geojson_cache[cache_key]

    _year_loaders = {"2018": _load_2018_results, "2022": _load_2022_results, "2024": _load_2024_results}
    start_results = _year_loaders[start_year]() if start_year in _year_loaders else _load_historical_results(start_year)
    end_results = _year_loaders[end_year]() if end_year in _year_loaders else _load_historical_results(end_year)
    start_winners = _congress_winner_lookup(start_results)
    end_winners = _congress_winner_lookup(end_results)
    decorated = json.loads(json.dumps(_get_va_cd().to_json()))
    decorated = json.loads(decorated)

    for feat in decorated.get("features", []):
        props = feat.setdefault("properties", {})
        district = int(props.get("CD118FP") or 0)
        start_winner = start_winners.get(district, {})
        end_winner = end_winners.get(district, {})
        start_party = start_winner.get("party", "Unknown")
        end_party = end_winner.get("party", "Unknown")
        flipped = start_party != end_party and "Unknown" not in (start_party, end_party)
        if flipped and end_party == "Democrat":
            status = "Flipped Democratic"
        elif flipped and end_party == "Republican":
            status = "Flipped Republican"
        elif end_party == "Democrat":
            status = "Held Democratic"
        elif end_party == "Republican":
            status = "Held Republican"
        else:
            status = "No data"
        props["DISTRICTN"] = district
        props["_flip_status"] = status
        props["_start_party"] = start_party
        props["_end_party"] = end_party
        props["_start_year"] = start_year
        props["_end_year"] = end_year
        props["_start_winner"] = start_winner.get("name", "No data")
        props["_end_winner"] = end_winner.get("name", "No data")
        props["_start_pct"] = start_winner.get("pct", 0.0)
        props["_end_pct"] = end_winner.get("pct", 0.0)
        props["_flip_label"] = f"{start_party} to {end_party}" if flipped else status

    _congress_flip_geojson_cache[cache_key] = decorated
    return _congress_flip_geojson_cache[cache_key]


def _build_hod_2017_2021_flip_geojson() -> dict:
    global _hod_2017_2021_flip_geojson_cache
    if _hod_2017_2021_flip_geojson_cache is not None:
        return _hod_2017_2021_flip_geojson_cache
    _hod_2017_2021_flip_geojson_cache = _build_hod_flip_geojson("2019", "2021")
    return _hod_2017_2021_flip_geojson_cache


def _build_hod_flip_geojson(start_year: str, end_year: str) -> dict:
    cache_key = f"{start_year}_{end_year}"
    if cache_key in _hod_flip_geojson_cache:
        return _hod_flip_geojson_cache[cache_key]

    # Elections before 2023 used pre-redistricting (2010-cycle) district boundaries
    if int(end_year) < 2023:
        decorated = json.loads(json.dumps(_load_old_hod_geojson()))
    else:
        decorated = json.loads(json.dumps(_load_hod_geojson()))
    start_results = _load_results_for_year(start_year).get("hod", {})
    end_results = _load_results_for_year(end_year).get("hod", {})
    for feat in decorated.get("features", []):
        props = feat.setdefault("properties", {})
        district = int(props.get("DISTRICTN") or props.get("DISTRICT") or 0)
        start_race = start_results.get(str(district), start_results.get(district, {}))
        end_race = end_results.get(str(district), end_results.get(district, {}))
        start_winner = (start_race.get("candidates") or [{}])[0]
        end_winner = (end_race.get("candidates") or [{}])[0]
        start_party = _normalize_major_party(start_winner.get("party", ""))
        end_party = _normalize_major_party(end_winner.get("party", ""))
        flipped = start_party != end_party and "Unknown" not in (start_party, end_party)
        if flipped and end_party == "Democrat":
            status = "Flipped Democratic"
        elif flipped and end_party == "Republican":
            status = "Flipped Republican"
        elif end_party == "Democrat":
            status = "Held Democratic"
        elif end_party == "Republican":
            status = "Held Republican"
        else:
            status = "No data"
        props["_flip_status"] = status
        props["_start_party"] = start_party
        props["_end_party"] = end_party
        props["_start_year"] = start_year
        props["_end_year"] = end_year
        props["_start_winner"] = start_winner.get("name", "No data")
        props["_end_winner"] = end_winner.get("name", "No data")
        props["_start_pct"] = float(start_winner.get("pct") or 0.0)
        props["_end_pct"] = float(end_winner.get("pct") or 0.0)
        props["_flip_label"] = f"{start_party} to {end_party}" if flipped else status

    _hod_flip_geojson_cache[cache_key] = decorated
    return _hod_flip_geojson_cache[cache_key]


def _load_results_for_year(year: str) -> dict:
    if year == "2025":
        return _load_2025_results()
    if year == "2024":
        return _load_2024_results()
    if year == "2023":
        return _load_2023_results()
    if year == "2022":
        return _load_2022_results()
    if year == "2021":
        return _load_2021_results()
    if year == "2020":
        return _load_2020_results()
    if year == "2018":
        return _load_2018_results()
    return _load_historical_results(year)


def _build_hod_density_geojson(year: str) -> dict:
    if year in _hod_density_geojson_cache:
        return _hod_density_geojson_cache[year]

    if int(year) < 2023:
        decorated = json.loads(json.dumps(_load_old_hod_geojson()))
    else:
        decorated = json.loads(json.dumps(_load_hod_geojson()))
    hod_results = _load_results_for_year(year).get("hod", {})
    max_total = 0

    for feat in decorated.get("features", []):
        props = feat.setdefault("properties", {})
        district = int(props.get("DISTRICTN") or props.get("DISTRICT") or 0)
        race = hod_results.get(str(district), hod_results.get(district, {}))
        candidates = race.get("candidates", []) if isinstance(race, dict) else []
        winner = candidates[0] if candidates else {}
        total = int(race.get("total") or sum(int(c.get("votes") or 0) for c in candidates)) if isinstance(race, dict) else 0
        max_total = max(max_total, total)
        props["_density_year"] = year
        props["_density_total"] = total
        props["_density_winner"] = winner.get("name", "No data")
        props["_density_party"] = _normalize_major_party(winner.get("party", ""))
        props["_density_winner_pct"] = float(winner.get("pct") or 0.0)

    for feat in decorated.get("features", []):
        props = feat.setdefault("properties", {})
        total = props.get("_density_total", 0)
        props["_density_ratio"] = round(total / max_total, 4) if max_total else 0
        if total == 0:
            props["_density_bucket"] = "No data"
        elif props["_density_ratio"] >= 0.75:
            props["_density_bucket"] = "Very high"
        elif props["_density_ratio"] >= 0.5:
            props["_density_bucket"] = "High"
        elif props["_density_ratio"] >= 0.25:
            props["_density_bucket"] = "Medium"
        else:
            props["_density_bucket"] = "Low"

    _hod_density_geojson_cache[year] = decorated
    return _hod_density_geojson_cache[year]


def _build_hod_density_points_geojson(year: str) -> dict:
    if year in _hod_density_points_geojson_cache:
        return _hod_density_points_geojson_cache[year]

    polygons = _build_hod_density_geojson(year)
    points = {"type": "FeatureCollection", "features": []}
    for feat in polygons.get("features", []):
        try:
            centroid = shape(feat.get("geometry", {})).centroid
        except Exception:
            continue
        points["features"].append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [centroid.x, centroid.y]},
            "properties": dict(feat.get("properties", {})),
        })

    _hod_density_points_geojson_cache[year] = points
    return _hod_density_points_geojson_cache[year]


def _build_state_leg_2023_flip_geojson(chamber: str) -> dict:
    if chamber in _state_leg_2023_flip_geojson_cache:
        return _state_leg_2023_flip_geojson_cache[chamber]

    is_senate = chamber == "senate"
    base_geojson = _load_sd_geojson() if is_senate else _load_hod_geojson()
    decorated = json.loads(json.dumps(base_geojson))
    baseline = SD_2019_PARTY if is_senate else HOD_2021_PARTY
    baseline_year = "2019" if is_senate else "2021"
    results_2023 = _load_2023_results().get(chamber, {})

    for feat in decorated.get("features", []):
        props = feat.setdefault("properties", {})
        district = int(props.get("DISTRICTN") or props.get("DISTRICT") or 0)
        race = results_2023.get(district, {})
        candidates = race.get("candidates", [])
        winner = candidates[0] if candidates else {}
        party_prior = baseline.get(district, "Unknown")
        party_2023 = _normalize_major_party(winner.get("party", ""))
        flipped = party_prior != party_2023 and "Unknown" not in (party_prior, party_2023)
        if flipped and party_2023 == "Democrat":
            status = "Flipped Democratic"
        elif flipped and party_2023 == "Republican":
            status = "Flipped Republican"
        elif party_2023 == "Democrat":
            status = "Held Democratic"
        elif party_2023 == "Republican":
            status = "Held Republican"
        else:
            status = "No data"
        props["_flip_status"] = status
        props["_prior_party"] = party_prior
        props["_current_party"] = party_2023
        props["_baseline_year"] = baseline_year
        props["_winner_2023"] = winner.get("name", "No data")
        props["_winner_pct_2023"] = float(winner.get("pct") or 0.0)
        props["_flip_label"] = f"{party_prior} to {party_2023}" if flipped else status

    _state_leg_2023_flip_geojson_cache[chamber] = decorated
    return _state_leg_2023_flip_geojson_cache[chamber]


def _shp_to_geojson(shp_path: str) -> dict:
    gdf = gpd.read_file(shp_path).to_crs(epsg=4326)
    gdf["geometry"] = gdf["geometry"].simplify(0.005, preserve_topology=True)
    return json.loads(gdf.to_json())


def _load_hod_geojson():
    global _va_hod_geojson_cache
    if _va_hod_geojson_cache is not None:
        return _va_hod_geojson_cache
    path = os.path.join(BASE_DIR, "SCV Final 2021 Redistricting Plans", "SCV FINAL HOD.shp")
    _va_hod_geojson_cache = _shp_to_geojson(path)
    return _va_hod_geojson_cache


def _load_old_hod_geojson():
    """Pre-2023 (2010-cycle) HOD boundaries from Census TIGER 2021 SLDL."""
    global _va_old_hod_geojson_cache
    if _va_old_hod_geojson_cache is not None:
        return _va_old_hod_geojson_cache
    path = os.path.join(BASE_DIR, "tl_2021_51_sldl", "tl_2021_51_sldl.shp")
    gdf = gpd.read_file(path).to_crs(epsg=4326)
    gdf["geometry"] = gdf["geometry"].simplify(0.005, preserve_topology=True)
    gdf["DISTRICT"] = gdf["SLDLST"].apply(lambda x: int(x))
    gdf["DISTRICTN"] = gdf["DISTRICT"]
    _va_old_hod_geojson_cache = json.loads(gdf.to_json())
    return _va_old_hod_geojson_cache


def _load_sd_geojson():
    global _va_sd_geojson_cache
    if _va_sd_geojson_cache is not None:
        return _va_sd_geojson_cache
    path = os.path.join(BASE_DIR, "SCV Final 2021 Redistricting Plans", "SCV FINAL SD.shp")
    _va_sd_geojson_cache = _shp_to_geojson(path)
    return _va_sd_geojson_cache


@app.get("/baseline-map", response_class=HTMLResponse)
def baseline_map_page():
    map_html = _build_district_map("locality_baseline")
    return HTMLResponse(content=map_html)


@app.get("/virignia-map", response_class=HTMLResponse)
@app.get("/virginia-map", response_class=HTMLResponse)
def virginia_map_page(layer: str = "counties", embed: bool = False):
    allowed_layers = {
        "counties", "pres_flip", "gov_flip", "gov_2025_flip", "congress_midterm_flip",
        "hod_state_flip", "density_2017", "density_2019", "density_2021",
        "density_2023", "density_2025", "sd_flip", "hod_flip", "hod", "sd",
    }
    initial_layer = layer if layer in allowed_layers else "counties"
    mapbox_token = os.getenv("MAPBOX_TOKEN", "")
    counties = _load_va_counties_geojson()
    hod = _load_hod_geojson()
    sd = _load_sd_geojson()
    pres_flip = _build_pres_2016_2020_flip_geojson()
    hod_flip = _build_state_leg_2023_flip_geojson("hod")
    sd_flip = _build_state_leg_2023_flip_geojson("senate")
    gov_flip = _build_locality_office_flip_geojson("2017", "2021", "Governor")
    gov_2025_flip = _build_locality_office_flip_geojson("2021", "2025", "Governor")
    congress_midterm_flip = _build_congress_flip_geojson("2018", "2022")
    hod_state_flip = _build_hod_2017_2021_flip_geojson()
    hod_density_layers = {year: _build_hod_density_geojson(year) for year in ("2017", "2019", "2021", "2023", "2025")}
    hod_density_point_layers = {year: _build_hod_density_points_geojson(year) for year in ("2017", "2019", "2021", "2023", "2025")}
    def _s(obj): return json.dumps(obj, default=str).replace("</script>", "<\\/script>")
    with open(os.path.join(BASE_DIR, "templates", "virginia_map.html"), "r", encoding="utf-8") as f:
        html = f.read()
    inject = (
        f"<script>"
        f"window._MAPBOX_TOKEN={json.dumps(mapbox_token)};"
        f"window._INITIAL_LAYER={json.dumps(initial_layer)};"
        f"window._VA_GEOJSON={_s(counties)};"
        f"window._HOD_GEOJSON={_s(hod)};"
        f"window._SD_GEOJSON={_s(sd)};"
        f"window._PRES_FLIP_GEOJSON={_s(pres_flip)};"
        f"window._HOD_FLIP_GEOJSON={_s(hod_flip)};"
        f"window._SD_FLIP_GEOJSON={_s(sd_flip)};"
        f"window._GOV_FLIP_GEOJSON={_s(gov_flip)};"
        f"window._GOV_2025_FLIP_GEOJSON={_s(gov_2025_flip)};"
        f"window._CONGRESS_MIDTERM_FLIP_GEOJSON={_s(congress_midterm_flip)};"
        f"window._HOD_STATE_FLIP_GEOJSON={_s(hod_state_flip)};"
        f"window._HOD_DENSITY_GEOJSON={_s(hod_density_layers)};"
        f"window._HOD_DENSITY_POINTS_GEOJSON={_s(hod_density_point_layers)};"
        f"</script>"
    )
    if embed:
        html = html.replace("<body>", '<body class="embed-mode">', 1)
    html = html.replace("</head>", inject + "</head>", 1)
    return html


@app.on_event("startup")
async def _on_startup():
    def _embed_task():
        try:
            n = backfill_news_embeddings()
            if n:
                print(f"Cohere: embedded {n} new news articles.")
        except Exception as exc:
            print(f"Cohere backfill skipped: {exc}")
    threading.Thread(target=_embed_task, daemon=True).start()


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
