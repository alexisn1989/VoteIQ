from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from voteiq.utils import _load_historical_results

router = APIRouter(tags=["elections"])

_BASE_DIR  = Path(__file__).resolve().parents[3]
_DATA_DIR  = Path(os.getenv("DATA_DIR", str(_BASE_DIR)))

# Keyed by int year (2018–2024); historical (2016–2020) uses _historical_data_cache
_election_data_cache:  dict[int, Any] = {}
_historical_data_cache: dict[str, Any] = {}


def _data_path(*parts: str) -> Path:
    # Election JSON/CSV files are committed to the repo (BASE_DIR), not the
    # mounted data disk (DATA_DIR=/var/data).  Always resolve relative to repo.
    return _BASE_DIR.joinpath(*parts)


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Election data file not found: {path.name}",
        )
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_csv(path: Path) -> list[dict]:
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Election CSV not found: {path.name}",
        )
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _inject_data(template_name: str, data: Any) -> str:
    safe = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    html_path = _BASE_DIR / "templates" / template_name
    with html_path.open("r", encoding="utf-8") as f:
        html = f.read()
    return html.replace(
        "</head>",
        f"<script>window._ELECTION_DATA={safe};</script></head>",
        1,
    )


# ── Per-year result loaders ───────────────────────────────────────────────────

def _build_2023_race(cands_dict: dict) -> dict:
    total = sum(c["votes"] for c in cands_dict.values())
    candidates = []
    for cname, cdata in cands_dict.items():
        votes = cdata["votes"]
        pct   = round(votes / total * 100, 1) if total else 0.0
        candidates.append({"name": cname, "party": cdata["party"], "votes": votes, "pct": pct})
    candidates.sort(key=lambda c: c["votes"], reverse=True)
    return {"total": total, "candidates": candidates}


def _load_2023_results() -> dict:
    if 2023 in _election_data_cache:
        return _election_data_cache[2023]

    json_path = _data_path("election_results_2023.json")
    if json_path.exists():
        with json_path.open(encoding="utf-8") as f:
            raw = json.load(f)
        _election_data_cache[2023] = {
            "senate": {int(k): v for k, v in raw.get("senate", {}).items()},
            "hod":    {int(k): v for k, v in raw.get("hod",    {}).items()},
        }
        return _election_data_cache[2023]

    csv_path = _data_path("Election Results_a6f500ae-6aed-4237-b29a-8a94a22dfbbb.csv")
    senate_races: dict = {}
    hod_races:    dict = {}
    try:
        with csv_path.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
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
                name  = row.get("CandidateName", "").strip()
                party = row.get("Party", "").strip()
                try:
                    votes = int(row.get("TOTAL_VOTES", "").strip())
                except (TypeError, ValueError):
                    votes = 0
                races = senate_races if district_type == "state-senate" else hod_races
                races.setdefault(district, {"candidates": {}})["candidates"].setdefault(
                    name, {"name": name, "party": party, "votes": 0}
                )["votes"] += votes
    except Exception as exc:
        print(f"_load_2023_results: {exc}")
        _election_data_cache[2023] = {"senate": {}, "hod": {}}
        return _election_data_cache[2023]

    _election_data_cache[2023] = {
        "senate": {d: _build_2023_race(v["candidates"]) for d, v in sorted(senate_races.items())},
        "hod":    {d: _build_2023_race(v["candidates"]) for d, v in sorted(hod_races.items())},
    }
    return _election_data_cache[2023]


def _load_year_results(year: int, fallback: dict) -> dict:
    if year in _election_data_cache:
        return _election_data_cache[year]
    try:
        with _data_path(f"election_results_{year}.json").open(encoding="utf-8") as f:
            _election_data_cache[year] = json.load(f)
    except Exception as e:
        print(f"_load_{year}_results: {e}")
        _election_data_cache[year] = fallback
    return _election_data_cache[year]


def _load_2024_results() -> dict:
    return _load_year_results(2024, {"statewide": [], "locality_results": {}, "congress": {}})

def _load_2022_results() -> dict:
    return _load_year_results(2022, {"congress": {}})

def _load_2021_results() -> dict:
    return _load_year_results(2021, {"statewide": [], "locality_results": {}, "hod": {}})

def _load_2020_results() -> dict:
    return _load_year_results(2020, {"statewide": [], "locality_results": {}, "congress": {}})

def _load_2018_results() -> dict:
    return _load_year_results(2018, {"statewide": [], "locality_results": {}, "congress": {}})


HISTORICAL_ELECTION_META: dict[str, dict] = {
    "2016": {
        "title":    "2016 Virginia Federal Election",
        "subtitle": "President and U.S. House results from the November 8, 2016 general election.",
        "date":     "November 8, 2016",
        "kind":     "federal",
        "maps": [
            {"tab": "president-map", "label": "President Map", "title": "President - Locality Map",              "layer": "pres_2016"},
            {"tab": "congress-map",  "label": "Congress Map",  "title": "U.S. House - District Results Map",     "url": "/maps/congress/2016"},
        ],
        "bubble_maps": [
            {"tab": "president-bubbles", "label": "President Bubbles", "title": "President - Statewide Vote Bubbles", "year": "2016", "office": "President"},
        ],
        "notes": ["Congressional district map shows 2010-cycle (113th Congress) boundaries with 2016 election results."],
    },
    "2017": {
        "title":    "2017 Virginia State Elections",
        "subtitle": "Governor, Lieutenant Governor, Attorney General, and House of Delegates results from the November 7, 2017 general election.",
        "date":     "November 7, 2017",
        "kind":     "statewide",
        "maps": [
            {"tab": "governor-map", "label": "Governor Map", "title": "Governor - Locality Map",            "layer": "gov_2017"},
            {"tab": "ltgov-map",    "label": "Lt. Gov Map",  "title": "Lieutenant Governor - Locality Map", "layer": "ltgov_2017"},
            {"tab": "ag-map",       "label": "AG Map",       "title": "Attorney General - Locality Map",    "layer": "ag_2017"},
            {"tab": "hod-map",      "label": "HOD Map",      "title": "House of Delegates - District Results Map", "url": "/maps/hod/2017"},
        ],
        "bubble_maps": [
            {"tab": "governor-bubbles", "label": "Gov Bubbles",     "title": "Governor - Statewide Vote Bubbles",           "year": "2017", "office": "Governor"},
            {"tab": "ltgov-bubbles",    "label": "Lt. Gov Bubbles", "title": "Lieutenant Governor - Statewide Vote Bubbles", "year": "2017", "office": "Lieutenant Governor"},
            {"tab": "ag-bubbles",       "label": "AG Bubbles",      "title": "Attorney General - Statewide Vote Bubbles",    "year": "2017", "office": "Attorney General"},
        ],
        "notes": [],
    },
    "2018": {
        "title":    "2018 Virginia Midterm Election",
        "subtitle": "U.S. Senate and U.S. House results from the November 6, 2018 general election.",
        "date":     "November 6, 2018",
        "kind":     "federal",
        "maps": [
            {"tab": "congress-map", "label": "Congress Map", "title": "U.S. House - District Results Map", "url": "/maps/congress/2018"},
        ],
        "notes": ["Congressional district map shows 2010-cycle (113th Congress) boundaries with 2018 election results."],
    },
    "2019": {
        "title":    "2019 Virginia State Legislative Elections",
        "subtitle": "State Senate and House of Delegates results from the November 5, 2019 general election.",
        "date":     "November 5, 2019",
        "kind":     "district",
        "maps": [
            {"tab": "senate-map",      "label": "Senate Map",      "title": "State Senate - District Results Map",        "url": "/maps/senate/2019"},
            {"tab": "senate-flip-map", "label": "Senate Flip Map", "title": "State Senate - 2019 to 2023 Flip Map",       "layer": "senate_2023_flip_2019"},
            {"tab": "hod-map",         "label": "HOD Map",         "title": "House of Delegates - District Results Map",  "url": "/maps/hod/2019"},
            {"tab": "hod-flip-map",    "label": "HOD Flip Map",    "title": "House of Delegates - 2017 to 2019 Flip Map", "layer": "hod_2019_flip_2017"},
        ],
        "notes": ["Maps show 2010-cycle district boundaries with 2019 election results."],
    },
    "2020": {
        "title":    "2020 Virginia Local Elections",
        "subtitle": "June 4 town election results from the uploaded CSV.",
        "date":     "June 4, 2020",
        "kind":     "local",
        "maps":     [],
        "notes":    ["This CSV contains local town races only, so no statewide choropleth map is available."],
    },
}


# ── /election-map ─────────────────────────────────────────────────────────────

@router.get("/election-map", response_class=HTMLResponse)
@router.get("/results-map",  response_class=HTMLResponse)
def election_map(mode: str = "total"):
    import main as _m
    if mode not in _m._election_maps:
        try:
            _m._election_maps[mode] = _m._build_election_map(mode)
        except Exception as e:
            print(f"Warning: could not build election map ({mode}): {e}")
            return f"<p style='font-family:sans-serif;padding:40px'>Map unavailable: {e}</p>"
    return _m._election_maps.get(mode, "")


# ── /past-elections ───────────────────────────────────────────────────────────

@router.get("/past-elections", response_class=HTMLResponse)
def election_results_page():
    import main as _m
    results = _m._load_2025_results()
    locality_results = {}
    for _office in ["Governor", "Lieutenant Governor", "Attorney General"]:
        try:
            locality_results[_office] = _m._load_2025_statewide_locality_results(_office)
        except Exception:
            locality_results[_office] = {}
    all_data = {
        **results,
        "locality_results": locality_results,
        "hod_flips": _m._build_2025_hod_flips(results.get("hod", {})),
    }
    return _inject_data("election_results.html", all_data)


@router.get("/api/election-results-2025")
def election_results_2025_api():
    import main as _m
    return _m._load_2025_results()


@router.get("/past-elections/2023", response_class=HTMLResponse)
def election_results_2023_page():
    import main as _m
    data = _load_2023_results()
    data = {
        **data,
        "senate_flips": _m._build_2023_state_leg_flips("senate", data.get("senate", {})),
        "hod_flips":    _m._build_2023_state_leg_flips("hod",    data.get("hod",    {})),
    }
    return _inject_data("election_results_2023.html", data)


@router.get("/past-elections/2024", response_class=HTMLResponse)
def election_results_2024_page():
    return _inject_data("election_results_2024.html", _load_2024_results())


@router.get("/past-elections/2022", response_class=HTMLResponse)
def election_results_2022_page():
    return _inject_data("election_results_2022.html", _load_2022_results())


@router.get("/past-elections/2021", response_class=HTMLResponse)
def election_results_2021_page():
    return _inject_data("election_results_2021.html", _load_2021_results())


@router.get("/past-elections/2020", response_class=HTMLResponse)
def election_results_2020_page():
    return _inject_data("election_results_2020.html", _load_2020_results())


@router.get("/past-elections/2018", response_class=HTMLResponse)
def election_results_2018_page():
    return _inject_data("election_results_2018.html", _load_2018_results())


@router.get("/past-elections/{year}", response_class=HTMLResponse)
def election_results_historical_page(year: str):
    year = str(year)
    if year not in HISTORICAL_ELECTION_META:
        raise HTTPException(status_code=404, detail="Election year not found")
    data = _load_historical_results(year)
    meta = HISTORICAL_ELECTION_META[year]
    safe_data = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    safe_meta = json.dumps(meta, default=str).replace("</script>", "<\\/script>")
    with (_BASE_DIR / "templates" / "election_results_historical.html").open("r", encoding="utf-8") as f:
        html = f.read()
    return html.replace(
        "</head>",
        f"<script>window._ELECTION_DATA={safe_data};window._ELECTION_META={safe_meta};</script></head>",
        1,
    )


# ── /api/races/2026 ───────────────────────────────────────────────────────────

@router.get("/api/races/2026")
def api_races_2026():
    import main as _m
    return _m._fetch_races_2026()


@router.get("/api/races/2026/refresh")
def api_races_2026_refresh():
    import main as _m
    _m._races_2026_cache    = None
    _m._races_2026_cache_ts = 0.0
    return _m._fetch_races_2026()


@router.get("/races-2026", response_class=HTMLResponse)
def races_2026_page():
    with (_BASE_DIR / "templates" / "races2026.html").open("r", encoding="utf-8") as f:
        return f.read()
