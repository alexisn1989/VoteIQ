"""Donor heat maps — state races and federal races, separate pages."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(tags=["donor_map"])

_BASE_DIR = Path(__file__).resolve().parents[3]
_data_dir_raw = os.getenv("DATA_DIR", str(_BASE_DIR))
_DATA_DIR = _data_dir_raw if os.path.isdir(_data_dir_raw) else str(_BASE_DIR)
_POLLS_DB = os.path.join(_DATA_DIR, "polls.db")
_CENTROIDS_FILE  = _BASE_DIR / "data" / "zip_centroids.json"
_CACHE_FILE      = _BASE_DIR / "data" / "donor_map_cache.json"
_MAPBOX_TOKEN    = os.getenv("MAPBOX_TOKEN", "")

# Skip states/territories that aren't US states
_SKIP_STATES = {"ZZ", "PR", "GU", "VI", "AS", "MP", "AA", "AE", "AP"}

try:
    with _CENTROIDS_FILE.open("r", encoding="utf-8") as _f:
        _CENTROIDS: dict[str, list[float]] = json.load(_f)
except FileNotFoundError:
    _CENTROIDS = {}

# Pre-computed cache — eliminates slow JOIN queries at request time
try:
    with _CACHE_FILE.open("r", encoding="utf-8") as _f:
        _DONOR_CACHE: dict = json.load(_f)
    print("[donor-map] Cache loaded from donor_map_cache.json")
except FileNotFoundError:
    _DONOR_CACHE = {}


def _conn():
    if not os.path.isfile(_POLLS_DB):
        raise HTTPException(status_code=503, detail="Database not available")
    c = sqlite3.connect(_POLLS_DB)
    c.row_factory = sqlite3.Row
    return c


def _inject(data: dict, template: str) -> str:
    safe  = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    token = json.dumps(_MAPBOX_TOKEN)
    with (_BASE_DIR / "templates" / template).open("r", encoding="utf-8") as f:
        html = f.read()
    return html.replace(
        "</head>",
        f"<script>window._MAP_DATA={safe};window._MAPBOX_TOKEN={token};</script></head>",
        1,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  STATE DONOR MAP  (/donor-map-state)
# ══════════════════════════════════════════════════════════════════════════════

def _zip_rows_to_list(rows) -> list[dict]:
    out = []
    for r in rows:
        coords = _CENTROIDS.get(r["z5"])
        if coords:
            out.append({
                "zip": r["z5"], "city": (r["city"] or "").title(),
                "lat": coords[0], "lng": coords[1],
                "total": r["total"], "donors": r["donors"],
            })
    return out


_STATE_SQL = """
    SELECT state_code AS state, ROUND(SUM(amount),0) AS total, COUNT(*) AS donors
    FROM   va_cf_schedule_a
    WHERE  state_code IS NOT NULL AND state_code != '' AND length(state_code)=2
    GROUP  BY state_code ORDER BY total DESC
"""
_STATE_PARTY_SQL = """
    SELECT a.state_code AS state, ROUND(SUM(a.amount),0) AS total, COUNT(*) AS donors
    FROM   va_cf_schedule_a a
    JOIN   va_cf_reports r ON r.ReportUID = a.report_uid
    WHERE  r.Party = ? AND a.state_code IS NOT NULL
      AND  a.state_code != '' AND length(a.state_code)=2
    GROUP  BY a.state_code ORDER BY total DESC
"""
_ZIP_SQL = """
    SELECT substr(zip_code,1,5) AS z5, MAX(city) AS city,
           ROUND(SUM(amount),0) AS total, COUNT(*) AS donors
    FROM   va_cf_schedule_a
    WHERE  state_code='VA' AND zip_code IS NOT NULL
      AND  length(zip_code)>=5 AND zip_code GLOB '[0-9]*'
    GROUP  BY z5 HAVING total>=50000 ORDER BY total DESC
"""
_ZIP_PARTY_SQL = """
    SELECT substr(a.zip_code,1,5) AS z5, MAX(a.city) AS city,
           ROUND(SUM(a.amount),0) AS total, COUNT(*) AS donors
    FROM   va_cf_schedule_a a
    JOIN   va_cf_reports r ON r.ReportUID = a.report_uid
    WHERE  r.Party = ? AND a.state_code='VA'
      AND  a.zip_code IS NOT NULL AND length(a.zip_code)>=5
      AND  a.zip_code GLOB '[0-9]*'
    GROUP  BY z5 HAVING total>=25000 ORDER BY total DESC
"""


def _state_donor_data(conn) -> dict:
    states     = [dict(r) for r in conn.execute(_STATE_SQL).fetchall()       if r["state"] not in _SKIP_STATES]
    states_dem = [dict(r) for r in conn.execute(_STATE_PARTY_SQL, ("Democratic",)).fetchall() if r["state"] not in _SKIP_STATES]
    states_rep = [dict(r) for r in conn.execute(_STATE_PARTY_SQL, ("Republican",)).fetchall() if r["state"] not in _SKIP_STATES]

    va_zips     = _zip_rows_to_list(conn.execute(_ZIP_SQL).fetchall())
    va_zips_dem = _zip_rows_to_list(conn.execute(_ZIP_PARTY_SQL, ("Democratic",)).fetchall())
    va_zips_rep = _zip_rows_to_list(conn.execute(_ZIP_PARTY_SQL, ("Republican",)).fetchall())

    return {
        "source": "state",
        "states": states, "states_dem": states_dem, "states_rep": states_rep,
        "va_zips": va_zips, "va_zips_dem": va_zips_dem, "va_zips_rep": va_zips_rep,
    }


@router.get("/api/donor-map-state")
def api_donor_map_state():
    if "state" in _DONOR_CACHE:
        return _DONOR_CACHE["state"]
    conn = _conn()
    try:
        return _state_donor_data(conn)
    finally:
        conn.close()


@router.get("/donor-map-state", response_class=HTMLResponse)
def donor_map_state_page():
    """State-race donor heat map — US choropleth + Virginia zip detail."""
    if "state" in _DONOR_CACHE:
        return _inject(_DONOR_CACHE["state"], "donor_map_state.html")
    conn = _conn()
    try:
        return _inject(_state_donor_data(conn), "donor_map_state.html")
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
#  FEDERAL DONOR MAP  (/donor-map-federal)
# ══════════════════════════════════════════════════════════════════════════════

def _norm_name(raw: str) -> str:
    """'Lastname, Firstname M.' → 'Firstname Lastname'."""
    raw = raw.strip()
    if "," in raw:
        last, first = raw.split(",", 1)
        first = re.sub(r"\s+[A-Z]\.$", "", first.strip())
        return f"{first.strip()} {last.strip()}"
    return raw


def _federal_donor_data(conn) -> dict:
    # Candidate→party lookup from congress_members
    party_map: dict[str, str] = {}   # bioguide_id → party
    for r in conn.execute("SELECT bioguide_id, party FROM congress_members").fetchall():
        party_map[r["bioguide_id"]] = r["party"]

    rows = conn.execute("""
        SELECT candidate_name, bioguide_id, state,
               ROUND(SUM(amount), 0) AS total,
               COUNT(*)              AS donors
        FROM   fec_individual_contributions
        WHERE  state IS NOT NULL AND state != ''
          AND  candidate_name IS NOT NULL
        GROUP  BY candidate_name, bioguide_id, state
        ORDER  BY total DESC
    """).fetchall()

    key_to_name:  dict[str, str]  = {}
    key_to_party: dict[str, str]  = {}
    cand_states:  dict[str, dict] = {}

    for r in rows:
        if r["state"] in _SKIP_STATES:
            continue
        bid   = (r["bioguide_id"] or "").strip()
        norm  = _norm_name(r["candidate_name"])
        key   = bid if bid else norm.lower()

        if key not in key_to_name:
            key_to_name[key]  = norm
            key_to_party[key] = party_map.get(bid, "Unknown") if bid else "Unknown"
        cname = key_to_name[key]

        cand_states.setdefault(cname, {})
        s = r["state"]
        if s not in cand_states[cname]:
            cand_states[cname][s] = {"state": s, "total": 0, "donors": 0}
        cand_states[cname][s]["total"]  += r["total"]
        cand_states[cname][s]["donors"] += r["donors"]

    # Party-split aggregate queries
    def _fed_party_states(party: str) -> list[dict]:
        rows2 = conn.execute("""
            SELECT f.state, ROUND(SUM(f.amount),0) AS total, COUNT(*) AS donors
            FROM   fec_individual_contributions f
            JOIN   congress_members m ON m.bioguide_id = f.bioguide_id
            WHERE  m.party = ? AND f.state IS NOT NULL AND f.state != ''
            GROUP  BY f.state ORDER BY total DESC
        """, (party,)).fetchall()
        return [dict(r) for r in rows2 if r["state"] not in _SKIP_STATES]

    all_rows = conn.execute("""
        SELECT state, ROUND(SUM(amount),0) AS total, COUNT(*) AS donors
        FROM   fec_individual_contributions
        WHERE  state IS NOT NULL AND state != ''
        GROUP  BY state ORDER BY total DESC
    """).fetchall()

    states_all = [dict(r) for r in all_rows if r["state"] not in _SKIP_STATES]
    states_dem = _fed_party_states("Democratic")
    states_rep = _fed_party_states("Republican")

    by_candidate = {
        name: sorted(states.values(), key=lambda x: -x["total"])
        for name, states in cand_states.items()
    }
    # candidates as list of {name, party}
    candidates = sorted(
        [{"name": k, "party": key_to_party[key]}
         for key, k in key_to_name.items()],
        key=lambda c: -sum(s["total"] for s in by_candidate[c["name"]])
    )

    return {
        "source":       "federal",
        "states_all":   states_all,
        "states_dem":   states_dem,
        "states_rep":   states_rep,
        "by_candidate": by_candidate,
        "candidates":   candidates,
    }


@router.get("/api/donor-map-federal")
def api_donor_map_federal():
    if "federal" in _DONOR_CACHE:
        return _DONOR_CACHE["federal"]
    conn = _conn()
    try:
        return _federal_donor_data(conn)
    finally:
        conn.close()


@router.get("/donor-map-federal", response_class=HTMLResponse)
def donor_map_federal_page():
    """Federal-race donor map — US choropleth with per-candidate filter."""
    if "federal" in _DONOR_CACHE:
        return _inject(_DONOR_CACHE["federal"], "donor_map_federal.html")
    conn = _conn()
    try:
        return _inject(_federal_donor_data(conn), "donor_map_federal.html")
    finally:
        conn.close()


_SKIP_SECTORS = {"Other", "Unknown", "Other/Unknown", "Retired", "Retired/Individual",
                  "Self-Employed", "Homemaker", "Grassroots"}
_TIER_BANDS = [
    ("Mid-Level ($1K-$3.3K)", 1000.0, 3300.0),
    ("Max-Out ($3.3K-$6.6K)", 3300.0, 6600.0),
    ("Super-Max ($6.6K+)", 6600.0, None),
]


@router.get("/api/federal/state-sectors/{state}")
def api_federal_state_sectors(state: str):
    """Employer-sector $ breakdown for contributions originating from a given US state."""
    state = state.upper()
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT employer_sector AS label,
                   ROUND(SUM(amount),0) AS total, COUNT(*) AS donors
            FROM   fec_individual_contributions
            WHERE  state = ? AND amount > 0
              AND  employer_sector IS NOT NULL
            GROUP  BY employer_sector
            ORDER  BY total DESC
        """, (state,)).fetchall()
        sectors = [
            {"label": r["label"], "total": r["total"], "donors": r["donors"]}
            for r in rows
            if r["label"] not in _SKIP_SECTORS
        ]
        return {"state": state, "sectors": sectors}
    finally:
        conn.close()


@router.get("/api/federal/state-top-employers/{state}")
def api_federal_state_top_employers(state: str):
    """Top 20 employers by $ donated from a given US state to VA federal candidates."""
    state = state.upper()
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT contributor_employer         AS employer,
                   employer_sector             AS sector,
                   ROUND(SUM(amount), 0)       AS total,
                   COUNT(*)                    AS donors
            FROM   fec_individual_contributions
            WHERE  state = ? AND amount > 0
              AND  contributor_employer IS NOT NULL AND contributor_employer != ''
              AND  employer_sector NOT IN (
                       'Retired','Retired/Individual','Self-Employed',
                       'Homemaker','Other','Other/Unknown','Grassroots')
            GROUP  BY contributor_employer
            ORDER  BY total DESC
            LIMIT  20
        """, (state,)).fetchall()
        return {"state": state, "employers": [
            {"name": r["employer"], "sector": r["sector"] or "", "total": r["total"], "donors": r["donors"]}
            for r in rows
        ]}
    finally:
        conn.close()


@router.get("/api/federal/state-tiers/{state}")
def api_federal_state_tiers(state: str):
    """Donor-tier breakdown for itemized federal contributions from a given US state."""
    state = state.upper()
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT amount FROM fec_individual_contributions
            WHERE  state = ? AND amount > 0
        """, (state,)).fetchall()
        agg: dict[str, list] = {}
        for r in rows:
            amt = float(r["amount"])
            for label, lo, hi in _TIER_BANDS:
                if amt >= lo and (hi is None or amt < hi):
                    if label not in agg:
                        agg[label] = [0.0, 0]
                    agg[label][0] += amt
                    agg[label][1] += 1
                    break
        tiers = [
            {"tier": label, "total": round(agg[label][0], 0), "count": agg[label][1]}
            for label, _lo, _hi in _TIER_BANDS
            if label in agg
        ]
        return {"state": state, "tiers": tiers}
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
#  DONOR–LEGISLATION CORRELATION  (/donor-legislation)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/donor-legislation", response_class=HTMLResponse)
def donor_legislation_page():
    """Donor-to-legislation alignment for VA state legislators."""
    data = _DONOR_CACHE.get("state", {})
    return _inject(data, "donor_legislation.html")


@router.get("/analysis", response_class=HTMLResponse)
def analysis_page():
    """Comprehensive donor-influence analysis — all findings aggregated."""
    data = _DONOR_CACHE.get("state", {})
    return _inject(data, "analysis.html")


# ══════════════════════════════════════════════════════════════════════════════
#  FOLLOW THE MONEY  (/follow-the-money)
# ══════════════════════════════════════════════════════════════════════════════

def _build_money_vs_bills_data() -> dict:
    """Compute scatter data (donated $, bills sponsored) per sector per legislator."""
    import math as _math
    from collections import defaultdict

    conn = _conn()

    BILL_TAGS: dict[str, list[str]] = {
        "Commercial Real Estate":    ["zoning", "landlord", "eviction", "land use",
                                       "commercial development", "mixed use"],
        "Agribusiness":              ["agriculture", "farmer", "farm bureau", "agricultural",
                                       "pesticide", "crop insurance"],
        "Trial Lawyers":             ["wrongful death", "consumer protection",
                                       "personal injury", "class action", "plaintiff"],
        "Fossil Fuels":              ["coal", "natural gas pipeline", "fossil fuel",
                                       "petroleum", "fracking", "compressor station"],
        "Renewables":                ["renewable energy", "solar energy", "clean energy",
                                       "electric vehicle", "net metering"],
        "Service & Retail Workers":  ["minimum wage", "paid sick leave", "gig worker",
                                       "retail worker"],
        "Building & Industrial Unions": ["prevailing wage", "building trade",
                                          "project labor agreement"],
        "Gambling/Casinos":          ["casino", "gaming license", "sports betting",
                                       "horse racing"],
        "Health Professionals":      ["physician", "nurse practitioner", "medical licensure",
                                       "prior authorization"],
        "Banking":                   ["bank regulation", "lending", "credit union", "mortgage"],
    }

    def _tag(title: str, desc: str = "") -> set[str]:
        text = (title + " " + (desc or "")).lower()
        found: set[str] = set()
        for ind, kws in BILL_TAGS.items():
            for kw in kws:
                if kw in text:
                    found.add(ind)
                    break
        return found

    # Bill counts: legislator_name → sector → float (sponsor=1, cosponsor=0.5)
    bill_counts: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in conn.execute("""
        SELECT s.legislator_name, b.title, b.summary
        FROM va_legislator_sponsored_bills s
        JOIN legiscan_va_bills b ON b.bill_id = s.bill_id
        WHERE b.title IS NOT NULL
    """):
        for ind in _tag(r["title"], r["summary"] or ""):
            bill_counts[r["legislator_name"].strip()][ind] += 1.0
    for r in conn.execute("""
        SELECT s.legislator_name, b.title, b.summary
        FROM va_legislator_cosponsor_bills s
        JOIN legiscan_va_bills b ON b.bill_id = s.bill_id
        WHERE b.title IS NOT NULL
    """):
        for ind in _tag(r["title"], r["summary"] or ""):
            bill_counts[r["legislator_name"].strip()][ind] += 0.5

    def _get_bills(name: str, sector: str) -> float:
        parts = name.replace(".", "").split()
        best = 0.0
        for v in {name, f"{parts[0]} {parts[-1]}" if len(parts) >= 2 else name}:
            best = max(best, bill_counts[v].get(sector, 0.0))
        return round(best, 1)

    # Load legislators
    legs = []
    for r in conn.execute("""
        SELECT name, party, chamber, by_sector_json
        FROM campaign_finance_summary WHERE source LIKE '%sbe%'
    """):
        secs = json.loads(r["by_sector_json"] or "[]")
        party = r["party"] or ""
        p = "D" if "emocrat" in party else "R" if "epublican" in party else "I"
        legs.append({
            "name":    r["name"],
            "party":   p,
            "chamber": r["chamber"] or "",
            "sec_map": {e["sector"]: e["total"] for e in secs},
            "top_sector": secs[0]["sector"] if secs else None,
        })

    # Individual-donor totals per legislator per sector (for PAC vs. individual split)
    import re as _re
    _SECTOR_IND_KWS: list[tuple[str, list[str]]] = [
        ("Commercial Real Estate", ["real estate", "property management", "realty",
                                    "reit", "apartment", "homebuilder", "land develop"]),
        ("Agribusiness",           ["farm bureau", "agriculture", "farm", "dairy",
                                    "cattle", "poultry", "tyson", "smithfield", "perdue"]),
        ("Trial Lawyers",          ["trial lawyer", "plaintiff", "personal injury",
                                    "law firm", "attorney", "lawyer"]),
        ("Fossil Fuels",           ["coal", "natural gas", "petroleum", "dominion energy",
                                    "appalachian power", "columbia gas", "oil company"]),
        ("Renewables",             ["solar", "clean energy", "renewable", "wind energy",
                                    "apex clean"]),
        ("Service & Retail Workers", ["retail", "restaurant", "hotel", "hospitality",
                                      "food service"]),
        ("Building & Industrial Unions", ["union", "labor council", "teamster", "ibew",
                                          "carpenters", "plumbers", "sheet metal"]),
        ("Gambling/Casinos",       ["casino", "gaming", "draftkings", "fanduel",
                                    "caesars", "hard rock"]),
        ("Health Professionals",   ["hospital", "medical", "physician", "healthcare",
                                    "pharmacy", "dentist", "optometrist"]),
        ("Banking",                ["bank", "credit union", "financial services",
                                    "investment", "securities"]),
    ]

    def _norm_cand(s: str) -> str:
        return " ".join(_re.sub(r"[^a-z ]", "", s.lower()).split())

    def _classify_ind(emp: str, occ: str, co: str) -> str | None:
        text = " ".join([emp or "", occ or "", co or ""]).lower()
        for sect, kws in _SECTOR_IND_KWS:
            for kw in kws:
                if kw in text:
                    return sect
        return None

    # ind_totals[norm_name][sector] = sum of individual donations
    ind_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in conn.execute("""
        SELECT candidate_name, employer, occupation, last_or_company, amount
        FROM va_cf_schedule_a
        WHERE is_individual = 1 AND amount > 0
    """):
        if not r["candidate_name"]:
            continue
        sect = _classify_ind(r["employer"] or "", r["occupation"] or "",
                              r["last_or_company"] or "")
        if sect:
            ind_totals[_norm_cand(r["candidate_name"])][sect] += r["amount"]

    # Build scatter points per sector
    scatter_sectors: dict[str, list[dict]] = {}
    for sector in BILL_TAGS:
        pts = []
        for leg in legs:
            donated = leg["sec_map"].get(sector, 0)
            bills   = _get_bills(leg["name"], sector)
            if donated > 200 or bills > 0:
                nk      = _norm_cand(leg["name"])
                ind_d   = round(min(ind_totals[nk].get(sector, 0), donated))
                pac_d   = max(0, donated - ind_d)
                pts.append({
                    "name":        leg["name"],
                    "party":       leg["party"],
                    "chamber":     leg["chamber"],
                    "donated":     donated,
                    "pac_donated": pac_d,
                    "ind_donated": ind_d,
                    "bills":       bills,
                })
        if pts:
            scatter_sectors[sector] = pts

    # Pearson r (log-x for donations)
    def _pearson(pts: list[dict]) -> dict:
        valid = [p for p in pts if p["donated"] > 0]
        n = len(valid)
        if n < 5:
            return {"r": None, "p": None, "sig": "n/a", "n": n}
        xs = [_math.log10(p["donated"]) for p in valid]
        ys = [p["bills"] for p in valid]
        mx, my = sum(xs)/n, sum(ys)/n
        num = sum((x-mx)*(y-my) for x,y in zip(xs, ys))
        dx  = sum((x-mx)**2 for x in xs) ** 0.5
        dy  = sum((y-my)**2 for y in ys) ** 0.5
        if dx == 0 or dy == 0:
            return {"r": 0.0, "p": 1.0, "sig": "n.s.", "n": n}
        r = num / (dx * dy)
        t = r * (n-2)**0.5 / max(1e-10, (1-r*r)**0.5)
        p = 2*(1 - 0.5*(1 + _math.erf(abs(t)/_math.sqrt(2))))
        sig = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "n.s."
        # regression coefficients (log-x linear)
        slope = num / (dx**2) if dx > 0 else 0
        intercept = my - slope * mx
        return {
            "r": round(r, 3), "p": round(p, 4), "sig": sig, "n": n,
            "slope": round(slope, 4), "intercept": round(intercept, 4),
        }

    correlations = {s: _pearson(pts) for s, pts in scatter_sectors.items()}

    # Gatekeeper: top CRE donors with 0 bills
    cre_pts = sorted(scatter_sectors.get("Commercial Real Estate", []),
                     key=lambda x: -x["donated"])[:12]
    gatekeeper = {
        "sector":               "Commercial Real Estate",
        "total_donated_top10":  sum(p["donated"] for p in cre_pts[:10]),
        "bills_top10":          sum(p["bills"]   for p in cre_pts[:10]),
        "top_donors":           cre_pts[:10],
    }

    # Party alignment stats
    party_stats = {
        "D": {"aligned": 0, "opposed": 0, "legs": []},
        "R": {"aligned": 0, "opposed": 0, "legs": []},
    }
    for r in conn.execute("""
        SELECT name, party, alignment_json FROM campaign_finance_summary
        WHERE alignment_json IS NOT NULL AND source LIKE '%sbe%'
    """):
        party = r["party"] or ""
        p = "D" if "emocrat" in party else "R" if "epublican" in party else None
        if not p:
            continue
        for a in json.loads(r["alignment_json"]):
            if a["score"] >= 75:
                party_stats[p]["aligned"] += 1
            elif a["score"] <= 25:
                party_stats[p]["opposed"] += 1
                party_stats[p]["legs"].append(
                    {"name": r["name"], "sector": a["sector"], "score": a["score"]}
                )

    conn.close()

    return {
        "scatter_sectors": scatter_sectors,
        "correlations":    correlations,
        "gatekeeper":      gatekeeper,
        "party_stats":     party_stats,
        "sector_order": [
            "Commercial Real Estate", "Agribusiness", "Trial Lawyers",
            "Fossil Fuels", "Renewables", "Service & Retail Workers",
            "Building & Industrial Unions", "Gambling/Casinos",
            "Health Professionals", "Banking",
        ],
    }


def _build_timing_data() -> dict:
    """Compute contribution-timing analysis: session vs off-season, bundling, monthly."""
    from collections import defaultdict
    import re as _re

    conn = _conn()

    # Virginia GA session = January–March
    SESSION_MONTHS = {1, 2, 3}

    # Lightweight sector classifier for raw donor rows
    _TIMING_SECTORS: list[tuple[str, list[str]]] = [
        ("Fossil Fuels",           ["dominion energy", "appalachian power", "coal",
                                    "natural gas", "petroleum", "columbia gas"]),
        ("Renewables",             ["solar", "apex clean", "clean energy", "wind energy"]),
        ("Commercial Real Estate", ["real estate", "property management", "realty",
                                    "reit", "apartment assoc"]),
        ("Gambling/Casinos",       ["casino", "gaming", "draftkings", "fanduel",
                                    "caesars", "hard rock", "penn gaming", "pamunkey"]),
        ("Trial Lawyers",          ["trial lawyer", "plaintiff", "personal injury",
                                    "allen & allen", "marks & harrison"]),
        ("Corporate Law",          ["mcguirewoods", "hunton", "williams mullen",
                                    "troutman", "reed smith", "gentry locke"]),
        ("Homebuilders",           ["homebuilder", "home builder", "nvr ", "ryan homes",
                                    "kb home", "lennar", "pulte"]),
        ("Agribusiness",           ["farm bureau", "virginia farm", "tyson",
                                    "smithfield", "perdue"]),
        ("Banking",                ["bank of america", "wells fargo", "jpmorgan",
                                    "bb&t", "truist", "credit union"]),
        ("Health Professionals",   ["virginia hospital", "vhha", "medical society",
                                    "hca health", "inova", "sentara", "bon secours"]),
        ("Telecom",                ["at&t", "verizon", "comcast", "cox comm", "lumen"]),
        ("Defense",                ["lockheed", "northrop", "raytheon",
                                    "general dynamics", "booz allen", "leidos"]),
        ("Insurance",              ["state farm", "allstate", "aetna", "cigna",
                                    "anthem", "geico", "nationwide ins"]),
    ]

    def _classify(employer: str, occ: str, company: str) -> str | None:
        text = " ".join([employer or "", occ or "", company or ""]).lower()
        for sector, kws in _TIMING_SECTORS:
            for kw in kws:
                if kw in text:
                    return sector
        return None

    # ── 1. Session premium (PAC/corporate only, 2018+) ──────────────────────
    timing: dict[str, dict] = defaultdict(
        lambda: {"s_amt": 0.0, "o_amt": 0.0, "s_n": 0, "o_n": 0}
    )
    monthly_by_sector: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for r in conn.execute("""
        SELECT transaction_date, amount, employer, occupation, last_or_company
        FROM va_cf_schedule_a
        WHERE is_individual = 0
          AND transaction_date IS NOT NULL
          AND amount > 0
          AND election_cycle >= 2018
    """):
        sector = _classify(r["employer"], r["occupation"], r["last_or_company"])
        if not sector:
            continue
        try:
            mo = int(r["transaction_date"][5:7])
            ym = r["transaction_date"][:7]
        except (TypeError, ValueError):
            continue
        key = "s" if mo in SESSION_MONTHS else "o"
        timing[sector][key + "_amt"] += r["amount"]
        timing[sector][key + "_n"]   += 1
        if r["transaction_date"] >= "2023-01":
            monthly_by_sector[sector][ym] += r["amount"]

    session_premium = []
    for sector, d in timing.items():
        total = d["s_amt"] + d["o_amt"]
        if total < 5_000:
            continue
        avg_s = d["s_amt"] / max(d["s_n"], 1)
        avg_o = d["o_amt"] / max(d["o_n"], 1)
        ratio = round(avg_s / avg_o, 3) if avg_o > 0 else None
        session_premium.append({
            "sector":      sector,
            "session_amt": round(d["s_amt"]),
            "off_amt":     round(d["o_amt"]),
            "session_pct": round(d["s_amt"] / total * 100, 1),
            "avg_session": round(avg_s),
            "avg_off":     round(avg_o),
            "ratio":       ratio,
        })
    session_premium.sort(key=lambda x: -(x["ratio"] or 0))

    # Monthly series — only keep key sectors, last 24 months of data
    MONTHLY_SECTORS = [
        "Fossil Fuels", "Gambling/Casinos", "Commercial Real Estate",
        "Trial Lawyers", "Renewables", "Homebuilders",
    ]
    monthly_series: dict[str, list[dict]] = {}
    for sector in MONTHLY_SECTORS:
        raw = monthly_by_sector.get(sector, {})
        monthly_series[sector] = [
            {"ym": ym, "amount": round(amt), "session": int(ym[5:7]) in SESSION_MONTHS}
            for ym, amt in sorted(raw.items())
        ]

    # ── 2. Session bundling events ────────────────────────────────────────────
    bundling_rows = []
    for r in conn.execute("""
        SELECT
            COALESCE(last_or_company, employer) as entity,
            candidate_name,
            COUNT(*)    as n,
            SUM(amount) as total,
            MIN(transaction_date) as first_dt,
            CAST(substr(MIN(transaction_date),6,2) AS INTEGER) as mo
        FROM va_cf_schedule_a
        WHERE is_individual = 0
          AND transaction_date IS NOT NULL
          AND election_cycle >= 2020
          AND amount >= 500
          AND CAST(substr(transaction_date,6,2) AS INTEGER) BETWEEN 1 AND 3
          AND length(COALESCE(last_or_company, employer, '')) > 5
          AND lower(COALESCE(last_or_company, employer,'')) NOT LIKE '%retired%'
          AND lower(COALESCE(last_or_company, employer,'')) NOT LIKE '%not employ%'
          AND lower(COALESCE(last_or_company, employer,'')) NOT LIKE '%self emp%'
        GROUP BY lower(COALESCE(last_or_company, employer)),
                 lower(candidate_name),
                 substr(transaction_date, 1, 7)
        HAVING n >= 2 AND total >= 10000
        ORDER BY total DESC
        LIMIT 30
    """):
        # Classify the entity
        entity = r["entity"] or ""
        sector = _classify(entity, "", entity)
        bundling_rows.append({
            "entity":    entity[:40],
            "candidate": r["candidate_name"] or "",
            "n":         r["n"],
            "total":     round(r["total"]),
            "month":     r["first_dt"][:7] if r["first_dt"] else "",
            "sector":    sector or "Other",
        })

    conn.close()

    return {
        "timing_session_premium": session_premium,
        "timing_monthly":         monthly_series,
        "timing_bundling":        bundling_rows,
    }


@router.get("/follow-the-money", response_class=HTMLResponse)
def follow_the_money_page():
    """Donor $ vs. bill sponsorship scatter analysis for VA state legislators."""
    data = _build_money_vs_bills_data()
    data.update(_build_timing_data())
    return _inject(data, "follow_the_money.html")


@router.get("/dominion-analysis", response_class=HTMLResponse)
def dominion_analysis_page():
    """Dominion Energy utility donations vs energy bill votes for VA legislators."""
    data = _DONOR_CACHE.get("state", {})
    return _inject(data, "dominion.html")


# ── Legacy redirect ───────────────────────────────────────────────────────────

@router.get("/donor-map", response_class=HTMLResponse)
def donor_map_redirect():
    return RedirectResponse("/donor-map-state", status_code=301)
