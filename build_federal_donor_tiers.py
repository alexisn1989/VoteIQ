#!/usr/bin/env python3
"""Rebuild the federal section of donor_map_cache.json from current DB data.

Why a full rebuild (not just tiers): the cached federal section can go stale
relative to polls.db after a fresh FEC pull. For example a contributor row keyed
"Wittman, Robert J." normalizes to "Robert Wittman", but a stale cache may still
show "Rob Wittman" — so a tiers dict keyed off current data would not match the
stale by_candidate keys. Regenerating everything from the same DB snapshot in one
pass keeps every figure and key on the federal page mutually consistent.

Sections written to cache["federal"]:
  - source, states_all/dem/rep, by_candidate, candidates  (via _federal_donor_data)
  - emp_sectors      employer_sector $ breakdown (template drops Other/Unknown)
  - candidate_tiers  per-candidate large-donor size bands, keyed to by_candidate

Tier note: the itemized Schedule A pull only captures large-dollar gifts
(min observed = $1,000), so there is NO grassroots tier — these are all large
donors. Tiers are contribution-size bands:
    Mid-Level ($1K-$3.3K) | Max-Out ($3.3K-$6.6K) | Super-Max ($6.6K+)
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "polls.db"
CACHE_FILE = BASE_DIR / "data" / "donor_map_cache.json"

# Kept identical to voteiq/api/routes/donor_map.py so cache matches the live route.
_SKIP_STATES = {"ZZ", "PR", "GU", "VI", "AS", "MP", "AA", "AE", "AP"}


def _norm_name(raw: str) -> str:
    """'Lastname, Firstname M.' -> 'Firstname Lastname' (matches donor_map.py)."""
    raw = (raw or "").strip()
    if "," in raw:
        last, first = raw.split(",", 1)
        first = re.sub(r"\s+[A-Z]\.$", "", first.strip())
        return f"{first.strip()} {last.strip()}"
    return raw


def _federal_donor_data(conn: sqlite3.Connection) -> dict:
    """Inlined copy of donor_map.py::_federal_donor_data (sans fastapi dep)."""
    party_map: dict[str, str] = {}
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

    key_to_name: dict[str, str] = {}
    key_to_party: dict[str, str] = {}
    cand_states: dict[str, dict] = {}

    for r in rows:
        if r["state"] in _SKIP_STATES:
            continue
        bid = (r["bioguide_id"] or "").strip()
        norm = _norm_name(r["candidate_name"])
        key = bid if bid else norm.lower()
        if key not in key_to_name:
            key_to_name[key] = norm
            key_to_party[key] = party_map.get(bid, "Unknown") if bid else "Unknown"
        cname = key_to_name[key]
        cand_states.setdefault(cname, {})
        s = r["state"]
        if s not in cand_states[cname]:
            cand_states[cname][s] = {"state": s, "total": 0, "donors": 0}
        cand_states[cname][s]["total"] += r["total"]
        cand_states[cname][s]["donors"] += r["donors"]

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

    # Build last-name → (cand_id, chamber) lookups for profile links in donor map
    _last_to_house: dict[str, str] = {}
    _last_to_senate: dict[str, str] = {}
    try:
        for r in conn.execute("SELECT cand_id, name FROM fec_va_house_candidates"):
            last = r["name"].split(",")[0].strip().upper() if "," in r["name"] else r["name"].split()[-1].upper()
            _last_to_house[last] = r["cand_id"]
    except Exception:
        pass
    try:
        for r in conn.execute("SELECT cand_id, name FROM fec_va_senate_candidates"):
            last = r["name"].split(",")[0].strip().upper() if "," in r["name"] else r["name"].split()[-1].upper()
            _last_to_senate[last] = r["cand_id"]
    except Exception:
        pass

    def _cand_id_and_chamber(display_name: str) -> tuple[str, str]:
        last = display_name.split()[-1].upper() if display_name else ""
        if last in _last_to_house:
            return _last_to_house[last], "house"
        if last in _last_to_senate:
            return _last_to_senate[last], "senate"
        return "", ""

    candidates = sorted(
        [{"name": k, "party": key_to_party[key],
          "cand_id": _cand_id_and_chamber(k)[0],
          "chamber": _cand_id_and_chamber(k)[1]}
         for key, k in key_to_name.items()],
        key=lambda c: -sum(s["total"] for s in by_candidate[c["name"]]),
    )

    return {
        "source": "federal",
        "states_all": states_all,
        "states_dem": states_dem,
        "states_rep": states_rep,
        "by_candidate": by_candidate,
        "candidates": candidates,
    }


# Tier bands: (label, lower-inclusive, upper-exclusive or None)
TIERS = [
    ("Mid-Level ($1K-$3.3K)", 0.0, 3300.0),
    ("Max-Out ($3.3K-$6.6K)", 3300.0, 6600.0),
    ("Super-Max ($6.6K+)", 6600.0, None),
]


# Canonical sector name map — applied everywhere sectors are displayed
SECTOR_CANON: dict[str, str] = {
    "Finance":               "Finance & Banking",
    "Real Estate":           "Real Estate & Construction",
    "Defense":               "Defense & Aerospace",
    "Technology":            "Technology & IT",
    "Energy":                "Energy & Oil/Gas",
    "Hospitality":           "Retail & Hospitality",
    # Keep Lobbying and Consulting separate — merged label was too broad
    "Lobbying":              "Lobbying",
    "Consulting":            "Consulting",
}
_SECTOR_EXCLUDE_SQL = (
    "'Other','Other/Unknown','Retired','Retired/Individual',"
    "'Self-Employed','Homemaker'"
)


def _retired_sub(occ: str | None) -> str | None:
    occ = (occ or "").upper().strip()
    if any(k in occ for k in ("INVEST", "EQUITY", "CAPITAL", "VENTURE",
                               "LANDMAN", "REAL ESTATE", "FINANCIAL",
                               "ECON", "TAX PRACT")):
        return "Retired (Finance/Investing)"
    if any(k in occ for k in ("ATTORNEY", "LAWYER", "LEGAL", "COUNSEL")):
        return "Retired (Legal)"
    if any(k in occ for k in ("PHYSICIAN", "DOCTOR", "HEALTH", "MEDICAL",
                               "NURSE", "DENTIST")):
        return "Retired (Healthcare)"
    if any(k in occ for k in ("EXEC", "EXECUTIVE", "OWNER", "BUSINESS",
                               "CONSUL", "MANAGE", "DIRECTOR", "AUTO DEAL")):
        return "Retired (Business)"
    if occ in ("", "RETIRED", "NOT EMPLOYED", "NONE", "HOMEMAKER",
               "INFORMATION REQUESTED",
               "INFORMATION REQUESTED PER BEST EFFORTS",
               "SELF EMPLOYED", "SELF-EMPLOYED"):
        return None
    return "Retired (Other)"


def build_emp_sectors(conn: sqlite3.Connection, party: str | None = None) -> list[dict]:
    """Employer-sector $ breakdown. Pass party='Democratic'/'Republican' to filter."""
    if party:
        rows = conn.execute(
            f"""
            SELECT f.employer_sector AS label,
                   ROUND(SUM(f.amount), 0) AS total,
                   COUNT(*) AS donors
            FROM   fec_individual_contributions f
            JOIN   congress_members m ON m.bioguide_id = f.bioguide_id
            WHERE  f.amount > 0 AND m.party = ?
              AND  f.employer_sector IS NOT NULL
              AND  f.employer_sector NOT IN ({_SECTOR_EXCLUDE_SQL})
            GROUP  BY f.employer_sector
            ORDER  BY total DESC
            """, (party,)
        ).fetchall()
        ret_rows = conn.execute(
            f"""
            SELECT f.contributor_occupation AS occ,
                   ROUND(SUM(f.amount), 0) AS total,
                   COUNT(*) AS donors
            FROM   fec_individual_contributions f
            JOIN   congress_members m ON m.bioguide_id = f.bioguide_id
            WHERE  f.amount > 0 AND m.party = ?
              AND  f.employer_sector IN ('Retired', 'Retired/Individual')
            GROUP  BY f.contributor_occupation
            """, (party,)
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT employer_sector AS label,
                   ROUND(SUM(amount), 0) AS total,
                   COUNT(*) AS donors
            FROM   fec_individual_contributions
            WHERE  amount > 0
              AND  employer_sector IS NOT NULL
              AND  employer_sector NOT IN ({_SECTOR_EXCLUDE_SQL})
            GROUP  BY employer_sector
            ORDER  BY total DESC
            """
        ).fetchall()
        ret_rows = conn.execute(
            f"""
            SELECT contributor_occupation AS occ,
                   ROUND(SUM(amount), 0) AS total,
                   COUNT(*) AS donors
            FROM   fec_individual_contributions
            WHERE  amount > 0
              AND  employer_sector IN ('Retired', 'Retired/Individual')
            GROUP  BY contributor_occupation
            """
        ).fetchall()

    merged: dict[str, dict] = {}
    for r in rows:
        label = SECTOR_CANON.get(r["label"], r["label"])
        if label in merged:
            merged[label]["total"] += r["total"]
            merged[label]["donors"] += r["donors"]
        else:
            merged[label] = {"label": label, "total": r["total"], "donors": r["donors"]}

    for r in ret_rows:
        sub = _retired_sub(r["occ"])
        if sub is None:
            continue
        if sub in merged:
            merged[sub]["total"] += r["total"]
            merged[sub]["donors"] += r["donors"]
        else:
            merged[sub] = {"label": sub, "total": r["total"], "donors": r["donors"]}

    return sorted(merged.values(), key=lambda x: -x["total"])


_EMP_SKIP = {
    "RETIRED", "NOT EMPLOYED", "NONE", "N/A", "NA", "SELF-EMPLOYED", "SELF EMPLOYED",
    "HOMEMAKER", "INFORMATION REQUESTED",
    "INFORMATION REQUESTED PER BEST EFFORTS", "NONE PROVIDED",
}
_EMP_SKIP_SECTORS = {
    "Retired", "Retired/Individual", "Self-Employed", "Homemaker",
    "Other", "Other/Unknown", "Grassroots",
}


def build_top_employers(conn: sqlite3.Connection) -> list[dict]:
    """Top 20 individual employers by total $ donated across all VA federal candidates."""
    rows = conn.execute("""
        SELECT contributor_employer              AS employer,
               employer_sector                  AS sector,
               ROUND(SUM(amount), 0)            AS total,
               COUNT(*)                         AS donors
        FROM   fec_individual_contributions
        WHERE  amount > 0
          AND  contributor_employer IS NOT NULL AND contributor_employer != ''
        GROUP  BY contributor_employer
        ORDER  BY total DESC
        LIMIT  200
    """).fetchall()

    out: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        name = (r["employer"] or "").strip()
        key  = name.upper()
        if key in seen or key in _EMP_SKIP:
            continue
        sector = r["sector"] or ""
        if sector in _EMP_SKIP_SECTORS:
            continue
        seen.add(key)
        out.append({"name": name, "sector": sector, "total": r["total"], "donors": r["donors"]})
        if len(out) >= 20:
            break
    return out


def _key_to_name(conn: sqlite3.Connection) -> dict[str, str]:
    """Reproduce the candidate key->display-name map used by _federal_donor_data,
    so candidate_tiers keys match by_candidate keys exactly."""
    rows = conn.execute(
        """
        SELECT candidate_name, bioguide_id, state, ROUND(SUM(amount),0) AS total
        FROM   fec_individual_contributions
        WHERE  state IS NOT NULL AND state != '' AND candidate_name IS NOT NULL
        GROUP  BY candidate_name, bioguide_id, state
        ORDER  BY total DESC
        """
    ).fetchall()
    key_to_name: dict[str, str] = {}
    for r in rows:
        if r["state"] in _SKIP_STATES:
            continue
        bid = (r["bioguide_id"] or "").strip()
        norm = _norm_name(r["candidate_name"])
        key = bid if bid else norm.lower()
        if key not in key_to_name:
            key_to_name[key] = norm
    return key_to_name


def build_candidate_tiers(conn: sqlite3.Connection) -> dict:
    key_to_name = _key_to_name(conn)
    rows = conn.execute(
        """
        SELECT candidate_name, bioguide_id, amount
        FROM   fec_individual_contributions
        WHERE  amount > 0 AND candidate_name IS NOT NULL
        """
    ).fetchall()

    agg: dict[str, dict[str, list]] = {}
    for r in rows:
        bid = (r["bioguide_id"] or "").strip()
        norm = _norm_name(r["candidate_name"])
        key = bid if bid else norm.lower()
        name = key_to_name.get(key)
        if not name:  # not represented in by_candidate (e.g. skipped states only)
            continue
        amount = float(r["amount"])
        for label, lo, hi in TIERS:
            if amount >= lo and (hi is None or amount < hi):
                slot = agg.setdefault(name, {})
                slot.setdefault(label, [0.0, 0])
                slot[label][0] += amount
                slot[label][1] += 1
                break

    out: dict[str, list] = {}
    for name, tiers in agg.items():
        out[name] = [
            {"tier": label, "total": round(tiers[label][0], 2), "count": tiers[label][1]}
            for label, _lo, _hi in TIERS
            if label in tiers
        ]
    return out


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        fed = _federal_donor_data(conn)

        # Dedup candidates by name: multiple FEC name spellings can produce two
        # entries with the same display name (e.g. "Tim Kaine" from "Kaine, Tim" with
        # bioguide and "Tim Kaine" without). Prefer the entry whose party != "Unknown".
        seen_names: dict[str, int] = {}   # name -> index in deduped list
        deduped: list[dict] = []
        for c in fed["candidates"]:
            n = c["name"]
            if n not in seen_names:
                seen_names[n] = len(deduped)
                deduped.append(c)
            elif c["party"] != "Unknown":
                # upgrade the existing entry to the better-party version
                deduped[seen_names[n]] = c
        fed["candidates"] = deduped

        fed["emp_sectors"]     = build_emp_sectors(conn)
        fed["emp_sectors_dem"] = build_emp_sectors(conn, party="Democratic")
        fed["emp_sectors_rep"] = build_emp_sectors(conn, party="Republican")
        fed["candidate_tiers"] = build_candidate_tiers(conn)
        fed["top_employers"]   = build_top_employers(conn)
    finally:
        conn.close()

    with CACHE_FILE.open("r", encoding="utf-8") as f:
        cache = json.load(f)
    cache["federal"] = fed
    with CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(cache, f, separators=(",", ":"), default=str)

    tiers = fed["candidate_tiers"]
    print(f"Federal cache rebuilt: {len(fed['by_candidate'])} candidates, "
          f"{len(fed['emp_sectors'])} employer sectors, {len(tiers)} tier breakdowns")
    # Verify key alignment
    bc = set(fed["by_candidate"].keys())
    tk = set(tiers.keys())
    missing = sorted(bc - tk)
    extra = sorted(tk - bc)
    print(f"  by_candidate without tiers: {missing or 'none'}")
    print(f"  tiers without by_candidate: {extra or 'none'}")
    for name in sorted(tiers):
        total = sum(t["total"] for t in tiers[name])
        bands = ", ".join(f"{t['tier'].split(' (')[0]}=${t['total']:,.0f}" for t in tiers[name])
        print(f"  {name:<28} ${total:>11,.0f}  | {bands}")


if __name__ == "__main__":
    main()
