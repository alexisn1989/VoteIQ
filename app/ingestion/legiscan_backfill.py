#!/usr/bin/env python3
"""
app/ingestion/legiscan_backfill.py

Backfills va_bills in polls.db from LegiScan dataset ZIPs, one session at a time.

Usage:
    python app/ingestion/legiscan_backfill.py --session-id 2113

Uses getDataset (one ZIP per session) rather than individual getBill calls.
Upserts on (bill_number, legiscan_session_id). Skips procedural
commendation/congratulation bills with empty subjects.
Dataset hash is stored in legiscan_sessions so unchanged sessions are skipped
on subsequent runs.

Do NOT use this script for vote ingestion — bills only.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sqlite3
import urllib.request
import zipfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR         = Path(__file__).resolve().parent.parent.parent
POLLS_DB         = BASE_DIR / "polls.db"
LEGISCAN_API_KEY = os.getenv("LEGISCAN_API_KEY")

STATUS_LABELS: dict[str, str] = {
    "0": "Unknown",
    "1": "Introduced",
    "2": "Engrossed",
    "3": "Enrolled",
    "4": "Passed",
    "5": "Vetoed",
    "6": "Failed",
}

# Titles containing these words (case-insensitive) with no subjects are
# ceremonial resolutions that add no policy signal — skip them.
_PROCEDURAL_WORDS = frozenset({"commending", "congratulating"})


# ── API helper ─────────────────────────────────────────────────────────────────

def _legiscan(op: str, **params) -> dict:
    qs  = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"https://api.legiscan.com/?key={LEGISCAN_API_KEY}&op={op}&{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "VoteIQ/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    if data.get("status") == "ERROR":
        raise RuntimeError(f"LegiScan API error: {data}")
    return data


# ── Helpers ────────────────────────────────────────────────────────────────────

def _subjects_raw(subjects: list) -> str:
    """Flatten LegiScan subjects list → pipe-delimited string of subject names."""
    names: list[str] = []
    for s in subjects:
        if isinstance(s, dict):
            name = s.get("subject_name") or s.get("name") or ""
        else:
            name = str(s)
        name = name.strip()
        if name:
            names.append(name)
    return "|".join(names)


def _is_procedural(title: str, subjects: list) -> bool:
    """True when subjects is empty AND the title is a commendation or congratulation."""
    if subjects:
        return False
    title_lower = title.lower()
    return any(w in title_lower for w in _PROCEDURAL_WORDS)


def _session_label(session_name: str) -> str:
    """
    Map LegiScan session_name → short label used in va_bills.session.

    Examples:
        "2024 Regular Session"         → "2024"
        "2026 Regular Regular Session" → "2026"   (LegiScan duplicate-word quirk)
        "2024 Special I"               → "2024S1"
        "2021 Special II"              → "2021S2"
    """
    m = re.match(r"(\d{4})\s+Regular(?:\s+Regular)?\s+Session", session_name, re.I)
    if m:
        return m.group(1)
    m = re.match(r"(\d{4})\s+Special\s+([IVX]+)", session_name, re.I)
    if m:
        roman = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5"}
        num = roman.get(m.group(2).upper(), m.group(2))
        return f"{m.group(1)}S{num}"
    # Fallback: first 4-digit year
    m = re.search(r"\d{4}", session_name)
    return m.group(0) if m else session_name


# ── Main backfill logic ────────────────────────────────────────────────────────

def backfill(session_id: int) -> None:
    conn = sqlite3.connect(str(POLLS_DB))
    conn.row_factory = sqlite3.Row

    # ── Look up session metadata ──────────────────────────────────────────────
    sess_row = conn.execute(
        "SELECT * FROM legiscan_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not sess_row:
        print(f"ERROR: session_id {session_id} not found in legiscan_sessions.")
        print("       Run schema init script first (creates and populates legiscan_sessions).")
        conn.close()
        return

    session_name  = sess_row["session_name"]
    access_key    = sess_row["access_key"] or ""
    stored_hash   = sess_row["dataset_hash"] or ""
    session_label = _session_label(session_name)

    print(f"Session {session_id}: {session_name!r} -> label={session_label!r}")

    if not access_key:
        print(f"ERROR: no access_key for session {session_id}. Re-run schema init.")
        conn.close()
        return

    # ── Download dataset ZIP ──────────────────────────────────────────────────
    print(f"  Calling getDataset(id={session_id})...")
    data     = _legiscan("getDataset", id=session_id, access_key=access_key)
    ds       = data.get("dataset", {})
    new_hash = ds.get("dataset_hash", "")
    zip_b64  = ds.get("zip", "")

    if not zip_b64:
        print("  ERROR: no zip payload in dataset response.")
        conn.close()
        return

    # Skip when dataset is unchanged since last successful backfill
    if new_hash and new_hash == stored_hash and sess_row["last_backfill"]:
        print(f"  Dataset hash unchanged ({new_hash}). Nothing to do.")
        conn.close()
        return

    zip_bytes = base64.b64decode(zip_b64)
    print(f"  ZIP size: {len(zip_bytes):,} bytes")

    # ── Process bill JSON files ───────────────────────────────────────────────
    inserted = updated = skipped = 0

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        bill_files = [n for n in zf.namelist() if n.endswith(".json") and "/bill/" in n]
        print(f"  Bill JSON files: {len(bill_files)}")

        for fname in bill_files:
            try:
                raw  = json.loads(zf.read(fname))
            except Exception:
                continue
            bill        = raw.get("bill", {})
            bill_number = (bill.get("bill_number") or "").strip()
            if not bill_number:
                continue

            title    = (bill.get("title") or "").strip()
            subjects = bill.get("subjects") or []

            # Skip ceremonial resolutions with no subjects
            if _is_procedural(title, subjects):
                skipped += 1
                continue

            subjects_r      = _subjects_raw(subjects)
            description     = (bill.get("description") or "").strip()
            status_raw      = str(bill.get("status") or "0")
            status_label_s  = STATUS_LABELS.get(status_raw, status_raw)
            introduced_date = (bill.get("introduced_date") or "").strip()
            if not introduced_date:
                # Older LegiScan records only carry status_date
                introduced_date = (bill.get("status_date") or "").strip()

            # ── Upsert on (bill_number, session) — the table's UNIQUE key ────────
            # Track whether the row was new or already existed.
            pre = conn.execute(
                "SELECT id FROM va_bills WHERE bill_number = ? AND session = ?",
                (bill_number, session_label),
            ).fetchone()

            conn.execute(
                """INSERT INTO va_bills
                           (bill_number, session, legiscan_session_id,
                            title, description, status,
                            introduced_date, subjects_raw)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(bill_number, session) DO UPDATE SET
                       legiscan_session_id = excluded.legiscan_session_id,
                       title               = excluded.title,
                       description         = excluded.description,
                       status              = excluded.status,
                       introduced_date     = excluded.introduced_date,
                       subjects_raw        = excluded.subjects_raw""",
                (bill_number, session_label, session_id,
                 title, description, status_label_s,
                 introduced_date, subjects_r),
            )
            if pre:
                updated += 1
            else:
                inserted += 1

    conn.commit()

    # ── Store dataset_hash + backfill timestamp ───────────────────────────────
    conn.execute(
        """UPDATE legiscan_sessions
              SET dataset_hash  = ?,
                  last_backfill = datetime('now')
            WHERE session_id = ?""",
        (new_hash, session_id),
    )
    conn.commit()
    conn.close()

    total = inserted + updated + skipped
    print()
    print(f"  Results — session {session_id} ({session_name}):")
    print(f"    Inserted : {inserted:5d}")
    print(f"    Updated  : {updated:5d}")
    print(f"    Skipped  : {skipped:5d}  (no subjects + commending/congratulating)")
    print(f"    -----------------")
    print(f"    Total    : {total:5d}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="LegiScan bill backfill — one session at a time via getDataset."
    )
    parser.add_argument(
        "--session-id", type=int, required=True,
        help="LegiScan session_id to backfill (e.g. 2113 = VA 2024 Regular).",
    )
    args = parser.parse_args()

    if not LEGISCAN_API_KEY:
        print("ERROR: LEGISCAN_API_KEY not set in environment.")
        return

    backfill(args.session_id)


if __name__ == "__main__":
    main()
