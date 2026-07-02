#!/usr/bin/env python3
"""
backfill_missing_federal_finance.py

Backfills campaign_finance_summary for the 3 VA federal members missing from
the FEC build:

  Subramanyam, Suhas      (VA-10)  committee C00856963  cycle 2024
  Vindman, Eugene Simon   (VA-7)   committee C00856955  cycle 2024
  Walkinshaw, James R.    (VA-11)  committee C00904367  cycle 2026 (special)

Strategy:
  - Use FEC /schedules/schedule_a/by_employer/ for sector breakdown (fast aggregate)
  - Use /schedules/schedule_a/ sorted by amount desc for top_donors (first page only)
  - Use /candidate/{id}/totals/ for total_raised (authoritative)

Usage:
    python backfill_missing_federal_finance.py            # dry run
    python backfill_missing_federal_finance.py --write    # persist to polls.db
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
POLLS_DB = DATA_DIR / "polls.db"
if not POLLS_DB.is_file():
    POLLS_DB = BASE_DIR / "polls.db"

FEC_KEY = os.getenv("FEC_API_KEY", "DEMO_KEY")
FEC_BASE = "https://api.open.fec.gov/v1"

TARGETS = [
    {"bioguide_id": "S001230", "name": "Subramanyam, Suhas",
     "fec_id": "H4VA10279",  "committee_id": "C00856963",
     "cycle": 2024, "chamber": "House of Representatives",
     "district": "10", "party": "Democratic"},
    {"bioguide_id": "V000138", "name": "Vindman, Eugene Simon",
     "fec_id": "H4VA07234",  "committee_id": "C00856955",
     "cycle": 2024, "chamber": "House of Representatives",
     "district": "7",  "party": "Democratic"},
    {"bioguide_id": "W000831", "name": "Walkinshaw, James R.",
     "fec_id": "H6VA11066",  "committee_id": "C00904367",
     "cycle": 2026, "chamber": "House of Representatives",
     "district": "11", "party": "Democratic"},
]

# ── Sector classifier ─────────────────────────────────────────────────────────
SECTOR_KEYWORDS: list[tuple[str, list[str]]] = [
    ("Healthcare",     ["physician","doctor","nurse","hospital","medical","dental",
                        "health","pharmacy","surgeon","dentist","therapist","clinic",
                        "psychiatr","optom","veterinar","chiropract","radiol","oncol"]),
    ("Legal",          ["attorney","lawyer","law firm","counsel","solicitor",
                        "paralegal","esquire","barrister","litigation"]),
    ("Real Estate",    ["realtor","real estate","property","developer","construction",
                        "contractor","builder","architect","land","realty","housing",
                        "mortgage","title company","appraiser","plumb","hvac","electri"]),
    ("Finance",        ["bank","financial","investment","insurance","lender",
                        "accountant","cpa","auditor","broker","fund","capital",
                        "wealth","credit union","trading","securities","financi"]),
    ("Technology",     ["software","tech","information technology"," it ","computer",
                        "digital","data","cybersecurity","engineer","developer",
                        "programmer","telecom","network","cloud","ai ","saas"]),
    ("Energy",         ["oil","gas","energy","electric","utility","power","solar",
                        "wind","coal","petroleum","pipeline","refin","nuclear"]),
    ("Agriculture",    ["farm","agricultur","farmer","livestock","crop","dairy",
                        "forestry","timber","rancher","agronomist","horticultur"]),
    ("Education",      ["teacher","professor","school","universit","college","educat",
                        "principal","librarian","tutor","academic","faculty",
                        "superintendent"]),
    ("Transportation", ["trucking","logistics","transport","airline","rail","shipping",
                        "driver","pilot","fleet","freight","maritime"]),
    ("Labor/Union",    ["union","afl-cio","seiu","ufcw","teamster","uaw","ibew",
                        "iatse","craft","guild","labor council"]),
    ("Defense",        ["defense","military","veteran","aerospace","weapon",
                        "contractor dod","army","navy","marine","air force"]),
    ("Hospitality",    ["hotel","restaurant","hospitality","bar ","tavern","brewery",
                        "winery","catering","tourism","resort","motel"]),
    ("Manufacturing",  ["manufactur","factory","industrial","plant","assembly",
                        "production","machinery","fabricat","foundry","mill"]),
    ("Retail",         ["retail","store","shop ","merchant","dealer","wholesale"]),
    ("Ideological",    ["pac","political","committee","democrat","republican",
                        "libertarian","action fund","victory","caucus","leadership"]),
    ("Individual/Other", []),
]


def classify(employer: str) -> str:
    s = employer.lower()
    for sector, kws in SECTOR_KEYWORDS[:-1]:
        if any(k in s for k in kws):
            return sector
    return "Individual/Other"


# ── FEC API helpers ────────────────────────────────────────────────────────────

def _get(path: str, params: dict) -> dict:
    params = dict(params, api_key=FEC_KEY)
    resp = requests.get(f"{FEC_BASE}{path}", params=params, timeout=30,
                        headers={"User-Agent": "VoteIQ/1.0"})
    resp.raise_for_status()
    return resp.json()


def get_total_raised(fec_id: str, cycle: int) -> float:
    """Use /candidate/{id}/totals/ for the authoritative total raised."""
    try:
        data = _get(f"/candidate/{fec_id}/totals/",
                    {"cycle": cycle, "per_page": 1})
        results = data.get("results", [])
        if results:
            return float(results[0].get("receipts") or 0)
    except Exception as exc:
        print(f"  [WARN] totals lookup failed: {exc}")
    return 0.0


def get_by_employer(committee_id: str, cycle: int) -> list[dict]:
    """Page through /schedules/schedule_a/by_employer/ — fast aggregate."""
    rows = []
    page = 1
    last_index = None
    last_employer = None
    while True:
        params: dict = {
            "committee_id": committee_id,
            "two_year_transaction_period": cycle,
            "per_page": 100,
            "sort_hide_null": "true",
        }
        if last_index and last_employer is not None:
            params["last_index"] = last_index
            params["last_employer"] = last_employer
        try:
            data = _get("/schedules/schedule_a/by_employer/", params)
        except Exception as exc:
            print(f"  [WARN] by_employer page {page} failed: {exc}")
            break
        results = data.get("results", [])
        if not results:
            break
        rows.extend(results)
        pagination = data.get("pagination", {})
        pages = pagination.get("pages", 1)
        if page >= pages or not pagination.get("last_indexes"):
            break
        last_index = pagination["last_indexes"].get("last_index")
        last_employer = pagination["last_indexes"].get("last_employer", "")
        page += 1
        if page > 200:
            break
        time.sleep(0.25)
    print(f"  -> by_employer: {len(rows)} employer rows ({page} pages)")
    return rows


def get_top_donors(committee_id: str, cycle: int) -> list[dict]:
    """Fetch top 100 individual contributions sorted by amount descending."""
    try:
        data = _get("/schedules/schedule_a/", {
            "committee_id": committee_id,
            "two_year_transaction_period": cycle,
            "per_page": 100,
            "sort": "-contribution_receipt_amount",
        })
        return data.get("results", [])
    except Exception as exc:
        print(f"  [WARN] top donors fetch failed: {exc}")
        return []


# ── Aggregate ─────────────────────────────────────────────────────────────────

def build_summary(fec_id: str, committee_id: str, cycle: int) -> dict | None:
    total_raised = get_total_raised(fec_id, cycle)

    employer_rows = get_by_employer(committee_id, cycle)
    if not employer_rows:
        return None

    sector_total: dict[str, float] = defaultdict(float)
    sector_count: dict[str, int] = defaultdict(int)
    for r in employer_rows:
        emp = (r.get("employer") or "").strip()
        amt = float(r.get("total") or 0)
        cnt = int(r.get("count") or 0)
        if amt <= 0:
            continue
        sector = classify(emp)
        sector_total[sector] += amt
        sector_count[sector] += cnt

    if not sector_total:
        return None

    # If totals endpoint gave 0, fall back to sum of employer rows
    if not total_raised:
        total_raised = sum(sector_total.values())

    by_sector = sorted(
        [{"sector": s, "total": round(sector_total[s], 2),
          "donor_count": sector_count[s],
          "avg_donation": round(sector_total[s] / max(sector_count[s], 1), 2)}
         for s in sector_total],
        key=lambda d: d["total"], reverse=True,
    )

    raw_donors = get_top_donors(committee_id, cycle)
    time.sleep(0.3)
    top_donors = []
    seen: dict[str, float] = {}
    for c in raw_donors:
        name = (c.get("contributor_name") or "").strip()
        emp = (c.get("contributor_employer") or "").strip()
        amt = float(c.get("contribution_receipt_amount") or 0)
        if not name or amt <= 0:
            continue
        key = f"{name.lower()}|{emp.lower()}"
        seen[key] = seen.get(key, 0) + amt
        top_donors.append({
            "contributor_name": name,
            "employer": emp,
            "sector": classify(emp),
            "total": round(amt, 2),
            "count": 1,
        })
    # dedupe: keep highest single contribution per donor
    seen_keys: set[str] = set()
    deduped = []
    for d in sorted(top_donors, key=lambda x: x["total"], reverse=True):
        k = f"{d['contributor_name'].lower()}|{d['employer'].lower()}"
        if k not in seen_keys:
            seen_keys.add(k)
            deduped.append(d)
        if len(deduped) >= 20:
            break

    return {
        "total_raised": round(total_raised, 2),
        "by_sector": by_sector,
        "top_donors": deduped,
        "top_sector": by_sector[0]["sector"],
        "top_sector_pct": round(by_sector[0]["total"] / total_raised * 100, 1)
            if total_raised else None,
        "latest_cycle": str(cycle),
        "cycles": [str(cycle)],
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main(write: bool) -> None:
    conn = sqlite3.connect(str(POLLS_DB), timeout=30)
    conn.row_factory = sqlite3.Row
    already = {r["legislator_id"] for r in conn.execute(
        "SELECT legislator_id FROM campaign_finance_summary WHERE source='fec'")}

    built = []
    for t in TARGETS:
        if t["bioguide_id"] in already:
            print(f"  [SKIP] {t['name']:35s} already in summary")
            continue
        print(f"\n  Fetching {t['name']} (committee {t['committee_id']}, cycle {t['cycle']})...")
        summary = build_summary(t["fec_id"], t["committee_id"], t["cycle"])
        if not summary:
            print(f"  [SKIP] {t['name']} — no data")
            continue
        print(f"  [OK]   {t['name']:35s} ${summary['total_raised']:>12,.0f}  "
              f"top={summary['top_sector']}")
        built.append((t, summary))

    print(f"\nBuilt {len(built)} rows.")
    if write and built:
        now = datetime.now(timezone.utc).isoformat()
        conn.executemany("""
            INSERT INTO campaign_finance_summary
                (legislator_id, name, chamber, district, party, source,
                 latest_cycle, total_raised, top_sector, top_sector_pct,
                 by_sector_json, top_donors_json, cycles_json, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(legislator_id, source) DO UPDATE SET
                name=excluded.name, latest_cycle=excluded.latest_cycle,
                total_raised=excluded.total_raised, top_sector=excluded.top_sector,
                top_sector_pct=excluded.top_sector_pct,
                by_sector_json=excluded.by_sector_json,
                top_donors_json=excluded.top_donors_json,
                cycles_json=excluded.cycles_json, updated_at=excluded.updated_at
        """, [(t["bioguide_id"], t["name"], t["chamber"], t["district"], t["party"],
               "fec", s["latest_cycle"], s["total_raised"], s["top_sector"],
               s["top_sector_pct"], json.dumps(s["by_sector"]),
               json.dumps(s["top_donors"]), json.dumps(s["cycles"]), now)
              for t, s in built])
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM campaign_finance_summary").fetchone()[0]
        print(f"WROTE {len(built)} rows. campaign_finance_summary now has {total} rows.")
    elif not write:
        print("(dry run — pass --write to persist)")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    main(write=ap.parse_args().write)
