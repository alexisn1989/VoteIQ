#!/usr/bin/env python3
"""Load FEC PAC-to-candidate contributions for 2026 VA House races.

Source: raw/pas226.zip -> itpas2.txt (pipe-delimited)
Target: fec_va_house_pac_contributions
Output: data/fec_va_house_pac_contributions_seed.sql

itpas2.txt field index (0-based, pipe-delimited):
  0  CMTE_ID          recipient committee (candidate's own committee)
  5  TRANSACTION_TP   24K=direct, 24Z=in-kind, 24E=electioneering
  6  ENTITY_TP
  7  NAME             contributing PAC / committee name
  8  CITY
  9  STATE
  10 ZIP_CODE
  13 TRANSACTION_DT   MMDDYYYY
  14 TRANSACTION_AMT
  15 OTHER_ID         contributing PAC's own committee ID
  16 CAND_ID          FEC candidate ID
  17 TRAN_ID
  21 SUB_ID
"""
from __future__ import annotations

import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "polls.db"
ZIP_PATH = BASE_DIR / "raw" / "pas226.zip"
SEED_OUT = BASE_DIR / "data" / "fec_va_house_pac_contributions_seed.sql"

DDL = """
CREATE TABLE IF NOT EXISTS fec_va_house_pac_contributions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cmte_id          TEXT,
    cand_id          TEXT,
    cand_name        TEXT,
    district         INTEGER,
    party            TEXT,
    ici              TEXT,
    pac_id           TEXT,
    pac_name         TEXT,
    pac_city         TEXT,
    pac_state        TEXT,
    amount           REAL,
    contrib_date     TEXT,
    transaction_type TEXT,
    tran_id          TEXT,
    sub_id           TEXT,
    cycle            INTEGER DEFAULT 2026,
    loaded_at        TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uix_va_pac_tran ON fec_va_house_pac_contributions(tran_id);
CREATE INDEX IF NOT EXISTS ix_va_pac_cand ON fec_va_house_pac_contributions(cand_id);
CREATE INDEX IF NOT EXISTS ix_va_pac_pac ON fec_va_house_pac_contributions(pac_id);
"""

F_CMTE = 0
F_TTYPE = 5
F_PAC_NAME = 7
F_PAC_CITY = 8
F_PAC_STATE = 9
F_DATE = 13
F_AMOUNT = 14
F_PAC_ID = 15
F_CAND_ID = 16
F_TRAN_ID = 17
F_SUB_ID = 21


def _parse_date(raw: str) -> str:
    try:
        return datetime.strptime(raw.strip(), "%m%d%Y").strftime("%Y-%m-%d")
    except Exception:
        return raw.strip()


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Build lookup: committee_id -> candidate metadata
    cand_meta: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT committee_id, cand_id, name, district, party, ici FROM fec_va_house_candidates"
        " WHERE committee_id IS NOT NULL AND committee_id != ''"
    ).fetchall():
        cand_meta[r["committee_id"]] = {
            "cand_id": r["cand_id"],
            "cand_name": r["name"],
            "district": r["district"],
            "party": r["party"],
            "ici": r["ici"],
        }

    target_cmtes = set(cand_meta.keys())
    print(f"Filtering to {len(target_cmtes)} VA House committee IDs")

    rows: list[dict] = []
    seen_tran: set[str] = set()
    skipped_bad = 0

    with zipfile.ZipFile(ZIP_PATH) as z:
        with z.open("itpas2.txt") as f:
            for raw_line in f:
                line = raw_line.decode("latin-1").rstrip("\n\r")
                parts = line.split("|")
                if len(parts) < 18:
                    continue
                cmte = parts[F_CMTE].strip()
                if cmte not in target_cmtes:
                    continue

                tran_id = parts[F_TRAN_ID].strip()
                if not tran_id or tran_id in seen_tran:
                    continue
                seen_tran.add(tran_id)

                try:
                    amount = float(parts[F_AMOUNT].strip())
                except (ValueError, IndexError):
                    skipped_bad += 1
                    continue

                sub_id = parts[F_SUB_ID].strip() if len(parts) > F_SUB_ID else ""
                meta = cand_meta[cmte]
                rows.append({
                    "cmte_id":          cmte,
                    "cand_id":          meta["cand_id"],
                    "cand_name":        meta["cand_name"],
                    "district":         meta["district"],
                    "party":            meta["party"],
                    "ici":              meta["ici"],
                    "pac_id":           parts[F_PAC_ID].strip(),
                    "pac_name":         parts[F_PAC_NAME].strip(),
                    "pac_city":         parts[F_PAC_CITY].strip(),
                    "pac_state":        parts[F_PAC_STATE].strip(),
                    "amount":           amount,
                    "contrib_date":     _parse_date(parts[F_DATE]),
                    "transaction_type": parts[F_TTYPE].strip(),
                    "tran_id":          tran_id,
                    "sub_id":           sub_id,
                    "cycle":            2026,
                    "loaded_at":        datetime.utcnow().isoformat() + "+00:00",
                })

    print(f"Found {len(rows)} PAC contributions for VA House candidates ({skipped_bad} bad rows skipped)")

    # Load into DB
    conn.executescript(DDL)
    conn.execute("DELETE FROM fec_va_house_pac_contributions")
    conn.executemany(
        """
        INSERT OR IGNORE INTO fec_va_house_pac_contributions
            (cmte_id, cand_id, cand_name, district, party, ici,
             pac_id, pac_name, pac_city, pac_state,
             amount, contrib_date, transaction_type, tran_id, sub_id, cycle, loaded_at)
        VALUES
            (:cmte_id, :cand_id, :cand_name, :district, :party, :ici,
             :pac_id, :pac_name, :pac_city, :pac_state,
             :amount, :contrib_date, :transaction_type, :tran_id, :sub_id, :cycle, :loaded_at)
        """,
        rows,
    )
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM fec_va_house_pac_contributions").fetchone()[0]
    total_amt = conn.execute("SELECT SUM(amount) FROM fec_va_house_pac_contributions").fetchone()[0]
    print(f"Loaded {total} rows, total ${total_amt:,.0f}")

    # Export seed SQL
    SEED_OUT.parent.mkdir(exist_ok=True)
    lines = [
        "-- FEC PAC-to-candidate contributions for 2026 VA House races",
        f"-- Generated {datetime.utcnow().strftime('%Y-%m-%d')} | {total} rows",
        "",
        DDL.strip(),
        "",
        "DELETE FROM fec_va_house_pac_contributions;",
        "",
    ]
    for r in rows:
        def esc(v):
            if v is None:
                return "NULL"
            if isinstance(v, (int, float)):
                return str(v)
            return "'" + str(v).replace("'", "''") + "'"

        lines.append(
            "INSERT OR IGNORE INTO fec_va_house_pac_contributions"
            " (cmte_id,cand_id,cand_name,district,party,ici,"
            "pac_id,pac_name,pac_city,pac_state,"
            "amount,contrib_date,transaction_type,tran_id,sub_id,cycle,loaded_at) VALUES("
            + ",".join([
                esc(r["cmte_id"]), esc(r["cand_id"]), esc(r["cand_name"]),
                esc(r["district"]), esc(r["party"]), esc(r["ici"]),
                esc(r["pac_id"]), esc(r["pac_name"]),
                esc(r["pac_city"]), esc(r["pac_state"]),
                esc(r["amount"]), esc(r["contrib_date"]),
                esc(r["transaction_type"]), esc(r["tran_id"]),
                esc(r["sub_id"]), esc(r["cycle"]), esc(r["loaded_at"]),
            ])
            + ");"
        )

    SEED_OUT.write_text("\n".join(lines), encoding="utf-8")
    size_mb = SEED_OUT.stat().st_size / 1024 / 1024
    print(f"Seed SQL -> {SEED_OUT} ({size_mb:.1f} MB)")

    conn.close()


if __name__ == "__main__":
    main()
