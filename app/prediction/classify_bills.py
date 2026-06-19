"""
One-time offline job to classify va_bills by policy_area.
Do NOT call this in the live request path.
Write results to a new table: va_bill_policy_areas

Usage:
    python app/prediction/classify_bills.py
    python app/prediction/classify_bills.py --dry-run
    python app/prediction/classify_bills.py --distribution-only
    python app/prediction/classify_bills.py --batch-size 25
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from anthropic import Anthropic

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR)))
POLLS_DB = _DATA_DIR / "polls.db"

LABELS = [
    "EDUCATION", "HEALTHCARE", "TAXATION", "ENVIRONMENT",
    "CRIMINAL_JUSTICE", "ELECTIONS", "TRANSPORTATION", "ENERGY",
    "HOUSING", "LABOR", "SOCIAL_SERVICES", "AGRICULTURE", "BUDGET", "OTHER",
]
VALID_LABELS = set(LABELS)

_BATCH_PROMPT = """\
Classify each Virginia bill title into exactly ONE policy area.
Return only a JSON array of labels in the same order as the input, nothing else.
No explanation, no markdown fences — just the JSON array.

Labels: {labels}

Bills:
{numbered_titles}"""

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS va_bill_policy_areas (
    bill_number   TEXT NOT NULL,
    session       TEXT NOT NULL,
    policy_area   TEXT NOT NULL,
    confidence    TEXT CHECK(confidence IN ('high','low')),
    classified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (bill_number, session)
);
"""


def _get_unclassified_bills(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT b.bill_number, b.session, b.title
        FROM va_bills b
        LEFT JOIN va_bill_policy_areas pa
               ON pa.bill_number = b.bill_number AND pa.session = b.session
        WHERE pa.bill_number IS NULL
          AND b.title IS NOT NULL
          AND trim(b.title) != ''
        ORDER BY b.session DESC, b.bill_number
    """).fetchall()
    return [{"bill_number": r[0], "session": r[1], "title": r[2]} for r in rows]


def _classify_batch(client: Anthropic, batch: list[dict]) -> list[str | None]:
    numbered = "\n".join(f"{i + 1}. {b['title']}" for i, b in enumerate(batch))
    prompt = _BATCH_PROMPT.format(
        labels=", ".join(LABELS),
        numbered_titles=numbered,
    )
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # Strip accidental markdown fences
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1].strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()
        labels = json.loads(raw)
        if not isinstance(labels, list):
            return [None] * len(batch)
        result: list[str | None] = []
        for label in labels:
            clean = str(label).strip().upper()
            result.append(clean if clean in VALID_LABELS else None)
        # Pad or trim to match batch size exactly
        while len(result) < len(batch):
            result.append(None)
        return result[: len(batch)]
    except Exception as exc:
        print(f"  [batch error] {exc}")
        return [None] * len(batch)


def run_classification(batch_size: int = 50, dry_run: bool = False) -> None:
    conn = sqlite3.connect(POLLS_DB)
    conn.execute(CREATE_TABLE_SQL)
    conn.commit()

    bills = _get_unclassified_bills(conn)
    total = len(bills)
    total_batches = (total + batch_size - 1) // batch_size
    print(f"Bills to classify: {total}  ({total_batches} batches of {batch_size})")
    if total == 0:
        print("Nothing to do.")
        conn.close()
        return

    client = Anthropic()
    inserted = 0
    parse_failures = 0

    for batch_num, batch_start in enumerate(range(0, total, batch_size), 1):
        batch = bills[batch_start : batch_start + batch_size]
        print(
            f"  Batch {batch_num}/{total_batches}  ({len(batch)} bills)...",
            end=" ",
            flush=True,
        )

        labels = _classify_batch(client, batch)

        rows = []
        for bill, label in zip(batch, labels):
            if label is None:
                parse_failures += 1
                continue
            rows.append((bill["bill_number"], bill["session"], label, "high"))

        if not dry_run and rows:
            conn.executemany(
                """
                INSERT OR REPLACE INTO va_bill_policy_areas
                    (bill_number, session, policy_area, confidence)
                VALUES (?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
            inserted += len(rows)

        print(f"ok ({len(rows)} inserted, {len(batch) - len(rows)} parse-fail)")
        time.sleep(0.3)

    conn.close()
    print(f"\nDone. Inserted: {inserted}  Parse failures: {parse_failures}")
    if dry_run:
        print("[dry-run] Nothing was written to the database.")


def print_distribution() -> None:
    conn = sqlite3.connect(POLLS_DB)
    try:
        rows = conn.execute("""
            SELECT policy_area, COUNT(*) AS n
            FROM va_bill_policy_areas
            GROUP BY policy_area
            ORDER BY n DESC
        """).fetchall()
    finally:
        conn.close()

    if not rows:
        print("va_bill_policy_areas is empty — run classification first.")
        return

    total = sum(r[1] for r in rows)
    print(f"\n{'Policy Area':<25} {'Count':>6}  {'Pct':>6}")
    print("-" * 42)
    for area, count in rows:
        pct = 100 * count / total
        flag = "  <-- WARNING: > 20%" if area == "OTHER" and pct > 20 else ""
        print(f"{area:<25} {count:>6}  {pct:>5.1f}%{flag}")

    other_pct = next((100 * r[1] / total for r in rows if r[0] == "OTHER"), 0.0)
    if other_pct > 20:
        print(
            f"\nWARNING: OTHER is {other_pct:.1f}% of classified bills."
            " Consider expanding the label set before proceeding to Phase 2."
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Classify va_bills by policy area (offline, run once)")
    parser.add_argument("--dry-run", action="store_true", help="Classify but do not write to DB")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--distribution-only", action="store_true", help="Print distribution of existing labels")
    args = parser.parse_args()

    if args.distribution_only:
        print_distribution()
    else:
        run_classification(batch_size=args.batch_size, dry_run=args.dry_run)
        print_distribution()
