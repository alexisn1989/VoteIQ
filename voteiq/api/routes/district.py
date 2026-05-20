from __future__ import annotations

import json
import os
from html import escape
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["district"])

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
