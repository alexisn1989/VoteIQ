#!/usr/bin/env python3
"""
backfill_missing_finance.py

Backfills campaign_finance_summary rows for the VA legislators that the
two-stage SBE pipeline (build_va_state_finance.py -> build_campaign_finance_summary.py)
failed to match.

Root cause of the gap: the SBE Report.csv candidate names carry full middle
names and honorifics ("Aaron Roosevelt Rouse", "Delegate Luke Edward Torian",
"Lillie Louise Lucas") that the resolver could not bridge to the canonical
legislator names ("Aaron R. Rouse", etc.). The raw contributions, however, are
already present in polls.db -> va_cf_schedule_a (cycles through 2026).

This script reads the raw table directly, aggregates sectors + top donors, and
upserts straight into campaign_finance_summary -- bypassing the enriched
va_sbe_contributions / members tables (which only live on the production disk).

Matching strategy (intentionally conservative to avoid cross-matching relatives
such as Gretchen vs David Bulova or Kirk vs Jeremy McPike):
  * first+last key with a nickname map (same approach as the live app), OR
  * an explicit alias listing the exact raw candidate_name(s) for people who
    go by a middle name / nickname that the key cannot recover.

Usage:
    python backfill_missing_finance.py            # dry run: report only
    python backfill_missing_finance.py --write     # write rows to polls.db
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from build_va_state_finance import classify_sector

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
POLLS_DB = DATA_DIR / "polls.db"
if not POLLS_DB.is_file():
    POLLS_DB = BASE_DIR / "polls.db"

SESSION = "2026"

# ── Name normalization (mirrors voteiq/api/routes/legislators.py) ──────────────
_NICKNAMES = {
    "david": "dave", "joseph": "joe", "joshua": "josh", "william": "bill",
    "robert": "bob", "michael": "mike", "christopher": "chris", "daniel": "dan",
    "thomas": "tom", "kathleen": "kathy", "jennifer": "jen", "matthew": "matt",
    "richard": "rick", "edward": "ed", "anthony": "tony", "nicholas": "nick",
    "samuel": "sam", "benjamin": "ben", "andrew": "andy", "stephen": "steve",
    "steven": "steve", "patricia": "pat", "deborah": "debbie", "elizabeth": "liz",
}
_PREFIX = re.compile(
    r"^(mr|mrs|ms|dr|hon|rev|judge|senator|delegate|del|the honorable|sheriff|prof)\.?\s+",
    re.I,
)


def _name_key(full_name: str) -> tuple[str, str] | None:
    """Return a (first, last) key for fuzzy matching, or None if unusable."""
    n = re.sub(r'"[^"]*"', "", full_name)                     # drop "Buddy" etc.
    n = re.sub(r"\b(Jr|Sr|II|III|IV)\b", "", n, flags=re.I)   # drop suffixes
    n = _PREFIX.sub("", n.strip())                            # drop honorifics
    n = re.sub(r"[.,]", " ", n)                               # punctuation -> space
    tokens = [t for t in n.split() if len(t) > 1]             # drop single-letter initials
    if len(tokens) < 2:
        return None
    first = _NICKNAMES.get(tokens[0].lower(), tokens[0].lower())
    last = tokens[-1].lower()
    return (first, last)


# ── Explicit aliases for people who file under a middle name / nickname ────────
# Maps the vote_summary name -> the EXACT raw candidate_name(s) in
# va_cf_schedule_a. Used only when the first+last key cannot recover them.
# Each was verified by inspecting the distinct raw candidate list; entries
# deliberately EXCLUDE same-surname relatives (e.g. Jeremy McPike, Sheriff
# Robert Joseph Deeds, Lisa Lucas-Burke).
_ALIASES: dict[str, list[str]] = {
    'C.E. Cliff Hayes, Jr.':   ['Mr. Clifton Eugene Hayes Jr.'],
    'Charlie Schmidt':         ['Charles  Schmidt', 'Charles Schmidt'],
    'J.D. "Danny" Diggs':      ['Mr. Joseph Daniel Diggs'],
    'James A. "Jay" Leftwich': ['James Jay" A. Leftwich Jr."'],
    'Kirk McPike':             ['Mr Richard Kirk McPike', 'Richard Kirk McPike'],
    'L. Louise Lucas':         ['Lillie Louise Lucas'],
    'R. Creigh Deeds':         ['Robert Creigh Deeds'],
    'Timmy French':            ['Timothy Frank French'],
    # Thomas C. Wright Jr. and Thomas Judson Wright V share the ('tom','wright')
    # key — pin to Cole Wright only to avoid merging two different legislators.
    'Thomas C. Wright, Jr.':   ['Mr. Thomas Cole Wright Jr'],
    # Lily V. Franklin files under her formal first name "Lillian".
    'Lily V. Franklin':        ['Lillian  Franklin'],
    # Will Davis (Eastern Shore delegate) files as "William Pearson Davis".
    # William Savoi Davis ($3,850 total) is a different person.
    'Will Davis':              ['William Pearson Davis'],
    # JJ Singh won a special election Jan 2024; SBE data starts 2024 under "Jas Singh".
    # Moderate confidence — timing of data matches his election entry exactly.
    'JJ Singh':                ['Jas  Singh'],
}


def _contrib_name(first: str, last_company: str, is_individual: int) -> str:
    if is_individual:
        return re.sub(r"\s+", " ", f"{first} {last_company}").strip()
    return last_company.strip()


def build_summary(conn: sqlite3.Connection, raw_names: list[str]) -> dict | None:
    """Aggregate va_cf_schedule_a rows for the given candidate names."""
    placeholders = ",".join("?" for _ in raw_names)
    rows = conn.execute(f"""
        SELECT first_name, last_or_company, employer, occupation,
               is_individual, amount, election_cycle
        FROM va_cf_schedule_a
        WHERE candidate_name IN ({placeholders}) AND amount > 0
    """, raw_names).fetchall()
    if not rows:
        return None

    sector_total: dict[str, float] = defaultdict(float)
    sector_count: dict[str, int] = defaultdict(int)
    donor_total: dict[tuple, float] = defaultdict(float)
    donor_count: dict[tuple, int] = defaultdict(int)
    donor_sector: dict[tuple, str] = {}
    cycles: set[str] = set()
    total_raised = 0.0

    for r in rows:
        amt = float(r["amount"])
        is_ind = int(r["is_individual"] or 0)
        company = r["last_or_company"] if not is_ind else ""
        sector = classify_sector(r["occupation"] or "", r["employer"] or "", company or "")
        cname = _contrib_name(r["first_name"] or "", r["last_or_company"] or "", is_ind)
        key = (cname.lower(), (r["employer"] or "").lower())

        total_raised += amt
        sector_total[sector] += amt
        sector_count[sector] += 1
        donor_total[key] += amt
        donor_count[key] += 1
        donor_sector.setdefault(key, sector)
        if cname and not donor_meta.get(key):
            donor_meta[key] = (cname, r["employer"] or "")
        if r["election_cycle"]:
            cycles.add(str(r["election_cycle"]))

    by_sector = sorted(
        ({
            "sector": s,
            "total": round(sector_total[s], 2),
            "donor_count": sector_count[s],
            "avg_donation": round(sector_total[s] / max(sector_count[s], 1), 2),
        } for s in sector_total),
        key=lambda d: d["total"], reverse=True,
    )
    top_donors = sorted(
        ({
            "contributor_name": donor_meta[k][0],
            "employer": donor_meta[k][1],
            "sector": donor_sector[k],
            "total": round(donor_total[k], 2),
            "count": donor_count[k],
        } for k in donor_total),
        key=lambda d: d["total"], reverse=True,
    )[:20]
    cycle_list = sorted(cycles, reverse=True)

    top_sector = by_sector[0]["sector"] if by_sector else None
    top_sector_pct = (
        round(by_sector[0]["total"] / total_raised * 100, 1)
        if by_sector and total_raised else None
    )
    return {
        "total_raised": round(total_raised, 2),
        "top_sector": top_sector,
        "top_sector_pct": top_sector_pct,
        "latest_cycle": cycle_list[0] if cycle_list else None,
        "by_sector": by_sector,
        "top_donors": top_donors,
        "cycles": cycle_list,
        "n_contributions": len(rows),
    }


# module-level scratch for donor display names
donor_meta: dict[tuple, tuple[str, str]] = {}


def main(write: bool) -> None:
    conn = sqlite3.connect(str(POLLS_DB), timeout=30)
    conn.row_factory = sqlite3.Row

    # Existing finance coverage (by name key)
    fin_keys = {
        _name_key(r["name"]) for r in conn.execute(
            "SELECT name FROM campaign_finance_summary")
    }
    # Chamber/party come from vote_summary (authoritative for the 2026 roster)
    roster = {
        r["voter_name"]: r for r in conn.execute(
            "SELECT voter_name, chamber, party FROM va_legislator_vote_summary "
            "WHERE session = ?", (SESSION,))
    }
    # Optional district/lis_id from legislators table
    leg_meta: dict[tuple, sqlite3.Row] = {}
    for r in conn.execute("SELECT lis_id, name, chamber, district, party FROM legislators"):
        k = _name_key(r["name"])
        if k:
            leg_meta.setdefault(k, r)

    # Raw candidate index: first+last key -> set of candidate_name
    raw_index: dict[tuple, set] = defaultdict(set)
    for r in conn.execute("SELECT DISTINCT candidate_name FROM va_cf_schedule_a"):
        k = _name_key(r["candidate_name"])
        if k:
            raw_index[k].add(r["candidate_name"])

    missing = [n for n in roster if _name_key(n) not in fin_keys]
    print(f"2026 roster: {len(roster)}   missing from finance: {len(missing)}\n")

    built = skipped = 0
    rows_to_write = []

    for name in sorted(missing):
        key = _name_key(name)
        donor_meta.clear()
        if name in _ALIASES:
            raw_names = list(_ALIASES[name])
            src = "alias"
        elif key in raw_index:
            raw_names = sorted(raw_index[key])
            src = "key"
        else:
            print(f"  [SKIP] {name:32s} - no SBE candidate found")
            skipped += 1
            continue

        summary = build_summary(conn, raw_names)
        if not summary or not summary["by_sector"]:
            print(f"  [SKIP] {name:32s} - no contributions in {raw_names}")
            skipped += 1
            continue

        built += 1
        info = roster[name]
        meta = leg_meta.get(key)
        if meta and meta["lis_id"]:
            leg_id = meta["lis_id"]
        elif key:
            leg_id = f"va_sbe:{key[0]}_{key[1]}"
        else:
            # key is None (e.g. "J.D. 'Danny' Diggs" — initials only)
            slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            leg_id = f"va_sbe:{slug}"
        district = str(meta["district"]) if meta and meta["district"] else ""

        print(f"  [OK]   {name:32s} ${summary['total_raised']:>12,.0f}  "
              f"top={summary['top_sector']:<16s} ({src}: {raw_names})")

        rows_to_write.append((
            leg_id, name, info["chamber"], district, info["party"], "va_sbe",
            summary["latest_cycle"], summary["total_raised"],
            summary["top_sector"], summary["top_sector_pct"],
            json.dumps(summary["by_sector"]), json.dumps(summary["top_donors"]),
            json.dumps(summary["cycles"]), datetime.now(timezone.utc).isoformat(),
        ))

    print(f"\nBuilt {built}, skipped {skipped} (of {len(missing)} missing).")

    if write and rows_to_write:
        conn.executemany("""
            INSERT INTO campaign_finance_summary
                (legislator_id, name, chamber, district, party, source,
                 latest_cycle, total_raised, top_sector, top_sector_pct,
                 by_sector_json, top_donors_json, cycles_json, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(legislator_id, source) DO UPDATE SET
                name=excluded.name, chamber=excluded.chamber,
                district=excluded.district, party=excluded.party,
                latest_cycle=excluded.latest_cycle, total_raised=excluded.total_raised,
                top_sector=excluded.top_sector, top_sector_pct=excluded.top_sector_pct,
                by_sector_json=excluded.by_sector_json,
                top_donors_json=excluded.top_donors_json,
                cycles_json=excluded.cycles_json, updated_at=excluded.updated_at
        """, rows_to_write)
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM campaign_finance_summary").fetchone()[0]
        print(f"WROTE {len(rows_to_write)} rows. campaign_finance_summary now has {total} rows.")
    elif not write:
        print("\n(dry run -- pass --write to persist)")

    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="persist rows to polls.db")
    args = ap.parse_args()
    main(write=args.write)
