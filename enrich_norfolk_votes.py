"""
Enrich Norfolk City Council vote titles with:
  - plain_english: 1-2 sentence plain-English description
  - topic:         short category label (short-term-rental, rezoning, budget, etc.)
  - state_tags:    VA state bill topic keywords for cross-referencing
  - federal_tags:  federal program/bill topic keywords

Results stored in norfolk_vote_enrichment table, keyed by title text.
The database_context layer joins on title to surface descriptions in chat.

Usage:
    python enrich_norfolk_votes.py
    python enrich_norfolk_votes.py --reset   # re-enrich everything
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path

from dotenv import load_dotenv

try:
    from google import genai as _genai
    from google.genai import types as _gtypes
    _GENAI_OK = True
except ImportError:
    _GENAI_OK = False

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH  = BASE_DIR / "polls.db"
GEMINI_MODEL = "gemini-2.5-flash"
BATCH_SIZE = 20

ENRICH_PROMPT = """You are a civic information assistant helping citizens understand what Norfolk, Virginia City Council actually voted on.

For each ordinance title below, return a JSON array where each object has:
{
  "title": "exact title as given",
  "plain_english": "1-2 clear sentences a resident would understand. Start with the action (approved/denied/rezoned/funded/etc.) and the key impact. No legal jargon.",
  "topic": "one of: short-term-rental | rezoning | housing | budget | schools | infrastructure | public-safety | economic-development | environment | personnel | procedural | other",
  "state_tags": ["2-4 Virginia state legislative topic keywords that relate, e.g. 'short-term rental regulation', 'affordable housing', 'school construction bonds'"],
  "federal_tags": ["1-3 federal program or law keywords, e.g. 'HUD community development', 'FEMA flood mitigation', 'FAA airport improvement' — omit if no clear federal connection"]
}

Be specific about LOCATION and PARTIES when mentioned in the title (address, developer name, LLC name).
Return ONLY a valid JSON array. No markdown.

Titles:
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS norfolk_vote_enrichment (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            title           TEXT UNIQUE NOT NULL,
            plain_english   TEXT,
            topic           TEXT,
            state_tags      TEXT,   -- JSON array
            federal_tags    TEXT,   -- JSON array
            enriched_at     TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nve_title ON norfolk_vote_enrichment(title)")
    conn.commit()


def fetch_unenriched_titles(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("""
        SELECT DISTINCT v.title
        FROM norfolk_council_votes v
        LEFT JOIN norfolk_vote_enrichment e ON v.title = e.title
        WHERE v.category IN ('substantive', 'consent')
          AND v.title != ''
          AND e.title IS NULL
        ORDER BY v.title
    """).fetchall()
    return [r[0] for r in rows]


def enrich_batch(titles: list[str], client) -> list[dict]:
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[ENRICH_PROMPT + numbered],
            config=_gtypes.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
            ),
        )
        raw = response.text.strip()
        import re
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception as exc:
        print(f"    Gemini error: {exc}")
        return []


def store_enrichments(conn: sqlite3.Connection, enrichments: list[dict]) -> int:
    count = 0
    for e in enrichments:
        title = e.get("title", "").strip()
        if not title:
            continue
        try:
            conn.execute("""
                INSERT OR REPLACE INTO norfolk_vote_enrichment
                    (title, plain_english, topic, state_tags, federal_tags)
                VALUES (?, ?, ?, ?, ?)
            """, (
                title,
                e.get("plain_english", ""),
                e.get("topic", "other"),
                json.dumps(e.get("state_tags") or []),
                json.dumps(e.get("federal_tags") or []),
            ))
            count += 1
        except Exception as exc:
            print(f"    store error for '{title[:40]}': {exc}")
    conn.commit()
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true",
                        help="Re-enrich all titles (drop existing enrichments first)")
    args = parser.parse_args()

    if not _GENAI_OK:
        raise SystemExit("google-genai not installed. Run: pip install google-genai")

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY not set in .env")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_table(conn)

    if args.reset:
        conn.execute("DELETE FROM norfolk_vote_enrichment")
        conn.commit()
        print("Cleared all enrichments.")

    titles = fetch_unenriched_titles(conn)
    print(f"Titles to enrich: {len(titles)}")
    if not titles:
        print("Nothing to do.")
        conn.close()
        return

    client = _genai.Client(api_key=api_key)
    total = 0
    for i in range(0, len(titles), BATCH_SIZE):
        batch = titles[i: i + BATCH_SIZE]
        print(f"  Batch {i//BATCH_SIZE + 1}: {len(batch)} titles...", end=" ", flush=True)
        enrichments = enrich_batch(batch, client)
        n = store_enrichments(conn, enrichments)
        print(f"{n} stored")
        if i + BATCH_SIZE < len(titles):
            time.sleep(0.5)
        total += n

    conn.close()
    print(f"\nTotal enriched: {total}")


if __name__ == "__main__":
    main()
