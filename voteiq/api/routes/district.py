from __future__ import annotations

import json
import os
import re
from html import escape
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["district"])


def _get_district_contexts():
    from main import HOD_CONTEXT, SD_CONTEXT, DISTRICT_CONTEXT, _HOD_MEMBER_URL
    return HOD_CONTEXT, SD_CONTEXT, DISTRICT_CONTEXT, _HOD_MEMBER_URL

_BASE_DIR     = Path(__file__).resolve().parents[3]
_DATA_DIR     = Path(os.getenv("DATA_DIR", str(_BASE_DIR)))
_TEMPLATE_DIR = _BASE_DIR / "templates"

_DISTRICT_MAP_FILES: dict[tuple[str, str], str] = {
    ("congress", "2016"): "va_congress_2016.html",
    ("congress", "2018"): "va_congress_2018.html",
    ("hod",      "2017"): "va_house_delegates_2017.html",
    ("senate",   "2019"): "va_senate_2019.html",
    ("hod",      "2019"): "va_house_delegates_2019.html",
}

_district_map_cache: dict[tuple[str, str], str] = {}


# ── /map ──────────────────────────────────────────────────────────────────────

@router.get("/map", response_class=HTMLResponse)
def get_map(address: str):
    import main as _m
    import folium
    result = _m.find_district(address)
    if "error" in result:
        return "<p>Address not found</p>"
    m = folium.Map(location=[result["lat"], result["lng"]], zoom_start=12)
    folium.Marker(
        location=[result["lat"], result["lng"]],
        popup=f"{result['district']}",
        icon=folium.Icon(color="red"),
    ).add_to(m)
    folium.GeoJson(_m._get_va_cd()).add_to(m)
    return m.get_root().render()


# ── /statewide-bubble-map ─────────────────────────────────────────────────────

@router.get("/statewide-bubble-map", response_class=HTMLResponse)
def statewide_bubble_map(year: str, office: str, v: str = None):
    import main as _m
    key = f"{year}:{office}"
    if key not in _m._statewide_bubble_maps:
        _m._statewide_bubble_maps[key] = _m._build_statewide_bubble_map(year, office)
    return _m._statewide_bubble_maps[key]


# ── /district-map ─────────────────────────────────────────────────────────────

_STATIC_LAYERS = {
    "congressional", "hod_results", "hod_flip", "gov_results", "ltgov_results",
    "ag_results", "pres_2024", "senate_2024", "congress_2024",
    "senate_2023_results", "hod_2023_results", "senate_2023_flip_2019",
    "hod_2023_flip_2021", "gov_2021", "ltgov_2021", "ag_2021",
    "hod_2021_results", "congress_2022", "gov_2017", "ltgov_2017", "ag_2017",
    "senate_2019_results", "hod_2019_results", "pres_2016", "congress_2016",
    "pres_2020", "senate_2020", "congress_2020", "senate_2018", "congress_2018",
    "pres_flip", "gov_flip", "gov_2025_flip", "congress_midterm_flip",
    "hod_state_flip", "pres_2020_2024_flip", "congress_2024_flip",
}


@router.get("/district-map", response_class=HTMLResponse)
def district_map(
    layer:    str   = "congressional",
    lat:      float = None,
    lng:      float = None,
    district: int   = None,
):
    import main as _m
    if layer in _STATIC_LAYERS and lat is None:
        if layer not in _m._district_maps:
            try:
                _m._district_maps[layer] = _m._build_district_map(layer)
            except Exception as e:
                return f"<p style='font-family:sans-serif;padding:40px'>Could not build map: {escape(str(e))}</p>"
        return _m._district_maps[layer]
    else:
        try:
            return _m._build_district_map(layer, user_lat=lat, user_lng=lng, district=district)
        except Exception as e:
            return f"<p style='font-family:sans-serif;padding:40px'>Could not build {escape(layer)} map: {escape(str(e))}</p>"


# ── /maps/{chamber}/{year} ────────────────────────────────────────────────────

@router.get("/maps/{chamber}/{year}", response_class=HTMLResponse)
def district_result_map(chamber: str, year: str):
    key = (chamber.lower().strip(), year.strip())
    if key not in _DISTRICT_MAP_FILES:
        raise HTTPException(status_code=404, detail=f"No map for {chamber}/{year}")
    if key not in _district_map_cache:
        path = _TEMPLATE_DIR / _DISTRICT_MAP_FILES[key]
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"Map file missing: {_DISTRICT_MAP_FILES[key]}")
        with path.open("r", encoding="utf-8") as f:
            _district_map_cache[key] = f.read()
    return _district_map_cache[key]


# ── /baseline-map ─────────────────────────────────────────────────────────────

@router.get("/baseline-map", response_class=HTMLResponse)
def baseline_map_page():
    import main as _m
    return HTMLResponse(content=_m._build_district_map("locality_baseline"))


# ── /virignia-map + /virginia-map ─────────────────────────────────────────────

@router.get("/virignia-map", response_class=HTMLResponse)
@router.get("/virginia-map",  response_class=HTMLResponse)
def virginia_map_page(layer: str = "counties", embed: bool = False):
    import main as _m
    _allowed = {
        "counties", "pres_flip", "gov_flip", "gov_2025_flip", "congress_midterm_flip",
        "hod_state_flip", "density_2017", "density_2019", "density_2021",
        "density_2023", "density_2025", "sd_flip", "hod_flip", "hod", "sd",
    }
    initial_layer = layer if layer in _allowed else "counties"
    mapbox_token  = os.getenv("MAPBOX_TOKEN", "")
    counties                 = _m._load_va_counties_geojson()
    hod                      = _m._load_hod_geojson()
    sd                       = _m._load_sd_geojson()
    pres_flip                = _m._build_pres_2016_2020_flip_geojson()
    hod_flip                 = _m._build_state_leg_2023_flip_geojson("hod")
    sd_flip                  = _m._build_state_leg_2023_flip_geojson("senate")
    gov_flip                 = _m._build_locality_office_flip_geojson("2017", "2021", "Governor")
    gov_2025_flip            = _m._build_locality_office_flip_geojson("2021", "2025", "Governor")
    congress_midterm_flip    = _m._build_congress_flip_geojson("2018", "2022")
    hod_state_flip           = _m._build_hod_2017_2021_flip_geojson()
    hod_density_layers       = {y: _m._build_hod_density_geojson(y)        for y in ("2017", "2019", "2021", "2023", "2025")}
    hod_density_point_layers = {y: _m._build_hod_density_points_geojson(y) for y in ("2017", "2019", "2021", "2023", "2025")}

    def _s(obj):
        return json.dumps(obj, default=str).replace("</script>", "<\\/script>")

    with (_TEMPLATE_DIR / "virginia_map.html").open("r", encoding="utf-8") as f:
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


# ── / ─────────────────────────────────────────────────────────────────────────

def _index_html() -> str:
    with (_TEMPLATE_DIR / "index.html").open("r", encoding="utf-8") as f:
        return f.read()


@router.get("/", response_class=HTMLResponse)
def home():
    return _index_html()


@router.get("/ask", response_class=HTMLResponse)
def ask_page():
    return _index_html()


# ── /api/lookup ───────────────────────────────────────────────────────────────

_NORFOLK_COUNCIL_URLS: dict[str, str] = {
    "martin a thomas jr":   "https://www.norfolk.gov/542/Vice-Mayor-Martin-A-Thomas-Jr",
    "courtney doyle":       "https://www.norfolk.gov/4111/Courtney-R-Doyle",
    "courtney r doyle":     "https://www.norfolk.gov/4111/Courtney-R-Doyle",
    "mamie johnson":        "https://www.norfolk.gov/2932/Mamie-B-Johnson",
    "mamie b johnson":      "https://www.norfolk.gov/2932/Mamie-B-Johnson",
    "john e jp paige":      "https://www.norfolk.gov/538/John-E-JP-Paige",
    "tommy r smigiel jr":   "https://www.norfolk.gov/539/Thomas-R-Smigiel-Jr",
    "thomas r smigiel jr":  "https://www.norfolk.gov/539/Thomas-R-Smigiel-Jr",
    "jeremy d mcgee":       "https://www.norfolk.gov/6429/Jeremy-D-McGee",
    "carlos j clanton":     "https://www.norfolk.gov/6428/Carlos-J-Clanton",
}

_NORFOLK_SCHOOL_BOARD_URLS: dict[str, str] = {
    "dr adale m martin":      "https://www.npsk12.com/our-division/school-board/school-board-members/dr-adale-m-martin-member",
    "adale m martin":         "https://www.npsk12.com/our-division/school-board/school-board-members/dr-adale-m-martin-member",
    "tanya k bhasin":         "https://www.npsk12.com/our-division/school-board/school-board-members/ms-tanya-k-bhasin-member",
    "tiffany moore buffaloe": "https://www.npsk12.com/our-division/school-board/school-board-members/mrs-tiffany-moore-buffaloe-member",
    "ken d paulson":          "https://www.npsk12.com/our-division/school-board/school-board-members/mr-kenneth-paulson-member",
    "kenneth d paulson":      "https://www.npsk12.com/our-division/school-board/school-board-members/mr-kenneth-paulson-member",
    "kenneth paulson":        "https://www.npsk12.com/our-division/school-board/school-board-members/mr-kenneth-paulson-member",
    "jason inge":             "https://www.npsk12.com/our-division/school-board/school-board-members/mr-jason-inge-member",
    "jodi m slaughter":       "https://www.npsk12.com/our-division/school-board/school-board-members/mr-jason-inge-member",
    "sarah e dicalogero":     "https://www.npsk12.com/our-division/school-board/school-board-members/ms-sarah-e-dicalogero-chair",
    "alfreda a thomas":       "https://www.npsk12.com/our-division/school-board/school-board-members/ms-alfreda-a-thomas-vice-chair",
    "alfreda thomas":         "https://www.npsk12.com/our-division/school-board/school-board-members/ms-alfreda-a-thomas-vice-chair",
    "carlos j clanton":       "https://www.norfolk.gov/6428/Carlos-J-Clanton",
}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def _official(officials: dict, key: str) -> dict | None:
    o = officials.get(key)
    return {"name": o["name"], "party": o.get("party", ""), "url": o.get("url", "")} if o else None


@router.get("/api/lookup")
def lookup(address: str):
    from address_lookup import (
        _vb_council, _vb_school_board,
        _norfolk_officials, _chesapeake_officials, _portsmouth_officials,
        _hampton_officials, _newport_news_officials, _suffolk_officials,
    )
    import address_lookup as _al
    HOD_CONTEXT, SD_CONTEXT, DISTRICT_CONTEXT, _HOD_MEMBER_URL = _get_district_contexts()

    result = _al.find_district(address)
    if "error" in result:
        return {"district": result}

    hod_num  = result.get("hod_district")
    sd_num   = result.get("sd_district")
    hod_info = HOD_CONTEXT.get(hod_num) if hod_num else None
    sd_info  = SD_CONTEXT.get(sd_num)   if sd_num  else None

    cd_num_raw = result.get("district_number", "")
    try:
        cd_key = f"VA-{int(cd_num_raw):02d}"
    except (ValueError, TypeError):
        cd_key = "VA-00"
    cd_info = DISTRICT_CONTEXT.get(cd_key, {})

    vb_num    = result.get("vb_council_district")
    vb_info   = _vb_council.get(vb_num) if vb_num is not None else None
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
        "name":         precinct_name,
        "number":       precinct_number,
        "location":     (polling_place or {}).get("location") or result.get("hampton_polling_location") or result.get("portsmouth_polling_location") or result.get("suffolk_polling_location") or result.get("chesapeake_polling_location") or result.get("norfolk_polling_location") or result.get("newport_news_polling_location"),
        "address":      (polling_place or {}).get("full_address") or result.get("newport_news_polling_address"),
        "address_line_1": (polling_place or {}).get("address_line_1"),
        "address_line_2": (polling_place or {}).get("address_line_2"),
        "city":         (polling_place or {}).get("city"),
        "state":        (polling_place or {}).get("state"),
        "zip_code":     (polling_place or {}).get("zip_code"),
        "room":         (polling_place or {}).get("room"),
    } if precinct_name and precinct_name != "Not found" else None

    locality = result.get("locality", "").lower()

    return {
        "district":     result,
        "precinct_info": precinct_info,
        "vb_council": {
            "district_number": vb_num,
            "name":     vb_info["name"],
            "email":    vb_info["email"],
            "district": vb_info["district"],
            "party":    vb_info.get("party", ""),
            "mayor":    {"name": mayor_info["name"], "email": mayor_info["email"], "party": mayor_info.get("party", "")} if mayor_info else None,
            "sheriff":                _officer("Sheriff"),
            "commonwealths_attorney": _officer("Commonwealth's Attorney"),
            "commissioner":           _officer("Commissioner of the Revenue"),
            "treasurer":              _officer("City Treasurer"),
            "clerk":                  _officer("Clerk of the Circuit Court"),
        } if vb_info else None,
        "norfolk": {
            "precinct":          result.get("norfolk_precinct") or result.get("precinct"),
            "precinct_number":   result.get("norfolk_precinct_number"),
            "polling_location":  result.get("norfolk_polling_location"),
            "polling_address":   (result.get("norfolk_polling_place") or {}).get("full_address"),
            "mayor":                  _official(_norfolk_officials, "mayor"),
            "sheriff":                _official(_norfolk_officials, "sheriff"),
            "commonwealths_attorney": _official(_norfolk_officials, "commonwealths_attorney"),
            "treasurer":              _official(_norfolk_officials, "treasurer"),
            "commissioner":           _official(_norfolk_officials, "commissioner"),
            "ward":             result.get("norfolk_ward"),
            "ward_rep":         result.get("norfolk_ward_rep"),
            "ward_rep_url":     result.get("norfolk_ward_rep_url") or _NORFOLK_COUNCIL_URLS.get(_slug(result.get("norfolk_ward_rep")), ""),
            "ward_sbm":         result.get("norfolk_ward_sbm"),
            "ward_sbm_url":     result.get("norfolk_ward_sbm_url") or _NORFOLK_SCHOOL_BOARD_URLS.get(_slug(result.get("norfolk_ward_sbm")), ""),
            "superward":        result.get("norfolk_superward"),
            "superward_rep":    result.get("norfolk_superward_rep"),
            "superward_rep_url": result.get("norfolk_superward_rep_url") or _NORFOLK_COUNCIL_URLS.get(_slug(result.get("norfolk_superward_rep")), ""),
            "superward_sbm":    result.get("norfolk_superward_sbm"),
            "superward_sbm_url": result.get("norfolk_superward_sbm_url") or _NORFOLK_SCHOOL_BOARD_URLS.get(_slug(result.get("norfolk_superward_sbm")), ""),
        } if "norfolk" in locality else None,
        "chesapeake": {
            "precinct":         result.get("chesapeake_precinct") or result.get("precinct"),
            "precinct_number":  result.get("chesapeake_precinct_number"),
            "polling_location": result.get("chesapeake_polling_location"),
            "polling_address":  (result.get("chesapeake_polling_place") or {}).get("full_address"),
            "polling_room":     (result.get("chesapeake_polling_place") or {}).get("room"),
            "mayor":                  _official(_chesapeake_officials, "mayor"),
            "vice_mayor":             _official(_chesapeake_officials, "vice_mayor"),
            "sheriff":                _official(_chesapeake_officials, "sheriff"),
            "commonwealths_attorney": _official(_chesapeake_officials, "commonwealths_attorney"),
            "commissioner":           _official(_chesapeake_officials, "commissioner"),
            "treasurer":              _official(_chesapeake_officials, "treasurer"),
            "clerk":                  _official(_chesapeake_officials, "clerk"),
            "council":       [{"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} for m in _chesapeake_officials.get("council", [])],
            "school_board":  [{"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} for m in _chesapeake_officials.get("school_board", [])],
        } if "chesapeake" in locality else None,
        "portsmouth": {
            "precinct":         result.get("portsmouth_precinct") or result.get("precinct"),
            "precinct_number":  result.get("portsmouth_precinct_number"),
            "polling_location": result.get("portsmouth_polling_location"),
            "polling_address":  (result.get("portsmouth_polling_place") or {}).get("full_address"),
            "polling_room":     (result.get("portsmouth_polling_place") or {}).get("room"),
            "mayor":                  _official(_portsmouth_officials, "mayor"),
            "vice_mayor":             _official(_portsmouth_officials, "vice_mayor"),
            "sheriff":                _official(_portsmouth_officials, "sheriff"),
            "commonwealths_attorney": _official(_portsmouth_officials, "commonwealths_attorney"),
            "commissioner":           _official(_portsmouth_officials, "commissioner"),
            "treasurer":              _official(_portsmouth_officials, "treasurer"),
            "clerk":                  _official(_portsmouth_officials, "clerk"),
            "council":      [{"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} for m in _portsmouth_officials.get("council", [])],
            "school_board": [{"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} for m in _portsmouth_officials.get("school_board", [])],
        } if "portsmouth" in locality else None,
        "hampton": {
            "precinct":         result.get("hampton_precinct") or result.get("precinct"),
            "precinct_number":  result.get("hampton_precinct_number"),
            "polling_location": result.get("hampton_polling_location"),
            "polling_address":  (result.get("hampton_polling_place") or {}).get("full_address"),
            "polling_room":     (result.get("hampton_polling_place") or {}).get("room"),
            "mayor":                  _official(_hampton_officials, "mayor"),
            "vice_mayor":             _official(_hampton_officials, "vice_mayor"),
            "sheriff":                _official(_hampton_officials, "sheriff"),
            "commonwealths_attorney": _official(_hampton_officials, "commonwealths_attorney"),
            "commissioner":           _official(_hampton_officials, "commissioner"),
            "treasurer":              _official(_hampton_officials, "treasurer"),
            "clerk":                  _official(_hampton_officials, "clerk"),
            "council":      [{"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} for m in _hampton_officials.get("council", [])],
            "school_board": [{"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} for m in _hampton_officials.get("school_board", [])],
        } if "hampton" in locality else None,
        "newport_news": {
            "precinct":           result.get("newport_news_precinct") or result.get("precinct"),
            "precinct_number":    result.get("newport_news_precinct_number"),
            "polling_location":   result.get("newport_news_polling_location"),
            "polling_address":    result.get("newport_news_polling_address"),
            "mayor":                  _official(_newport_news_officials, "mayor"),
            "vice_mayor":             _official(_newport_news_officials, "vice_mayor"),
            "sheriff":                _official(_newport_news_officials, "sheriff"),
            "commonwealths_attorney": _official(_newport_news_officials, "commonwealths_attorney"),
            "commissioner":           _official(_newport_news_officials, "commissioner"),
            "treasurer":              _official(_newport_news_officials, "treasurer"),
            "clerk":                  _official(_newport_news_officials, "clerk"),
            "council_district":       result.get("nn_council_district"),
            "council_district_name":  result.get("nn_council_district_name"),
            "council":      [{"name": m["name"], "party": m.get("party", ""), "url": m.get("url", "")} for m in _newport_news_officials.get(f"council_{result.get('nn_council_district')}", [])],
            "school_board": (
                [{"name": m["name"], "party": m.get("party", ""), "url": m.get("url", ""), "district": m.get("district", "")} for m in _newport_news_officials.get(f"school_board_{result.get('nn_council_district')}", [])] +
                [{"name": m["name"], "party": m.get("party", ""), "url": m.get("url", ""), "district": m.get("district", "")} for m in _newport_news_officials.get("school_board_at_large", [])]
            ),
        } if "newport news" in locality else None,
        "suffolk": {
            "precinct":         result.get("suffolk_precinct") or result.get("precinct"),
            "precinct_number":  result.get("suffolk_precinct_number"),
            "polling_location": result.get("suffolk_polling_location"),
            "polling_address":  (result.get("suffolk_polling_place") or {}).get("full_address"),
            "polling_room":     (result.get("suffolk_polling_place") or {}).get("room"),
            "mayor":                  _official(_suffolk_officials, "mayor"),
            "vice_mayor":             _official(_suffolk_officials, "vice_mayor"),
            "sheriff":                _official(_suffolk_officials, "sheriff"),
            "commonwealths_attorney": _official(_suffolk_officials, "commonwealths_attorney"),
            "commissioner":           _official(_suffolk_officials, "commissioner"),
            "treasurer":              _official(_suffolk_officials, "treasurer"),
            "clerk":                  _official(_suffolk_officials, "clerk"),
            "council": [{"name": m["name"], "party": m.get("party", "")} for m in _suffolk_officials.get("council", [])],
        } if "suffolk" in locality else None,
        "vb_school_board": {
            "member":   (lambda o: {"name": o["name"], "party": o.get("party", ""), "url": o.get("url", "")} if o else None)(_vb_school_board.get(vb_num)),
            "at_large": (lambda o: {"name": o["name"], "party": o.get("party", ""), "url": o.get("url", "")} if o else None)(_vb_school_board.get("at_large")),
        } if vb_num is not None else None,
        "us_rep": {
            "district_number": int(cd_num_raw) if cd_num_raw not in ("", "N/A") else None,
            "rep":    cd_info.get("rep"),
            "party":  cd_info.get("party"),
            "region": cd_info.get("region"),
            "url":    cd_info.get("url"),
        } if cd_info.get("rep") else None,
        "state_delegate": {
            "district_number": hod_num,
            "delegate":        hod_info["delegate"],
            "party":           hod_info["party"],
            "locality":        hod_info["locality"],
            "url":             _HOD_MEMBER_URL.get(hod_num),
        } if hod_info else None,
        "state_senator": {
            "district_number": sd_num,
            "senator":         sd_info["senator"],
            "party":           sd_info["party"],
            "region":          sd_info["region"],
            "url":             sd_info.get("url"),
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


# ── /results + /api/results-data ─────────────────────────────────────────────

@router.get("/results", response_class=HTMLResponse)
def results_page():
    with (_TEMPLATE_DIR / "results.html").open("r", encoding="utf-8") as f:
        return f.read()


def _group_counts(ballot_options: list, vote_name: str) -> tuple[int, int, int, int]:
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


@router.get("/api/results-data")
def results_data():
    with (_DATA_DIR / "data_2026_special.json").open(encoding="utf-8") as f:
        data = json.load(f)

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
            "name":      j["name"].strip(),
            "yes": yes_v, "no": no_v, "total": total,
            "pct_yes":   round(yes_v / total * 100, 1) if total else 50.0,
            "winner":    "Yes" if yes_v >= no_v else "No",
            "early_yes": yes_early, "early_no": no_early,
            "eday_yes":  yes_eday,  "eday_no":  no_eday,
            "mail_yes":  yes_mail,  "mail_no":  no_mail,
        })

    return {
        "statewide": {
            "yes": yes_state, "no": no_state,
            "early":        {"yes": yes_early_s, "no": no_early_s},
            "election_day": {"yes": yes_eday_s,  "no": no_eday_s},
            "mail":         {"yes": yes_mail_s,   "no": no_mail_s},
        },
        "local": local,
    }
