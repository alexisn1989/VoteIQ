#!/usr/bin/env python3
"""
Prewarm VoteIQ's query_cache with representative profile answers.

This builds durable cache hits for common "what has my rep done?" questions
using the already-generated va_rep_profiles.jsonl snapshot. It does not call
Claude, OpenStates, LIS, Voyage, or Chroma.

Usage:
    python prewarm_rep_profile_cache.py
    python prewarm_rep_profile_cache.py --dry-run
"""

import argparse
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

import main as voteiq_main


BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "openstates_va.db"
PROFILES_PATH = BASE_DIR / "va_rep_profiles.jsonl"
CANONICAL_MY_REP_QUERY = "what has my rep done?"


def _cache_key(query: str, district_note: str) -> str:
    normalized = re.sub(r"\s+", " ", query.strip().lower())
    raw = f"{normalized}||{district_note}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _district_note(*, hod: str = "", sd: str = "") -> str:
    parts = []
    if hod:
        parts.append(f"HOD district: {hod}")
    if sd:
        parts.append(f"Senate district: {sd}")
    return f"\nUSER'S DISTRICT CONTEXT: {', '.join(parts)}\n" if parts else ""


def _is_senate_profile(text: str) -> bool:
    return "Virginia Senate" in text


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def _profile_chamber(profile: dict) -> str:
    meta = profile.get("metadata") or {}
    name = _norm_name(meta.get("name", ""))
    sd_names = {_norm_name(info.get("senator")) for info in voteiq_main.SD_CONTEXT.values()}
    hod_names = {_norm_name(info.get("delegate")) for info in voteiq_main.HOD_CONTEXT.values()}
    if name in sd_names:
        return "senate"
    if name in hod_names:
        return "house"
    return "senate" if _is_senate_profile(profile.get("text", "")) else "house"


def _line_after(text: str, prefix: str) -> str:
    for line in text.splitlines():
        if line.startswith(prefix):
            return line
    return ""


def _section(text: str, marker: str, stop_markers: tuple[str, ...]) -> str:
    start = text.find(marker)
    if start < 0:
        return ""
    end = len(text)
    for stop in stop_markers:
        pos = text.find(stop, start + len(marker))
        if pos > start:
            end = min(end, pos)
    return text[start:end].strip()


def _first_bullets(section: str, max_items: int) -> list[str]:
    items = []
    for line in section.splitlines():
        cleaned = line.strip()
        if cleaned.startswith("-"):
            items.append(cleaned)
        elif cleaned.startswith("[") or re.match(r"^[A-Za-z &]+:", cleaned):
            items.append(f"- {cleaned}")
        if len(items) >= max_items:
            break
    return items


def _profile_reply(profile: dict) -> str:
    text = profile["text"]
    meta = profile.get("metadata") or {}
    name = meta.get("name", "This representative")
    party = meta.get("party", "")
    district = meta.get("district", "")
    chamber_type = _profile_chamber(profile)
    chamber = "state senator" if chamber_type == "senate" else "state delegate"
    district_part = f", District {district}" if district else ""
    party_part = f" ({party}{district_part})" if party else (f" (District {district})" if district else "")

    caucus = _line_after(text, "Caucus read")
    floor_record = _section(
        text,
        "[CONFIRMED — OpenStates floor vote records",
        ("  [CONFIRMED — OpenStates vote records] Voted NO", "[CONFIRMED — OpenStates committee vote records"),
    )
    key_votes = _section(
        text,
        "[CONFIRMED — OpenStates vote records] Key votes",
        ("[CONFIRMED — OpenStates floor vote records",),
    )
    sponsored = _section(
        text,
        "[CONFIRMED — OpenStates sponsorship record]",
        ("[CONFIRMED — OpenStates vote records] Key votes",),
    )
    partnerships = _section(
        text,
        "[CONFIRMED — OpenStates sponsorship records] Legislative partnerships",
        ("[METHODOLOGY",),
    )
    methodology = _section(text, "[METHODOLOGY", ())

    lines = [f"Your {chamber} profile is **{name}{party_part}**."]
    if caucus:
        lines.append(f"\n**Caucus Read:**\n- {caucus.split(':', 1)[-1].strip()}")
    if floor_record:
        bullets = _first_bullets(floor_record, 3)
        if bullets:
            lines.append("\n**2026 Session Voting Record:**")
            lines.extend(bullets)
    if key_votes:
        bullets = _first_bullets(key_votes, 5)
        if bullets:
            lines.append("\n**Key Votes:**")
            lines.extend(bullets)
    if sponsored:
        bullets = _first_bullets(sponsored, 6)
        if bullets:
            lines.append("\n**Bills Sponsored:**")
            lines.extend(bullets)
    if partnerships:
        bullets = _first_bullets(partnerships, 5)
        if bullets:
            lines.append("\n**Legislative Partnerships:**")
            lines.extend(bullets)
    if methodology:
        lines.append(
            "\n**Methodology note:** Caucus read and party alignment are derived from "
            "OpenStates roll-call data. Issue-area tags are keyword-based, not official categories."
        )
    lines.append("\n**Data source:** OpenStates and LIS cached snapshot.")
    lines.append(
        f"\n**Want to dig deeper?** Ask me how {name} voted on "
        f"[gun legislation](#ask:How did {name} vote on gun legislation?), "
        f"[education](#ask:How did {name} vote on education?), "
        f"or [housing](#ask:How did {name} vote on housing?). "
        f"Or ask about a specific bill by number."
    )
    return "\n".join(lines)


def _name_variants(name: str) -> set[str]:
    cleaned = re.sub(r"\s+", " ", name or "").strip()
    if not cleaned:
        return set()
    variants = {cleaned}
    no_periods = cleaned.replace(".", "")
    variants.add(no_periods)
    parts = cleaned.split()
    if len(parts) >= 3:
        variants.add(f"{parts[0]} {parts[-1]}")
        variants.add(f"{parts[0].replace('.', '')} {parts[-1].replace('.', '')}")
    return {v.strip() for v in variants if v.strip()}


def _load_profiles(session: str | None = "2026") -> list[dict]:
    profiles = []
    with PROFILES_PATH.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                profile = json.loads(line)
                meta = profile.get("metadata") or {}
                profile_session = str(meta.get("session") or profile.get("session_id") or "")
                if session and profile_session != session:
                    continue
                profiles.append(profile)
    return profiles


def _init_cache(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS query_cache "
        "(cache_key TEXT PRIMARY KEY, reply TEXT, created_at INTEGER)"
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(query_cache)").fetchall()}
    if "cache_type" not in cols:
        conn.execute("ALTER TABLE query_cache ADD COLUMN cache_type TEXT DEFAULT 'ad_hoc'")
    conn.commit()


def prewarm(dry_run: bool = False, session: str | None = "2026", append: bool = False) -> int:
    if not DB_PATH.exists():
        raise SystemExit(f"ERROR: {DB_PATH} not found")
    if not PROFILES_PATH.exists():
        raise SystemExit(f"ERROR: {PROFILES_PATH} not found. Run build_rep_profiles.py first.")

    profiles = _load_profiles(session=session)
    conn = sqlite3.connect(DB_PATH)
    _init_cache(conn)
    now = int(time.time())

    rows = []
    for profile in profiles:
        meta = profile.get("metadata") or {}
        name = meta.get("name", "").strip()
        district = str(meta.get("district") or "").strip()
        reply = _profile_reply(profile)
        text = profile.get("text", "")

        if name:
            for n in _name_variants(name):
                query_variants = {
                    f"what has {n} done?",
                    f"tell me about {n}",
                    f"{n} voting record",
                }
                for query in query_variants:
                    rows.append((_cache_key(query, ""), reply, now, "prewarm"))
        if district:
            note = _district_note(sd=district) if _profile_chamber(profile) == "senate" else _district_note(hod=district)
            rows.append((_cache_key(CANONICAL_MY_REP_QUERY, note), reply, now, "prewarm"))

    # Dedupe by cache key, keeping latest row.
    deduped = {row[0]: row for row in rows}
    if dry_run:
        print(f"[prewarm] would write {len(deduped)} cache entries from {len(profiles)} profiles")
        return len(deduped)

    if not append:
        conn.execute("DELETE FROM query_cache WHERE cache_type='prewarm'")
    conn.executemany(
        "INSERT OR REPLACE INTO query_cache (cache_key, reply, created_at, cache_type) VALUES (?, ?, ?, ?)",
        list(deduped.values()),
    )
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM query_cache WHERE cache_type='prewarm'").fetchone()[0]
    conn.close()
    print(f"[prewarm] wrote {len(deduped)} entries from {len(profiles)} profiles")
    print(f"[prewarm] durable prewarm cache entries: {total}")
    return len(deduped)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prewarm representative profile query cache")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--session", default="2026", help="Profile session to prewarm; use ALL for every session")
    parser.add_argument("--append", action="store_true", help="Append to existing prewarm entries instead of replacing them")
    args = parser.parse_args()
    session = None if str(args.session).upper() == "ALL" else args.session
    prewarm(dry_run=args.dry_run, session=session, append=args.append)


if __name__ == "__main__":
    main()
