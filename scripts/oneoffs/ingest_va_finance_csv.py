"""
Ingest Virginia state campaign finance CSVs into polls.db.

Expected files (place in VA_FINANCE_DIR or alongside this script):
  Report.csv       — committee/candidate metadata (anchor table)
  ScheduleA.csv    — monetary contributions received
  ScheduleB.csv    — in-kind contributions received
  ScheduleC.csv    — other receipts (interest, refunds)
  ScheduleD.csv    — expenditures
  ScheduleE.csv    — loans received
  ScheduleF.csv    — loan repayments
  ScheduleG.csv    — summary totals per report
  ScheduleH.csv    — balance sheet
  ScheduleI.csv    — dispositions (often empty)

ReportUID is the join key linking all schedules back to Report.csv.
"""

import os
import sys
import sqlite3
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "polls.db")

# Look for CSVs in a finance_csv/ subfolder, then fall back to project root.
VA_FINANCE_DIR = os.path.join(BASE_DIR, "finance_csv")
if not os.path.isdir(VA_FINANCE_DIR):
    VA_FINANCE_DIR = BASE_DIR

# Map CSV filename → SQLite table name
FILE_TABLE_MAP = {
    "Report.csv":    "va_finance_reports",
    "ScheduleA.csv": "va_finance_contributions",
    "ScheduleB.csv": "va_finance_inkind",
    "ScheduleC.csv": "va_finance_other_receipts",
    "ScheduleD.csv": "va_finance_expenditures",
    "ScheduleE.csv": "va_finance_loans",
    "ScheduleF.csv": "va_finance_loan_repayments",
    "ScheduleG.csv": "va_finance_summary",
    "ScheduleH.csv": "va_finance_balance_sheet",
    "ScheduleI.csv": "va_finance_dispositions",
}

AMOUNT_COLS = {
    "Amount", "TotalToDate", "LoanBalance",
    "BeginningBalance", "ContributionsReceived",
    "TotalExpendableFunds", "EndingBalance",
    "TotalReceived", "TotalExpended",
}


def clean_amount(series: pd.Series) -> pd.Series:
    """Strip $ and commas, coerce to float."""
    return (
        series.astype(str)
        .str.replace(r"[\$,]", "", regex=True)
        .str.strip()
        .replace({"": None, "nan": None})
        .astype(float, errors="ignore")
    )


def load_csv(path: str) -> pd.DataFrame:
    for enc in ("utf-8-sig", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(path, dtype=str, encoding=enc, low_memory=False)
            df.columns = [c.strip() for c in df.columns]
            # Clean dollar-amount columns
            for col in df.columns:
                if col in AMOUNT_COLS:
                    df[col] = clean_amount(df[col])
            # Normalize date-ish columns to ISO
            for col in df.columns:
                if "Date" in col or "date" in col:
                    try:
                        df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d")
                    except Exception:
                        pass
            return df
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"Could not decode {path}")


def ingest():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")

    missing = []
    loaded = []

    for filename, table in FILE_TABLE_MAP.items():
        path = os.path.join(VA_FINANCE_DIR, filename)
        if not os.path.exists(path):
            missing.append(filename)
            continue

        df = load_csv(path)
        if df.empty:
            print(f"  {filename}: empty, skipping")
            continue

        df.to_sql(table, con, if_exists="replace", index=False)
        con.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_reportuid ON {table}(ReportUID)")
        if table == "va_finance_reports":
            con.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_year ON {table}(ReportYear)")
            con.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_committee ON {table}(CommitteeCode)")
        con.commit()
        loaded.append((filename, table, len(df)))
        print(f"  {filename} → {table}: {len(df):,} rows")

    con.close()

    if loaded:
        print(f"\nLoaded {len(loaded)} file(s) into {DB_PATH}")
    if missing:
        print(f"\nNot found (place in {VA_FINANCE_DIR}):")
        for f in missing:
            print(f"  {f}")


if __name__ == "__main__":
    print(f"Looking for CSVs in: {VA_FINANCE_DIR}\n")
    ingest()
