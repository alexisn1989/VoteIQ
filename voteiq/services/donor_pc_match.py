"""
Cross-references Planning Commission rezoning/CUP applicants against
campaign donor records (va_cf_schedule_a), scoped to the 2 cities whose PC
agenda tables carry a real `council_hearing_date` -- Portsmouth and Newport
News -- because that's the only reliable join from a PC case to its actual
recorded City Council vote (same date-spelling + title-fuzzy-match approach
as the PC->Council outcome chain in
voteiq/services/context/builders/local.py, lines ~3331-3388).

IMPORTANT — what a "match" means here: a fuzzy name match between an
applicant/property-owner string and a campaign-finance donor record is a
LEAD, not a finding. Company names are abbreviated/varied across filings,
common surnames collide, and an LLC's donation doesn't prove any specific
council member knew who was behind it. Every result carries a `confidence`
("high" only for a distinctive company-style name match; "low" for a
personal-name last-name-only match) and should be presented to a reader as
"worth an independent look," never as a proven conflict of interest.
"""
from __future__ import annotations

import difflib
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.getenv("DATA_DIR", str(BASE_DIR))) / "polls.db"

# (pc_table, council_votes_table, city_display)
CITIES: list[tuple[str, str, str]] = [
    ("portsmouth_pc_upcoming_agenda", "portsmouth_council_votes", "Portsmouth"),
    ("newport_news_pc_upcoming_agenda", "newport_news_council_votes", "Newport News"),
]

_COMPANY_HINT_RE = re.compile(
    r"\b(LLC|L\.L\.C\.|INC|CORP|CO\.|COMPANY|HOMES|BUILDERS|DEVELOPMENT|"
    r"PARTNERS|GROUP|PROPERTIES|HOLDINGS|CONSTRUCTION|REALTY|VENTURES|AUTHORITY)\b",
    re.I,
)
# "M. Venable", "K. Alexander" -- initial(s) + surname, the shape Portsmouth's
# source PDFs use for individual applicants.
_PERSONAL_NAME_RE = re.compile(r"^([A-Z]\.?\s*){1,2}([A-Z][a-zA-Z'\-]+)$")

_STOP_WORDS_RE = re.compile(
    r"\b(pac|political action committee|fund|inc|llc|l\.l\.c\.|corp|co\.|"
    r"company|association|committee|foundation|authority|and|for)\b\.?",
    re.I,
)


def _vote_date_spellings(iso_date: str) -> list[str]:
    """Duplicated from builders/local.py rather than imported -- that module
    pulls in the full chat-context import graph, too heavy for a standalone
    script. Keep in sync if the source format list there changes."""
    try:
        d = datetime.strptime(iso_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return [iso_date]
    mon_abbr, mon_full = d.strftime("%b"), d.strftime("%B")
    return list({
        iso_date,
        f"{mon_abbr} {d.day:02d}, {d.year}", f"{mon_abbr} {d.day}, {d.year}",
        f"{mon_full} {d.day:02d}, {d.year}", f"{mon_full} {d.day}, {d.year}",
        f"{mon_abbr} {d.day:02d} {d.year}", f"{mon_abbr} {d.day} {d.year}",
        f"{d.month:02d}/{d.day:02d}/{d.year}", f"{d.month}/{d.day}/{d.year}",
    })


def _brand_key(name: str) -> str:
    """First 2 significant words (>=4 chars) after stripping entity-type
    suffixes and punctuation. Empty string if the name doesn't reduce to
    anything specific."""
    s = _STOP_WORDS_RE.sub(" ", name or "")
    s = re.sub(r"[^\w\s-]", " ", s).strip()  # strip commas/periods/etc.
    words = [w for w in s.split() if len(w) >= 4][:2]
    return " ".join(words).lower() if words else ""


def _classify_name(name: str) -> str:
    """'company' | 'personal' | 'unclear'."""
    name = (name or "").strip()
    if not name:
        return "unclear"
    if _COMPANY_HINT_RE.search(name) or name.isupper():
        return "company"
    if _PERSONAL_NAME_RE.match(name):
        return "personal"
    return "unclear"


def _find_donors(conn: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    """Search va_cf_schedule_a for donations plausibly from `name`."""
    kind = _classify_name(name)
    if kind == "company":
        key = _brand_key(name)
        words = key.split()
        # A single significant word is enough to search on (many real
        # company names, e.g. "Ellicott 77 LLC", reduce to just one word
        # >=4 chars once the year/lot number and LLC/Inc are stripped) --
        # find_matches() separately checks how many *distinct* companies
        # a key actually returns and downgrades confidence if it's noisy.
        if len(words) < 1:
            return []
        clause = " AND ".join("lower(last_or_company) LIKE ?" for _ in words)
        params = [f"%{w}%" for w in words]
        return conn.execute(
            f"SELECT * FROM va_cf_schedule_a WHERE {clause} AND is_individual = 0",
            params,
        ).fetchall()
    if kind == "personal":
        m = _PERSONAL_NAME_RE.match(name.strip())
        surname = m.group(2)
        initial = m.group(1).strip()[:1]
        if not initial:
            return []
        # Surname alone is far too noisy (common surnames -> thousands of
        # rows) -- also require the donor record's first name to start with
        # the same initial. Still weak (many "K ___"s share an initial),
        # which is exactly why this branch is always confidence="low".
        return conn.execute(
            "SELECT * FROM va_cf_schedule_a "
            "WHERE lower(last_or_company) = lower(?) AND is_individual = 1 "
            "AND lower(substr(first_name, 1, 1)) = lower(?)",
            (surname, initial),
        ).fetchall()
    return []


def _council_member_last_names(votes_json_members: dict) -> dict[str, str]:
    """{lower(last_name): vote_choice} from a votes_json dict."""
    return {k.strip().lower(): v for k, v in (votes_json_members or {}).items()}


def find_matches(city_tables: list[tuple[str, str, str]] | None = None) -> list[dict]:
    """Run the full cross-reference for the given (or all default) cities.
    Returns a list of match dicts, most-recent case first. Each dict has:
    city, item_ref, case_title, applicant_name, matched_role
    ("applicant"/"property_owner"), donor_record (name/amount/date),
    council_member, member_vote, vote_result, vote_count, confidence."""
    import json as _json

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    results: list[dict] = []

    for pc_table, votes_table, city in (city_tables or CITIES):
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (pc_table,)
        ).fetchone():
            continue
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (votes_table,)
        ).fetchone():
            continue

        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({pc_table})")}
        has_owner = "property_owner" in cols

        select_cols = "item_ref, meeting_date, title, applicant_name, council_hearing_date"
        if has_owner:
            select_cols += ", property_owner"
        pc_rows = conn.execute(f"""
            SELECT {select_cols} FROM {pc_table}
            WHERE council_hearing_date IS NOT NULL
              AND council_hearing_date < date('now')
            ORDER BY council_hearing_date DESC
        """).fetchall()

        for pc in pc_rows:
            names_to_check = [("applicant", pc["applicant_name"])]
            if has_owner:
                names_to_check.append(("property_owner", pc["property_owner"]))

            # Join PC case -> recorded council vote (same fuzzy approach as
            # the chat outcome chain).
            spellings = _vote_date_spellings(pc["council_hearing_date"])
            ph = ",".join("?" for _ in spellings)
            v_rows = conn.execute(f"""
                SELECT title, result, vote_count, votes_json FROM {votes_table}
                WHERE meeting_date IN ({ph})
            """, spellings).fetchall()
            if not v_rows:
                continue
            best_ratio, best = 0.0, None
            for v in v_rows:
                r = difflib.SequenceMatcher(
                    None, (pc["title"] or "").lower()[:120], (v["title"] or "").lower()[:120],
                ).ratio()
                if r > best_ratio:
                    best_ratio, best = r, v
            if best_ratio < 0.4 or best is None:
                continue

            try:
                member_votes = _council_member_last_names(_json.loads(best["votes_json"] or "{}"))
            except Exception:
                member_votes = {}
            if not member_votes:
                continue

            for role, raw_name in names_to_check:
                if not raw_name or not raw_name.strip():
                    continue
                kind = _classify_name(raw_name)
                if kind == "unclear":
                    continue
                donors = _find_donors(conn, raw_name)
                if kind == "company":
                    # A company brand_key that matches many distinct
                    # last_or_company values means the key was too generic
                    # to be a specific match -- downgrade confidence rather
                    # than treat it the same as a one-company hit.
                    distinct_companies = {(d["last_or_company"] or "").lower() for d in donors}
                    confidence = "high" if len(distinct_companies) <= 3 else "low"
                else:
                    confidence = "low"
                for d in donors:
                    candidate = d["candidate_name"] or ""
                    cand_last = candidate.strip().split()[-1].lower() if candidate.strip() else ""
                    if cand_last not in member_votes:
                        continue
                    results.append({
                        "city": city,
                        "item_ref": pc["item_ref"],
                        "case_title": pc["title"],
                        "matched_role": role,
                        "matched_name": raw_name,
                        "donor_name": (d["first_name"] or "") + " " + (d["last_or_company"] or ""),
                        "donor_amount": d["amount"],
                        "donor_date": d["transaction_date"],
                        "donor_cycle": d["election_cycle"],
                        "council_member": candidate,
                        "member_vote": member_votes[cand_last],
                        "vote_result": best["result"],
                        "vote_count": best["vote_count"],
                        "council_hearing_date": pc["council_hearing_date"],
                        "confidence": confidence,
                    })

    conn.close()

    # va_cf_schedule_a itself carries genuine duplicate transaction rows
    # (same donor/candidate/amount/date filed more than once) -- collapse
    # identical findings so one real donation isn't shown as several.
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for r in results:
        key = tuple(r.items())
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped
