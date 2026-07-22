"""
Unified City Council roster + member-profile-URL helpers, spanning all 7
Hampton Roads cities VoteIQ covers. Three different underlying sources,
because that's genuinely how the data arrived across sessions:

  - Virginia Beach: the `vb_council_members` DB table (freshly scraped,
    clean structure with district_num/email).
  - Norfolk: no DB roster table exists yet -- falls back to the hardcoded
    _NORFOLK_MEMBERS/_NORFOLK_MAYOR list already in
    voteiq/api/routes/local_council.py (duplicated here rather than
    imported, to avoid a route module importing another route module).
  - Chesapeake/Suffolk/Hampton/Newport News/Portsmouth: voteiq_officials.json,
    filtered to rows whose `district` contains "Council" or "Mayor" (which
    also catches "Vice Mayor" as a substring).

Every city's per-member vote table (`{prefix}_council_member_votes`) stores
`member_name` differently -- Virginia Beach and Newport News use the
member's full (messy, honorific-laden) name, the other 5 cities use
last-name-only -- and this isn't even consistent *within* a city's own
Gemini extraction prompt wording session to session. Rather than trying to
algorithmically derive "the" last name from a roster string (tried that;
broke on real data -- "Marcellus L. Harris III, D. Div." reduced to "D"),
`resolve_vote_key()` instead matches against the DISTINCT member_name
values actually observed in that city's vote table, via a normalized
token-subset comparison. That's the real ground truth already used
throughout the vote-scraping pipeline, so matching against it directly is
more reliable than re-deriving it from scratch.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.getenv("DATA_DIR", str(BASE_DIR))) / "polls.db"
_OFFICIALS_JSON = BASE_DIR / "voteiq_officials.json"

CITY_DISPLAY = {
    "chesapeake": "Chesapeake",
    "suffolk": "Suffolk",
    "hampton": "Hampton",
    "newport-news": "Newport News",
    "portsmouth": "Portsmouth",
    "norfolk": "Norfolk",
    "virginia-beach": "Virginia Beach",
}
# jurisdiction string in voteiq_officials.json, keyed by our URL slug
_OFFICIALS_JURISDICTION = {
    "chesapeake": "Chesapeake",
    "suffolk": "Suffolk",
    "hampton": "Hampton",
    "newport-news": "Newport News",
    "portsmouth": "Portsmouth",
}
_NORFOLK_MAYOR = {"name": "Kenneth Cooper Alexander", "role": "Mayor",
                  "email": "mayor@norfolk.gov", "url": "https://www.norfolk.gov/government/mayor"}
_NORFOLK_MEMBERS = [
    {"name": "Martin A. Thomas Jr.", "role": "Ward 1 · Vice Mayor", "email": "mthomas@norfolk.gov", "url": "https://www.norfolk.gov/542/Vice-Mayor-Martin-A-Thomas-Jr"},
    {"name": "Courtney Doyle",       "role": "Ward 2",              "email": "cdoyle@norfolk.gov",   "url": "https://www.norfolk.gov/4111/Courtney-R-Doyle"},
    {"name": "Mamie Johnson",        "role": "Ward 3",              "email": "mjohnson@norfolk.gov", "url": "https://www.norfolk.gov/2932/Mamie-B-Johnson"},
    {"name": 'John E. "JP" Paige',   "role": "Ward 4",              "email": "jpaige@norfolk.gov",   "url": "https://www.norfolk.gov/538/John-E-JP-Paige"},
    {"name": "Tommy R Smigiel Jr.",  "role": "Ward 5",              "email": "tsmigiel@norfolk.gov", "url": "https://www.norfolk.gov/539/Thomas-R-Smigiel-Jr"},
    {"name": "Jeremy D McGee",       "role": "Superward 6",         "email": "jmcgee@norfolk.gov",   "url": "https://www.norfolk.gov/6429/Jeremy-D-McGee"},
    {"name": "Carlos J Clanton",     "role": "Superward 7",         "email": "cclanton@norfolk.gov", "url": "https://www.norfolk.gov/6428/Carlos-J-Clanton"},
]

_HONORIFIC_RE = re.compile(r'^(Dr\.?|Mr\.?|Mrs\.?|Ms\.?|Rev\.?|RADM|Lt\s+Gov\.?)\s+', re.I)
_QUOTED_RE = re.compile(r'\s*["“”][^"“”]*["“”]\s*')
_SUFFIX_RE = re.compile(r',?\s*(Jr\.?|Sr\.?|II|III|IV|MD|Esq\.?|PhD|Ph\.D\.?)\.?\s*$', re.I)


def last_name(full_name: str) -> str:
    """Normalize a full name down to a bare last name, stripping quoted
    nicknames, honorific prefixes, and generational/degree suffixes."""
    s = (full_name or "").strip()
    s = _QUOTED_RE.sub(" ", s)
    s = _HONORIFIC_RE.sub("", s)
    prev = None
    while prev != s:
        prev = s
        s = _SUFFIX_RE.sub("", s).strip()
    s = re.sub(r"\s+", " ", s).strip()
    parts = s.split(" ")
    return parts[-1] if parts else ""


def slugify(name: str) -> str:
    """URL-safe slug for a member profile page, derived from their last
    name + first name/initial for uniqueness (two members can share a
    surname)."""
    s = (name or "").strip()
    s = _QUOTED_RE.sub(" ", s)
    s = _HONORIFIC_RE.sub("", s)
    s = re.sub(r"[^\w\s-]", "", s)  # drop periods, commas
    s = re.sub(r"\s+", "-", s.strip()).lower()
    return s


# URL slug -> {prefix}_council_member_votes table prefix
TABLE_PREFIX = {
    "chesapeake": "chesapeake", "suffolk": "suffolk", "hampton": "hampton",
    "newport-news": "newport_news", "portsmouth": "portsmouth",
    "norfolk": "norfolk", "virginia-beach": "vb",
}

_SUFFIX_WORDS = {"jr", "sr", "ii", "iii", "iv", "md", "esq", "phd", "div", "min", "pe", "mpa", "dds"}


def _core_tokens(name: str) -> set[str]:
    s = re.sub(r"[^\w\s-]", " ", name or "")
    return {w.lower() for w in s.split() if len(w) > 2 and w.lower() not in _SUFFIX_WORDS}


def _resolve_key(observed: list[str], roster_name: str) -> str | None:
    """Find which of `observed` (distinct name values really present in
    some table) corresponds to `roster_name`, via normalized token overlap
    anchored on the longest (almost always the surname) token agreeing --
    see module docstring for why this beats deriving a canonical name."""
    roster_core = _core_tokens(roster_name)
    best, best_overlap = None, 0
    for obs in observed:
        obs_core = _core_tokens(obs)
        if not obs_core:
            continue
        overlap = obs_core & roster_core
        longest = max(obs_core, key=len)
        if longest in roster_core and len(overlap) > best_overlap:
            best, best_overlap = obs, len(overlap)
    return best


def _distinct(table: str, col: str) -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    try:
        return [r[0] for r in conn.execute(f"SELECT DISTINCT {col} FROM {table}").fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def resolve_vote_key(city_slug: str, roster_name: str) -> str | None:
    """The actual member_name value used in {prefix}_council_member_votes
    (and, for Virginia Beach/Norfolk, {prefix}_voting_blocs -- confirmed
    both use the same name convention as council_member_votes) for this
    roster member."""
    prefix = TABLE_PREFIX.get(city_slug)
    if not prefix:
        return None
    return _resolve_key(_distinct(f"{prefix}_council_member_votes", "member_name"), roster_name)


def resolve_finance_key(city_slug: str, roster_name: str) -> str | None:
    """{prefix}_finance_summary uses last-name-only for BOTH Virginia Beach
    and Norfolk, unlike vb_council_member_votes' full names -- a separate
    resolution against that table's own observed values."""
    prefix = TABLE_PREFIX.get(city_slug)
    if not prefix:
        return None
    return _resolve_key(_distinct(f"{prefix}_finance_summary", "member_name"), roster_name)


def _load_officials() -> list[dict]:
    if not _OFFICIALS_JSON.exists():
        return []
    with open(_OFFICIALS_JSON, encoding="utf-8") as f:
        return json.load(f)


def get_roster(city_slug: str) -> list[dict]:
    """[{name, role, email, url, slug, last_name}] for the given city slug."""
    if city_slug == "virginia-beach":
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT name, district, email FROM vb_council_members ORDER BY district_num"
        ).fetchall()
        conn.close()
        out = []
        for name, district, email in rows:
            out.append({
                "name": name, "role": district, "email": email or "",
                "url": "https://www.vbgov.com/government/departments/city-council/Pages/default.aspx",
            })
    elif city_slug == "norfolk":
        out = [dict(_NORFOLK_MAYOR)] + [dict(m) for m in _NORFOLK_MEMBERS]
    elif city_slug in _OFFICIALS_JURISDICTION:
        jurisdiction = _OFFICIALS_JURISDICTION[city_slug]
        officials = _load_officials()
        out = [
            {"name": r["name"], "role": r["district"], "email": r.get("email") or "", "url": r.get("url") or ""}
            for r in officials
            if r.get("jurisdiction") == jurisdiction
            and ("Council" in r.get("district", "") or "Mayor" in r.get("district", ""))
        ]
    else:
        return []

    for m in out:
        m["slug"] = slugify(m["name"])
        m["last_name"] = last_name(m["name"])
    return out


def find_member(city_slug: str, member_slug: str) -> dict | None:
    for m in get_roster(city_slug):
        if m["slug"] == member_slug:
            return m
    return None
