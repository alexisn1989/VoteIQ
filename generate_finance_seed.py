#!/usr/bin/env python3
"""
generate_finance_seed.py
Exports pre-aggregated Spanberger campaign finance data
from the local polls.db into data/finance_seed.sql.

Run once locally, commit the SQL file, never run on Render.
"""
import sqlite3
import os
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent
DB       = BASE_DIR / "polls.db"
SEED_OUT = BASE_DIR / "data" / "finance_seed.sql"

SECTOR_KEYWORDS = [
    ("Healthcare",     ["physician","doctor","nurse","hospital","medical","dental","health",
                        "pharmacy","surgeon","dentist","therapist","clinic","psychiatr",
                        "optom","veterinar","chiropract","radiol","oncol"]),
    ("Legal",          ["attorney","lawyer","law firm","counsel","solicitor","paralegal",
                        "esquire","barrister","litigation"]),
    ("Real Estate",    ["realtor","real estate","property","developer","construction",
                        "contractor","builder","architect","land","realty","housing",
                        "mortgage","title company","appraiser","plumb","hvac","electri"]),
    ("Finance",        ["bank","financial","investment","insurance","lender","accountant",
                        "cpa","auditor","broker","fund","capital","wealth","credit union",
                        "trading","securities","financi"]),
    ("Technology",     ["software","tech","information technology"," it ","computer",
                        "digital","data","cybersecurity","engineer","developer","programmer",
                        "telecom","network","cloud","ai ","saas"]),
    ("Energy",         ["oil","gas","energy","electric","utility","power","solar","wind",
                        "coal","petroleum","pipeline","refin","nuclear"]),
    ("Agriculture",    ["farm","agricultur","farmer","livestock","crop","dairy","forestry",
                        "timber","rancher","agronomist","horticultur"]),
    ("Education",      ["teacher","professor","school","universit","college","educat",
                        "principal","librarian","tutor","academic","faculty","superintendent"]),
    ("Transportation", ["trucking","logistics","transport","airline","rail","shipping",
                        "driver","pilot","fleet","freight","maritime"]),
    ("Government",     ["government","federal","state","county","city","military",
                        "public sector","civil servant","legislat","congress","senate",
                        "official"]),
    ("Nonprofit",      ["nonprofit","non-profit","charity","foundation","association",
                        "ngo","volunteer","advocacy","501"]),
    ("Media",          ["media","journalist","reporter","publisher","broadcast",
                        "television","radio","film","entertainment","actor","writer",
                        "author"]),
    ("Retail/Food",    ["retail","restaurant","food","beverage","grocery","hotel",
                        "hospitality","tourism","chef","catering"]),
    ("Manufacturing",  ["manufactur","factory","production","assembly","industrial",
                        "aerospace","defense","chemical","material"]),
    ("Retired",        ["retired","retiree"]),
    ("Self-Employed",  ["self-employed","self employed","owner","ceo","president",
                        "founder","entrepreneur","consultant"]),
]

def classify_sector(occupation: str, employer: str, company: str) -> str:
    combined = f"{occupation} {employer} {company}".lower()
    for sector, kws in SECTOR_KEYWORDS:
        if any(kw in combined for kw in kws):
            return sector
    return "Other"

def esc(v):
    if v is None:
        return ""
    return str(v).replace("'", "''")


def main():
    if not DB.exists():
        print(f"ERROR: polls.db not found at {DB}")
        return

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    # 1. Cycle totals
    print("Querying cycle totals...")
    cycle_rows = conn.execute("""
        SELECT election_cycle,
               ROUND(SUM(amount), 2)  AS total,
               COUNT(*)               AS records,
               COUNT(DISTINCT CASE WHEN is_individual = 1
                   THEN TRIM(COALESCE(first_name,'') || ' ' || COALESCE(last_or_company,''))
               END)                   AS unique_individuals,
               MAX(transaction_date)  AS latest_date
        FROM va_cf_schedule_a
        WHERE lower(candidate_name) LIKE '%spanberger%'
          AND amount > 0
        GROUP BY election_cycle
        ORDER BY CAST(election_cycle AS INTEGER) DESC
    """).fetchall()
    print(f"  {len(cycle_rows)} cycles: {[r['election_cycle'] for r in cycle_rows]}")

    # 2. Top 500 donors per cycle
    print("Querying top donors per cycle...")
    top_donors = []
    for row in cycle_rows:
        cyc = row["election_cycle"]
        donors = conn.execute("""
            SELECT
                CASE WHEN is_individual = 1
                     THEN TRIM(COALESCE(first_name,'') || ' ' || COALESCE(last_or_company,''))
                     ELSE COALESCE(last_or_company,'') END AS donor_name,
                COALESCE(employer,'')    AS employer,
                COALESCE(occupation,'') AS occupation,
                is_individual,
                ROUND(SUM(amount), 2)   AS total,
                COUNT(*)                AS records
            FROM va_cf_schedule_a
            WHERE lower(candidate_name) LIKE '%spanberger%'
              AND amount > 0
              AND election_cycle = ?
            GROUP BY donor_name, employer, occupation
            HAVING donor_name != ''
            ORDER BY total DESC
            LIMIT 500
        """, (cyc,)).fetchall()
        for rank, d in enumerate(donors, 1):
            top_donors.append((cyc, rank,
                               d["donor_name"], d["employer"], d["occupation"],
                               d["is_individual"], d["total"], d["records"]))
        print(f"  Cycle {cyc}: {len(donors)} donor rows")

    # 3. Sector totals per cycle
    print("Computing sector totals per cycle...")
    sector_data = {}
    for row in cycle_rows:
        cyc = row["election_cycle"]
        fin_rows = conn.execute("""
            SELECT occupation, employer, last_or_company, is_individual, amount
            FROM va_cf_schedule_a
            WHERE lower(candidate_name) LIKE '%spanberger%'
              AND amount > 0
              AND election_cycle = ?
        """, (cyc,)).fetchall()
        totals = defaultdict(lambda: {"total": 0.0, "records": 0})
        for fr in fin_rows:
            sector = classify_sector(
                fr["occupation"] or "",
                fr["employer"]   or "",
                fr["last_or_company"] or "" if not fr["is_individual"] else "",
            )
            totals[sector]["total"]   += float(fr["amount"] or 0)
            totals[sector]["records"] += 1
        sector_data[cyc] = dict(totals)
        print(f"  Cycle {cyc}: {len(totals)} sectors from {len(fin_rows):,} rows")

    conn.close()

    # 4. Write SQL
    print(f"\nWriting {SEED_OUT}...")
    lines = [
        "-- finance_seed.sql",
        "-- Pre-aggregated Spanberger campaign finance data",
        "-- Generated from Virginia SBE va_cf_schedule_a (local polls.db)",
        "-- Seeded at startup by _finance_seed_task() in main.py",
        "-- DO NOT EDIT BY HAND — regenerate with generate_finance_seed.py",
        "",
        "-- ── Cycle totals ─────────────────────────────────────────────────────────────",
        "CREATE TABLE IF NOT EXISTS va_finance_cycle_totals (",
        "    candidate_name     TEXT,",
        "    election_cycle     TEXT,",
        "    total              REAL,",
        "    records            INTEGER,",
        "    unique_individuals INTEGER,",
        "    latest_date        TEXT,",
        "    PRIMARY KEY (candidate_name, election_cycle)",
        ");",
        "DELETE FROM va_finance_cycle_totals",
        "  WHERE lower(candidate_name) LIKE '%spanberger%';",
        "",
    ]
    for r in cycle_rows:
        lines.append(
            f"INSERT INTO va_finance_cycle_totals VALUES("
            f"'Abigail Spanberger','{esc(r['election_cycle'])}',"
            f"{r['total'] or 0},{r['records'] or 0},"
            f"{r['unique_individuals'] or 0},'{esc(r['latest_date'])}');"
        )

    lines += [
        "",
        "-- ── Top donors per cycle ─────────────────────────────────────────────────────",
        "CREATE TABLE IF NOT EXISTS va_finance_top_donors (",
        "    candidate_name TEXT,",
        "    election_cycle TEXT,",
        "    rank           INTEGER,",
        "    donor_name     TEXT,",
        "    employer       TEXT,",
        "    occupation     TEXT,",
        "    is_individual  INTEGER,",
        "    total          REAL,",
        "    records        INTEGER,",
        "    PRIMARY KEY (candidate_name, election_cycle, rank)",
        ");",
        "DELETE FROM va_finance_top_donors",
        "  WHERE lower(candidate_name) LIKE '%spanberger%';",
        "",
    ]
    for (cyc, rank, dname, emp, occ, is_ind, tot, rec) in top_donors:
        lines.append(
            f"INSERT INTO va_finance_top_donors VALUES("
            f"'Abigail Spanberger','{esc(cyc)}',{rank},"
            f"'{esc(dname)}','{esc(emp)}','{esc(occ)}',"
            f"{1 if is_ind else 0},{tot or 0},{rec or 0});"
        )

    lines += [
        "",
        "-- ── Sector totals per cycle ──────────────────────────────────────────────────",
        "CREATE TABLE IF NOT EXISTS va_finance_sector_totals (",
        "    candidate_name TEXT,",
        "    election_cycle TEXT,",
        "    sector         TEXT,",
        "    total          REAL,",
        "    records        INTEGER,",
        "    PRIMARY KEY (candidate_name, election_cycle, sector)",
        ");",
        "DELETE FROM va_finance_sector_totals",
        "  WHERE lower(candidate_name) LIKE '%spanberger%';",
        "",
    ]
    for cyc, totals in sector_data.items():
        for sector, stats in sorted(totals.items(),
                                    key=lambda x: x[1]["total"], reverse=True):
            lines.append(
                f"INSERT INTO va_finance_sector_totals VALUES("
                f"'Abigail Spanberger','{esc(cyc)}','{esc(sector)}',"
                f"{round(stats['total'], 2)},{stats['records']});"
            )

    SEED_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    size_kb = SEED_OUT.stat().st_size / 1024
    print(f"\nDone.")
    print(f"  File:         {SEED_OUT}")
    print(f"  Size:         {size_kb:.0f} KB")
    print(f"  Cycle rows:   {len(cycle_rows)}")
    print(f"  Donor rows:   {len(top_donors)}")
    print(f"  Sector rows:  {sum(len(v) for v in sector_data.values())}")


if __name__ == "__main__":
    main()
