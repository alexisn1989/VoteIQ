"""
Individual City Council member profile pages, spanning all 7 Hampton Roads
cities. Built on voteiq/services/council_roster.py (roster + name
resolution) and council_member_votes.py (voting record + blocs/finance).

GET /council/{city}/{member}       HTML profile page
GET /api/council/{city}/{member}   JSON data backing that page
GET /api/council-roster/all        flat [{name, city_slug, slug}] across all
                                    7 cities -- used by the chat frontend to
                                    linkify recognized member names in
                                    replies. A distinct path prefix rather
                                    than /api/council/all-members: FastAPI
                                    would otherwise route that straight into
                                    local_council.py's /api/council/{city}
                                    with city="all-members" and 404 there,
                                    since dynamic-segment routes match
                                    before any literal registered later.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from voteiq.services.council_member_votes import (
    get_finance_summary, get_recent_votes, get_voting_blocs, get_vote_summary,
)
from voteiq.services.council_roster import CITY_DISPLAY, find_member, get_roster

router = APIRouter(tags=["council_member"])

_BASE_DIR = Path(__file__).resolve().parents[3]


@router.get("/api/council-roster/all")
def council_roster_all():
    """Flat member list for the chat frontend's link-detection pass."""
    out = []
    for city_slug, city_display in CITY_DISPLAY.items():
        for m in get_roster(city_slug):
            out.append({
                "name": m["name"], "city_slug": city_slug,
                "city_display": city_display, "slug": m["slug"],
            })
    return JSONResponse(out)


@router.get("/council/{city}/{member}", response_class=HTMLResponse)
def council_member_page(city: str, member: str):
    if city not in CITY_DISPLAY:
        return HTMLResponse("<h1>City not found</h1>", status_code=404)
    path = _BASE_DIR / "templates" / "council_member_profile.html"
    with open(path, encoding="utf-8") as f:
        return HTMLResponse(f.read())


@router.get("/api/council/{city}/{member}")
def council_member_api(city: str, member: str):
    if city not in CITY_DISPLAY:
        raise HTTPException(404, "City not found")
    m = find_member(city, member)
    if not m:
        raise HTTPException(404, "Member not found")

    summary = get_vote_summary(city, m["name"])
    return JSONResponse({
        "city": city,
        "city_display": CITY_DISPLAY[city],
        "name": m["name"],
        "role": m["role"],
        "email": m["email"],
        "url": m["url"],
        "vote_summary": summary,
        "recent_votes": get_recent_votes(city, m["name"], limit=25),
        "voting_blocs": get_voting_blocs(city, m["name"], limit=5),
        "finance": get_finance_summary(city, m["name"]),
    })
