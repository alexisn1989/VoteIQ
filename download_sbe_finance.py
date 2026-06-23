"""
Download Virginia SBE campaign finance CSVs and ingest local-race contributions.

Source: https://apps.elections.virginia.gov/SBE_CSV/CF/YYYY_MM/
Report.csv   — filer metadata (IsLocal flag, OfficeSought, CandidateName, etc.)
ScheduleA.csv — individual contributions

Usage:
    python download_sbe_finance.py                   # 2021-2026, local races only
    python download_sbe_finance.py --years 2023 2024
    python download_sbe_finance.py --all-offices     # include state races too
    python download_sbe_finance.py --dry-run         # download only, no DB write
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sqlite3
import time
import urllib.request
from pathlib import Path

BASE_URL  = "https://apps.elections.virginia.gov/SBE_CSV/CF"
BASE_DIR  = Path(__file__).resolve().parent
DB_PATH   = Path(os.getenv("DATA_DIR", str(BASE_DIR))) / "polls.db"
CACHE_DIR = BASE_DIR / "sbe_cache"

HAMPTON_ROADS = {
    "Virginia Beach", "Norfolk", "Chesapeake", "Hampton",
    "Newport News", "Portsmouth", "Suffolk", "Poquoson",
    "Williamsburg", "Isle of Wight", "James City", "York",
    "Gloucester", "Surry", "Franklin",
}


def _get(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (VoteIQ)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.status == 200:
                return r.read()
    except Exception:
        pass
    return None


def fetch_month(period: str) -> tuple[Path | None, Path | None]:
    cache = CACHE_DIR / period
    cache.mkdir(parents=True, exist_ok=True)
    results = []
    for fname in ("Report.csv", "ScheduleA.csv"):
        dest = cache / fname
        if dest.exists():
            results.append(dest)
            continue
        data = _get(f"{BASE_URL}/{period}/{fname}")
        if data:
            dest.write_bytes(data)
            results.append(dest)
        else:
            results.append(None)
        time.sleep(0.25)
    return tuple(results)


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    for enc in ("utf-8-sig", "latin-1", "cp1252"):
        try:
            text = path.read_text(encoding=enc)
            reader = csv.DictReader(io.StringIO(text))
            rows = list(reader)
            return reader.fieldnames or [], rows
        except UnicodeDecodeError:
            continue
    return [], []


def ingest_period(report_path: Path, sched_path: Path, conn: sqlite3.Connection,
                  all_offices: bool, period: str) -> int:
    _, reports = read_csv(report_path)
    _, contribs = read_csv(sched_path)

    if not reports or not contribs:
        return 0

    # Whitelist of genuinely local offices (excludes state/federal races)
    LOCAL_OFFICE_KEYWORDS = {
        "mayor", "city council", "town council", "board of supervisors",
        "school board", "sheriff", "treasurer", "commonwealth",
        "commissioner of revenue", "clerk of court", "clerk of circuit",
        "registrar", "electoral board", "constitutional officer",
    }

    def is_local_office(office: str) -> bool:
        o = office.strip().lower()
        return any(k in o for k in LOCAL_OFFICE_KEYWORDS)

    if not all_offices:
        reports = [r for r in reports if is_local_office(r.get("OfficeSought", ""))]

    # Hampton Roads locality filter
    if HAMPTON_ROADS:
        reports = [r for r in reports
                   if r.get("City", "").strip() in HAMPTON_ROADS]

    if not reports:
        return 0

    # Build lookup: ReportUID -> report metadata
    report_map = {
        r["ReportUID"]: r
        for r in reports
        if r.get("ReportUID")
    }
    if not report_map:
        return 0

    rows_to_insert = []
    for c in contribs:
        uid = c.get("ReportUID", "")
        rep = report_map.get(uid)
        if not rep:
            continue

        try:
            amount = float(str(c.get("Amount", "") or "").replace("$", "").replace(",", "").strip() or 0)
        except ValueError:
            amount = 0.0

        rows_to_insert.append({
            "period":           period,
            "report_uid":       uid,
            "candidate_name":   rep.get("CandidateName", "").strip(),
            "committee_name":   rep.get("CommitteeName", "").strip(),
            "office_sought":    rep.get("OfficeSought", "").strip(),
            "district":         rep.get("District", "").strip(),
            "locality":         rep.get("City", "").strip(),
            "party":            rep.get("Party", "").strip(),
            "election_cycle":   rep.get("ElectionCycle", "").strip(),
            "report_year":      rep.get("ReportYear", "").strip(),
            "is_statewide":     rep.get("IsStateWide", "").strip(),
            "is_general_assembly": rep.get("IsGeneralAssembly", "").strip(),
            "contributor_first":   c.get("FirstName", "").strip(),
            "contributor_last":    c.get("LastOrCompanyName", "").strip(),
            "employer":            c.get("NameOfEmployer", "").strip(),
            "occupation":          c.get("OccupationOrTypeOfBusiness", "").strip(),
            "contributor_city":    c.get("City", "").strip(),
            "contributor_state":   c.get("StateCode", "").strip(),
            "contributor_zip":     c.get("ZipCode", "").strip(),
            "is_individual":       c.get("IsIndividual", "").strip(),
            "transaction_date":    c.get("TransactionDate", "").strip(),
            "amount":              amount,
            "total_to_date":       c.get("TotalToDate", "").strip(),
            "schedule_a_id":       c.get("ScheduleAId", "").strip(),
        })

    if not rows_to_insert:
        return 0

    cols = list(rows_to_insert[0].keys())
    placeholders = ", ".join("?" for _ in cols)
    quoted_cols  = ", ".join(f'"{c}"' for c in cols)
    sql = f"INSERT INTO sbe_local_contributions ({quoted_cols}) VALUES ({placeholders})"

    conn.executemany(sql, [[r[c] for c in cols] for r in rows_to_insert])
    return len(rows_to_insert)


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sbe_local_contributions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            period              TEXT,
            report_uid          TEXT,
            candidate_name      TEXT,
            committee_name      TEXT,
            office_sought       TEXT,
            district            TEXT,
            locality            TEXT,
            party               TEXT,
            election_cycle      TEXT,
            report_year         TEXT,
            is_statewide        TEXT,
            is_general_assembly TEXT,
            contributor_first   TEXT,
            contributor_last    TEXT,
            employer            TEXT,
            occupation          TEXT,
            contributor_city    TEXT,
            contributor_state   TEXT,
            contributor_zip     TEXT,
            is_individual       TEXT,
            transaction_date    TEXT,
            amount              REAL,
            total_to_date       TEXT,
            schedule_a_id       TEXT,
            UNIQUE (schedule_a_id, report_uid)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sbe_candidate  ON sbe_local_contributions(candidate_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sbe_office     ON sbe_local_contributions(office_sought)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sbe_locality   ON sbe_local_contributions(locality)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sbe_cycle      ON sbe_local_contributions(election_cycle)")
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", nargs="+", type=int, default=list(range(2021, 2027)))
    parser.add_argument("--all-offices", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Download only, skip DB write")
    args = parser.parse_args()

    CACHE_DIR.mkdir(exist_ok=True)

    periods = [f"{y}_{m:02d}" for y in args.years for m in range(1, 13)]

    if args.dry_run:
        for period in periods:
            print(f"  {period} ...", end=" ", flush=True)
            rp, sp = fetch_month(period)
            print("ok" if rp and sp else "missing")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_table(conn)

    total = 0
    for period in periods:
        print(f"  {period} ...", end=" ", flush=True)
        rp, sp = fetch_month(period)
        if not rp or not sp:
            print("skip")
            continue
        n = ingest_period(rp, sp, conn, args.all_offices, period)
        conn.commit()
        print(f"{n:,} rows")
        total += n

    conn.close()
    print(f"\nTotal: {total:,} local contributions in sbe_local_contributions")


if __name__ == "__main__":
    main()
