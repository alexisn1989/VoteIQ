#!/usr/bin/env python3
"""
Download FEC indiv26.zip, filter to VA House 2026 committee IDs,
load into fec_va_house_contributions in polls.db, then export seed SQL.

Run locally — never at runtime (Render 512MB limit).
Usage:
    python build_va_house_contributions.py
    python build_va_house_contributions.py --skip-download  # if zip already cached
"""
import argparse
import io
import os
import sqlite3
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

INDIV_URL = "https://www.fec.gov/files/bulk-downloads/2026/indiv26.zip"
CACHE_PATH = Path("raw/indiv26.zip")
DB_PATH = Path("polls.db")
SEED_PATH = Path("data/fec_va_house_contributions_seed.sql")

# itcont.txt field indices (pipe-delimited, no header)
F_CMTE_ID      = 0
F_NAME         = 7
F_CITY         = 8
F_STATE        = 9
F_ZIP          = 10
F_EMPLOYER     = 11
F_OCCUPATION   = 12
F_DATE         = 13   # MMDDYYYY
F_AMOUNT       = 14
F_TRAN_ID      = 16
F_SUB_ID       = 20


def _get_committee_ids(db: Path) -> dict[str, dict]:
    """Return {committee_id: {name, district, party, ici}} for VA House candidates."""
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT committee_id, name, district, party, ici, cand_id "
        "FROM fec_va_house_candidates "
        "WHERE committee_id IS NOT NULL AND committee_id != ''"
    ).fetchall()
    conn.close()
    return {r[0]: {"name": r[1], "district": r[2], "party": r[3], "ici": r[4], "cand_id": r[5]}
            for r in rows}


def _parse_date(raw: str) -> str | None:
    """MMDDYYYY -> YYYY-MM-DD, or None."""
    raw = (raw or "").strip()
    if len(raw) == 8:
        try:
            return f"{raw[4:]}-{raw[:2]}-{raw[2:4]}"
        except Exception:
            pass
    return None


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url} -> {dest} ...")
    mb = 0
    with urllib.request.urlopen(url) as resp, open(dest, "wb") as f:
        while chunk := resp.read(1 << 20):  # 1 MB chunks
            f.write(chunk)
            mb += 1
            if mb % 50 == 0:
                print(f"  {mb} MB downloaded ...", flush=True)
    print(f"  Done: {dest.stat().st_size / 1e6:.1f} MB")


def _create_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS fec_va_house_contributions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            cmte_id         TEXT,
            cand_id         TEXT,
            cand_name       TEXT,
            district        INTEGER,
            party           TEXT,
            ici             TEXT,
            contributor     TEXT,
            city            TEXT,
            state           TEXT,
            zip             TEXT,
            employer        TEXT,
            occupation      TEXT,
            contrib_date    TEXT,
            amount          REAL,
            tran_id         TEXT,
            sub_id          TEXT,
            cycle           INTEGER DEFAULT 2026,
            loaded_at       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_vahc_cmte     ON fec_va_house_contributions(cmte_id);
        CREATE INDEX IF NOT EXISTS idx_vahc_district ON fec_va_house_contributions(district);
        CREATE INDEX IF NOT EXISTS idx_vahc_amount   ON fec_va_house_contributions(amount);
        CREATE INDEX IF NOT EXISTS idx_vahc_employer ON fec_va_house_contributions(employer);
    """)


def _stream_and_load(zip_path: Path, committee_map: dict, conn: sqlite3.Connection) -> int:
    now = datetime.now(timezone.utc).isoformat()
    total = 0
    batch: list[tuple] = []

    with zipfile.ZipFile(zip_path) as zf:
        # The file inside is itcont.txt
        member = next((n for n in zf.namelist() if n.endswith(".txt")), None)
        if not member:
            raise RuntimeError(f"No .txt file found inside {zip_path}")
        print(f"  Streaming {member} ...")

        with zf.open(member) as raw:
            for line_bytes in raw:
                try:
                    line = line_bytes.decode("latin-1").rstrip("\r\n")
                except Exception:
                    continue
                parts = line.split("|")
                if len(parts) < 15:
                    continue
                cmte_id = parts[F_CMTE_ID].strip()
                if cmte_id not in committee_map:
                    continue
                info = committee_map[cmte_id]
                try:
                    amount = float(parts[F_AMOUNT]) if parts[F_AMOUNT].strip() else 0.0
                except ValueError:
                    amount = 0.0
                sub_id = parts[F_SUB_ID].strip() if len(parts) > F_SUB_ID else ""
                batch.append((
                    cmte_id,
                    info["cand_id"],
                    info["name"],
                    info["district"],
                    info["party"],
                    info["ici"],
                    parts[F_NAME].strip(),
                    parts[F_CITY].strip(),
                    parts[F_STATE].strip(),
                    parts[F_ZIP].strip(),
                    parts[F_EMPLOYER].strip(),
                    parts[F_OCCUPATION].strip(),
                    _parse_date(parts[F_DATE]),
                    amount,
                    parts[F_TRAN_ID].strip() if len(parts) > F_TRAN_ID else "",
                    sub_id,
                    2026,
                    now,
                ))
                if len(batch) >= 5000:
                    conn.executemany(
                        "INSERT OR IGNORE INTO fec_va_house_contributions "
                        "(cmte_id,cand_id,cand_name,district,party,ici,"
                        "contributor,city,state,zip,employer,occupation,"
                        "contrib_date,amount,tran_id,sub_id,cycle,loaded_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        batch,
                    )
                    conn.commit()
                    total += len(batch)
                    batch.clear()
                    if total % 50000 == 0:
                        print(f"  {total:,} rows loaded ...", flush=True)

    if batch:
        conn.executemany(
            "INSERT OR IGNORE INTO fec_va_house_contributions "
            "(cmte_id,cand_id,cand_name,district,party,ici,"
            "contributor,city,state,zip,employer,occupation,"
            "contrib_date,amount,tran_id,sub_id,cycle,loaded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            batch,
        )
        conn.commit()
        total += len(batch)

    return total


def _export_seed(db: Path, dest: Path) -> int:
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT cmte_id,cand_id,cand_name,district,party,ici,"
        "contributor,city,state,zip,employer,occupation,"
        "contrib_date,amount,tran_id,sub_id,cycle,loaded_at "
        "FROM fec_va_house_contributions ORDER BY district,amount DESC"
    ).fetchall()
    conn.close()

    def q(v):
        if v is None:
            return "NULL"
        if isinstance(v, (int, float)):
            return repr(v)
        return "'" + str(v).replace("'", "''") + "'"

    ddl = """\
CREATE TABLE IF NOT EXISTS fec_va_house_contributions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cmte_id         TEXT,
    cand_id         TEXT,
    cand_name       TEXT,
    district        INTEGER,
    party           TEXT,
    ici             TEXT,
    contributor     TEXT,
    city            TEXT,
    state           TEXT,
    zip             TEXT,
    employer        TEXT,
    occupation      TEXT,
    contrib_date    TEXT,
    amount          REAL,
    tran_id         TEXT,
    sub_id          TEXT,
    cycle           INTEGER DEFAULT 2026,
    loaded_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_vahc_cmte     ON fec_va_house_contributions(cmte_id);
CREATE INDEX IF NOT EXISTS idx_vahc_district ON fec_va_house_contributions(district);
CREATE INDEX IF NOT EXISTS idx_vahc_amount   ON fec_va_house_contributions(amount);
CREATE INDEX IF NOT EXISTS idx_vahc_employer ON fec_va_house_contributions(employer);

"""
    vals = ",\n".join(
        f"    ({q(r[0])},{q(r[1])},{q(r[2])},{q(r[3])},{q(r[4])},{q(r[5])},"
        f"{q(r[6])},{q(r[7])},{q(r[8])},{q(r[9])},{q(r[10])},{q(r[11])},"
        f"{q(r[12])},{q(r[13])},{q(r[14])},{q(r[15])},{q(r[16])},{q(r[17])})"
        for r in rows
    )
    sql = (
        ddl
        + f"-- {len(rows):,} individual contributions to VA House 2026 candidates\n"
        + "INSERT OR IGNORE INTO fec_va_house_contributions\n"
        + "    (cmte_id,cand_id,cand_name,district,party,ici,contributor,city,state,zip,"
        + "employer,occupation,contrib_date,amount,tran_id,sub_id,cycle,loaded_at)\nVALUES\n"
        + vals
        + ";\n"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(sql, encoding="utf-8")
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-download", action="store_true",
                    help="Use cached raw/indiv26.zip instead of downloading")
    ap.add_argument("--no-seed", action="store_true",
                    help="Skip exporting seed SQL (useful for large result sets)")
    args = ap.parse_args()

    committee_map = _get_committee_ids(DB_PATH)
    print(f"Filtering to {len(committee_map)} VA House committee IDs")

    if not args.skip_download:
        if CACHE_PATH.exists():
            print(f"Using cached {CACHE_PATH} ({CACHE_PATH.stat().st_size / 1e6:.1f} MB)")
        else:
            _download(INDIV_URL, CACHE_PATH)
    else:
        if not CACHE_PATH.exists():
            raise FileNotFoundError(f"--skip-download set but {CACHE_PATH} not found")
        print(f"Using cached {CACHE_PATH} ({CACHE_PATH.stat().st_size / 1e6:.1f} MB)")

    conn = sqlite3.connect(str(DB_PATH))
    _create_table(conn)
    # Clear existing rows so re-runs don't double-count
    conn.execute("DELETE FROM fec_va_house_contributions")
    conn.commit()

    print("Streaming contributions ...")
    n = _stream_and_load(CACHE_PATH, committee_map, conn)
    conn.close()
    print(f"\nLoaded {n:,} contributions into fec_va_house_contributions")

    # Summary
    conn = sqlite3.connect(str(DB_PATH))
    print("\n=== Per-district summary ===")
    for r in conn.execute("""
        SELECT district, COUNT(*) contribs, COUNT(DISTINCT contributor) donors,
               ROUND(SUM(amount)) total, ROUND(AVG(amount)) avg
        FROM fec_va_house_contributions
        GROUP BY district ORDER BY district
    """).fetchall():
        print(f"  VA-{r[0]:02d}  {r[1]:>6,} contribs  {r[2]:>5,} donors  "
              f"${r[3]:>12,.0f} total  ${r[4]:>6,.0f} avg")

    top_employers = conn.execute("""
        SELECT employer, COUNT(*) n, ROUND(SUM(amount)) total
        FROM fec_va_house_contributions
        WHERE employer NOT IN ('', 'RETIRED', 'SELF-EMPLOYED', 'NOT EMPLOYED', 'SELF')
          AND employer IS NOT NULL
        GROUP BY employer ORDER BY total DESC LIMIT 10
    """).fetchall()
    print("\n=== Top employers (all districts) ===")
    for r in top_employers:
        print(f"  {r[0]:<40} {r[1]:>5,} contribs  ${r[2]:>10,.0f}")
    conn.close()

    if not args.no_seed:
        print(f"\nExporting seed SQL to {SEED_PATH} ...")
        seed_n = _export_seed(DB_PATH, SEED_PATH)
        size_mb = SEED_PATH.stat().st_size / 1e6
        print(f"  {seed_n:,} rows -> {size_mb:.1f} MB")
        if size_mb > 50:
            print("  WARNING: seed file >50 MB — consider --no-seed and using the polls.db "
                  "seed URL approach instead (POLLS_DB_SEED_URL in build.sh)")

    print("\nDone. Next steps:")
    print("  1. git add data/fec_va_house_contributions_seed.sql build_va_house_contributions.py")
    print("  2. Add STEP 4c3 to build.sh (seed fec_va_house_contributions)")
    print("  3. git commit + push")


if __name__ == "__main__":
    main()
