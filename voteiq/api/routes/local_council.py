"""Local council profile pages — Virginia Beach and Norfolk."""
from __future__ import annotations

import os
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(tags=["local_council"])

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# ── Virginia Beach ─────────────────────────────────────────────────────────────
_VB_MAYOR = {
    "name": 'Robert M. "Bobby" Dyer',
    "role": "Mayor",
    "email": "mayorsoffice@vbgov.com",
    "url": "https://www.vbgov.com/government/departments/city-council/Pages/bios/dyer.aspx",
    "district_num": 0,
}

_VB_MEMBERS = [
    {"name": "David Hutcheson",           "role": "District 1",  "district_num": 1,  "email": "dhutcheson@vbgov.com",    "url": "https://www.vbgov.com/government/departments/city-council/Pages/default.aspx"},
    {"name": "Barbara Henley",            "role": "District 2",  "district_num": 2,  "email": "bhenley@vbgov.com",       "url": "https://www.vbgov.com/government/departments/city-council/Pages/default.aspx"},
    {"name": "Michael Berlucchi",         "role": "District 3",  "district_num": 3,  "email": "mberlucc@vbgov.com",      "url": "https://www.vbgov.com/government/departments/city-council/Pages/default.aspx"},
    {"name": "Dr. Amelia Ross-Hammond",   "role": "District 4",  "district_num": 4,  "email": "arosshammond@vbgov.com",  "url": "https://www.vbgov.com/government/departments/city-council/Pages/default.aspx"},
    {"name": "Rosemary Wilson",           "role": "District 5",  "district_num": 5,  "email": "rcwilson@vbgov.com",      "url": "https://www.vbgov.com/government/departments/city-council/Pages/default.aspx"},
    {"name": 'Robert W. "Worth" Remick',  "role": "District 6",  "district_num": 6,  "email": "wremick@vbgov.com",       "url": "https://www.vbgov.com/government/departments/city-council/Pages/default.aspx"},
    {"name": 'Cal "Cash" Jackson-Green',  "role": "District 7",  "district_num": 7,  "email": "cjacksongreen@vbgov.com", "url": "https://www.vbgov.com/government/departments/city-council/Pages/default.aspx"},
    {"name": "Stacy Cummings",            "role": "District 8",  "district_num": 8,  "email": "stcummings@vbgov.com",    "url": "https://www.vbgov.com/government/departments/city-council/Pages/default.aspx"},
    {"name": "Joashua F. Schulman",       "role": "District 9",  "district_num": 9,  "email": "jschulman@vbgov.com",     "url": "https://www.vbgov.com/government/departments/city-council/Pages/default.aspx"},
    {"name": "Jennifer V. Rouse",         "role": "District 10", "district_num": 10, "email": "jvrouse@vbgov.com",       "url": "https://www.vbgov.com/government/departments/city-council/Pages/default.aspx"},
]

# ── Norfolk ────────────────────────────────────────────────────────────────────
_NORFOLK_MAYOR = {
    "name": "Kenneth Cooper Alexander",
    "role": "Mayor",
    "email": "mayor@norfolk.gov",
    "url": "https://www.norfolk.gov/government/mayor",
    "district_num": 0,
}

_NORFOLK_MEMBERS = [
    {"name": "Martin A. Thomas Jr.", "role": "Ward 1 · Vice Mayor", "district_num": 1, "email": "mthomas@norfolk.gov",   "url": "https://www.norfolk.gov/542/Vice-Mayor-Martin-A-Thomas-Jr"},
    {"name": "Courtney Doyle",       "role": "Ward 2",              "district_num": 2, "email": "cdoyle@norfolk.gov",    "url": "https://www.norfolk.gov/4111/Courtney-R-Doyle"},
    {"name": "Mamie Johnson",        "role": "Ward 3",              "district_num": 3, "email": "mjohnson@norfolk.gov",  "url": "https://www.norfolk.gov/2932/Mamie-B-Johnson"},
    {"name": 'John E. "JP" Paige',   "role": "Ward 4",              "district_num": 4, "email": "jpaige@norfolk.gov",    "url": "https://www.norfolk.gov/538/John-E-JP-Paige"},
    {"name": "Tommy R Smigiel Jr.",  "role": "Ward 5",              "district_num": 5, "email": "tsmigiel@norfolk.gov",  "url": "https://www.norfolk.gov/539/Thomas-R-Smigiel-Jr"},
    {"name": "Jeremy D McGee",       "role": "Superward 6",         "district_num": 6, "email": "jmcgee@norfolk.gov",    "url": "https://www.norfolk.gov/6429/Jeremy-D-McGee"},
    {"name": "Carlos J Clanton",     "role": "Superward 7",         "district_num": 7, "email": "cclanton@norfolk.gov",  "url": "https://www.norfolk.gov/6428/Carlos-J-Clanton"},
]

_CITIES: dict[str, dict] = {
    "virginia-beach": {
        "name": "Virginia Beach",
        "slug": "virginia-beach",
        "mayor": _VB_MAYOR,
        "members": _VB_MEMBERS,
        "council_url": "https://www.vbgov.com/government/departments/city-council/Pages/default.aspx",
        "structure": "10 geographic districts + at-large Mayor",
        "seats": 11,
    },
    "norfolk": {
        "name": "Norfolk",
        "slug": "norfolk",
        "mayor": _NORFOLK_MAYOR,
        "members": _NORFOLK_MEMBERS,
        "council_url": "https://www.norfolk.gov/government/city-council",
        "structure": "5 Wards + 2 Superwards + at-large Mayor",
        "seats": 8,
    },
}


@router.get("/council/{city}", response_class=HTMLResponse)
def council_page(city: str):
    if city not in _CITIES:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    path = os.path.join(_BASE_DIR, "templates", "local_council.html")
    with open(path, encoding="utf-8") as f:
        return HTMLResponse(f.read())


@router.get("/api/council/{city}")
def council_api(city: str):
    if city not in _CITIES:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(_CITIES[city])
