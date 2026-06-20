"""VA Legislators visualization endpoints — sortable/filterable HTML + JSON API."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["legislators"])

_BASE_DIR = Path(__file__).resolve().parents[3]

# ── Donor-legislation correlation cache ───────────────────────────────────────
# Pre-loaded once; silently absent if cache not yet built.
_DONOR_ANALYSIS_LOOKUP: dict[str, dict] = {}
try:
    _cache = json.loads(
        (_BASE_DIR / "data" / "donor_map_cache.json").read_text(encoding="utf-8")
    )
    for _rec in _cache.get("state", {}).get("donor_legislation", []):
        _nm = (_rec.get("name") or "").strip().lower()
        if _nm:
            _DONOR_ANALYSIS_LOOKUP[_nm] = _rec
except Exception:
    pass
_data_dir_raw = os.getenv("DATA_DIR", str(_BASE_DIR))
_DATA_DIR = _data_dir_raw if os.path.isdir(_data_dir_raw) else str(_BASE_DIR)
_POLLS_DB = os.path.join(_DATA_DIR, "polls.db")

# Name prefixes/suffixes stripped when normalizing a full legal name to a
# "first last" lookup key. Mirrors the logic used to build cf_candidate_keys.
_NAME_PREFIXES = {"mr", "mrs", "ms", "miss", "dr", "rev", "hon", "sen", "rep",
                  "the", "honorable"}
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v", "md", "phd", "esq", "esquire"}


def _norm_cand_key(name: str) -> str:
    """Normalize a full name to a 'first last' key (lowercased, titles dropped).

    'Mr. Aaron Roosevelt Rouse' -> 'aaron rouse'; 'Aaron R. Rouse' -> 'aaron rouse'.
    Used to match a legislator against the donor table via the indexed
    cf_candidate_keys lookup instead of a slow leading-wildcard LIKE scan.
    """
    if not name:
        return ""
    toks = [t.strip(".,").lower() for t in name.split()]
    toks = [t for t in toks if t]
    while toks and toks[0] in _NAME_PREFIXES:
        toks.pop(0)
    while toks and toks[-1] in _NAME_SUFFIXES:
        toks.pop()
    if not toks:
        return ""
    if len(toks) == 1:
        return toks[0]
    return toks[0] + " " + toks[-1]


def _polls_conn():
    path = _POLLS_DB
    if not os.path.isfile(path):
        raise HTTPException(status_code=503, detail="Legislator database not available")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


# Common first-name nicknames for fuzzy campaign-finance matching.
# The finance table sometimes lists a legislator under a nickname
# (e.g. "Joe McNamara" vs the formal "Joseph P. McNamara").
_NICKNAMES = {
    "david": "dave", "joseph": "joe", "joshua": "josh", "william": "bill",
    "robert": "bob", "michael": "mike", "christopher": "chris", "daniel": "dan",
    "thomas": "tom", "kathleen": "kathy", "jennifer": "jen", "matthew": "matt",
    "richard": "rick", "edward": "ed", "anthony": "tony", "nicholas": "nick",
    "samuel": "sam", "benjamin": "ben", "andrew": "andy", "stephen": "steve",
    "steven": "steve", "patricia": "pat", "deborah": "debbie", "elizabeth": "liz",
    "charles": "charlie", "lillian": "lily", "will": "bill",
}


def _name_key(full_name: str) -> str:
    """Normalize a name to a 'first last' key for fuzzy finance matching.

    Drops middle names/initials, generational suffixes (Jr/Sr/II/III), and
    quoted nicknames, then applies a nickname map to the first name. Requires
    BOTH first and last name to match — this prevents false positives between
    different legislators who share a surname (e.g. Gretchen Bulova must NOT
    match David Bulova; Kirk McPike must NOT match Jeremy McPike).
    """
    n = re.sub(r'"[^"]*"', "", full_name)                       # drop "Buddy" etc.
    n = re.sub(r"\b(Jr|Sr|II|III|IV)\b", "", n, flags=re.I)     # drop suffixes
    n = re.sub(r"[.,]", " ", n)                                  # punctuation -> space
    tokens = [t for t in n.split() if len(t) > 1]               # drop single-letter initials
    if not tokens:
        return ""
    first = _NICKNAMES.get(tokens[0].lower(), tokens[0].lower())
    last = tokens[-1].lower()
    return f"{first} {last}"


def _same_person(a: str, b: str) -> bool:
    """True when two name strings refer to the same legislator.

    Keys must match on first AND last name so shared surnames never
    cross-match (Don Scott vs Phillip A. Scott). A degenerate key — a
    surname-only string like 'J.D. "Danny" Diggs', whose initials and quoted
    nickname are stripped, leaving 'diggs diggs' — may match on surname alone.
    """
    ka, kb = _name_key(a), _name_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    fa, la = ka.split()[0], ka.split()[-1]
    fb, lb = kb.split()[0], kb.split()[-1]
    return la == lb and (fa == la or fb == lb)


def _person_name_variants(conn, table: str, column: str, person_name: str) -> list[str]:
    """Exact name strings in table.column that belong to person_name.

    Replaces last-name LIKE joins, which cross-matched legislators sharing a
    surname and treated ', Jr.' as a surname (matching every Jr. in the
    table). Matches whole names via _name_key; falls back to surname-only
    when exactly one person in the column carries that surname. Returns []
    when the person can't be identified unambiguously — callers should treat
    that as "no data", never fall back to a broader match.
    """
    if not person_name:
        return []
    try:
        rows = [r[0] for r in conn.execute(
            f"SELECT DISTINCT {column} FROM {table}"
        ).fetchall() if r[0]]
    except Exception:
        return []
    variants = [n for n in rows if _same_person(n, person_name)]
    target_key = _name_key(person_name)
    if variants:
        # A surname-only target ("Scott" -> degenerate key) matches every
        # Scott — bail out if it matched more than one distinct person.
        keys = {_name_key(n) for n in variants}
        if (target_key and target_key.split()[0] == target_key.split()[-1]
                and len(keys) > 1):
            return []
        return variants
    if not target_key:
        return []
    t_last = target_key.split()[-1]
    by_person: dict[str, list[str]] = {}
    for n in rows:
        k = _name_key(n)
        if k and k.split()[-1] == t_last:
            by_person.setdefault(k, []).append(n)
    if len(by_person) == 1:
        return next(iter(by_person.values()))
    return []


def _inject(template: str, key: str, data) -> str:
    safe = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    with (_BASE_DIR / "templates" / template).open("r", encoding="utf-8") as f:
        html = f.read()
    return html.replace("</head>", f"<script>window.{key}={safe};</script></head>", 1)


def _bill_match_key(voter_name: str) -> str:
    """Normalize a vote-summary name to the short form used in the bill tables.

    Replicates the SQL CASE expression formerly used in the join:
    "Aaron R. Rouse" -> "aaron rouse" (drops the middle initial). Names without
    a "<initial>. " pattern are simply lowercased.
    """
    if ". " in voter_name:
        first_space = voter_name.find(" ")
        dot = voter_name.find(". ")
        return (voter_name[:first_space] + voter_name[dot + 1:]).lower()
    return voter_name.lower()


def _legislators_list(conn) -> list[dict]:
    # NOTE: This was previously a single query with two LEFT JOINs whose ON
    # clauses matched on a computed expression (lower(substr(...)||substr(...)))
    # on both sides. SQLite could not use any index for that, producing a full
    # cross-product (~145 x 7.3k x 24k rows) that took ~160s. We now run three
    # fast indexed scans and merge the bill counts in Python — identical output,
    # sub-second runtime.
    rows = conn.execute("""
        SELECT voter_name, chamber, party,
               yes_count, no_count, not_voting, abstain, total_votes,
               ROUND(CAST(yes_count AS REAL) / MAX(total_votes, 1) * 100, 1) AS yes_rate
        FROM va_legislator_vote_summary
        WHERE session = '2026'
        ORDER BY chamber, party, voter_name
    """).fetchall()

    # Build a first+last → party lookup from campaign_finance_summary to fill
    # blank party values (va_legislator_vote_summary often has party='')
    try:
        party_lookup: dict[str, str] = {}
        for r in conn.execute(
            "SELECT name, party FROM campaign_finance_summary WHERE source='va_sbe' AND party != ''"
        ).fetchall():
            key = _name_key(r["name"] if hasattr(r, "__getitem__") else r[0])
            party_lookup[key] = r["party"] if hasattr(r, "__getitem__") else r[1]
    except Exception:
        party_lookup = {}

    sponsored = {
        r[0]: r[1] for r in conn.execute("""
            SELECT lower(legislator_name), COUNT(DISTINCT bill_id)
            FROM va_legislator_sponsored_bills
            WHERE session = '2026'
            GROUP BY lower(legislator_name)
        """).fetchall()
    }
    cosponsored = {
        r[0]: r[1] for r in conn.execute("""
            SELECT lower(legislator_name), COUNT(DISTINCT bill_id)
            FROM va_legislator_cosponsor_bills
            WHERE session = '2026'
            GROUP BY lower(legislator_name)
        """).fetchall()
    }

    out = []
    for r in rows:
        d = dict(r)
        key = _bill_match_key(d["voter_name"])
        d["bills_introduced"] = sponsored.get(key, 0)
        d["bills_cosponsored"] = cosponsored.get(key, 0)
        # Fill blank party from campaign_finance_summary lookup
        if not d.get("party") and party_lookup:
            fk = _name_key(d["voter_name"])
            d["party"] = party_lookup.get(fk, "")
        out.append(d)
    return out


def _fetch_profile(conn, name: str, session: str = "2026") -> dict:
    """Return full legislator profile or raise 404."""
    row = conn.execute("""
        SELECT voter_name, chamber, party,
               yes_count, no_count, not_voting, abstain, total_votes,
               ROUND(CAST(yes_count AS REAL) / MAX(total_votes, 1) * 100, 1) AS yes_rate
        FROM va_legislator_vote_summary
        WHERE session = ? AND lower(voter_name) = lower(?)
        LIMIT 1
    """, (session, name)).fetchone()

    if not row:
        # Resolve by whole-name key, not LIKE %last% — a surname lookup like
        # "Phillip Scott" must never resolve to Don Scott's profile.
        cands = conn.execute("""
            SELECT voter_name, chamber, party,
                   yes_count, no_count, not_voting, abstain, total_votes,
                   ROUND(CAST(yes_count AS REAL) / MAX(total_votes, 1) * 100, 1) AS yes_rate
            FROM va_legislator_vote_summary
            WHERE session = ?
        """, (session,)).fetchall()
        # Surname-only input ("Scott") matches every Scott via the degenerate
        # key rule, so accept only when all matches are the same person.
        matches = [r for r in cands if _same_person(r["voter_name"], name)]
        if matches and len({_name_key(r["voter_name"]) for r in matches}) == 1:
            row = matches[0]

    if not row:
        raise HTTPException(status_code=404, detail=f"Legislator '{name}' not found")

    resolved = row["voter_name"]
    profile = dict(row)
    profile["current_session"] = session

    # All sessions this legislator has data for — used by UI session switcher
    profile["available_sessions"] = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT session FROM va_legislator_vote_summary "
            "WHERE lower(voter_name) = lower(?) ORDER BY session DESC",
            (resolved,),
        ).fetchall()
    ]

    # vote_summary uses formal names (e.g. "Aaron R. Rouse") but bill tables
    # use short names ("Aaron Rouse") — strip middle initial for bill lookups
    bill_name = re.sub(r'\s+[A-Z]\.\s+', ' ', resolved).strip()

    profile["recent_votes"] = [dict(r) for r in conn.execute("""
        SELECT bill_id, vote_date, option, chamber, motion, title
        FROM va_legislator_recent_votes
        WHERE session = ? AND voter_name = ?
        ORDER BY vote_date DESC LIMIT 30
    """, (session, resolved)).fetchall()]

    profile["sponsored_bills"] = [dict(r) for r in conn.execute("""
        SELECT DISTINCT bill_id, bill_number, title, status_label, subject, status_date
        FROM va_legislator_sponsored_bills
        WHERE session = ? AND lower(legislator_name) = lower(?)
        ORDER BY status_date DESC
    """, (session, bill_name)).fetchall()]

    profile["cosponsored_bills"] = [dict(r) for r in conn.execute("""
        SELECT bill_id, bill_number, title, status_label, subject, sponsor_order
        FROM va_legislator_cosponsor_bills
        WHERE session = ? AND lower(legislator_name) = lower(?)
        ORDER BY sponsor_order LIMIT 50
    """, (session, bill_name)).fetchall()]

    # ── Bill topic breakdown — combined introduced + cosponsored ─────────────
    # (text before first ';' in title; deduped by bill_id within each table)
    profile["bill_topics"] = [dict(r) for r in conn.execute("""
        SELECT topic, SUM(cnt) AS count
        FROM (
            SELECT TRIM(SUBSTR(title, 1, INSTR(title || ';', ';') - 1)) AS topic,
                   COUNT(DISTINCT bill_id) AS cnt
            FROM va_legislator_sponsored_bills
            WHERE session = ? AND lower(legislator_name) = lower(?)
              AND title != '' AND title IS NOT NULL
            GROUP BY topic
            UNION ALL
            SELECT TRIM(SUBSTR(title, 1, INSTR(title || ';', ';') - 1)) AS topic,
                   COUNT(DISTINCT bill_id) AS cnt
            FROM va_legislator_cosponsor_bills
            WHERE session = ? AND lower(legislator_name) = lower(?)
              AND title != '' AND title IS NOT NULL
            GROUP BY topic
        )
        WHERE topic != ''
        GROUP BY topic
        ORDER BY count DESC
        LIMIT 12
    """, (session, bill_name, session, bill_name)).fetchall()]

    # ── Bill pass rate by topic (all sessions, sponsored bills only) ─────────
    _NOISE = (
        "governor", "confirming", "celebrating", "memorial",
        "recognizing", "commending", "designating", "in memoriam",
        "joint resolution", "congratulat",
    )
    raw_pass_rates = conn.execute("""
        WITH deduped AS (
            SELECT DISTINCT bill_id, title, status_label
            FROM va_legislator_sponsored_bills
            WHERE lower(legislator_name) = lower(?)
              AND title != '' AND title IS NOT NULL
        ),
        with_topic AS (
            SELECT CASE WHEN instr(title, ';') > 0
                        THEN trim(substr(title, 1, instr(title, ';') - 1))
                        ELSE substr(title, 1, 50) END AS topic,
                   CASE WHEN status_label = 'Passed' THEN 1 ELSE 0 END AS passed
            FROM deduped
        )
        SELECT topic,
               COUNT(*) AS total,
               SUM(passed) AS passed_count,
               CAST(ROUND(100.0 * SUM(passed) / COUNT(*)) AS INTEGER) AS pass_pct
        FROM with_topic
        GROUP BY topic
        HAVING total >= 2
        ORDER BY total DESC, passed_count DESC
        LIMIT 20
    """, (bill_name,)).fetchall()

    profile["topic_pass_rates"] = [
        dict(r) for r in raw_pass_rates
        if not any(r["topic"].lower().startswith(p) for p in _NOISE)
    ]

    # ── Governor alignment score ──────────────────────────────────────────────
    try:
        gov_rows = conn.execute("""
            WITH latest_vote AS (
                SELECT voter_name, bill_id, session,
                       option, title,
                       ROW_NUMBER() OVER (
                           PARTITION BY voter_name, bill_id, session
                           ORDER BY vote_date DESC
                       ) AS rn
                FROM va_legislator_recent_votes
            ),
            deduped AS (
                SELECT voter_name, bill_id, session, option, title
                FROM latest_vote WHERE rn = 1
            ),
            scored AS (
                SELECT ga.bill_number, ga.action, ga.title AS gov_title,
                       d.option, d.title,
                       ga.governor,
                       CASE
                           WHEN ga.action = 'signed'
                                AND d.option = 'yes'  THEN 1
                           WHEN ga.action IN ('vetoed','veto_sustained')
                                AND d.option = 'no'   THEN 1
                           ELSE 0
                       END AS aligned,
                       CASE WHEN d.option IN ('yes','no') THEN 1 ELSE 0 END AS countable
                FROM governor_actions ga
                JOIN deduped d ON d.bill_id = ga.bill_number
                               AND d.session = ga.session
                WHERE d.voter_name = ?
                  AND ga.action IN ('signed','vetoed','veto_sustained')
            )
            SELECT governor,
                   COUNT(*) AS total,
                   SUM(countable) AS voted,
                   SUM(aligned) AS aligned_ct,
                   CAST(ROUND(100.0 * SUM(aligned) / NULLIF(SUM(countable), 0)) AS INTEGER) AS pct
            FROM scored
            GROUP BY governor
            HAVING voted > 0
            LIMIT 1
        """, (resolved,)).fetchone()

        if gov_rows and gov_rows["voted"] >= 5:
            opposed_ct = gov_rows["voted"] - gov_rows["aligned_ct"]
            profile["governor_alignment"] = {
                "governor":   gov_rows["governor"],
                "pct":        gov_rows["pct"],
                "aligned_ct": gov_rows["aligned_ct"],
                "opposed_ct": opposed_ct,
                "total_voted": gov_rows["voted"],
            }
            # Key opposition votes (bucked the governor — most newsworthy)
            opp_rows = conn.execute("""
                WITH latest_vote AS (
                    SELECT voter_name, bill_id, session, option, title,
                           ROW_NUMBER() OVER (
                               PARTITION BY voter_name, bill_id, session
                               ORDER BY vote_date DESC
                           ) AS rn
                    FROM va_legislator_recent_votes
                ),
                deduped AS (SELECT voter_name, bill_id, session, option, title
                            FROM latest_vote WHERE rn = 1)
                SELECT ga.bill_number, ga.action, ga.title, d.option
                FROM governor_actions ga
                JOIN deduped d ON d.bill_id = ga.bill_number AND d.session = ga.session
                WHERE d.voter_name = ?
                  AND ga.action IN ('signed','vetoed','veto_sustained')
                  AND d.option IN ('yes','no')
                  AND NOT (
                      (ga.action = 'signed' AND d.option = 'yes') OR
                      (ga.action IN ('vetoed','veto_sustained') AND d.option = 'no')
                  )
                ORDER BY ga.action_date DESC
                LIMIT 5
            """, (resolved,)).fetchall()
            profile["governor_alignment"]["opposition_votes"] = [dict(r) for r in opp_rows]
        else:
            profile["governor_alignment"] = None
    except Exception:
        profile["governor_alignment"] = None

    # ── Committee roles + donor-overlap flags ────────────────────────────────
    _CMTE_SECTOR_MAP: dict[str, list[str]] = {
        "abc":               ["Wine & Spirits"],
        "gaming":            ["Gambling/Casinos"],
        "agriculture":       ["Agribusiness", "Livestock & Poultry"],
        "natural resources": ["Fossil Fuels", "Agribusiness"],
        "conservation":      ["Fossil Fuels"],
        "finance":           ["Financial Services", "Banking", "Investment & Securities"],
        "appropriations":    ["Financial Services", "Banking"],
        "commerce":          ["Financial Services", "Utilities", "Retail"],
        "labor":             ["Trial Lawyers", "Building & Industrial Unions",
                              "Public Employee Unions"],
        "courts":            ["Trial Lawyers", "Corporate Law", "Legal"],
        "technology":        ["Telecom", "Software & IT", "IT & Engineering",
                              "Artificial Intelligence"],
        "communications":    ["Telecom"],
        "health profession": ["Health Professionals", "Pharma", "Health Insurance"],
        "health":            ["Health Professionals", "Hospitals", "Pharma",
                              "Health Insurance", "Healthcare Worker Unions"],
        "transportation":    ["Transportation"],
        "housing":           ["Realtors", "Commercial Real Estate", "Homebuilders"],
        "insurance":         ["Insurance", "Financial Services"],
        "energy":            ["Utilities", "Fossil Fuels", "Renewables"],
        "education":         ["Education", "Education Unions"],
    }
    try:
        cmte_variants = _person_name_variants(
            conn, "va_committee_assignments", "member_name", resolved)
        cmte_rows = conn.execute(
            f"""SELECT committee, role FROM va_committee_assignments
               WHERE role IN ('chair','vice_chair')
                 AND member_name IN ({",".join("?" * len(cmte_variants))})""",
            cmte_variants,
        ).fetchall() if cmte_variants else []
        profile["committee_roles"] = [
            {"committee": r["committee"], "role": r["role"]} for r in cmte_rows
        ]
        fin_variants = _person_name_variants(
            conn, "campaign_finance_summary", "name", resolved)
        fin_sec_row = conn.execute(
            f"SELECT by_sector_json FROM campaign_finance_summary"
            f" WHERE source='va_sbe' AND name IN ({','.join('?' * len(fin_variants))})"
            f" LIMIT 1",
            fin_variants,
        ).fetchone() if fin_variants else None
        donor_sectors_lower = (
            [s["sector"].lower() for s in json.loads(fin_sec_row["by_sector_json"] or "[]")[:8]]
            if fin_sec_row else []
        )
        flags: list[dict] = []
        for r in cmte_rows:
            cmte_lower = r["committee"].lower()
            for kw, sectors in _CMTE_SECTOR_MAP.items():
                if kw in cmte_lower:
                    for sec in sectors:
                        if sec.lower() in donor_sectors_lower:
                            flags.append({
                                "committee": r["committee"],
                                "role": r["role"],
                                "sector": sec,
                            })
        profile["committee_influence_flags"] = flags
    except Exception:
        profile["committee_roles"] = []
        profile["committee_influence_flags"] = []

    # ── Campaign finance sectors ─────────────────────────────────────────────
    # Guard: query only the columns that actually exist (Render may have a
    # stale schema from before overall_va_pct / alignment_json were added).
    _fin_cols_present = {r[1] for r in conn.execute(
        "PRAGMA table_info(campaign_finance_summary)"
    ).fetchall()}
    _fin_select = ", ".join(
        c if c in _fin_cols_present else f"NULL AS {c}"
        for c in ["total_raised", "top_sector", "top_sector_pct", "latest_cycle",
                  "by_sector_json", "top_donors_json", "overall_va_pct", "alignment_json",
                  "district", "cycles_json"]
    )
    try:
        fin_row = conn.execute(f"""
            SELECT {_fin_select}
            FROM campaign_finance_summary
            WHERE lower(name) = lower(?)
            LIMIT 1
        """, (bill_name,)).fetchone()
    except Exception:
        fin_row = None

    # Fuzzy fallback: the finance table may list this legislator under a
    # nickname or dropped middle name (e.g. "Joe McNamara" for "Joseph P.
    # McNamara"). Match on a first+last key so we never cross-match two
    # different legislators who happen to share a surname.
    if not fin_row:
        target_key = _name_key(resolved)
        if target_key:
            try:
                for cand in conn.execute(f"""
                    SELECT {_fin_select}, name
                    FROM campaign_finance_summary
                """).fetchall():
                    if _name_key(cand["name"]) == target_key:
                        fin_row = cand
                        break
            except Exception:
                pass

    # ── Known utility/energy PAC sector override ─────────────────────────────
    # Dominion Energy PAC and similar entries are mis-classified as "Ideological"
    # in cached data because the SECTOR_KEYWORDS " pac" substring fires before
    # the Utilities entry. Correct at display time without a full cache rebuild.
    _UTILITY_PAC_RE = re.compile(
        r'dominion\s+(energy|power|virginia|resources|political)'
        r'|appalachian\s+power|american\s+electric\s+power|\baep\b',
        re.I,
    )

    def _fix_pac_sectors(
        donors: list[dict], sectors: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        move: dict[str, float] = {}
        fixed = []
        for d in donors:
            name = d.get("contributor_name") or d.get("name") or ""
            if _UTILITY_PAC_RE.search(name) and d.get("sector") not in (
                "Utilities", "Renewables", "Fossil Fuels"
            ):
                move[d.get("sector", "Ideological")] = (
                    move.get(d.get("sector", "Ideological"), 0) + float(d.get("total") or 0)
                )
                d = {**d, "sector": "Utilities"}
            fixed.append(d)
        if not move:
            return fixed, sectors
        utils_add = sum(move.values())
        new_sectors: list[dict] = []
        found_utils = False
        for s in sectors:
            sname = s.get("sector", "")
            deduct = move.get(sname, 0)
            new_total = float(s.get("total") or 0) - deduct
            if sname == "Utilities":
                found_utils = True
                new_sectors.append({**s, "total": round(new_total + utils_add, 2)})
            elif new_total > 0:
                new_sectors.append({**s, "total": round(new_total, 2)})
        if not found_utils and utils_add:
            new_sectors.append({"sector": "Utilities", "total": round(utils_add, 2),
                                 "donor_count": 0, "avg_donation": 0})
        new_sectors.sort(key=lambda s: s.get("total", 0), reverse=True)
        return fixed, new_sectors

    if fin_row:
        # Bug 1: district from campaign_finance_summary (authoritative, session-refreshed)
        cfs_district = (fin_row["district"] if "district" in fin_row.keys() else None) or ""
        if cfs_district and not profile.get("district"):
            profile["district"] = cfs_district

        raw_donors  = json.loads(fin_row["top_donors_json"] or "[]")[:10]
        raw_sectors = json.loads(fin_row["by_sector_json"] or "[]")
        # Bug 3: reclassify known utility PAC donors mis-filed as Ideological
        fixed_donors, fixed_sectors = _fix_pac_sectors(raw_donors, raw_sectors)

        cycles = json.loads(fin_row["cycles_json"] or "[]") if "cycles_json" in fin_row.keys() else []
        cycle_range = (
            f"{cycles[-1]}–{cycles[0]}" if len(cycles) >= 2
            else (cycles[0] if cycles else (fin_row["latest_cycle"] or ""))
        )

        # Bug 2: label total_raised with explicit time window
        profile["finance_meta"] = {
            "total_raised":             fin_row["total_raised"],
            "total_raised_label":       f"Multi-cycle total ({cycle_range})",
            "top_sector":               fixed_sectors[0]["sector"] if fixed_sectors else fin_row["top_sector"],
            "top_sector_pct":           fin_row["top_sector_pct"],
            "latest_cycle":             fin_row["latest_cycle"],
            "overall_va_pct":           fin_row["overall_va_pct"],
            "finance_window":           cycle_range,
            "cycles":                   cycles,
            # Bug 6: methodology version on every derived finance section
            "methodology_version":      "VoteIQ Finance Analysis v2.0",
            "source":                   "Virginia SBE Schedule A filings",
            "method":                   "SQL aggregate",
        }
        profile["finance_sectors"] = fixed_sectors
        profile["top_donors"]      = fixed_donors
        profile["donor_alignment"] = json.loads(fin_row["alignment_json"] or "[]")

        # Bug 5: basic statistical quality score
        n_sectors = len([s for s in fixed_sectors if s.get("sector") != "Individual/Other"])
        attributed = sum(
            s.get("total", 0) for s in fixed_sectors
            if s.get("sector") not in ("Individual/Other", "")
        )
        total = fin_row["total_raised"] or 1
        attr_pct = attributed / total * 100
        n_cycles = len(cycles)
        if attr_pct >= 70 and n_cycles >= 4 and n_sectors >= 5:
            score, score_reason = "A", "High attribution, multi-cycle data, broad sector coverage"
        elif attr_pct >= 50 and n_cycles >= 2 and n_sectors >= 3:
            score, score_reason = "B", "Good attribution and coverage"
        elif attr_pct >= 30 or n_cycles >= 2:
            score, score_reason = "C", "Partial attribution or limited cycle data"
        else:
            score, score_reason = "D", "Low attribution or single-cycle data only"
        profile["finance_quality"] = {
            "score":                score,
            "reason":               score_reason,
            "attribution_pct":      round(attr_pct, 1),
            "cycles_available":     n_cycles,
            "methodology_version":  "VoteIQ Finance Analysis v2.0",
        }
    else:
        profile["finance_meta"]    = None
        profile["finance_sectors"] = []
        profile["top_donors"]      = []
        profile["donor_alignment"] = []
        profile["finance_quality"] = None

    # ── Donor tiers: corporate/PAC vs individual size buckets ───────────────
    def _donor_tiers(cand_names) -> list:
        """Aggregate donor tiers across one or more exact candidate_name values.

        Each name hits the lower(candidate_name) functional index, so an IN-list
        of variants stays index-fast (no full-table scan).
        Returns [] silently if va_cf_schedule_a is not yet ingested on this deployment.
        """
        if isinstance(cand_names, str):
            cand_names = [cand_names]
        cand_names = [n for n in cand_names if n]
        if not cand_names:
            return []
        placeholders = ",".join("lower(?)" for _ in cand_names)
        try:
            rows = conn.execute(f"""
                SELECT
                    CASE
                        WHEN is_individual = 0 THEN 'Corporate / PAC'
                        WHEN amount < 200      THEN 'Small (< $200)'
                        WHEN amount < 1000     THEN 'Mid ($200–$999)'
                        WHEN amount < 2500     THEN 'Large ($1k–$2.5k)'
                        ELSE                        'Major ($2,500+)'
                    END AS tier,
                    COUNT(*)    AS cnt,
                    SUM(amount) AS total
                FROM va_cf_schedule_a
                WHERE lower(candidate_name) IN ({placeholders}) AND amount > 0
                GROUP BY tier ORDER BY total DESC
            """, cand_names).fetchall()
            return [{"tier": r["tier"], "count": r["cnt"], "total": r["total"]} for r in rows]
        except Exception:
            return []

    tiers = _donor_tiers(bill_name) or _donor_tiers(resolved)
    if not tiers:
        # Indexed fuzzy fallback: normalize the legislator name to a
        # "first last" key and pull every matching donor-name variant from the
        # pre-built cf_candidate_keys table, then aggregate them in one shot.
        # Replaces a 15s leading-wildcard LIKE scan over 2.2M rows.
        key = _norm_cand_key(bill_name) or _norm_cand_key(resolved)
        if key:
            try:
                variants = [
                    m["candidate_name"] for m in conn.execute(
                        "SELECT candidate_name FROM cf_candidate_keys WHERE cand_key = ?",
                        (key,)
                    ).fetchall()
                ]
            except sqlite3.OperationalError:
                variants = []
            if variants:
                tiers = _donor_tiers(variants)
    profile["donor_tiers"] = tiers

    # ── Donor-industry analysis (from pre-computed correlation cache) ──────────
    # Try exact name match first, then last-name fuzzy fallback.
    resolved_lower = resolved.strip().lower()
    donor_rec = _DONOR_ANALYSIS_LOOKUP.get(resolved_lower)
    if not donor_rec and resolved_lower.split():
        # Last-name fallback: only accept if exactly ONE record shares the surname,
        # to prevent e.g. "Don Scott" → last="scott" → matching "Scott A. Surovell"
        # (district 34) instead of Don Scott (district 88).
        last_lower = resolved_lower.split()[-1].rstrip(".,")
        matches = [v for k, v in _DONOR_ANALYSIS_LOOKUP.items()
                   if k.split()[-1] == last_lower]
        if len(matches) == 1:
            donor_rec = matches[0]

    # Bug 4: dedup bills within each industry_votes entry (cache builder can
    # produce duplicate bill references when a bill appears in multiple sessions).
    if donor_rec and donor_rec.get("industry_votes"):
        for industry, iv in donor_rec["industry_votes"].items():
            bills = iv.get("bills") or []
            seen_bills: set[str] = set()
            deduped: list[dict] = []
            for b in bills:
                key = b.get("bill_id", "") + b.get("option", "")
                if key not in seen_bills:
                    seen_bills.add(key)
                    deduped.append(b)
            iv["bills"] = deduped

    profile["donor_analysis"] = donor_rec  # None if no match

    # ── Voting similarity matrix (allies & opponents) ────────────────────────
    try:
        sim_rows = conn.execute(
            """SELECT other.voter_name, vs.party,
                      SUM(CASE WHEN a.option=other.option THEN 1 ELSE 0 END) agree,
                      COUNT(*) total,
                      ROUND(100.0*SUM(CASE WHEN a.option=other.option
                                     THEN 1 ELSE 0 END)/COUNT(*), 1) pct
               FROM va_legislator_recent_votes a
               JOIN va_legislator_recent_votes other
                   ON a.bill_id=other.bill_id AND a.session=other.session
               JOIN va_legislator_vote_summary vs
                   ON vs.voter_name=other.voter_name AND vs.session=?
               WHERE a.voter_name = ? AND a.session=?
                 AND a.option IN ('yes','no')
                 AND other.option IN ('yes','no')
                 AND other.voter_name != a.voter_name
               GROUP BY other.voter_name
               HAVING total >= 15""",
            (session, resolved, session),
        ).fetchall()
        my_party = profile.get("party", "")
        same = sorted(
            [dict(voter_name=r["voter_name"], party=r["party"],
                  agree=r["agree"], total=r["total"], pct=r["pct"])
             for r in sim_rows if r["party"] == my_party],
            key=lambda x: x["pct"], reverse=True,
        )
        cross = sorted(
            [dict(voter_name=r["voter_name"], party=r["party"],
                  agree=r["agree"], total=r["total"], pct=r["pct"])
             for r in sim_rows if r["party"] != my_party],
            key=lambda x: x["pct"], reverse=True,
        )
        profile["voting_allies"] = {
            "same_party":  same[:8],
            "cross_party": cross[:5],
            "most_opposed": sorted(cross, key=lambda x: x["pct"])[:5],
            "caucus_outliers": sorted(
                [r for r in same if r["pct"] < 90], key=lambda x: x["pct"]
            )[:5],
        }
    except Exception:
        profile["voting_allies"] = None

    # ── Financial disclosure conflicts (securities vs committee chair roles) ──
    _ENTITY_SECTOR: dict[str, str] = {
        "abbvie": "Healthcare", "pfizer": "Healthcare", "merck": "Healthcare",
        "eli lilly": "Healthcare", "lilly": "Healthcare",
        "astrazeneca": "Healthcare", "astra zeneca": "Healthcare",
        "johnson": "Healthcare", "bristol myers": "Healthcare",
        "novartis": "Healthcare", "amgen": "Healthcare",
        "gilead": "Healthcare", "biogen": "Healthcare",
        "unitedhealth": "Healthcare", "united health": "Healthcare",
        "cvs health": "Healthcare", "anthem": "Healthcare",
        "cigna": "Healthcare", "humana": "Healthcare",
        "abbott": "Healthcare", "medtronic": "Healthcare",
        "alcon": "Healthcare", "regeneron": "Healthcare",
        "moderna": "Healthcare", "stryker": "Healthcare",
        "boston scientific": "Healthcare", "danaher": "Healthcare",
        "mckesson": "Healthcare", "cardinal health": "Healthcare",
        "exxon": "Energy", "chevron": "Energy",
        "conocophillips": "Energy", "duke energy": "Energy",
        "dominion energy": "Energy", "nextera": "Energy",
        "amer electric": "Energy", "american electric": "Energy",
        "halliburton": "Energy", "marathon oil": "Energy",
        "valero": "Energy", "phillips 66": "Energy",
        "pioneer natural": "Energy", "baker hughes": "Energy",
        "jpmorgan": "Finance", "jp morgan": "Finance",
        "wells fargo": "Finance", "bank of america": "Finance",
        "citigroup": "Finance", "goldman sachs": "Finance",
        "morgan stanley": "Finance", "blackrock": "Finance",
        "ameriprise": "Finance", "charles schwab": "Finance",
        "towne bank": "Finance", "truist": "Finance",
        "pnc financial": "Finance", "capital one": "Finance",
        "american express": "Finance", "berkshire": "Finance",
        "brinker capital": "Finance", "aflac": "Finance",
        "allstate": "Finance", "metlife": "Finance",
        "prudential": "Finance", "unum": "Finance",
        "visa inc": "Finance", "mastercard": "Finance",
        "microsoft": "Technology", "apple": "Technology",
        "amazon": "Technology", "alphabet": "Technology",
        "google": "Technology", "meta platforms": "Technology",
        "nvidia": "Technology", "intel": "Technology",
        "qualcomm": "Technology", "cisco": "Technology",
        "oracle": "Technology", "ibm": "Technology",
        "salesforce": "Technology", "adobe": "Technology",
        "broadcom": "Technology", "asml": "Technology",
        "analog devices": "Technology", "arista": "Technology",
        "evolv": "Technology", "amdocs": "Technology",
        "accenture": "Technology", "at&t": "Technology",
        "verizon": "Technology", "comcast": "Technology",
        "boeing": "Defense", "lockheed": "Defense",
        "raytheon": "Defense", "northrop": "Defense",
        "general dynamics": "Defense", "l3harris": "Defense",
        "leidos": "Defense", "booz allen": "Defense",
        "altria": "Alcohol/Tobacco", "philip morris": "Alcohol/Tobacco",
        "molson": "Alcohol/Tobacco", "constellation brands": "Alcohol/Tobacco",
        "anheuser": "Alcohol/Tobacco", "diageo": "Alcohol/Tobacco",
        "realty income": "Real Estate", "prologis": "Real Estate",
        "welltower": "Real Estate", "avalonbay": "Real Estate",
        "simon property": "Real Estate", "public storage": "Real Estate",
    }
    _CONFLICT_CMTE_KWS: dict[str, list[str]] = {
        "Healthcare": ["health", "behavioral health", "health professions"],
        "Energy":     ["energy", "agriculture", "natural resources",
                       "transportation", "highway safety"],
        "Finance":    ["finance", "appropriations", "commerce",
                       "labor and commerce"],
        "Technology": ["technology", "communications"],
        "Defense":    ["public safety", "firearms"],
        "Alcohol/Tobacco": ["abc", "gaming"],
        "Real Estate": ["housing"],
    }

    def _classify_entity(ename: str) -> str:
        el = ename.lower()
        for kw, sector in _ENTITY_SECTOR.items():
            if kw in el:
                return sector
        return ""

    try:
        disc_variants = _person_name_variants(
            conn, "legislator_financial_disclosures", "legislator_name", resolved)
        sec_rows = conn.execute(
            f"""SELECT entity_name, sector, amount_range
               FROM legislator_financial_disclosures
               WHERE legislator_name IN ({",".join("?" * len(disc_variants))})
                 AND part = 'securities'
                 AND entity_name NOT IN ('NONE DISCLOSED','REDACTED','N/A','','None')
                 AND entity_name IS NOT NULL""",
            disc_variants,
        ).fetchall() if disc_variants else []
        disc_cmte_variants = _person_name_variants(
            conn, "va_committee_assignments", "member_name", resolved)
        cmte_chair_rows = conn.execute(
            f"""SELECT committee, role FROM va_committee_assignments
               WHERE role IN ('chair','vice_chair')
                 AND member_name IN ({",".join("?" * len(disc_cmte_variants))})""",
            disc_cmte_variants,
        ).fetchall() if disc_cmte_variants else []

        holdings_by_sector: dict[str, list[dict]] = {}
        for row in sec_rows:
            entity = (row["entity_name"] or "").strip()
            sector = (row["sector"] or "").strip()
            if not sector:
                sector = _classify_entity(entity)
            if sector:
                holdings_by_sector.setdefault(sector, [])
                if not any(h["entity"] == entity for h in holdings_by_sector[sector]):
                    holdings_by_sector[sector].append({
                        "entity": entity,
                        "amount_range": row["amount_range"] or "",
                    })

        conflicts: list[dict] = []
        for sector, holdings in holdings_by_sector.items():
            cmte_kws = _CONFLICT_CMTE_KWS.get(sector, [])
            for cr in cmte_chair_rows:
                cmte_lower = cr["committee"].lower()
                if any(kw in cmte_lower for kw in cmte_kws):
                    conflicts.append({
                        "sector":    sector,
                        "committee": cr["committee"],
                        "role":      cr["role"],
                        "holdings":  holdings[:5],
                    })

        profile["financial_conflicts"] = conflicts
        profile["financial_holdings"] = {k: v[:5] for k, v in holdings_by_sector.items()}
    except Exception:
        profile["financial_conflicts"] = []
        profile["financial_holdings"] = {}

    # ── Lobbyist connections (principal-sector vs committee oversight) ─────────
    _PRINCIPAL_SECTOR: dict[str, str] = {
        # Healthcare
        "health": "Healthcare", "hospital": "Healthcare", "medical": "Healthcare",
        "dental": "Healthcare", "pharma": "Healthcare", "pharmaceutical": "Healthcare",
        "nursing": "Healthcare", "cancer": "Healthcare", "diabetes": "Healthcare",
        "behavioral health": "Healthcare", "mental health": "Healthcare",
        "heart association": "Healthcare", "physical therapy": "Healthcare",
        "pharmacy": "Healthcare", "aarp": "Healthcare", "physician": "Healthcare",
        "optom": "Healthcare", "astrazeneca": "Healthcare", "amgen": "Healthcare",
        "biogen": "Healthcare", "pfizer": "Healthcare", "boehringer": "Healthcare",
        "biotechnology": "Healthcare",
        # Energy
        "electric power": "Energy", "electric coop": "Energy",
        "power agency": "Energy", "solar": "Energy", "natural gas": "Energy",
        "petroleum": "Energy", "nuclear": "Energy", "clean power": "Energy",
        "clean energy": "Energy", "renewable": "Energy", "coal": "Energy",
        "dominion": "Energy", "appalachian power": "Energy", "atmos": "Energy",
        "bloom energy": "Energy",
        # Finance
        "bank": "Finance", "financial": "Finance", "insurance": "Finance",
        "credit union": "Finance", "lending": "Finance", "mortgage": "Finance",
        "life insurer": "Finance", "life insurance": "Finance",
        "casualty": "Finance", "aflac": "Finance",
        # Technology (specific names, not generic "technology")
        "google": "Technology", "amazon.com": "Technology",
        "apple inc": "Technology", "microsoft": "Technology",
        "tiktok": "Technology", "salesforce": "Technology",
        "waymo": "Technology", "autonomous vehicle": "Technology",
        "artificial intelligence": "Technology", "broadband": "Technology",
        "software": "Technology", "at&t": "Technology",
        "digital innovation": "Technology", "robotics": "Technology",
        "technology council": "Technology", "accenture": "Technology",
        "cyber": "Technology",
        # Agriculture
        "agriculture": "Agriculture", "equine": "Agriculture",
        "livestock": "Agriculture", "fisheries": "Agriculture",
        "seafood": "Agriculture", "dairy": "Agriculture",
        "harvest": "Agriculture", "forestry": "Agriculture",
        # Gambling
        "casino": "Gambling", "gaming corporation": "Gambling",
        "gaming llc": "Gambling", "gaming, llc": "Gambling",
        "sports betting": "Gambling", "lottery": "Gambling",
        "bally": "Gambling", "boyd gaming": "Gambling",
        # Alcohol / Tobacco
        "beer wholesal": "Alcohol/Tobacco", "wine": "Alcohol/Tobacco",
        "spirits": "Alcohol/Tobacco", "brewery": "Alcohol/Tobacco",
        "anheuser": "Alcohol/Tobacco", "altria": "Alcohol/Tobacco",
        "tobacco": "Alcohol/Tobacco", "distill": "Alcohol/Tobacco",
        "vapor": "Alcohol/Tobacco",
        # Transportation
        "highway contractor": "Transportation", "transit": "Transportation",
        "railroad": "Transportation", "airline": "Transportation",
        "trucking": "Transportation", "motor vehicle": "Transportation",
        "transurban": "Transportation",
        # Real Estate
        "real estate": "Real Estate", "realt": "Real Estate",
        "homebuilder": "Real Estate", "apartment and office": "Real Estate",
        "airbnb": "Real Estate",
        # Education
        "education": "Education", "community college": "Education",
        "university": "Education",
        # Defense / Firearms
        "firearm": "Defense", "gun": "Defense", "law enforcement": "Defense",
        # Labor
        "afl-cio": "Labor",
    }
    _CMTE_LOBBY_MAP: dict[str, list[str]] = {
        "Healthcare":    ["health", "behavioral health", "health professions"],
        "Energy":        ["energy", "natural resources", "transportation infrastructure",
                          "highway safety"],
        "Finance":       ["finance", "appropriations", "commerce",
                          "labor and commerce"],
        "Technology":    ["technology", "communications"],
        "Agriculture":   ["agriculture", "natural resources"],
        "Gambling":      ["abc", "gaming"],
        "Alcohol/Tobacco": ["abc"],
        "Transportation": ["transportation", "highway safety"],
        "Real Estate":   ["housing", "counties, cities"],
        "Education":     ["education"],
        "Defense":       ["public safety", "firearms"],
        "Labor":         ["labor", "commerce and labor"],
    }

    try:
        lob_variants = _person_name_variants(
            conn, "va_committee_assignments", "member_name", resolved)
        lob_cmte_rows = conn.execute(
            f"""SELECT committee, role FROM va_committee_assignments
               WHERE role IN ('chair','vice_chair')
                 AND member_name IN ({",".join("?" * len(lob_variants))})""",
            lob_variants,
        ).fetchall() if lob_variants else []

        lob_connections: list[dict] = []
        if lob_cmte_rows:
            all_principals = conn.execute(
                """SELECT principal_name, COUNT(*) AS n
                   FROM lobbyist_registrations
                   WHERE year_range = '2026-2027' AND status = 'Approved'
                   GROUP BY principal_name""",
            ).fetchall()

            def _classify_principal(pname: str) -> str:
                pl = pname.lower()
                for kw, sec in _PRINCIPAL_SECTOR.items():
                    if kw in pl:
                        return sec
                return ""

            principals_by_sector: dict[str, list[tuple]] = {}
            for prow in all_principals:
                sec = _classify_principal(prow["principal_name"])
                if sec:
                    principals_by_sector.setdefault(sec, []).append(
                        (prow["principal_name"], prow["n"])
                    )

            for cr in lob_cmte_rows:
                cmte_lower = cr["committee"].lower()
                for sector, cmte_kws in _CMTE_LOBBY_MAP.items():
                    if any(kw in cmte_lower for kw in cmte_kws):
                        plist = principals_by_sector.get(sector, [])
                        if plist:
                            top = sorted(plist, key=lambda x: x[1], reverse=True)[:6]
                            total_lob = sum(p[1] for p in plist)
                            lob_connections.append({
                                "committee":       cr["committee"],
                                "role":            cr["role"],
                                "sector":          sector,
                                "principal_count": len(plist),
                                "lobbyist_count":  total_lob,
                                "top_principals": [
                                    {"name": p[0], "count": p[1]} for p in top
                                ],
                            })

        profile["lobbyist_connections"] = lob_connections
    except Exception:
        profile["lobbyist_connections"] = []

    # ── Bill patron topics vs vote record (floor speech proxy) ────────────────
    _STATE_NOISE: tuple[str, ...] = (
        "commend", "celebrat", "honor", "recogni", "in memoriam",
        "life of", "congratul", "designat", "proclaim",
    )
    _STATE_TOPIC_MAP: dict[str, list[str]] = {
        "Healthcare":   ["health", "medicaid", "prescription", "opioid", "mental health",
                         "cancer", "hospital", "insurance coverage", "pharma", "drug pricing",
                         "nursing", "dental", "behavioral health", "nurse", "physician",
                         "reproductive", "abortion", "contracepti"],
        "Housing":      ["housing", "rent", "affordable housing", "homeless", "mortgage",
                         "eviction", "tenant", "landlord", "zoning", "short-term rental"],
        "Environment":  ["climate", "environment", "clean energy", "renewable", "emissions",
                         "conservation", "pollution", "carbon", "solar", "wind energy",
                         "chesapeake", "water quality", "stormwater", "forest"],
        "Education":    ["education", "school", "student loan", "college", "university",
                         "workforce training", "higher education", "teacher", "curriculum",
                         "early childhood"],
        "Labor/Economy": ["labor", "worker", "wage", "minimum wage", "employ", "union",
                          "small business", "manufactur", "econom", "trade", "tariff",
                          "paid leave", "noncompete"],
        "Taxation":     ["tax", "revenue", "fiscal", "budget", "appropriation",
                         "income tax", "property tax"],
        "Gun Policy":   ["gun", "firearm", "second amendment", "background check", "weapon"],
        "Criminal Justice": ["criminal", "crime", "sentenc", "prison", "jail", "parole",
                             "probation", "police", "law enforcement", "expunge", "juvenile"],
        "Transportation": ["highway", "transportation", "road", "transit", "rail", "airport",
                           "bridge", "vehicle", "driver", "truck", "bicycle", "pedestrian"],
        "Technology":   ["technology", "artificial intelligence", "cyber", "internet",
                         "data privacy", "broadband", "semiconductor", "autonomous"],
        "Gambling":     ["gaming", "casino", "lottery", "sports betting", "wagering"],
        "Alcohol/Tobacco": ["alcohol", "beer", "wine", "spirits", "distill",
                            "tobacco", "vapor"],
        "Voting/Elections": ["election", "voter", "ballot", "redistrict", "campaign finance"],
    }

    def _classify_bill_title(title: str) -> str:
        tl = title.lower()
        for topic, kws in _STATE_TOPIC_MAP.items():
            if any(kw in tl for kw in kws):
                return topic
        return ""

    def _is_noise_bill(title: str) -> bool:
        tl = title.lower()
        return any(n in tl for n in _STATE_NOISE)

    def _extract_primary_sponsor(desc: str) -> str:
        if not desc:
            return ""
        import re as _re
        m = _re.search(
            r"Sponsor\(s\):\s*(.+?)(?:\nStatus:|\.\nStatus:|\.\nLatest)",
            desc, _re.DOTALL,
        )
        if not m:
            m = _re.search(r"Sponsor\(s\):\s*(.+?)\.?\s*\n", desc)
        if not m:
            return ""
        raw = m.group(1).strip().rstrip(".")
        sponsors = [s.strip() for s in raw.split(",") if s.strip()]
        return sponsors[0] if sponsors else ""

    try:
        bp_last = resolved.strip().split()[-1]

        all_state_bills = conn.execute(
            "SELECT bill_number, title, description FROM va_bills WHERE session='2026'"
        ).fetchall()

        bills_by_topic: dict[str, list[dict]] = {}
        for brow in all_state_bills:
            t = brow["title"] or ""
            if _is_noise_bill(t):
                continue
            topic = _classify_bill_title(t)
            if not topic:
                continue
            primary = _extract_primary_sponsor(brow["description"] or "")
            if primary:
                bills_by_topic.setdefault(topic, []).append({
                    "bill": brow["bill_number"],
                    "title": t,
                    "primary": primary,
                })

        patron_topics: list[dict] = []
        bp_votes = conn.execute(
            """SELECT bill_id, option FROM va_legislator_recent_votes
               WHERE session='2026' AND option IN ('yes','no') AND voter_name=?""",
            (resolved,),
        ).fetchall()
        voted_map = {r["bill_id"]: r["option"] for r in bp_votes}

        for topic, topic_bills in bills_by_topic.items():
            my_bills = [b for b in topic_bills if bp_last.lower() in b["primary"].lower()]
            if not my_bills:
                continue
            my_ids = {b["bill"] for b in my_bills}
            other = [b for b in topic_bills if b["bill"] not in my_ids and b["bill"] in voted_map]
            if other:
                yes = sum(1 for b in other if voted_map[b["bill"]] == "yes")
                pct = round(100 * yes / len(other))
                flag = pct < 40 and len(my_bills) >= 2 and len(other) >= 2
            else:
                pct, flag = None, False
            patron_topics.append({
                "topic":         topic,
                "patron_count":  len(my_bills),
                "patron_bills":  [{"bill": b["bill"], "title": b["title"]} for b in my_bills[:4]],
                "yes_pct":       pct,
                "vote_sample":   len(other) if other else 0,
                "flag":          flag,
            })

        patron_topics.sort(key=lambda x: (-x["patron_count"], x["topic"]))
        profile["bill_patron_topics"] = patron_topics
    except Exception:
        profile["bill_patron_topics"] = []

    return profile


# ── JSON API ──────────────────────────────────────────────────────────────────

@router.get("/api/legislators")
def api_legislators(
    chamber: str = Query(default=""),
    party: str = Query(default=""),
):
    """Return all VA legislators with aggregated vote/bill stats."""
    conn = _polls_conn()
    try:
        data = _legislators_list(conn)
        if chamber:
            data = [r for r in data if r["chamber"].lower() == chamber.lower()]
        if party:
            data = [r for r in data if r["party"].lower() == party.lower()]
        return {"legislators": data, "total": len(data)}
    finally:
        conn.close()


@router.get("/api/legislators/{name}")
def api_legislator_profile(name: str, session: str = Query(default="2026")):
    """Return full profile for one legislator: votes, bills, co-sponsorships."""
    name = unquote(name).strip()
    conn = _polls_conn()
    try:
        return _fetch_profile(conn, name, session)
    finally:
        conn.close()


@router.get("/api/legislators/{name}/sector-detail/{sector}")
def api_sector_contributors(name: str, sector: str):
    """Top contributors in a specific donor sector for one legislator."""
    from build_va_state_finance import classify_sector

    name   = unquote(name).strip()
    sector = unquote(sector).strip()
    conn   = _polls_conn()
    try:
        cand_key = _name_key(name)
        if not cand_key:
            return {"sector": sector, "contributors": [], "sector_total": 0}

        variants = [r["candidate_name"] for r in conn.execute(
            "SELECT candidate_name FROM cf_candidate_keys WHERE cand_key = ?",
            (cand_key,)
        ).fetchall()]

        if not variants:
            return {"sector": sector, "contributors": [], "sector_total": 0}

        placeholders = ",".join("?" * len(variants))
        rows = conn.execute(f"""
            SELECT lower(last_or_company)                  AS grp_key,
                   MAX(last_or_company)                    AS name,
                   MAX(occupation)                         AS occupation,
                   MAX(COALESCE(NULLIF(employer,''), ''))  AS employer,
                   MAX(state_code)                         AS state,
                   SUM(amount)                             AS total,
                   COUNT(*)                                AS records
            FROM va_cf_schedule_a
            WHERE candidate_name IN ({placeholders})
            GROUP BY lower(last_or_company)
            ORDER BY total DESC
        """, variants).fetchall()

        contributors = []
        sector_total = 0.0
        for r in rows:
            s = classify_sector(r["occupation"] or "", r["employer"] or "", r["name"] or "")
            if s != sector:
                continue
            sector_total += r["total"] or 0
            contributors.append({
                "name":       r["name"],
                "occupation": r["occupation"] or "",
                "state":      r["state"] or "",
                "total":      round(r["total"] or 0, 2),
                "records":    r["records"],
            })

        return {
            "sector":       sector,
            "contributors": contributors[:25],
            "sector_total": round(sector_total, 2),
        }
    finally:
        conn.close()


# ── HTML pages ────────────────────────────────────────────────────────────────

@router.get("/legislators", response_class=HTMLResponse)
def legislators_page():
    """Sortable/filterable table of all 145 VA legislators."""
    conn = _polls_conn()
    try:
        data = _legislators_list(conn)
    except Exception:
        data = []   # table not yet built on this deployment — show empty page
    finally:
        conn.close()
    return _inject("legislators.html", "_LEG_DATA", data)


@router.get("/legislators/{name}", response_class=HTMLResponse)
def legislator_profile_page(name: str, session: str = Query(default="2026")):
    """Individual legislator profile: vote record, bills, co-sponsorships."""
    name = unquote(name).strip()
    conn = _polls_conn()
    try:
        profile = _fetch_profile(conn, name, session)
        return _inject("legislator_profile.html", "_PROFILE_DATA", profile)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Profile temporarily unavailable: {exc}",
        ) from exc
    finally:
        conn.close()
