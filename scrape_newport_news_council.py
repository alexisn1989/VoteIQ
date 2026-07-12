"""
Scrape Newport News City Council meeting vote records.

Newport News runs CivicWeb (iCompass/Diligent), which — unlike Chesapeake's
Granicus archive or Portsmouth's Laserfiche scans — exposes a purpose-built,
structured "Attendance & Voting Records" search:

    https://nngov.civicweb.net/Portal/VotingRecords.aspx

No PDF parsing or Gemini extraction needed: selecting a member and clicking
"Show Recorded Votes" returns a paginated Telerik RadGrid with one row per
(member, agenda item) — Meeting Name, Meeting Date, Description, Motion,
Vote, Result (which embeds the numeric tally, e.g. "Carried\\n7-0"). The
page is classic ASP.NET WebForms (__doPostBack pagination), so this uses
Playwright rather than plain HTTP requests.

The results grid is member-scoped — there is no "show all members" mode —
so this loops over all 12 real council members (skipping the "-- Select
Member --" placeholder) and paginates each member's full result set via the
RadGrid's "Next Page" control.

Flow:
  1. For each member: select them, set a wide start date, submit
  2. Paginate the RadGrid (click .rgPageNext) until it's gone/disabled
  3. Parse each row into (meeting_date, item title, motion, vote, result,
     vote_count); derive a stable vote_id from (meeting_date, item title) so
     rows from different members on the same agenda item link together
  4. Store in newport_news_council_members / _council_votes /
     _council_member_votes (same shape as chesapeake_/portsmouth_*)

Usage:
    python scrape_newport_news_council.py --start-date "Jan 1 2023"
    python scrape_newport_news_council.py --member 582 --dry-run
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "polls.db"
VOTING_RECORDS_URL = "https://nngov.civicweb.net/Portal/VotingRecords.aspx"

# Member dropdown value -> real name (scraped 2026-07-12 from the Members
# <select> on VotingRecords.aspx — the site has no stable per-member URL,
# only this ASP.NET postback dropdown).
MEMBERS: dict[str, str] = {
    "582": "Cleon M. Long, P.E.",
    "580": "Curtis D. Bethany III",
    "90": "David H. Jenkins",
    "92": "Dr. Patricia P. Woodbury",
    "581": "John R. Eley III",
    "89": "Marcellus L. Harris III, D. Div.",
    "86": "McKinley L. Price, DDS",
    "579": "Phillip Jones",
    "1682": "Robert Coleman",
    "88": "Saundra N. Cherry, D. Min.",
    "91": "Sharon P. Scott, MPA",
    "87": "Tina L. Vick",
}

_VOTE_NORMALIZE = {
    "for": "yes",
    "against": "no",
    "abstain": "abstain",
    "abstained": "abstain",
    "absent": "absent",
    "excused": "absent",
    "recuse": "abstain",
    "recused": "abstain",
}


def _normalize_vote(raw: str) -> str:
    key = (raw or "").strip().lower()
    return _VOTE_NORMALIZE.get(key, key or "unknown")


def _item_number(description: str) -> str:
    """Leading item number from a Description cell, e.g. '3. - Resolution...'
    -> '3'. Falls back to a hash-stable slice of the text if no number."""
    m = re.match(r"\s*(\d+)\.\s*-", description or "")
    return m.group(1) if m else (description or "")[:24]


# ── Schema ─────────────────────────────────────────────────────────────────────

def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS newport_news_council_members (
            id INTEGER PRIMARY KEY, name TEXT, district TEXT, district_num INTEGER,
            email TEXT, jurisdiction TEXT, scraped_date TEXT
        );
        CREATE TABLE IF NOT EXISTS newport_news_council_votes (
            id INTEGER PRIMARY KEY, meeting_date TEXT, title TEXT,
            agenda_item TEXT, motion TEXT, moved_by TEXT, seconded_by TEXT,
            result TEXT, vote_count TEXT, votes_json TEXT, category TEXT, scraped_at TEXT,
            UNIQUE(meeting_date, agenda_item)
        );
        CREATE TABLE IF NOT EXISTS newport_news_council_member_votes (
            id INTEGER PRIMARY KEY, vote_id INTEGER, meeting_date TEXT,
            member_name TEXT, vote TEXT, agenda_item TEXT, title TEXT, result TEXT, category TEXT
        );
    """)
    conn.commit()
    conn.close()
    print(f"[OK] Database initialized: {DB_PATH}")


def seed_members() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM newport_news_council_members")
    for i, (value, name) in enumerate(MEMBERS.items()):
        conn.execute(
            "INSERT INTO newport_news_council_members "
            "(name, district, district_num, email, jurisdiction, scraped_date) "
            "VALUES (?, 'Council Member', ?, '', 'Newport News', date('now'))",
            (name, i),
        )
    conn.commit()
    conn.close()
    print(f"[OK] Seeded {len(MEMBERS)} council members")


# ── Scrape one member's full result set ───────────────────────────────────────

def scrape_member(page, member_value: str, member_name: str, start_date: str) -> list[dict]:
    page.goto(VOTING_RECORDS_URL, timeout=30000)
    page.wait_for_load_state("networkidle", timeout=20000)
    page.select_option("#ctl00_MainContent_Members", member_value)
    page.fill("#ctl00_MainContent_StartDate", start_date)
    page.click("#ctl00_MainContent_ShowVoting", timeout=15000)
    page.wait_for_load_state("networkidle", timeout=20000)

    def _extract_page_rows() -> list[dict]:
        grid = page.query_selector("#ctl00_MainContent_VotingResultsGrid_ctl00")
        if grid is None:
            return []
        out = []
        for tr in grid.query_selector_all("tr"):
            cells = tr.query_selector_all("td")
            if len(cells) < 6:
                continue
            meeting_date = cells[1].inner_text().strip()
            description = cells[2].inner_text().strip()
            if not meeting_date:
                continue
            result_lines = [l for l in cells[5].inner_text().strip().splitlines() if l.strip()]
            out.append({
                "meeting_name": cells[0].inner_text().strip(),
                "meeting_date": meeting_date,
                "description": description,
                "motion": cells[3].inner_text().strip(),
                "vote": _normalize_vote(cells[4].inner_text().strip()),
                "result": result_lines[0] if result_lines else "",
                "vote_count": result_lines[1] if len(result_lines) > 1 else "",
            })
        return out

    rows_out: list[dict] = []
    seen_pages = 0
    prev_page_rows: list[dict] | None = None
    while True:
        seen_pages += 1
        page_rows = _extract_page_rows()
        if not page_rows:
            break
        # The RadGrid's "Next" button has no disabled state on the last page —
        # clicking it just re-renders the same page. Identical (date,
        # description) pairs vs. the previous page means we've looped, not
        # advanced — stop instead of accumulating duplicates.
        page_key = [(r["meeting_date"], r["description"]) for r in page_rows]
        prev_key = [(r["meeting_date"], r["description"]) for r in (prev_page_rows or [])]
        if page_key == prev_key:
            break
        rows_out.extend(page_rows)
        prev_page_rows = page_rows

        next_btn = page.query_selector(".rgPageNext")
        if next_btn is None:
            break
        try:
            next_btn.click(timeout=10000)
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            break
        if seen_pages > 60:  # safety cap — no member has 1,200+ recorded votes
            print(f"    [{member_name}] hit page cap (60) — stopping early")
            break

    print(f"  [{member_name}] {len(rows_out)} rows across {seen_pages} page(s)")
    return rows_out


def store_rows(rows: list[dict]) -> int:
    conn = sqlite3.connect(DB_PATH)
    stored = 0
    for r in rows:
        item_key = _item_number(r["description"])
        vote_key = (r["meeting_date"], f"{item_key}|{r['description'][:120]}")
        cur = conn.execute(
            "SELECT id FROM newport_news_council_votes WHERE meeting_date=? AND agenda_item=?",
            vote_key,
        ).fetchone()
        if cur:
            vote_id = cur[0]
        else:
            conn.execute(
                "INSERT INTO newport_news_council_votes "
                "(meeting_date, title, agenda_item, motion, result, vote_count, category, scraped_at) "
                "VALUES (?, ?, ?, ?, ?, ?, '', datetime('now'))",
                (r["meeting_date"], r["description"], vote_key[1], r["motion"],
                 r["result"], r["vote_count"]),
            )
            vote_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        exists = conn.execute(
            "SELECT 1 FROM newport_news_council_member_votes "
            "WHERE vote_id=? AND member_name=?",
            (vote_id, r["member_name"]),
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO newport_news_council_member_votes "
                "(vote_id, meeting_date, member_name, vote, agenda_item, title, result, category) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, '')",
                (vote_id, r["meeting_date"], r["member_name"], r["vote"],
                 vote_key[1], r["description"], r["result"]),
            )
            stored += 1
    conn.commit()
    conn.close()
    return stored


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrape Newport News council votes")
    ap.add_argument("--start-date", default="Jan 1 2023")
    ap.add_argument("--member", default=None, help="Single member dropdown value (for testing)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("playwright not installed — run: pip install playwright && playwright install chromium")

    init_db()
    seed_members()

    targets = {args.member: MEMBERS[args.member]} if args.member else MEMBERS

    total_stored = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for value, name in targets.items():
            for attempt in range(3):
                try:
                    rows = scrape_member(page, value, name, args.start_date)
                    break
                except Exception as exc:
                    print(f"    [{name}] attempt {attempt + 1} failed: {exc}")
                    if attempt == 2:
                        rows = []
            if args.dry_run:
                for r in rows[:3]:
                    print("   ", r)
                continue
            for r in rows:
                r["member_name"] = name
            total_stored += store_rows(rows)
        browser.close()

    if not args.dry_run:
        conn = sqlite3.connect(DB_PATH)
        votes = conn.execute("SELECT COUNT(*) FROM newport_news_council_votes").fetchone()[0]
        member_votes = conn.execute("SELECT COUNT(*) FROM newport_news_council_member_votes").fetchone()[0]
        conn.close()
        print(f"\nDone — {total_stored} new member-vote rows this run")
        print(f"Totals: {votes} distinct votes, {member_votes} member-vote records")


if __name__ == "__main__":
    main()
