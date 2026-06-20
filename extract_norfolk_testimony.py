"""
extract_norfolk_testimony.py
Parse testimony (speaker name, stance, agenda item) from cached Norfolk
council PDFs and store in norfolk_council_testimony SQLite table.

Usage:
    python extract_norfolk_testimony.py            # process all cached PDFs
    python extract_norfolk_testimony.py --dry-run  # print without writing
"""
import argparse
import re
import sqlite3
from pathlib import Path
import os

try:
    from pypdf import PdfReader
    _PYPDF_OK = True
except ImportError:
    _PYPDF_OK = False

DB_PATH   = Path(os.getenv("DATA_DIR", str(Path(__file__).parent))) / "polls.db"
CACHE_DIR = Path(__file__).parent / "norfolk_council_cache"

# Match agenda item label at start of a line: C-4, PH-1, R-7, PH-1a, CA-2 etc.
ITEM_RE = re.compile(r"^((?:C|PH|R|CA|NB|CB)-\d+[a-z]?)\s{1,6}", re.MULTILINE)

# Match speaker lines — no DOTALL so address stays on same line as name
OPPOSE_RE  = re.compile(
    r"([A-Z][a-zA-Z\s\.\,]{2,50}?),\s+(\d+[^\n]{3,60}?),\s+spoke in opposition",
)
SUPPORT_RE = re.compile(
    r"([A-Z][a-zA-Z\s\.\,]{2,50}?),\s+(\d+[^\n]{3,60}?),\s+spoke in support",
)
PRESENT_RE = re.compile(
    r"(?:applicant|owner|representative)[,\s]+([A-Z][a-zA-Z\s\.\,]{2,50}?),\s+(\d+[^\n]{3,60}?),\s+was present",
    re.IGNORECASE,
)
# General public comment (new business / open comment)
COMMENT_RE = re.compile(
    r"([A-Z][a-zA-Z\s\.\,]{2,50}?),\s+(\d+[^\n]{3,60}?),\s+spoke (?:regarding|about|concerning)([^\n]{0,80})",
)


def extract_text(pdf_path: Path, max_pages: int = 15) -> str:
    reader = PdfReader(str(pdf_path))
    pages: list[str] = []
    for i, page in enumerate(reader.pages[:max_pages]):
        t = page.extract_text() or ""
        pages.append(t)
        if "EXHIBITS:" in t or (i > 3 and t.strip().startswith(("1\n", "2\n", "3\n"))):
            break
    return "\n".join(pages)


def parse_testimony(text: str, meeting_date: str) -> list[dict]:
    results: list[dict] = []

    splits = list(ITEM_RE.finditer(text))
    if not splits:
        # No agenda items found — try whole text for public comment
        splits = []

    def _add(item: str, name: str, address: str, stance: str, note: str = ""):
        results.append({
            "meeting_date": meeting_date,
            "agenda_item":  item,
            "speaker_name": name.strip()[:80],
            "speaker_addr": address.strip()[:120],
            "stance":       stance,
            "note":         note.strip()[:200],
        })

    for i, m in enumerate(splits):
        end   = splits[i + 1].start() if i + 1 < len(splits) else len(text)
        block = text[m.start():end]
        item  = m.group(1)

        for x in OPPOSE_RE.finditer(block):
            _add(item, x.group(1), x.group(2), "oppose")
        for x in SUPPORT_RE.finditer(block):
            _add(item, x.group(1), x.group(2), "support")
        for x in PRESENT_RE.finditer(block):
            _add(item, x.group(1), x.group(2), "present")
        for x in COMMENT_RE.finditer(block):
            _add(item, x.group(1), x.group(2), "comment", x.group(3))

    # Deduplicate: drop NEW BUSINESS entries if same speaker already captured
    # under a specific agenda item (public comment section mirrors item comments).
    # Keep NEW BUSINESS only for oppose/support stances not seen elsewhere.
    specific_keys: set[tuple] = set()
    for r in results:
        if r["agenda_item"] != "NEW BUSINESS":
            specific_keys.add((r["speaker_name"].lower(), r["stance"]))

    deduped: list[dict] = []
    seen: set[tuple] = set()
    for r in results:
        # Skip NEW BUSINESS comment duplicates already captured under specific items
        if r["agenda_item"] == "NEW BUSINESS" and r["stance"] == "comment":
            continue
        if r["agenda_item"] == "NEW BUSINESS":
            key = (r["speaker_name"].lower(), r["stance"])
            if key in specific_keys:
                continue
        # Global dedupe on item+name+stance
        uk = (r["agenda_item"], r["speaker_name"].lower(), r["stance"])
        if uk not in seen:
            seen.add(uk)
            deduped.append(r)

    return deduped


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS norfolk_council_testimony (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_date TEXT,
            agenda_item  TEXT,
            speaker_name TEXT,
            speaker_addr TEXT,
            stance       TEXT,  -- oppose | support | present | comment
            note         TEXT,
            scraped_at   TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
        uq_testimony ON norfolk_council_testimony
        (meeting_date, agenda_item, speaker_name, stance)
    """)
    conn.commit()


def meeting_date_from_db(conn: sqlite3.Connection, meeting_id: int) -> str | None:
    row = conn.execute(
        "SELECT meeting_date FROM norfolk_council_votes WHERE meeting_id=? LIMIT 1",
        (meeting_id,),
    ).fetchone()
    return row[0] if row else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    if not _PYPDF_OK:
        print("ERROR: pypdf not installed — pip install pypdf")
        return 1

    pdfs = sorted(CACHE_DIR.glob("meeting_*_minutes.pdf"))
    print(f"{len(pdfs)} PDFs found in {CACHE_DIR}")

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    if not args.dry_run:
        ensure_table(conn)

    total_inserted = 0
    for pdf in pdfs:
        # Extract meeting_id from filename
        m = re.search(r"meeting_(\d+)_", pdf.name)
        if not m:
            continue
        meeting_id  = int(m.group(1))
        meeting_date = meeting_date_from_db(conn, meeting_id)
        if not meeting_date:
            continue

        try:
            text     = extract_text(pdf)
            records  = parse_testimony(text, meeting_date)
        except Exception as e:
            print(f"  {pdf.name}: ERROR {e}")
            continue

        if not records:
            continue

        print(f"  {pdf.name} ({meeting_date}): {len(records)} testimony records")
        for r in records:
            print(f"    [{r['stance'].upper():>7}] {r['agenda_item']:>6}  {r['speaker_name']}")
            if args.dry_run:
                continue
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO norfolk_council_testimony
                    (meeting_date, agenda_item, speaker_name, speaker_addr, stance, note)
                    VALUES (?,?,?,?,?,?)
                """, (
                    r["meeting_date"], r["agenda_item"], r["speaker_name"],
                    r["speaker_addr"], r["stance"], r["note"],
                ))
            except Exception:
                pass
        if not args.dry_run:
            conn.commit()
            total_inserted += len(records)

    conn.close()
    print(f"\nDone. {total_inserted} records inserted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
