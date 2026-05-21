from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form, Query, Depends, Header
from civic_analyst import run_analyst, lookup_member
from voteiq.queries.timing import get_donation_timing, get_timing_summary
from voteiq.routes.voices import router as voices_router
from config.voices import VOICE_PROMPTS, TIER_MAX_TOKENS, TIER_VOICE_MAP
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


def _require_admin(
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    """FastAPI dependency — enforces ADMIN_TOKEN on admin/debug endpoints.
    Accepts the token as ?token= query param or Authorization: Bearer <token>.
    If ADMIN_TOKEN is not set the app is in dev mode and all requests pass.
    """
    expected = os.getenv("ADMIN_TOKEN", "")
    if not expected:
        return
    bearer = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    provided = bearer or (token or "")
    if provided != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing admin token")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI()
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
app.include_router(voices_router, prefix="/api/v1")

from voteiq.api.routes.polls import router as _polls_router
app.include_router(_polls_router)
from voteiq.api.routes.news import router as _news_router
app.include_router(_news_router)
from voteiq.api.routes.members import router as _members_router
app.include_router(_members_router)
from voteiq.api.routes.admin import router as _admin_router
app.include_router(_admin_router)
from voteiq.api.routes.chat import router as _chat_router
app.include_router(_chat_router)
from voteiq.api.routes.elections import router as _elections_router
app.include_router(_elections_router)
from voteiq.api.routes.district import router as _district_router
app.include_router(_district_router)
from voteiq.api.routes.feedback import router as _feedback_router
app.include_router(_feedback_router)

# DATA_DIR: set to Render persistent disk mount path (e.g. /var/data) in production.
# Falls back to the project directory for local development.
_DATA_DIR = os.getenv("DATA_DIR", BASE_DIR)
_OPENSTATES_DB = os.path.join(_DATA_DIR, "openstates_va.db")
_POLLS_DB = os.path.join(_DATA_DIR, "polls.db")
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


def _committees_are_fresh() -> bool:
    """Return True if committee assignments were fetched within the last 7 days."""
    from datetime import datetime, timezone, timedelta
    try:
        conn = sqlite3.connect(_POLLS_DB)
        row = conn.execute("SELECT MAX(fetched_at) FROM congress_committees").fetchone()
        conn.close()
        if row and row[0]:
            last = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
            return datetime.now(timezone.utc) - last < timedelta(days=7)
    except Exception:
        pass
    return False


def _run_committee_ingest_background() -> None:
    """Run ingest_committees.py if committee data is missing or stale (7-day TTL)."""
    if _committees_are_fresh():
        print("[committees] Data fresh — skipping.")
        return
    print("[committees] Committee data stale — fetching from unitedstates/congress-legislators…")
    script = os.path.join(BASE_DIR, "ingest_committees.py")
    if not os.path.exists(script):
        print("[committees] ingest_committees.py not found — skipping.")
        return
    try:
        result = subprocess.run(
            [sys.executable, "-X", "utf8", script],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            print("[committees] Ingestion complete.")
        else:
            print(f"[committees] Failed (rc={result.returncode}): {result.stderr[-300:]}")
    except subprocess.TimeoutExpired:
        print("[committees] Timed out.")
    except Exception as exc:
        print(f"[committees] Error: {exc}")


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
    Always reloads in-memory caches from the persistent disk at the end so
    federal vote/FEC data is available without a manual upload after each deploy.
    Full refresh can be triggered via /api/admin/ingest-congress.
    """
    if _congress_votes_are_fresh():
        print("[congress] Vote data fresh — skipping startup ingestion.")
    else:
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
        if os.path.exists(script):
            try:
                r = subprocess.run(
                    [sys.executable, script, "--house-limit", "50", "--senate-limit", "50"],
                    capture_output=True, text=True, timeout=180,
                )
                if r.returncode == 0:
                    print(f"[congress] Startup ingestion complete. {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ''}")
                else:
                    print(f"[congress] Ingestion failed (rc={r.returncode}): {r.stderr[-300:]}")
            except subprocess.TimeoutExpired:
                print("[congress] Ingestion timed out after 180s.")
            except Exception as exc:
                print(f"[congress] Ingestion error: {exc}")

    # Always reload caches from persistent disk so data is live after every deploy
    global _federal_members_cache
    _federal_members_cache = None
    _load_pac_cache()
    _load_votes_cache()
    print("[congress] In-memory caches reloaded from persistent disk.")


@app.on_event("startup")
async def startup_poll_ingest() -> None:
    threading.Thread(target=_poll_ingest_background, daemon=True).start()
    threading.Thread(target=_run_fec_ingest_background, daemon=True).start()
    threading.Thread(target=_congress_ingest_background, daemon=True).start()
    threading.Thread(target=_run_committee_ingest_background, daemon=True).start()


# /api/va-news and /api/admin/ingest-news moved to voteiq/api/routes/news.py


# ingest-congress, refresh-bill-text, ingest-polls, reload-votes, reload-pacs,
# ingest-fec-pacs, ingest-committees moved to voteiq/api/routes/admin.py


# pac-summary, donors, ingest-fec moved to voteiq/api/routes/admin.py

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

VOTEIQ_SYSTEM_PROMPT = VOICE_PROMPTS["free"]

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

# News review queue moved to voteiq/api/routes/news.py


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
            "SELECT bioguide_id, chamber, congress, session, vote_number, vote_date, bill, question, member_vote, result "
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
    db = _OPENSTATES_DB
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
    db = _OPENSTATES_DB
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
    db = _OPENSTATES_DB
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
    db = _OPENSTATES_DB
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
    db = _OPENSTATES_DB
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
    db = _OPENSTATES_DB
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
    db = _OPENSTATES_DB
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
    tier:  str = "free"
    voice: str = "free"

    @field_validator('hod_district', 'sd_district', mode='before')
    @classmethod
    def empty_str_to_none(cls, v):
        if v == '' or v is None:
            return None
        return int(v)

    @field_validator('voice')
    @classmethod
    def validate_voice(cls, v, info):
        tier    = (info.data or {}).get('tier', 'free')
        allowed = TIER_VOICE_MAP.get(tier, ['free'])
        if v not in VOICE_PROMPTS:
            return 'free'
        if v not in allowed:
            return 'free'
        return v

class ChatResponse(BaseModel):
    reply: str

class ElectionChatRequest(BaseModel):
    year: str
    messages: list[ChatMessage]
    tier:  str = "free"
    voice: str = "free"

    @field_validator('voice')
    @classmethod
    def validate_voice(cls, v, info):
        tier    = (info.data or {}).get('tier', 'free')
        allowed = TIER_VOICE_MAP.get(tier, ['free'])
        if v not in VOICE_PROMPTS:
            return 'free'
        if v not in allowed:
            return 'free'
        return v

# /election-map and /results-map moved to voteiq/api/routes/elections.py


# /results and /api/results-data moved to voteiq/api/routes/district.py

# GET / moved to voteiq/api/routes/district.py

# GET /api/lookup moved to voteiq/api/routes/district.py

# /map moved to voteiq/api/routes/district.py


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


# /statewide-bubble-map moved to voteiq/api/routes/district.py


# /district-map moved to voteiq/api/routes/district.py


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


def _fetch_news_context(query: str, politician_name: str = "", limit: int = 4) -> str:
    """Return recent Virginia news articles relevant to the query/politician as a context block."""
    if not os.path.exists(_POLLS_DB):
        return ""
    q = (query or "").strip()
    if not q:
        return ""

    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='news_intelligence'"
        ).fetchone()
        if table:
            like = f"%{q[:80]}%"
            rows = conn.execute("""
                SELECT
                    headline,
                    source,
                    published_date,
                    article_url,
                    key_facts,
                    topics,
                    politicians_mentioned,
                    bills_mentioned
                FROM news_intelligence
                WHERE headline LIKE ?
                   OR key_facts LIKE ?
                   OR topics LIKE ?
                   OR politicians_mentioned LIKE ?
                   OR bills_mentioned LIKE ?
                ORDER BY published_date DESC
                LIMIT ?
            """, (like, like, like, like, like, limit)).fetchall()
            conn.close()

            blocks = []
            for row in rows:
                blocks.append(
                    f"[news_article — {row['source']} | {row['published_date']}]\n"
                    f"Headline: {row['headline']}\n"
                    f"Topics: {row['topics']}\n"
                    f"Politicians: {row['politicians_mentioned']}\n"
                    f"Bills: {row['bills_mentioned']}\n"
                    f"Key facts: {row['key_facts']}\n"
                    f"Source: {row['article_url']}"
                )
            return "\n\n".join(blocks)
        conn.close()
    except Exception as e:
        return f"[news_error]\nCould not load news context: {e}"

    try:
        conn = sqlite3.connect(_POLLS_DB)
        rows = conn.execute(
            """SELECT title, source, published_at, gemini_json, url
               FROM va_news
               WHERE gemini_json IS NOT NULL
               ORDER BY COALESCE(published_at, fetched_at) DESC
               LIMIT 100"""
        ).fetchall()
        conn.close()
    except Exception:
        return ""

    q_lower = query.lower()
    pol_lower = politician_name.lower()
    # Build a set of name tokens to match against: full name, last name, first name.
    # Handles both "Kiggans, Jennifer A." (inverted DB format) and "Jennifer Kiggans".
    pol_tokens: set[str] = set()
    if pol_lower:
        pol_tokens.add(pol_lower)
        parts = [p.strip(" ,") for p in pol_lower.replace(",", " ").split() if len(p.strip(" ,")) > 2]
        pol_tokens.update(parts)  # individual name tokens (last, first, middle initial stripped)

    scored = []
    for title, source, published_at, gemini_json_str, url in rows:
        try:
            g = json.loads(gemini_json_str or "{}")
        except Exception:
            g = {}
        politicians_mentioned = g.get("politicians") or []
        politicians = [p.get("name", "").lower() for p in politicians_mentioned]
        topics = g.get("topics") or []
        topic_terms = [t.lower() for t in topics]
        summary = (g.get("summary") or "").strip()
        bills_mentioned = g.get("bills") or g.get("bills_mentioned") or []
        if not bills_mentioned:
            bill_text = " ".join([title or "", summary])
            bills_mentioned = sorted(set(
                re.findall(r"\b(?:HB|SB|HJ|SJ|HR|H\.R\.|S\.)\s*\.?\s*\d+\b", bill_text, flags=re.I)
            ))
        metadata = {
            "source": "news",
            "content_type": "article",
            "outlet": g.get("outlet") or source,
            "published_date": (g.get("published") or published_at or "")[:10],
            "url": url,
            "topics": topics,
            "politicians": politicians_mentioned,
            "bills": bills_mentioned,
        }

        score = 0
        if pol_tokens:
            combined = " ".join(politicians)
            if any(tok in combined for tok in pol_tokens if len(tok) > 3):
                score += 3
        if any(kw in q_lower for kw in topic_terms):
            score += 2
        if any(kw in (title or "").lower() for kw in q_lower.split() if len(kw) > 4):
            score += 1
        if score > 0:
            scored.append((score, title, source, published_at, summary, url, metadata))

    if not scored:
        return ""

    scored.sort(key=lambda x: -x[0])
    lines = ["[Virginia News — recent coverage]"]
    for _, title, source, published_at, summary, url, metadata in scored[:limit]:
        date = (published_at or "")[:10]
        lines.append(f"  • {date} | {source} | {title}")
        lines.append(f"    Metadata: {json.dumps(metadata, ensure_ascii=False)}")
        if summary:
            lines.append(f"    Summary: {summary}")
    return "\n".join(lines)


def _fetch_governor_action_context(query: str, bill_numbers: list[str] | None = None) -> str:
    """Fetch governor bill actions from polls.db governor_actions table."""
    if not os.path.exists(_POLLS_DB):
        return ""
    blocks: list[str] = []
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row

        # Confirm the table exists
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='governor_actions'"
        ).fetchone()
        if not tbl:
            conn.close()
            return ""

        if bill_numbers:
            placeholders = ",".join("?" * len(bill_numbers))
            rows = conn.execute(
                f"""
                SELECT bill_number, session, title, action_label, action_date,
                       governor, sponsor_name, sponsor_party, source_url, raw_status
                FROM governor_actions
                WHERE bill_number IN ({placeholders})
                ORDER BY action_date DESC
                LIMIT 10
                """,
                bill_numbers,
            ).fetchall()
        else:
            _gov_intent = {"governor", "spanberger", "signed", "vetoed", "veto", "executive", "governer"}
            q_lower = query.lower()
            is_gov_query = any(w in q_lower for w in _gov_intent)
            if is_gov_query:
                rows = conn.execute("""
                    SELECT bill_number, session, title, action_label, action_date,
                           governor, sponsor_name, sponsor_party, source_url, raw_status
                    FROM governor_actions
                    ORDER BY action_date DESC
                    LIMIT 10
                """).fetchall()
            else:
                keywords = [w for w in q_lower.split() if len(w) > 3][:6]
                if not keywords:
                    rows = []
                else:
                    kw_clauses = " OR ".join(
                        "(lower(title) LIKE ? OR lower(action_label) LIKE ?)" for _ in keywords
                    )
                    kw_params: list = []
                    for kw in keywords:
                        p = f"%{kw}%"
                        kw_params.extend([p, p])
                    rows = conn.execute(
                        f"""
                        SELECT bill_number, session, title, action_label, action_date,
                               governor, sponsor_name, sponsor_party, source_url, raw_status
                        FROM governor_actions
                        WHERE {kw_clauses}
                        ORDER BY action_date DESC
                        LIMIT 10
                        """,
                        kw_params,
                    ).fetchall()

        conn.close()
        for row in rows:
            url_line = f"\nSource: {row['source_url']}" if row['source_url'] else ""
            party = f" ({row['sponsor_party']})" if row['sponsor_party'] else ""
            blocks.append(
                f"[Governor Action — {row['governor']} | {row['action_date']}]\n"
                f"Bill: {row['bill_number']} ({row['session']}) — {row['title']}\n"
                f"Action: {row['action_label']}\n"
                f"Sponsor: {row['sponsor_name']}{party}"
                f"{url_line}"
            )
    except Exception:
        return ""

    return "\n\n".join(blocks)


def _fetch_va_state_member_context(
    query: str,
    hod_info: dict | None = None,
    sd_info: dict | None = None,
) -> str:
    """Return recent VA state bills introduced by the user's HOD delegate and/or state senator."""
    if not hod_info and not sd_info:
        return ""
    blocks: list[str] = []
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row

        members_to_fetch: list[tuple[str, str, str]] = []  # (name, chamber_label, lis_id)
        if hod_info:
            row = conn.execute(
                "SELECT lis_id FROM legislators WHERE lower(name) LIKE lower(?)",
                (f"%{hod_info['delegate'].split('(')[0].strip()}%",),
            ).fetchone()
            if row:
                members_to_fetch.append((hod_info["delegate"], "Delegate", row["lis_id"]))
        if sd_info:
            row = conn.execute(
                "SELECT lis_id FROM legislators WHERE lower(name) LIKE lower(?)",
                (f"%{sd_info['senator'].split('(')[0].strip()}%",),
            ).fetchone()
            if row:
                members_to_fetch.append((sd_info["senator"], "Senator", row["lis_id"]))

        q_lower = query.lower()
        keywords = [w for w in q_lower.split() if len(w) > 4]

        for name, chamber_label, lis_id in members_to_fetch:
            if keywords:
                kw_conditions = " OR ".join(
                    ["lower(title) LIKE ? OR lower(subjects) LIKE ?"] * len(keywords)
                )
                kw_params: list = []
                for kw in keywords:
                    p = f"%{kw}%"
                    kw_params.extend([p, p])
                topic_rows = conn.execute(
                    f"""
                    SELECT bill_number, session, title, status, introduced_date, subjects
                    FROM va_bills
                    WHERE introduced_by = ? AND ({kw_conditions})
                    ORDER BY session DESC, introduced_date DESC
                    LIMIT 5
                    """,
                    [lis_id] + kw_params,
                ).fetchall()
            else:
                topic_rows = []

            recent_rows = conn.execute(
                """
                SELECT bill_number, session, title, status, introduced_date, subjects
                FROM va_bills
                WHERE introduced_by = ?
                ORDER BY session DESC, introduced_date DESC
                LIMIT 5
                """,
                (lis_id,),
            ).fetchall()

            seen_ids: set[str] = set()
            combined: list = []
            for r in list(topic_rows) + list(recent_rows):
                key = f"{r['bill_number']}|{r['session']}"
                if key not in seen_ids:
                    seen_ids.add(key)
                    combined.append(r)
                if len(combined) >= 8:
                    break

            if not combined:
                continue

            total = conn.execute(
                "SELECT COUNT(*) FROM va_bills WHERE introduced_by = ?", (lis_id,)
            ).fetchone()[0]

            lines = [f"[VA {chamber_label} Bills — {name} | {total} total introduced]"]
            for r in combined:
                subj = f" [{r['subjects']}]" if r['subjects'] else ""
                lines.append(
                    f"  {r['bill_number']} ({r['session']}) — {r['title'][:80]}\n"
                    f"    Status: {r['status']}{subj}"
                )
            blocks.append("\n".join(lines))

        conn.close()
    except Exception:
        return ""
    return "\n\n".join(blocks)


def _fetch_governor_eo_context(query: str) -> str:
    """Fetch Spanberger executive orders relevant to the query from polls.db."""
    blocks: list[str] = []
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        q_lower = query.lower()
        _eo_intent = {"governor", "spanberger", "executive", "signed", "order", "directive", "governer", "eo"}
        is_gov_query = any(w in q_lower for w in _eo_intent)

        if is_gov_query:
            rows = conn.execute("""
                SELECT order_number, title, governor, signed_date, summary, source_url, policy_topics
                FROM governor_executive_orders
                ORDER BY signed_date DESC
                LIMIT 8
            """).fetchall()
        else:
            keywords = [w for w in q_lower.split() if len(w) > 4][:6]
            if not keywords:
                conn.close()
                return ""
            conditions = " OR ".join(
                ["lower(title) LIKE ? OR lower(summary) LIKE ? OR lower(policy_topics) LIKE ?"] * len(keywords)
            )
            params = []
            for kw in keywords:
                p = f"%{kw}%"
                params.extend([p, p, p])
            rows = conn.execute(
                f"""
                SELECT order_number, title, governor, signed_date, summary, source_url, policy_topics
                FROM governor_executive_orders
                WHERE {conditions}
                ORDER BY signed_date DESC
                LIMIT 5
                """,
                params,
            ).fetchall()

        conn.close()
        for row in rows:
            url_line = f"\nSource: {row['source_url']}" if row['source_url'] else ""
            summary = (row['summary'] or "")[:400]
            topics_line = ""
            if row["policy_topics"]:
                try:
                    t = json.loads(row["policy_topics"])
                    if t:
                        topics_line = f"\nTopics: {', '.join(t[:6])}"
                except Exception:
                    pass
            blocks.append(
                f"[Governor Executive Order — {row['governor']} | {row['signed_date']}]\n"
                f"Order {row['order_number']}: {row['title']}\n"
                f"Summary: {summary}"
                f"{topics_line}"
                f"{url_line}"
            )
    except Exception:
        return ""
    return "\n\n".join(blocks)


def _fetch_foreign_policy_ie_context(fec_candidate_id: str, cycle: int | None = None) -> str:
    """
    Fetch foreign-policy-aligned independent expenditures for a candidate.

    Surfaces U.S. PACs classified as having foreign-policy issue alignment
    (pro-Israel, India, Taiwan, Ukraine, Armenia, etc.).

    This does not mean foreign government money, foreign national money,
    coordination, motive, or causation.
    """
    if not fec_candidate_id:
        return ""
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT
                ie.committee_name,
                ie.support_oppose,
                ROUND(SUM(COALESCE(ie.expenditure_amount, 0)), 2) AS total,
                COUNT(*) AS records,
                COALESCE(p.ideology, '')          AS ideology,
                COALESCE(p.network, '')           AS network,
                COALESCE(p.issue_focus, '')       AS issue_focus,
                COALESCE(p.foreign_alignment, '') AS foreign_alignment,
                COALESCE(p.foreign_country, '')   AS foreign_country,
                COALESCE(p.short_name, '')        AS short_name
            FROM fec_independent_expenditures ie
            JOIN pac_ideology p
                ON lower(p.committee_name) = lower(ie.committee_name)
                OR (
                    p.short_name IS NOT NULL
                    AND LENGTH(TRIM(p.short_name)) >= 4
                    AND lower(ie.committee_name) LIKE lower('%' || p.short_name || '%')
                )
            WHERE ie.fec_candidate_id = ?
              AND (? IS NULL OR ie.cycle = ?)
              AND COALESCE(p.foreign_country, '') != ''
            GROUP BY ie.committee_name, ie.support_oppose,
                     p.ideology, p.network, p.issue_focus,
                     p.foreign_alignment, p.foreign_country, p.short_name
            ORDER BY total DESC
            LIMIT 10
        """, (fec_candidate_id, cycle, cycle)).fetchall()
        conn.close()

        if not rows:
            return (
                "[foreign_policy_donors]\n"
                "No foreign-policy-aligned independent expenditure records found "
                "for this candidate in the current VoteIQ dataset."
            )

        blocks = ["[foreign_policy_donors — FEC Schedule E outside spending]"]
        by_country: dict[str, list] = {}
        for r in rows:
            label = r["foreign_alignment"] or r["foreign_country"] or "Foreign-policy issue"
            by_country.setdefault(label, []).append(r)

        for country, expenditures in by_country.items():
            total = sum(float(r["total"] or 0) for r in expenditures)
            blocks.append(f"\n{country.upper()} — ${total:,.0f} total:")
            for r in expenditures:
                direction = "Supporting" if r["support_oppose"] == "S" else "Opposing" if r["support_oppose"] == "O" else "Unknown direction"
                network = r["network"] or r["ideology"] or "classified PAC network"
                focus = f" | Focus: {r['issue_focus']}" if r["issue_focus"] else ""
                blocks.append(
                    f"  {direction}: {r['committee_name']} [{network}]: "
                    f"${float(r['total'] or 0):,.0f} ({int(r['records'] or 0)} expenditures){focus}"
                )

        blocks.append(
            "\nSource: FEC Schedule E filings. "
            "These are independent expenditures by U.S. committees, not direct campaign donations. "
            "Foreign-policy alignment classifications are VoteIQ labels based on PAC self-identification, "
            "committee names, issue focus, and public records. "
            "This does not mean foreign government funding, foreign national funding, coordination, motive, "
            "or causation. Correlation does not imply causation."
        )
        return "\n".join(blocks)
    except Exception:
        return ""


def _fetch_ie_context(bioguide_ids: list[str], question: str) -> str:
    """Return top independent expenditure spenders for/against the given members."""
    if not bioguide_ids:
        return ""
    q_lower = question.lower()
    ie_keywords = [
        "outside spending", "super pac", "independent expenditure", "dark money",
        "outside group", "who spent", "who ran ads", "ads against", "ads for",
        "attack ad", "outside money", "nrcc", "dccc", "club for growth",
        "who spent money",
    ]
    foreign_keywords = [
        "israel", "aipac", "pro-israel", "dmfi", "j street",
        "india", "taiwan", "china", "ukraine", "iran", "saudi", "turkey",
        "armenia", "greece", "foreign policy", "foreign pac", "foreign money",
        "middle east", "asia policy", "nato", "foreign influence",
        "who funded", "foreign aligned", "udp", "united democracy project",
    ]
    is_ie_query      = any(kw in q_lower for kw in ie_keywords)
    is_foreign_query = any(kw in q_lower for kw in foreign_keywords)
    if not is_ie_query and not is_foreign_query:
        return ""
    foreign_only = is_foreign_query and not is_ie_query
    blocks: list[str] = []
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        # Resolve bioguide -> fec_candidate_id via the member cache
        fec_ids_by_bgid: dict[str, str] = {}
        for bgid in bioguide_ids:
            row = conn.execute(
                "SELECT fec_candidate_id FROM fec_independent_expenditures WHERE bioguide_id = ? LIMIT 1",
                (bgid,),
            ).fetchone()
            if row and row["fec_candidate_id"]:
                fec_ids_by_bgid[bgid] = row["fec_candidate_id"]
        for bgid, fec_cand_id in fec_ids_by_bgid.items():
            if foreign_only:
                rows = conn.execute("""
                    SELECT ie.committee_name, ie.support_oppose,
                           SUM(ie.expenditure_amount) as total,
                           COUNT(*) as count,
                           MAX(ie.expenditure_date) as latest
                    FROM fec_independent_expenditures ie
                    INNER JOIN pac_ideology pi
                        ON lower(pi.committee_name) = lower(ie.committee_name)
                    WHERE ie.fec_candidate_id = ?
                      AND pi.foreign_alignment IS NOT NULL
                    GROUP BY ie.committee_name, ie.support_oppose
                    ORDER BY total DESC
                    LIMIT 10
                """, (fec_cand_id,)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT MAX(committee_name) as committee_name, support_oppose,
                           SUM(expenditure_amount) as total,
                           COUNT(*) as count,
                           MAX(expenditure_date) as latest
                    FROM fec_independent_expenditures
                    WHERE fec_candidate_id = ?
                    GROUP BY committee_id, support_oppose
                    ORDER BY total DESC
                    LIMIT 10
                """, (fec_cand_id,)).fetchall()
            if not rows:
                continue
            cand_name = conn.execute(
                "SELECT candidate_name FROM fec_independent_expenditures WHERE fec_candidate_id = ? LIMIT 1",
                (fec_cand_id,),
            ).fetchone()
            cand = (cand_name["candidate_name"] if cand_name else None) or bgid
            support = [r for r in rows if r["support_oppose"] == "S"]
            oppose  = [r for r in rows if r["support_oppose"] == "O"]
            total_s = sum(r["total"] for r in support)
            total_o = sum(r["total"] for r in oppose)
            net = abs(total_o - total_s)
            net_dir = "AGAINST" if total_o > total_s else "FOR"
            lines = [
                f"[Outside Spending (IE) — {cand}]",
                f"Supporting total: ${total_s:,.0f}  |  Opposing total: ${total_o:,.0f}  |  Net: ${net:,.0f} {net_dir}",
            ]
            def _ideology_tag(committee_name: str) -> str:
                row = conn.execute(
                    "SELECT short_name, network, ideology, foreign_alignment, foreign_country FROM pac_ideology WHERE lower(committee_name) = lower(?) LIMIT 1",
                    (committee_name,),
                ).fetchone()
                if not row:
                    row = conn.execute(
                        "SELECT short_name, network, ideology, foreign_alignment, foreign_country FROM pac_ideology WHERE lower(committee_name) LIKE lower(?) LIMIT 1",
                        (f"%{committee_name[:30]}%",),
                    ).fetchone()
                if row:
                    parts = [p for p in [row["short_name"], row["network"], row["ideology"]] if p]
                    if row["foreign_alignment"]:
                        parts.append(f"{row['foreign_alignment']} [{row['foreign_country']}]")
                    return f" [{', '.join(parts)}]"
                return ""

            if support:
                lines.append(f"SUPPORTING (${total_s:,.0f}):")
                for r in support:
                    tag = _ideology_tag(r["committee_name"])
                    lines.append(f"  {r['committee_name']}{tag}: ${r['total']:,.0f} ({r['count']} expenditures, latest {r['latest']})")
            if oppose:
                lines.append(f"OPPOSING (${total_o:,.0f}):")
                for r in oppose:
                    tag = _ideology_tag(r["committee_name"])
                    lines.append(f"  {r['committee_name']}{tag}: ${r['total']:,.0f} ({r['count']} expenditures, latest {r['latest']})")
            lines.append("Source: FEC Schedule E filings. Independent expenditures are not coordinated with campaigns.")
            blocks.append("\n".join(lines))
        conn.close()
    except Exception:
        return ""
    return "\n\n".join(blocks)


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


_SPANBERGER_FINANCE_CACHE: str | None = None


def _fetch_spanberger_finance_context(question: str = "") -> str:
    """Return Governor Spanberger campaign-finance context from local Virginia SBE Schedule A rows."""
    global _SPANBERGER_FINANCE_CACHE
    q = (question or "").lower()
    if not any(
        term in q
        for term in (
            "spanberger", "governor", "campaign finance", "finance", "financial",
            "donor", "donors", "funding", "money", "contribution", "contributions",
            "profile", "who funded", "who backs", "who supports",
        )
    ):
        return ""
    if _SPANBERGER_FINANCE_CACHE is not None:
        return _SPANBERGER_FINANCE_CACHE
    if not os.path.exists(_POLLS_DB):
        return ""

    try:
        from build_va_state_finance import classify_sector

        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        person = conn.execute(
            """
            SELECT person_name, office, party, committee_name, finance_url, source_url, fetched_at
            FROM va_finance_people
            WHERE lower(person_name) LIKE '%spanberger%'
            ORDER BY fetched_at DESC
            LIMIT 1
            """
        ).fetchone()
        rows = conn.execute(
            """
            SELECT candidate_name, election_cycle, first_name, last_or_company,
                   employer, occupation, is_individual, transaction_date, amount
            FROM va_cf_schedule_a
            WHERE lower(candidate_name) LIKE '%spanberger%'
              AND amount > 0
              AND CAST(election_cycle AS INTEGER) >= 2023
            """
        ).fetchall()
        conn.close()
    except Exception:
        return ""

    if not rows:
        return ""

    by_cycle: dict[str, dict] = {}
    sector_totals: dict[tuple[str, str], dict] = {}
    donor_totals: dict[tuple[str, str], dict] = {}
    latest_date = ""

    for row in rows:
        cycle = str(row["election_cycle"] or "")
        if not cycle:
            continue
        amount = float(row["amount"] or 0)
        latest_date = max(latest_date, row["transaction_date"] or "")
        cycle_stats = by_cycle.setdefault(cycle, {"total": 0.0, "count": 0, "latest": ""})
        cycle_stats["total"] += amount
        cycle_stats["count"] += 1
        cycle_stats["latest"] = max(cycle_stats["latest"], row["transaction_date"] or "")

        sector = classify_sector(
            row["occupation"] or "",
            row["employer"] or "",
            row["last_or_company"] or "" if not row["is_individual"] else "",
        )
        sector_key = (cycle, sector)
        sector_stats = sector_totals.setdefault(sector_key, {"total": 0.0, "count": 0})
        sector_stats["total"] += amount
        sector_stats["count"] += 1

        donor_name = (
            f"{row['first_name'] or ''} {row['last_or_company'] or ''}".strip()
            if row["is_individual"]
            else (row["last_or_company"] or "").strip()
        ) or "Unknown donor"
        donor_key = (cycle, donor_name)
        donor_stats = donor_totals.setdefault(donor_key, {
            "total": 0.0,
            "count": 0,
            "sector": sector,
            "employer": row["employer"] or "",
            "occupation": row["occupation"] or "",
        })
        donor_stats["total"] += amount
        donor_stats["count"] += 1

    latest_cycle = max(by_cycle, key=lambda c: int(c) if c.isdigit() else 0)
    latest_total = by_cycle[latest_cycle]["total"]
    all_total = sum(v["total"] for v in by_cycle.values())
    all_count = sum(v["count"] for v in by_cycle.values())

    lines = [
        "[Governor Campaign Finance — Abigail D. Spanberger]",
        "Office: Governor of Virginia",
        f"Committee: {(person['committee_name'] if person else '') or 'Spanberger for Governor'}",
        "Source: Virginia SBE Schedule A campaign finance filings",
        "Note: These are itemized contribution records from the local SBE import; Virginia has no state contribution limit.",
        f"Imported cycles covered: {', '.join(sorted(by_cycle))}",
        f"All covered cycles total: ${all_total:,.0f} from {all_count:,} contribution records",
        f"Latest cycle in local data: {latest_cycle} — ${latest_total:,.0f} from {by_cycle[latest_cycle]['count']:,} records"
        + (f", latest transaction {by_cycle[latest_cycle]['latest']}" if by_cycle[latest_cycle]["latest"] else ""),
    ]

    if person and person["finance_url"]:
        lines.append(f"VPAP/finance URL: {person['finance_url']}")

    lines.append("Cycle totals:")
    for cycle in sorted(by_cycle, key=lambda c: int(c) if c.isdigit() else 0, reverse=True):
        stats = by_cycle[cycle]
        lines.append(
            f"- {cycle}: ${stats['total']:,.0f} from {stats['count']:,} records"
            + (f" (latest {stats['latest']})" if stats["latest"] else "")
        )

    latest_sectors = sorted(
        ((sector, stats) for (cycle, sector), stats in sector_totals.items() if cycle == latest_cycle),
        key=lambda item: item[1]["total"],
        reverse=True,
    )[:8]
    if latest_sectors:
        lines.append(f"Top sectors in {latest_cycle}:")
        for sector, stats in latest_sectors:
            pct = stats["total"] / latest_total * 100 if latest_total else 0
            lines.append(f"- {sector}: ${stats['total']:,.0f} from {stats['count']:,} records ({pct:.1f}%)")

    latest_donors = sorted(
        ((donor, stats) for (cycle, donor), stats in donor_totals.items() if cycle == latest_cycle),
        key=lambda item: item[1]["total"],
        reverse=True,
    )[:10]
    if latest_donors:
        lines.append(f"Top aggregated donors in {latest_cycle}:")
        for donor, stats in latest_donors:
            lines.append(
                f"- {donor}: ${stats['total']:,.0f} across {stats['count']:,} records"
                + (f" | sector: {stats['sector']}" if stats.get("sector") else "")
            )

    _SPANBERGER_FINANCE_CACHE = "\n".join(lines)
    return _SPANBERGER_FINANCE_CACHE


def _fetch_governor_action_money_analyst_context(question: str = "") -> str:
    """Return structured analyst context for governor actions crossed with sponsor finance."""
    q = (question or "").lower()
    if not any(term in q for term in ("analy", "analyst", "pattern", "overlap", "compare", "relationship")):
        return ""
    if not any(term in q for term in ("governor", "spanberger", "signed", "veto", "action", "bill outcome")):
        return ""
    if not any(term in q for term in ("money", "donor", "finance", "funding", "sector", "contribution", "campaign")):
        return ""

    try:
        from voteiq.queries.governor_actions import (
            get_governor_action_counts,
            get_governor_action_money_patterns,
            get_top_sponsors_by_action,
        )
        from voteiq.analysis.governor_action_money import (
            get_governor_action_analysis_guardrails,
            get_governor_action_voteiq_finding,
        )

        session = "2026"
        finance_cycle = "2025" if "2025" in q or "spanberger" in q else "2026"
        counts = get_governor_action_counts(session=session, governor="Spanberger")
        money_rows = get_governor_action_money_patterns(
            session=session,
            finance_cycle=finance_cycle,
            governor="Spanberger",
        )
        sponsors_by_action = get_top_sponsors_by_action(
            session=session,
            governor="Spanberger",
            finance_cycle=finance_cycle,
            limit_per_action=5,
        )
        guardrails = get_governor_action_analysis_guardrails(
            session=session,
            finance_cycle=finance_cycle,
        )
        finding = get_governor_action_voteiq_finding(
            session=session,
            finance_cycle=finance_cycle,
        )
    except Exception:
        return ""

    if not counts and not money_rows and not sponsors_by_action:
        return ""

    lines = [
        "[VoteIQ Analyst Context — Governor Action Money]",
        f"Governor: Spanberger",
        f"Legislative session: {session}",
        f"Finance cycle used for sponsor donor-sector comparison: {finance_cycle}",
        "Purpose: Analyze public-record overlaps between governor bill outcomes and bill sponsors' donor-sector profiles.",
        "Required wording: Correlation does not imply causation. Do not infer motive, pressure, reward, influence, or causation.",
    ]

    if counts:
        lines.append("Governor action counts:")
        for row in counts:
            label = row.get("action_label") or row.get("action") or "Unknown"
            lines.append(f"- {label}: {row.get('count', 0)}")

    if money_rows:
        combined: dict[tuple[str, str, str, str], dict] = {}
        for row in money_rows:
            label = row.get("action_label") or "Unknown"
            if not row.get("sector") or row.get("total_amount") is None:
                continue
            key = (
                label,
                row.get("sponsor_name") or "Unknown sponsor",
                row.get("sponsor_party") or "",
                row.get("sector") or "Unknown",
            )
            current = combined.setdefault(key, {
                "action_label": label,
                "sponsor_name": row.get("sponsor_name") or "Unknown sponsor",
                "sponsor_party": row.get("sponsor_party") or "",
                "sector": row.get("sector") or "Unknown",
                "total_amount": 0.0,
                "donor_count": 0,
            })
            current["total_amount"] += float(row.get("total_amount") or 0)
            current["donor_count"] += int(row.get("donor_count") or 0)

        grouped: dict[str, list[dict]] = {}
        for row in combined.values():
            grouped.setdefault(row["action_label"], []).append(row)

        lines.append("Top sponsor donor-sector patterns by governor outcome:")
        for label in sorted(grouped):
            rows = sorted(
                grouped[label],
                key=lambda r: float(r.get("total_amount") or 0),
                reverse=True,
            )[:6]
            lines.append(f"{label}:")
            for row in rows:
                total = float(row.get("total_amount") or 0)
                donors = int(row.get("donor_count") or 0)
                sponsor = row.get("sponsor_name") or "Unknown sponsor"
                party = row.get("sponsor_party") or ""
                sector = row.get("sector") or "Unknown"
                lines.append(
                    f"- {sponsor}{f' ({party})' if party else ''}: {sector} "
                    f"${total:,.0f} across {donors:,} donor records"
                )

    if sponsors_by_action:
        lines.append("Top bill sponsors by governor outcome:")
        for action_key, section in sponsors_by_action.items():
            label = section.get("action_label") or action_key
            sponsors = section.get("sponsors") or []
            if not sponsors:
                continue
            lines.append(f"{label}:")
            for sponsor in sponsors[:5]:
                lines.append(
                    f"- {sponsor.get('sponsor_name')}: {sponsor.get('bill_count')} bills; "
                    f"leading donor sector {sponsor.get('sector')}"
                )

    if finding:
        lines.append(f"VoteIQ finding: {finding}")

    if guardrails:
        lines.append("Analyst guardrails:")
        lines.extend(f"- {rule}" for rule in guardrails)

    return "\n".join(lines)


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
    """Add embedding and embedding_model columns to va_news if missing."""
    try:
        conn = sqlite3.connect(_POLLS_DB)
        for col in ("embedding TEXT", "embedding_model TEXT"):
            try:
                conn.execute(f"ALTER TABLE va_news ADD COLUMN {col}")
            except Exception:
                pass
        conn.commit()
        conn.close()
    except Exception:
        pass


def _embed_news_texts(texts: list[str]) -> list[list[float]]:
    """Embed news document texts using Voyage AI voyage-3."""
    if not texts:
        return []
    try:
        result = _get_voyage_client().embed(texts, model=_NEWS_MODEL, input_type="document")
        return result.embeddings
    except Exception:
        return []


def _embed_news_query(question: str) -> list[float]:
    """Embed a news search query using Voyage AI voyage-3."""
    try:
        result = _get_voyage_client().embed([question], model=_NEWS_MODEL, input_type="query")
        return result.embeddings[0]
    except Exception:
        return []


def backfill_news_embeddings(batch_size: int = 48) -> int:
    """Embed va_news rows using Voyage AI voyage-3. Re-embeds any row not yet tagged with the current model."""
    if not os.getenv("VOYAGE_API_KEY") or not os.path.exists(_POLLS_DB):
        return 0
    _ensure_embeddings_column()
    conn = sqlite3.connect(_POLLS_DB)
    rows = conn.execute(
        "SELECT article_id, title, gemini_json FROM va_news "
        "WHERE (embedding IS NULL OR embedding_model IS NULL OR embedding_model != ?) "
        "AND gemini_json IS NOT NULL",
        (_NEWS_MODEL,),
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

        vecs = _embed_news_texts(texts)
        if not vecs:
            break
        for (article_id, _, _), vec in zip(batch, vecs):
            conn.execute(
                "UPDATE va_news SET embedding=?, embedding_model=? WHERE article_id=?",
                (json.dumps(vec), _NEWS_MODEL, article_id),
            )
        conn.commit()
        embedded += len(vecs)

    conn.close()
    return embedded


def _semantic_news_candidates(question: str, limit: int = 20) -> list[dict]:
    """Return articles ranked by semantic similarity using Voyage AI embeddings."""
    q_vec = _embed_news_query(question)
    if not q_vec:
        return []
    conn = sqlite3.connect(_POLLS_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT article_id, url, title, gemini_json, embedding FROM va_news "
        "WHERE embedding IS NOT NULL AND gemini_json IS NOT NULL "
        "AND embedding_model = ? "
        "ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT 200",
        (_NEWS_MODEL,),
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

        if os.getenv("VOYAGE_API_KEY"):
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
            fallback_name = (politician_names or [""])[0]
            return _fetch_news_context(question, politician_name=fallback_name, limit=limit)

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
            bills_mentioned = d.get("bills") or d.get("bills_mentioned") or []
            if not bills_mentioned:
                bill_text = " ".join([headline, summary])
                bills_mentioned = sorted(set(
                    re.findall(r"\b(?:HB|SB|HJ|SJ|HR|H\.R\.|S\.)\s*\.?\s*\d+\b", bill_text, flags=re.I)
                ))
            metadata = {
                "source": "news",
                "content_type": "article",
                "outlet": outlet,
                "published_date": pub,
                "url": url,
                "topics": d.get("topics") or [],
                "politicians": d.get("politicians") or [],
                "bills": bills_mentioned,
            }
            byline = outlet + (f" — {author}" if author else "") + (f" ({pub})" if pub else "")
            lines.append(f"- {headline}" + (f" [{byline}]" if byline else ""))
            lines.append(f"  Metadata: {json.dumps(metadata, ensure_ascii=False)}")
            if summary:
                lines.append(f"  Summary: {summary}")
            if url:
                lines.append(f"  Source: {url}")
        return "\n".join(lines)
    except Exception:
        return ""


# /chat moved to voteiq/api/routes/chat.py

# /api/gemini-chat moved to voteiq/api/routes/chat.py

# /past-elections/* routes and data helpers moved to voteiq/api/routes/elections.py


# ── Pre-built district result maps (2016-2019) ────────────────────────────────

_DISTRICT_MAP_FILES = {
    ("congress", "2016"): "va_congress_2016.html",
    ("congress", "2018"): "va_congress_2018.html",
    ("hod",      "2017"): "va_house_delegates_2017.html",
    ("senate",   "2019"): "va_senate_2019.html",
    ("hod",      "2019"): "va_house_delegates_2019.html",
}

_district_map_cache: dict[tuple, str] = {}


# /maps/{chamber}/{year} moved to voteiq/api/routes/district.py


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


# /api/election-chat moved to voteiq/api/routes/chat.py


# ── Bills RAG chat ────────────────────────────────────────────────────────────

_BILLS_MODEL      = "voyage-law-2"
_NEWS_MODEL       = "voyage-3"
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
    votes, bills, committees = [], [], []
    db_available = False
    try:
        conn = sqlite3.connect(_POLLS_DB)
        votes = conn.execute(
            """SELECT cv.vote_date, cv.bill, cv.question, cv.member_vote, cv.result,
                      COALESCE(cb.title, bd.title, '') AS bill_title,
                      COALESCE(cb.policy_area, bd.policy_area, '') AS policy_area
               FROM congress_votes cv
               LEFT JOIN congress_bills cb
                   ON cv.congress = cb.congress
                   AND cv.bill = (cb.bill_type || ' ' || cb.bill_number)
               LEFT JOIN congress_bill_details bd
                   ON cv.congress = bd.congress
                   AND cv.bill = (bd.bill_type || ' ' || bd.bill_number)
               WHERE cv.bioguide_id = ?
               ORDER BY cv.vote_date DESC LIMIT 40""",
            (bio,),
        ).fetchall()
        bills = conn.execute(
            """SELECT bill_type, bill_number, title, policy_area,
                      latest_action, latest_action_date, role
               FROM congress_bills WHERE sponsor_id = ?
               ORDER BY latest_action_date DESC LIMIT 25""",
            (bio,),
        ).fetchall()
        committees = conn.execute(
            """SELECT committee_name, is_subcommittee, parent_name, role
               FROM congress_committees WHERE bioguide_id = ?
               ORDER BY is_subcommittee, committee_name""",
            (bio,),
        ).fetchall()
        conn.close()
        db_available = True
    except Exception:
        db_available = False

    lines = [
        f"[Representative Profile — {name} ({party}) | Federal | 119th Congress]",
        f"Office: {seat} | Chamber: {chamber}",
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

    # Vote summary stats — mirror state rep format
    if votes:
        yes_ct  = sum(1 for r in votes if str(r[3]).upper() in ("YEA", "YES", "AYE"))
        no_ct   = sum(1 for r in votes if str(r[3]).upper() in ("NAY", "NO", "NAYE"))
        pres_ct = sum(1 for r in votes if str(r[3]).upper() in ("PRESENT",))
        nv_ct   = sum(1 for r in votes if str(r[3]).upper() in ("NOT VOTING",))
        total_v = len(votes)
        yes_rate = round(yes_ct / total_v * 100, 1) if total_v else 0
        lines.append(
            f"\nOverall voting record (119th Congress, {total_v} roll calls shown): "
            f"{yes_ct} Yes, {no_ct} No, {nv_ct} Not voting, {pres_ct} Present"
        )
        lines.append(f"Yes rate: {yes_rate}%")

    if committees:
        lines.append(f"\nCommittee Assignments ({len(committees)} total):")
        for cname, is_sub, parent, role in committees:
            role_tag = f" [{role}]" if role else ""
            if is_sub:
                lines.append(f"  └─ {cname}{role_tag} (Subcommittee of {parent})")
            else:
                lines.append(f"  • {cname}{role_tag}")
    else:
        lines.append("\nNo committee data available.")

    if bills:
        lines.append(f"\nSponsored / Co-Sponsored Bills ({len(bills)} shown):")
        for bill_type, bill_number, title, policy_area, latest_action, latest_action_date, role in bills:
            bid = f"{bill_type.upper()} {bill_number}"
            area = f" [{policy_area}]" if policy_area else ""
            lines.append(f"  {bid}: {title}{area} | {role} | Last action: {latest_action_date} — {latest_action}")
    else:
        lines.append("\nNo sponsored bills in dataset.")

    if votes:
        lines.append(f"\nRoll-Call Votes (most recent first — {len(votes)} shown):")
        for row in votes:
            vote_date, bill, question, member_vote, result = row[0], row[1], row[2], row[3], row[4]
            bill_title = row[5] if len(row) > 5 else ""
            policy_area = row[6] if len(row) > 6 else ""
            title_tag = f" — {bill_title}" if bill_title else ""
            area_tag = f" [{policy_area}]" if policy_area else ""
            lines.append(f"  {vote_date} | {bill}{title_tag}{area_tag} | {question} | Voted: {member_vote} | Result: {result}")
    else:
        lines.append("\nNo roll-call votes in dataset.")

    return "\n".join(lines)


# ── Federal bill type map (prefix → DB bill_type) ─────────────────────────────

_FEDERAL_BILL_TYPE_MAP: dict[str, str] = {
    "HR": "hr",
    "SR": "sres",   # _federal_sqlite_bill_lookup falls back to "s" when sres misses
    "HJ": "hjres",
    "SJ": "sjres",
    "HC": "hconres",
    "SC": "sconres",
}


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


_FEDERAL_BILL_TYPE_MAP = {
    "HR": "hr",
    "SR": "sres",
    "HJ": "hjres",
    "SJ": "sjres",
}

def _federal_sqlite_bill_lookup(bill_ids: list[str]) -> str:
    """Return a formatted excerpt for federal bill IDs (e.g. 'HR1137') from congress_bill_texts."""
    if not bill_ids or not os.path.exists(_POLLS_DB):
        return ""
    federal_ids = [(bid, _FEDERAL_BILL_TYPE_MAP.get(re.match(r"^([A-Z]+)", bid).group(1), ""))
                   for bid in bill_ids
                   if re.match(r"^([A-Z]+)", bid) and re.match(r"^([A-Z]+)", bid).group(1) in _FEDERAL_BILL_TYPE_MAP]
    if not federal_ids:
        return ""
    try:
        conn = sqlite3.connect(_POLLS_DB)
        lines = []
        for bid, btype in federal_ids:
            bnum = re.sub(r"^[A-Z]+", "", bid)
            # try exact bill_type first, then fallback ("SR" → try "s" if "sres" misses)
            row = conn.execute(
                """SELECT bt.text, COALESCE(cb.title,''), COALESCE(cb.policy_area,''),
                          COALESCE(cb.latest_action,''), bt.congress
                   FROM congress_bill_texts bt
                   LEFT JOIN congress_bills cb
                       ON cb.congress=bt.congress AND cb.bill_type=bt.bill_type AND cb.bill_number=bt.bill_number
                   WHERE bt.bill_type=? AND bt.bill_number=?
                   ORDER BY bt.congress DESC LIMIT 1""",
                (btype, bnum),
            ).fetchone()
            if not row and btype == "sres":
                row = conn.execute(
                    """SELECT bt.text, COALESCE(cb.title,''), COALESCE(cb.policy_area,''),
                              COALESCE(cb.latest_action,''), bt.congress
                       FROM congress_bill_texts bt
                       LEFT JOIN congress_bills cb
                           ON cb.congress=bt.congress AND cb.bill_type=bt.bill_type AND cb.bill_number=bt.bill_number
                       WHERE bt.bill_type='s' AND bt.bill_number=?
                       ORDER BY bt.congress DESC LIMIT 1""",
                    (bnum,),
                ).fetchone()
            if not row:
                continue
            text, title, policy_area, latest_action, congress = row
            excerpt = (text or "")[:2500].rstrip()
            session_label = "2025" if int(congress or 119) >= 119 else str(congress)
            area_tag = f" [{policy_area}]" if policy_area else ""
            action_tag = f"\nLatest action: {latest_action}" if latest_action else ""
            lines.append(
                f"[Federal Bill Text — {bid} ({session_label} Congress)]{area_tag}\n"
                f"Title: {title}\n"
                + action_tag
                + f"\n\nText excerpt:\n{excerpt}"
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
    tier:  str = "free"
    voice: str = "free"

    @field_validator('hod_district', 'sd_district', mode='before')
    @classmethod
    def empty_str_to_none(cls, v):
        if v == '' or v is None:
            return None
        return int(v)

    @field_validator('voice')
    @classmethod
    def validate_voice(cls, v, info):
        tier    = (info.data or {}).get('tier', 'free')
        allowed = TIER_VOICE_MAP.get(tier, ['free'])
        if v not in VOICE_PROMPTS:
            return 'free'
        if v not in allowed:
            return 'free'
        return v


class FeedbackRequest(BaseModel):
    rating: str
    query: str = ""
    reply_hash: str = ""
    district: str = ""


# /api/feedback moved to voteiq/api/routes/feedback.py


# /api/pdf-chat moved to voteiq/api/routes/chat.py


# upload-db, congress-debug moved to voteiq/api/routes/admin.py


# /api/admin/bills-debug moved to voteiq/api/routes/admin.py


# /api/admin/bill-descriptions/search moved to voteiq/api/routes/admin.py


# polls routes moved to voteiq/api/routes/polls.py


# congress/members, federal-profiles, representatives, va-officials moved to voteiq/api/routes/members.py


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


# /api/races/2026, /api/races/2026/refresh, /races-2026 moved to voteiq/api/routes/elections.py


# /news route moved to voteiq/api/routes/news.py


# /polls route moved to voteiq/api/routes/polls.py


# /api/bills-chat moved to voteiq/api/routes/chat.py


# /api/bills-chat-stream moved to voteiq/api/routes/chat.py


_SOURCE_LINE = (
    "\n\n---\n"
    "*Sources: Congress.gov · Congressional Record/GovInfo where available · "
    "OpenStates · Virginia LIS · FEC where available. "
    "Data current through May 16, 2026. "
    "VoteIQ does not infer motive, intent, or causation from votes, donations, or bill activity.*"
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


# /baseline-map moved to voteiq/api/routes/district.py


# /virignia-map and /virginia-map moved to voteiq/api/routes/district.py


# /api/analyst/{member_id} — live in voteiq/api/routes/analyst.py


# /api/timing/{member_id} — live in voteiq/api/routes/analyst.py


@app.on_event("startup")
async def _on_startup():
    def _embed_task():
        try:
            n = backfill_news_embeddings()
            if n:
                print(f"Voyage: embedded {n} news articles.")
        except Exception as exc:
            print(f"Voyage backfill skipped: {exc}")

    def _gov_actions_task():
        try:
            import ingest_governor_actions
            n = ingest_governor_actions.run(sessions=["2025", "2026"])
            print(f"Governor actions: upserted {n} records into polls.db.")
        except Exception as exc:
            print(f"Governor actions ingest skipped: {exc}")

    threading.Thread(target=_embed_task, daemon=True).start()
    threading.Thread(target=_gov_actions_task, daemon=True).start()


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
