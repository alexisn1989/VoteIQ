#!/usr/bin/env python3
"""
Normalize messy business/org names in Virginia campaign finance CSVs using
fuzzy matching + Gemini.

Pipeline:
  1. Extract unique org names (IsIndividual=False) from all finance_csv/ CSVs
  2. Fuzzy-cluster similar names with rapidfuzz (threshold configurable)
  3. Send suspicious clusters to Gemini to confirm identity + pick canonical name
  4. Store canonical mappings in polls.db: name_normalization table

Usage:
    python normalize_va_finance_names.py              # dry run, show clusters
    python normalize_va_finance_names.py --apply      # write to DB
    python normalize_va_finance_names.py --since 2024_01          # only recent periods
    python normalize_va_finance_names.py --threshold 88 --apply   # adjust fuzzy threshold
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from dotenv import load_dotenv

try:
    from rapidfuzz import fuzz, process as rf_process
    _RAPIDFUZZ = True
except ImportError:
    _RAPIDFUZZ = False

try:
    from google import genai as _genai
    from google.genai import types as _gtypes
    _GENAI_OK = True
except ImportError:
    _GENAI_OK = False

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
FINANCE_DIR = BASE_DIR / "finance_csv"
DB_PATH = BASE_DIR / "polls.db"
GEMINI_MODEL = "gemini-2.5-flash"

CSV_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")

# Legal-suffix tokens to strip before comparing (keep original for display)
_SUFFIX_RE = re.compile(
    r"\b(LLC|L\.L\.C|INC|INCORPORATED|CORP|CORPORATION|CO\b|LTD|LP|LLP|"
    r"P\.C\.|PC|PLLC|DBA|D/B/A|T/A|GROUP|GRP|ASSOC|ASSOCIATION|ENTERPRISES?|"
    r"ENT|HOLDINGS?|SERVICES?|SVC|SOLUTIONS?|CONSULTING|CONSULTANTS?)\b\.?",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^A-Z0-9 ]")

GEMINI_PROMPT = """You are a data-quality expert reviewing Virginia campaign finance filings.

Below are groups of business/organization name variants found across different filing periods.
Each group is a numbered cluster of names that a fuzzy-matching algorithm flagged as possibly
the same entity.

For each cluster:
- If all names clearly refer to the same organization, return the single best canonical name
  (most complete/official version, proper capitalization, include the legal suffix).
- If names in the cluster are actually DIFFERENT organizations, split them by returning
  each as its own canonical name.
- Ignore clusters where the variants are already identical or trivially different (spacing/punctuation).

Return ONLY a JSON object in this format:
{{
  "1": {{
    "canonical": "Best Official Name LLC",
    "members": ["name variant 1", "name variant 2"]
  }},
  "2": {{
    "canonical": "Another Org Inc",
    "members": ["name variant a"]
  }}
}}

Clusters to review:
{clusters_json}
"""


def _read_csv(path: Path) -> list[dict]:
    for enc in CSV_ENCODINGS:
        try:
            with path.open("r", encoding=enc, newline="") as fh:
                return list(csv.DictReader(fh))
        except UnicodeDecodeError:
            continue
    return []


def extract_org_names(finance_dir: Path, since: str | None) -> dict[str, int]:
    """Return {raw_name: occurrence_count} for all org names in ScheduleA + ScheduleD."""
    counts: dict[str, int] = {}
    periods = sorted(p.name for p in finance_dir.iterdir() if p.is_dir())
    if since:
        periods = [p for p in periods if p >= since]

    for period in periods:
        for schedule in ("ScheduleA.csv", "ScheduleD.csv"):
            path = finance_dir / period / schedule
            if not path.exists():
                continue
            for row in _read_csv(path):
                is_individual = row.get("IsIndividual", "True").strip().lower()
                if is_individual == "true":
                    continue
                name = row.get("LastOrCompanyName", "").strip()
                if name and len(name) > 2:
                    counts[name] = counts.get(name, 0) + 1

    return counts


def _normalize_key(name: str) -> str:
    """Collapse a name to a comparison key (uppercase, no suffixes, no punct)."""
    key = name.upper()
    key = _SUFFIX_RE.sub(" ", key)
    key = _PUNCT_RE.sub(" ", key)
    key = re.sub(r"\s+", " ", key).strip()
    return key


def build_clusters(name_counts: dict[str, int], threshold: int) -> list[list[str]]:
    """Group names whose normalized keys are within `threshold` fuzzy similarity."""
    if not _RAPIDFUZZ:
        print("ERROR: rapidfuzz not installed — run: pip install rapidfuzz")
        return []

    names = sorted(name_counts.keys(), key=lambda n: -name_counts[n])
    keys = [_normalize_key(n) for n in names]

    assigned: set[int] = set()
    clusters: list[list[str]] = []

    for i, (name, key) in enumerate(zip(names, keys)):
        if i in assigned or not key:
            continue
        cluster = [name]
        assigned.add(i)
        for j in range(i + 1, len(names)):
            if j in assigned:
                continue
            score = fuzz.token_sort_ratio(key, keys[j])
            if score >= threshold:
                cluster.append(names[j])
                assigned.add(j)
        if len(cluster) > 1:
            clusters.append(cluster)

    return clusters


def gemini_canonicalize(clusters: list[list[str]], api_key: str, batch_size: int = 15) -> dict[str, str]:
    """Ask Gemini to confirm clusters and return {raw_name: canonical_name} mapping."""
    if not _GENAI_OK:
        print("ERROR: google-genai not installed")
        return {}

    client = _genai.Client(api_key=api_key)
    mapping: dict[str, str] = {}

    for start in range(0, len(clusters), batch_size):
        batch = clusters[start: start + batch_size]
        clusters_json = json.dumps(
            {str(i + 1): batch[i] for i in range(len(batch))},
            indent=2,
        )
        prompt = GEMINI_PROMPT.format(clusters_json=clusters_json)

        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
            )
            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            result = json.loads(text)
            for cluster_data in result.values():
                canonical = cluster_data.get("canonical", "").strip()
                members = cluster_data.get("members", [])
                if canonical and members:
                    for raw_name in members:
                        mapping[raw_name] = canonical
        except Exception as exc:
            print(f"  Gemini error (batch {start // batch_size + 1}): {exc}")

        if start + batch_size < len(clusters):
            time.sleep(2)

    return mapping


def setup_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS name_normalization (
            raw_name      TEXT PRIMARY KEY,
            canonical     TEXT NOT NULL,
            source        TEXT DEFAULT 'gemini',
            created_at    TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_name_norm_canonical ON name_normalization(canonical)"
    )
    conn.commit()


def store_mapping(conn: sqlite3.Connection, mapping: dict[str, str]) -> int:
    stored = 0
    for raw, canonical in mapping.items():
        if raw == canonical:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO name_normalization (raw_name, canonical) VALUES (?, ?)",
            (raw, canonical),
        )
        stored += 1
    conn.commit()
    return stored


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize org names in VA campaign finance data")
    parser.add_argument("--since", default=None, help="Only process periods >= this (e.g. 2020_01)")
    parser.add_argument("--threshold", type=int, default=87, help="Fuzzy match threshold 0-100 (default 87)")
    parser.add_argument("--batch-size", type=int, default=15, help="Clusters per Gemini call (default 15)")
    parser.add_argument("--apply", action="store_true", help="Write canonical names to DB")
    parser.add_argument("--top", type=int, default=0, help="Only process top N most-common names (0=all)")
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set in .env")
        return

    print(f"Scanning {FINANCE_DIR} for org names" + (f" since {args.since}" if args.since else "") + "...")
    name_counts = extract_org_names(FINANCE_DIR, args.since)
    print(f"Found {len(name_counts):,} unique org names")

    if args.top:
        top_names = dict(sorted(name_counts.items(), key=lambda x: -x[1])[: args.top])
        print(f"Limiting to top {args.top} by occurrence count")
    else:
        top_names = name_counts

    print(f"Building fuzzy clusters (threshold={args.threshold})...")
    clusters = build_clusters(top_names, args.threshold)
    print(f"Found {len(clusters)} clusters with 2+ variants")

    if not clusters:
        print("No clusters to process.")
        return

    for i, cluster in enumerate(clusters[:20], 1):
        print(f"\n  Cluster {i}:")
        for name in cluster:
            print(f"    [{name_counts.get(name, 0):>4}x] {name}")
    if len(clusters) > 20:
        print(f"\n  ... and {len(clusters) - 20} more clusters")

    if not args.apply:
        print(f"\n[dry run] {len(clusters)} clusters ready for Gemini. Pass --apply to process.")
        return

    print(f"\nSending {len(clusters)} clusters to Gemini in batches of {args.batch_size}...")
    mapping = gemini_canonicalize(clusters, api_key, args.batch_size)
    print(f"Received {len(mapping)} name mappings from Gemini")

    changes = {raw: canonical for raw, canonical in mapping.items() if raw != canonical}
    print(f"\n{len(changes)} actual name changes:")
    for raw, canonical in sorted(changes.items())[:50]:
        print(f"  {raw!r:50s}  →  {canonical!r}")
    if len(changes) > 50:
        print(f"  ... and {len(changes) - 50} more")

    conn = sqlite3.connect(DB_PATH)
    setup_db(conn)
    existing_rows = conn.execute("SELECT COUNT(*) FROM name_normalization").fetchone()[0]
    stored = store_mapping(conn, mapping)
    conn.close()
    print(f"\nStored {stored} mappings in name_normalization (was {existing_rows} rows, now {existing_rows + stored})")
    print(f"DB: {DB_PATH}")


if __name__ == "__main__":
    main()
