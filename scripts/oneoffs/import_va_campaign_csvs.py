#!/usr/bin/env python3
"""
Import Virginia ELECT campaign-finance CSV exports into polls.db.

Examples:
    python import_va_campaign_csvs.py
    python import_va_campaign_csvs.py "C:\\Users\\Alexis\\Downloads\\Report (1).csv"
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "polls.db"
DEFAULT_FILES = [
    Path(r"C:\Users\Alexis\Downloads\Report (1).csv"),
    Path(r"C:\Users\Alexis\Downloads\ScheduleG.csv"),
    Path(r"C:\Users\Alexis\Downloads\ScheduleG (1).csv"),
    Path(r"C:\Users\Alexis\Downloads\ScheduleH.csv"),
    Path(r"C:\Users\Alexis\Downloads\ScheduleI.csv"),
]
TABLE_BY_PREFIX = {
    "report": "va_cf_reports",
    "scheduleg": "va_cf_schedule_g",
    "scheduleh": "va_cf_schedule_h",
    "schedulei": "va_cf_schedule_i",
}
PRIMARY_KEYS = {
    "va_cf_reports": ["ReportId"],
    "va_cf_schedule_g": ["ScheduleGId"],
    "va_cf_schedule_h": ["ScheduleHId"],
    "va_cf_schedule_i": ["ScheduleIId", "ReportId", "ScheduleId"],
}
CSV_ENCODINGS = ("cp1252", "utf-8-sig", "latin-1")


def table_for_path(path: Path) -> str:
    compact = re.sub(r"[^a-z0-9]+", "", path.stem.lower())
    for prefix, table in TABLE_BY_PREFIX.items():
        if compact.startswith(prefix):
            return table
    raise ValueError(f"Cannot infer table for {path}")


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def read_header(path: Path) -> list[str]:
    for encoding in CSV_ENCODINGS:
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                reader = csv.reader(handle)
                return next(reader)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("csv", b"", 0, 1, f"Could not decode {path}")


def open_csv_dict(path: Path):
    for encoding in CSV_ENCODINGS:
        try:
            path.read_text(encoding=encoding)
            handle = path.open("r", encoding=encoding, newline="")
            return handle
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("csv", b"", 0, 1, f"Could not decode {path}")


def create_table(conn: sqlite3.Connection, table: str, columns: list[str]) -> None:
    col_defs = [f"{quote_ident(column)} TEXT" for column in columns]
    col_defs.append("source_file TEXT")
    col_defs.append("imported_at TEXT DEFAULT CURRENT_TIMESTAMP")
    conn.execute(f"CREATE TABLE IF NOT EXISTS {quote_ident(table)} ({', '.join(col_defs)})")

    pk_cols = [col for col in PRIMARY_KEYS.get(table, []) if col in columns]
    if pk_cols:
        index_cols = ", ".join(quote_ident(col) for col in pk_cols)
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {quote_ident('idx_' + table + '_natural_key')} "
            f"ON {quote_ident(table)} ({index_cols})"
        )


def import_csv(conn: sqlite3.Connection, path: Path) -> int:
    table = table_for_path(path)
    columns = read_header(path)
    create_table(conn, table, columns)

    pk_cols = [col for col in PRIMARY_KEYS.get(table, []) if col in columns]
    insert_columns = [*columns, "source_file"]
    placeholders = ", ".join("?" for _ in insert_columns)
    quoted_columns = ", ".join(quote_ident(col) for col in insert_columns)

    if pk_cols:
        update_columns = [col for col in insert_columns if col not in pk_cols]
        conflict_cols = ", ".join(quote_ident(col) for col in pk_cols)
        update_clause = ", ".join(
            f"{quote_ident(col)} = excluded.{quote_ident(col)}"
            for col in update_columns
        )
        sql = (
            f"INSERT INTO {quote_ident(table)} ({quoted_columns}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict_cols}) DO UPDATE SET {update_clause}"
        )
    else:
        sql = f"INSERT INTO {quote_ident(table)} ({quoted_columns}) VALUES ({placeholders})"

    written = 0
    with open_csv_dict(path) as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            values = [row.get(column, "") for column in columns]
            values.append(str(path))
            conn.execute(sql, values)
            written += 1

    conn.commit()
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Virginia campaign-finance CSV exports into polls.db.")
    parser.add_argument("files", nargs="*", type=Path, help="CSV files to import. Defaults to open Downloads files.")
    args = parser.parse_args()

    files = args.files or DEFAULT_FILES
    conn = sqlite3.connect(DB_PATH)
    try:
        total = 0
        for path in files:
            if not path.exists():
                print(f"missing: {path}")
                continue
            count = import_csv(conn, path)
            table = table_for_path(path)
            total += count
            print(f"{table}: imported {count} rows from {path.name}")
        print(f"done: imported {total} rows into {DB_PATH.name}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
