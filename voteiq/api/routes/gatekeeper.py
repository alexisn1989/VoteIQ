"""
Gatekeeper Effect — Committee chairs × donor sector correlation.
Shows which committee chairs receive money from industries their committee regulates.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["gatekeeper"])

_BASE_DIR = Path(__file__).resolve().parents[3]
_data_dir_raw = os.getenv("DATA_DIR", str(_BASE_DIR))
_DATA_DIR = _data_dir_raw if os.path.isdir(_data_dir_raw) else str(_BASE_DIR)
_POLLS_DB = os.path.join(_DATA_DIR, "polls.db")


def _conn():
    if not os.path.isfile(_POLLS_DB):
        raise HTTPException(status_code=503, detail="Database not available")
    c = sqlite3.connect(_POLLS_DB)
    c.row_factory = sqlite3.Row
    return c


# ── Committee → jurisdiction sector tags ─────────────────────────────────────
# Sectors that are "on point" for each committee's policy domain.
# Keys must match the exact committee name stored in va_committee_assignments.
_COMMITTEE_JURISDICTION: dict[str, list[str]] = {
    # ── Senate ──────────────────────────────────────────────────────────────
    # "Banking" in data is called "Financial Services"; Insurance is a sub-sector
    "Finance and Appropriations":                    ["Financial Services", "Health Professionals", "Insurance"],
    # Energy + gambling licensing flow through Commerce and Labor
    "Commerce and Labor":                            ["Fossil Fuels", "Utilities", "Gambling/Casinos"],
    # Tobacco IS agribusiness in Virginia — include it
    "Agriculture, Conservation and Natural Resources": ["Agribusiness", "Tobacco", "Livestock & Poultry"],
    "Courts of Justice":                             ["Trial Lawyers", "Corporate Law"],
    # General Laws handles licensing: real estate, casinos
    "General Laws and Technology":                   ["Gambling/Casinos", "Commercial Real Estate"],
    "Privileges and Elections":                      ["Commercial Real Estate", "Ideological"],
    "Education and Health":                          ["Health Professionals", "Pharmaceuticals / Health Products"],
    "Transportation":                                ["Transportation", "Automotive"],
    "Rehabilitation and Social Services":            ["Health Professionals"],
    "Local Government":                              ["Commercial Real Estate", "Construction"],
    "Rules":                                         [],

    # ── House ───────────────────────────────────────────────────────────────
    "Appropriations":                                ["Financial Services", "Fossil Fuels", "Health Professionals"],
    "Finance":                                       ["Financial Services", "Insurance", "Commercial Real Estate"],
    "Agriculture, Chesapeake and Natural Resources": ["Agribusiness", "Tobacco", "Livestock & Poultry"],
    "Courts of Justice":                             ["Trial Lawyers", "Corporate Law", "Financial Services"],
    "Labor and Commerce":                            ["Service & Retail Workers", "Building & Industrial Unions", "Utilities"],
    "General Laws":                                  ["Gambling/Casinos", "Commercial Real Estate", "Wine & Spirits"],
    "Commerce and Energy":                           ["Fossil Fuels", "Utilities", "Gambling/Casinos"],
    # ABC/Gaming — alcohol & gaming licensing
    "ABC/Gaming":                                    ["Wine & Spirits", "Gambling/Casinos", "Tobacco"],
    "Health Professions":                            ["Health Professionals", "Pharmaceuticals / Health Products", "Hospitals"],
    "Health and Human Services":                     ["Health Professionals", "Hospitals"],
    "Health":                                        ["Health Professionals", "Hospitals"],
    "Education":                                     ["Education"],
    "Transportation":                                ["Transportation", "Automotive"],
    "Transportation Infrastructure and Funding":     ["Transportation", "Automotive"],
    "Communications, Technology and Innovation":     ["Technology", "Telecom", "Software & IT"],
    "Public Safety":                                 ["Defense"],
    "Housing/Consumer Protection":                   ["Commercial Real Estate", "Construction"],
    "Rules":                                         [],
}


# ── Name normalisation for fuzzy matching ─────────────────────────────────────
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v", "jr.", "sr."}

def _norm_name(s: str) -> tuple[str, str]:
    """Return (first_word, last_word) both lowercased, punctuation-stripped.
    Strips generational suffixes so 'Hayes, Jr.' → last='hayes'."""
    parts = re.sub(r"[^a-z ]", "", s.lower()).split()
    # Drop trailing suffixes
    while parts and parts[-1] in _SUFFIXES:
        parts.pop()
    if not parts:
        return ("", "")
    return (parts[0], parts[-1])


def _find_cfs(chair_name: str, lookup: dict) -> Optional[dict]:
    """Multi-strategy name lookup: exact → first-4-chars → last-only."""
    first, last = _norm_name(chair_name)
    # 1. Exact normalised key
    hit = lookup.get((first, last))
    if hit:
        return hit
    # 2. First-4-char prefix (handles Dave/David, Josh/Joshua)
    prefix = first[:4]
    candidates = [v for (f, l), v in lookup.items() if l == last and f.startswith(prefix)]
    if len(candidates) == 1:
        return candidates[0]
    # 3. Last name only — only if unambiguous
    candidates = [v for (f, l), v in lookup.items() if l == last]
    if len(candidates) == 1:
        return candidates[0]
    return None


_DEATH_PREFIXES = re.compile(
    r"(?:"
    r"Left in Committee\s+"        # Left in Committee X
    r"|Left in\s+"                 # Left in X  (no "Committee" keyword)
    r"|Continued to next session in\s+"   # Senate carryover = effectively dead this session
    r"|Tabled in\s+"              # Explicit kill vote
    r"|Passed by indefinitely in\s+"      # PBI = explicit kill
    r"|Stricken from docket by\s+"        # Removed from agenda by committee
    r"|Stricken at request of Patron in\s+"  # Sponsor withdrew in committee
    r"|Failed to report \(defeated\) in\s+"  # Failed committee vote
    r")",
    re.IGNORECASE,
)
_VOTE_SUFFIX = re.compile(r"\s*\(\d+[-–]\w.*$")   # strip " (15-Y 0-N)" etc.


def _parse_committee_from_status(status: str) -> Optional[str]:
    """Extract committee name from any 'died in committee' status string.
    Returns None if the status doesn't describe a committee death."""
    m = _DEATH_PREFIXES.search(status or "")
    if not m:
        return None
    remainder = status[m.end():].strip()
    # Strip trailing vote tally "(15-Y 0-N)" or "(Voice Vote)"
    remainder = _VOTE_SUFFIX.sub("", remainder).strip()
    return remainder if remainder else None


def _norm_committee(name: str) -> str:
    """Lowercase + collapse spaces for fuzzy committee name matching."""
    return re.sub(r"\s+", " ", name.lower().strip())


def _build_gatekeeper_data() -> dict:
    """Query DB and build full gatekeeper payload."""
    conn = _conn()

    # ── 1. All committee chairs (both chambers) ───────────────────────────────
    chairs_rows = conn.execute(
        """
        SELECT DISTINCT committee, chamber, member_name
        FROM va_committee_assignments
        WHERE role = 'chair'
        ORDER BY chamber DESC, committee
        """
    ).fetchall()

    # ── 2. All CFS records with sector data ───────────────────────────────────
    cfs_rows = conn.execute(
        """
        SELECT name, chamber, party, total_raised, by_sector_json, top_sector, alignment_json
        FROM campaign_finance_summary
        WHERE source = 'va_sbe'
        """
    ).fetchall()

    # ── 3. Bills killed in committee (all 3 sessions: 2024, 2025, 2026) ─────────
    _DEAD_SQL = """
        SELECT bill_number, title, status, session
        FROM va_bills
        WHERE session IN ('2026', '2025', '2024')
          AND (
               status LIKE 'Left in%'
            OR status LIKE 'Continued to next session in%'
            OR status LIKE 'Tabled in%'
            OR status LIKE 'Passed by indefinitely in%'
            OR status LIKE 'Stricken from docket by%'
            OR status LIKE 'Stricken at request of Patron in%'
            OR status LIKE 'Failed to report%in%'
          )
    """
    bill_rows = conn.execute(_DEAD_SQL).fetchall()

    # ── 4. Companion kills: same bill blocked in BOTH House and Senate ────────
    _COMP_SQL = """
        SELECT h.bill_number AS h_bill, h.title, h.status AS h_status, h.session,
               s.bill_number AS s_bill, s.status AS s_status
        FROM va_bills h
        JOIN va_bills s ON (
            lower(trim(h.title)) = lower(trim(s.title))
            AND h.session = s.session
            AND h.bill_number LIKE 'H%'
            AND s.bill_number LIKE 'S%'
        )
        WHERE h.session IN ('2026', '2025', '2024')
          AND (h.status LIKE 'Left in%' OR h.status LIKE 'Tabled in%'
               OR h.status LIKE 'Passed by indefinitely in%' OR h.status LIKE 'Stricken%'
               OR h.status LIKE 'Failed to report%'
               OR h.status LIKE 'Continued to next session%')
          AND (s.status LIKE 'Left in%' OR s.status LIKE 'Tabled in%'
               OR s.status LIKE 'Passed by indefinitely in%' OR s.status LIKE 'Stricken%'
               OR s.status LIKE 'Failed to report%'
               OR s.status LIKE 'Continued to next session%')
    """
    comp_rows = conn.execute(_COMP_SQL).fetchall()
    conn.close()

    # Build killed-bills lookup per session: (chamber_prefix, norm_committee, session) → count/list
    from collections import defaultdict
    killed: dict[tuple, list] = defaultdict(list)          # 2026 sample
    kills_by_year: dict[tuple, dict[str, int]] = defaultdict(lambda: {"2026": 0, "2025": 0, "2024": 0})
    for br in bill_rows:
        cmt_raw = _parse_committee_from_status(br["status"])
        if not cmt_raw:
            continue
        prefix = "House" if br["bill_number"].upper().startswith("H") else "Senate"
        norm_key = (prefix, _norm_committee(cmt_raw))
        yr = br["session"]
        kills_by_year[norm_key][yr] += 1
        if yr == "2026":
            killed[norm_key].append({"bill": br["bill_number"], "title": br["title"] or ""})

    # Build lookup: (first_word, last_word) → cfs_row
    cfs_lookup: dict[tuple, dict] = {}
    for row in cfs_rows:
        key = _norm_name(row["name"])
        cfs_lookup[key] = dict(row)

    # ── 3. For each chair, find CFS match and build card data ─────────────────
    senate_cards: list[dict] = []
    house_cards: list[dict] = []

    for row in chairs_rows:
        committee = row["committee"]
        chamber = row["chamber"]
        chair_name = row["member_name"]

        # Fuzzy match (exact → first-4-prefix → last-only)
        cfs = _find_cfs(chair_name, cfs_lookup)

        # Parse sector data
        top_sectors: list[dict] = []
        total_raised = 0
        party = "?"
        alignment_score = None

        if cfs:
            party = (cfs.get("party") or "?")[0].upper() if cfs.get("party") else "?"
            total_raised = cfs.get("total_raised") or 0

            raw_sectors = cfs.get("by_sector_json") or "[]"
            try:
                sector_list = json.loads(raw_sectors)
                if isinstance(sector_list, list):
                    top_sectors = sorted(sector_list, key=lambda x: -(x.get("total") or 0))[:6]
            except (json.JSONDecodeError, TypeError):
                pass

            raw_align = cfs.get("alignment_json") or "{}"
            try:
                parsed = json.loads(raw_align)
                if isinstance(parsed, dict):
                    alignment_score = parsed.get("overall_score")
            except (json.JSONDecodeError, TypeError):
                pass

        # Jurisdiction match: does top sector overlap with committee's domain?
        jurisdiction = _COMMITTEE_JURISDICTION.get(committee, [])
        top_sector_names = [s.get("sector", "") for s in top_sectors[:3]]
        matching_sectors = [s for s in top_sector_names if s in jurisdiction]
        jurisdiction_match = len(matching_sectors) > 0

        # Killed bills: match on (chamber, normalised committee name)
        killed_key = (chamber, _norm_committee(committee))
        killed_bills = killed.get(killed_key, [])
        yr_counts = dict(kills_by_year.get(killed_key, {"2026": 0, "2025": 0, "2024": 0}))
        # Also try without comma variants (status omits commas: "Agriculture Chesapeake...")
        if not killed_bills:
            alt_key = (chamber, _norm_committee(committee.replace(",", "")))
            killed_bills = killed.get(alt_key, [])
            if not yr_counts or yr_counts == {"2026": 0, "2025": 0, "2024": 0}:
                yr_counts = dict(kills_by_year.get(alt_key, {"2026": 0, "2025": 0, "2024": 0}))

        card = {
            "committee": committee,
            "chamber": chamber,
            "chair": chair_name,
            "party": party,
            "total_raised": total_raised,
            "top_sectors": top_sectors,
            "jurisdiction": jurisdiction,
            "matching_sectors": matching_sectors,
            "jurisdiction_match": jurisdiction_match,
            "has_data": cfs is not None,
            "bills_killed": yr_counts.get("2026", 0),
            "bills_killed_2025": yr_counts.get("2025", 0),
            "bills_killed_2024": yr_counts.get("2024", 0),
            "killed_sample": [b["bill"] + " — " + b["title"][:70] for b in killed_bills[:5]],
        }

        if chamber == "Senate":
            senate_cards.append(card)
        else:
            house_cards.append(card)

    # Sort: jurisdiction matches first, then by total raised
    for cards in (senate_cards, house_cards):
        cards.sort(key=lambda c: (-int(c["jurisdiction_match"]), -(c["total_raised"] or 0)))

    # ── 5. Process companion kills ────────────────────────────────────────────
    # Deduplicate by (session, normalised_title) to avoid counting same pair twice
    seen_pairs: set = set()
    pair_counts: dict = defaultdict(lambda: {"count": 0, "examples": []})
    total_coord = 0

    for cr in comp_rows:
        norm_t = re.sub(r"\s+", " ", (cr["title"] or "").lower().strip())[:80]
        pair_key_dedup = (cr["session"], norm_t)
        if pair_key_dedup in seen_pairs:
            continue
        seen_pairs.add(pair_key_dedup)
        total_coord += 1

        h_cmt = _parse_committee_from_status(cr["h_status"]) or "Unknown"
        s_cmt = _parse_committee_from_status(cr["s_status"]) or "Unknown"
        pair_key = (_norm_committee(h_cmt), _norm_committee(s_cmt))
        pair_counts[pair_key]["count"] += 1
        if len(pair_counts[pair_key]["examples"]) < 3:
            pair_counts[pair_key]["examples"].append(
                f"{cr['h_bill']}/{cr['s_bill']} — {(cr['title'] or '')[:60]}"
            )
        # Store human-readable names once
        if "h_committee" not in pair_counts[pair_key]:
            pair_counts[pair_key]["h_committee"] = h_cmt
            pair_counts[pair_key]["s_committee"] = s_cmt

    # Build top coordinated-kill pairs (by count)
    top_pairs = sorted(pair_counts.values(), key=lambda x: -x["count"])[:8]
    # Attach chair names to top pairs
    chair_by_cmt: dict = {}
    for card in senate_cards + house_cards:
        chair_by_cmt[_norm_committee(card["committee"])] = {
            "chair": card["chair"],
            "chamber": card["chamber"],
            "top_sectors": [s.get("sector", "") for s in card["top_sectors"][:2]],
        }
    for p in top_pairs:
        p["h_chair"] = chair_by_cmt.get(_norm_committee(p["h_committee"]), {}).get("chair", "")
        p["s_chair"] = chair_by_cmt.get(_norm_committee(p["s_committee"]), {}).get("chair", "")
        h_sec = set(chair_by_cmt.get(_norm_committee(p["h_committee"]), {}).get("top_sectors", []))
        s_sec = set(chair_by_cmt.get(_norm_committee(p["s_committee"]), {}).get("top_sectors", []))
        p["shared_sectors"] = list(h_sec & s_sec)

    all_cards = senate_cards + house_cards
    return {
        "senate": senate_cards,
        "house": house_cards,
        "total_chairs": len(all_cards),
        "with_match": sum(1 for c in all_cards if c["jurisdiction_match"]),
        "total_bills_killed": sum(c["bills_killed"] for c in all_cards),
        "total_bills_killed_2025": sum(c["bills_killed_2025"] for c in all_cards),
        "total_bills_killed_2024": sum(c["bills_killed_2024"] for c in all_cards),
        "coordinated_kills": total_coord,
        "coordinated_kill_pairs": top_pairs,
    }


@router.get("/api/committee-gatekeeper")
def api_gatekeeper():
    return _build_gatekeeper_data()


@router.get("/gatekeeper", response_class=HTMLResponse)
def page_gatekeeper():
    data = _build_gatekeeper_data()
    template_path = _BASE_DIR / "templates" / "gatekeeper.html"
    if not template_path.exists():
        raise HTTPException(status_code=500, detail="Template not found")
    html = template_path.read_text(encoding="utf-8")
    payload = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    return HTMLResponse(html.replace("__GATEKEEPER_DATA__", payload))
