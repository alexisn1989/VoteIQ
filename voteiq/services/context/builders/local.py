"""Local council and district-representation builders.

Moved from database_context.py.  Imports the shared DB substrate from _db
so builders here work with the same connection cache and test fixtures as
the rest of the context system.
"""
from __future__ import annotations

import difflib
import json
import re
import sqlite3
from datetime import datetime

from voteiq.services.context._db import BASE_DIR, _connect, _table_exists
from voteiq.services.geo_lite import extract_address, geocode_lite, haversine_miles

# ── City → district mapping for "who represents me in [city]" queries ─────────
# Keyed by lowercase city name. state_reps = [(chamber, district), ...]
_CITY_REPRESENTATIVES: dict[str, dict] = {
    "portsmouth": {
        "city": "Portsmouth, VA",
        "note": (
            "Portsmouth, VA is in Virginia House District 88, "
            "Senate District 18, and U.S. Congressional District 2 (VA-02)."
        ),
        "state_reps": [("Delegate", "88"), ("Senator", "18")],
        "federal_district": "2",
    },
    "virginia beach": {
        "city": "Virginia Beach, VA",
        "note": (
            "Virginia Beach, VA is in U.S. Congressional District 2 (VA-02). "
            "State House and Senate districts vary by neighborhood within the city."
        ),
        "state_reps": [],
        "federal_district": "2",
    },
    "norfolk": {
        "city": "Norfolk, VA",
        "note": (
            "Norfolk, VA is in U.S. Congressional District 2 (VA-02). "
            "State districts vary by neighborhood."
        ),
        "state_reps": [],
        "federal_district": "2",
    },
    "chesapeake": {
        "city": "Chesapeake, VA",
        "note": (
            "Chesapeake, VA is in U.S. Congressional District 2 (VA-02). "
            "State districts vary by neighborhood."
        ),
        "state_reps": [],
        "federal_district": "2",
    },
    "hampton": {
        "city": "Hampton, VA",
        "note": (
            "Hampton, VA is in U.S. Congressional District 2 (VA-02). "
            "State districts vary by neighborhood."
        ),
        "state_reps": [],
        "federal_district": "2",
    },
}

_REPRESENTS_ME_RE = re.compile(
    r"\b(who\s+represents?\s+me|my\s+rep(?:resentatives?)?|represent(?:ing|s|ed)\s+me"
    r"|my\s+delegate|my\s+senator|my\s+congress(?:man|woman|member|person)?"
    r"|who\s+is\s+my\s+rep|who\s+represents?\s+\w+\s*,?\s*va)\b",
    re.I,
)


def _add_city_district_context(blocks: list[str], query: str) -> None:
    """Inject legislator records when a known city appears in a 'who represents me' query."""
    if not _REPRESENTS_ME_RE.search(query):
        return
    q_lower = (query or "").lower()
    city_info = next(
        (v for k, v in _CITY_REPRESENTATIVES.items() if k in q_lower), None
    )
    if not city_info:
        return

    conn = _connect("polls")
    if not conn:
        return

    lines: list[str] = [city_info["note"], ""]
    try:
        # State legislators
        for chamber, district in city_info.get("state_reps", []):
            row = conn.execute(
                "SELECT l.name, l.party, l.chamber, l.district, "
                "       n.narrative_short "
                "FROM legislators l "
                "LEFT JOIN legislator_narratives n ON lower(n.name) LIKE '%' || lower(l.name) || '%' "
                "WHERE l.chamber=? AND l.district=? AND l.active=1 "
                "LIMIT 1",
                (chamber, district),
            ).fetchone()
            if row:
                short = (row["narrative_short"] or "").strip()[:300]
                lines.append(
                    f"Virginia {row['chamber']}, District {row['district']}: "
                    f"{row['name']} ({row['party']})"
                )
                if short:
                    lines.append(f"  Profile: {short}")

        # Federal representative
        fed_district = city_info.get("federal_district")
        if fed_district:
            row = conn.execute(
                "SELECT name, party, district FROM congress_members "
                "WHERE state='Virginia' AND district=? AND chamber='House of Representatives' "
                "LIMIT 1",
                (fed_district,),
            ).fetchone()
            if row:
                lines.append(
                    f"U.S. Representative, VA-{str(row['district']).zfill(2)}: "
                    f"{row['name']} ({row['party']})"
                )
    except Exception:
        pass
    finally:
        conn.close()

    if len(lines) > 2:
        blocks.append(
            f"[Database Context - representatives for {city_info['city']}]\n"
            + "\n".join(lines)
            + "\n\nSource: VoteIQ SQL database (legislators + congress_members)."
        )


# ── Local elected officials — Chesapeake/Hampton/Portsmouth/Suffolk/Newport News ──
# voteiq_officials.json is a general roster (Mayor, Council, School Board, Sheriff,
# Commonwealth's Attorney, etc.) covering 6 Hampton Roads localities. Norfolk and
# Virginia Beach are deliberately excluded here — they already have deeper
# dedicated builders (_add_norfolk_council_context / _add_vb_council_context) with
# real vote data, and this roster-only block would be a shallower duplicate.
_OFFICIALS_PATH = BASE_DIR / "voteiq_officials.json"
_OFFICIALS_LOCALITIES = ("chesapeake", "hampton", "portsmouth", "suffolk", "newport news")

_LOCAL_OFFICIALS_TRIGGER = re.compile(
    r"(chesapeake|hampton|portsmouth|suffolk|newport\s+news)"
    r".*(council|mayor|school\s*board|sheriff|commonwealth.s\s+attorney|"
    r"treasurer|clerk|commissioner|elected|official)"
    r"|(council|mayor|school\s*board|sheriff|elected|official)"
    r".*(chesapeake|hampton|portsmouth|suffolk|newport\s+news)",
    re.IGNORECASE,
)

_officials_cache: list[dict] | None = None


def _load_officials() -> list[dict]:
    global _officials_cache
    if _officials_cache is None:
        try:
            with open(_OFFICIALS_PATH, encoding="utf-8") as f:
                _officials_cache = json.load(f)
        except Exception:
            _officials_cache = []
    return _officials_cache


def _add_local_officials_context(blocks: list[str], query: str) -> None:
    """Roster-only local officials (Mayor, Council, School Board, row offices)
    for Hampton Roads localities that don't have a dedicated vote-data builder.

    Council rows for cities with a dedicated scraped-vote builder (Chesapeake,
    Portsmouth) are excluded here only when that builder has real data to
    serve (see _council_vote_data_available). This still serves those cities'
    other offices (Mayor, Sheriff, School Board, etc.) either way, since the
    dedicated builders only cover Council.
    """
    if not _LOCAL_OFFICIALS_TRIGGER.search(query or ""):
        return
    q_lower = (query or "").lower()
    locality = next((loc for loc in _OFFICIALS_LOCALITIES if loc in q_lower), None)
    if not locality:
        return

    officials = _load_officials()
    matches = [o for o in officials if (o.get("jurisdiction") or "").lower() == locality]
    scrape_prefix = locality.replace(" ", "_")  # "newport news" -> "newport_news"
    council_votes_live = (
        locality in ("chesapeake", "portsmouth", "newport news", "hampton")
        and _council_vote_data_available(scrape_prefix)
    )
    if council_votes_live:
        # startswith, not ==: Newport News uses ward-suffixed labels
        # ("Newport News Council 1/2/3"), not a flat "{City} Council".
        council_prefix = f"{locality.title()} Council"
        matches = [o for o in matches if not (o.get("district") or "").startswith(council_prefix)]
    if not matches:
        return

    lines = [f"[RETRIEVED RECORD - {locality.title()} Elected Officials]"]
    for o in matches:
        party = f" ({o['party']})" if o.get("party") else ""
        lines.append(f"- {o.get('district', 'Unknown office')}: {o.get('name', '')}{party}")
        if o.get("url"):
            lines.append(f"  Source: {o['url']}")
    no_votes_note = (
        "This is a roster only — Council vote records are available separately; "
        "vote records for other offices on this list are not tracked."
        if council_votes_live else
        "This is a roster only — vote records are not available for this locality."
    )
    lines.append(
        f"\nSource: VoteIQ local officials roster (scraped from official city sites). {no_votes_note}"
    )
    blocks.append("\n".join(lines))


_NORFOLK_COUNCIL_TRIGGER = re.compile(
    r"\b("
    r"norfolk\s+(city\s+)?council"
    r"|city\s+council\s+vote"
    r"|norfolk\s+vote"
    r"|council\s+member\s+(alexander|clanton|doyle|johnson|mcgee|paige|smigiel|thomas)"
    r"|norfolk\s+(mayor|councilmember|councilman|councilwoman)"
    r"|norfolk\s+(passed|voted|approved|denied|tabled)"
    r")\b",
    re.I,
)
_NORFOLK_MEMBER_NAMES = {
    "alexander", "clanton", "doyle", "johnson", "mcgee",
    "paige", "smigiel", "thomas", "royster", "mcclellan",
    "riddick",   # on council 2022–2023, before Paige/Clanton/McGee joined
}

# ── Norfolk factsheet + lint helpers ─────────────────────────────────────────

CONTESTED_VOTE_MIN = 100  # body-level minimum for comparative member differentiation

_NORFOLK_BANNED_OUTPUT_TERMS = frozenset([
    # Causal / intent (from donor-vote schema rework)
    "captured", "market protection", "bought", "because donors",
    "in exchange", "rewarded", "benefit directly",
    "paid to vote", "quid pro quo", "builders fund him",
    "because he opposes", "because she", "hotels benefit",
    # Ideological / motive (factsheet addition — per Step 5)
    "ideology", "ideological", "liberal", "conservative",
    "progressive", "believes", " stance", "is pro-", "is anti-",
    "aligns with the", "represents a", "characterized as",
])


def _lint_norfolk_output(text: str) -> str:
    """Raise ValueError if any banned causal or ideological term appears in output."""
    low = text.lower()
    for term in _NORFOLK_BANNED_OUTPUT_TERMS:
        if term in low:
            raise ValueError(f"[NORFOLK-LINT FAIL] Banned term: {repr(term)}")
    return text


def _norfolk_body_summary(conn: "sqlite3.Connection") -> list[str]:
    """Body-level consensus summary with methodology gate."""
    body = conn.execute("""
        SELECT COUNT(*) total,
            SUM(CASE WHEN vote_count IN ('8-0','7-0','6-0','5-0','0-8','0-7','0-6','0-5')
                     THEN 1 ELSE 0 END) unani
        FROM norfolk_council_votes WHERE vote_count != ''
    """).fetchone()
    if not body or not body[0]:
        return []

    total, unani = body[0], body[1] or 0
    contested = total - unani
    consensus_pct = round(100 * unani / total, 1)

    dates = conn.execute(
        "SELECT MIN(meeting_date), MAX(meeting_date) FROM norfolk_council_votes"
    ).fetchone()
    date_range = f"{dates[0]} – {dates[1]}" if dates and dates[0] else "2024–2026"

    lines: list[str] = [
        f"### Norfolk City Council — Body Summary ({date_range})",
        f"Recorded votes: {total}  |  Consensus rate: {consensus_pct}% "
        f"({unani} unanimous or near-unanimous)  |  "
        f"Contested: {contested} ({round(100*contested/total, 1)}%)",
    ]

    member_rows = conn.execute("""
        SELECT
            CASE WHEN member_name LIKE 'Smigiel%' THEN 'Smigiel Jr.'
                 WHEN member_name LIKE 'Thomas%'  THEN 'Thomas Jr.'
                 ELSE member_name END AS nm,
            COUNT(*) total,
            SUM(CASE WHEN vote='yes'    THEN 1 ELSE 0 END) yes_v,
            SUM(CASE WHEN vote='no'     THEN 1 ELSE 0 END) no_v,
            SUM(CASE WHEN vote='absent' THEN 1 ELSE 0 END) absent_v
        FROM norfolk_council_member_votes
        GROUP BY nm ORDER BY nm
    """).fetchall()

    if member_rows:
        lines += [
            "",
            f"{'Member':<20} {'Votes':>6} {'YES':>5} {'NO':>5} "
            f"{'Abs':>5} {'YES%':>6} {'NO%':>5}",
            "-" * 62,
        ]
        for r in member_rows:
            tot = r[1] or 1
            lines.append(
                f"{r[0]:<20} {r[1]:>6} {r[2]:>5} {r[3]:>5} {r[4]:>5} "
                f"{100*r[2]/tot:>5.1f}% {100*r[3]/tot:>4.1f}%"
            )

    # ── Topic / policy-area breakdown ────────────────────────────────────────
    # Enrichment is supplemental — skip the topic breakdown when the table is
    # absent so the body summary still renders member rows + methodology gate.
    topic_rows = []
    if _table_exists(conn, "norfolk_vote_enrichment"):
        topic_rows = conn.execute("""
            SELECT e.topic,
                   COUNT(*) total_votes,
                   SUM(CASE WHEN v.vote_count NOT IN ('8-0','7-0','6-0','5-0','0-8','0-7','0-6','0-5','')
                            THEN 1 ELSE 0 END) contested_votes
            FROM norfolk_vote_enrichment e
            JOIN norfolk_council_votes v ON v.title = e.title
            WHERE e.topic IS NOT NULL AND e.topic != 'procedural'
            GROUP BY e.topic
            ORDER BY total_votes DESC
            LIMIT 12
        """).fetchall()

    if topic_rows:
        lines += ["", "**Legislative agenda by topic** (excludes procedural votes)"]
        lines.append(f"  {'Topic':<26} {'Votes':>6}  {'Contested':>9}  {'Contested%':>10}  Stance")
        lines.append("  " + "-" * 70)
        for topic, tvotes, tcontest in topic_rows:
            pct = round(100 * tcontest / tvotes) if tvotes else 0
            stance = (
                "Most contested" if pct >= 15
                else "Occasionally divided" if pct >= 8
                else "Near-unanimous"
            )
            lines.append(
                f"  {topic:<26} {tvotes:>6}  {tcontest:>9}  {pct:>9d}%  {stance}"
            )

    gate_pass = contested >= CONTESTED_VOTE_MIN
    lines += [
        "",
        "**Methodology gate**",
        f"Contested vote count: {contested}  |  Threshold (CONTESTED_VOTE_MIN): {CONTESTED_VOTE_MIN}",
        (
            f"GATE NOT MET — comparative member rankings, agreement-score clustering, "
            f"and ideal-point analysis are suppressed. "
            f"This body decides by consensus ({consensus_pct}% of votes unanimous or near-unanimous); "
            f"the contested-vote sample ({contested} votes) is below the minimum required "
            f"for individual differentiation. Descriptive factsheets are available per member."
        ) if not gate_pass else (
            f"GATE MET — {contested} contested votes exceed threshold; "
            f"comparative analysis may proceed."
        ),
    ]
    return lines


def _norfolk_member_factsheet(
    conn: "sqlite3.Connection", member_term: str
) -> list[str]:
    """Per-member descriptive voting factsheet (no comparative ranking)."""
    display = member_term.title()
    if member_term == "smigiel":
        display = "Smigiel Jr."
    elif member_term == "thomas":
        display = "Thomas Jr."
    like = f"%{member_term}%"

    raw = conn.execute("""
        SELECT COUNT(*) total,
            SUM(CASE WHEN vote='yes'     THEN 1 ELSE 0 END) yes_v,
            SUM(CASE WHEN vote='no'      THEN 1 ELSE 0 END) no_v,
            SUM(CASE WHEN vote='abstain' THEN 1 ELSE 0 END) abs_v,
            SUM(CASE WHEN vote='absent'  THEN 1 ELSE 0 END) absent_v
        FROM norfolk_council_member_votes WHERE LOWER(member_name) LIKE ?
    """, (like,)).fetchone()
    if not raw or not raw[0]:
        return [f"No vote records found for '{member_term}'."]

    total, yes_v, no_v, abs_v, absent_v = raw
    participated = total - (absent_v or 0)
    yes_rate = round(100 * yes_v / participated, 1) if participated else 0
    no_rate  = round(100 * no_v  / participated, 1) if participated else 0

    dissents = conn.execute("""
        SELECT mv.meeting_date, mv.agenda_item, mv.vote, v.result, v.vote_count,
               v.title, e.plain_english
        FROM norfolk_council_member_votes mv
        JOIN norfolk_council_votes v ON mv.vote_id = v.id
        LEFT JOIN norfolk_vote_enrichment e ON e.title = v.title
        WHERE LOWER(mv.member_name) LIKE ?
          AND ((mv.vote = 'no' AND v.result = 'passed')
            OR (mv.vote = 'yes' AND v.result = 'failed'))
        ORDER BY mv.meeting_date DESC
    """, (like,)).fetchall()

    contested_body = conn.execute("""
        SELECT COUNT(*) FROM norfolk_council_votes
        WHERE vote_count != ''
          AND vote_count NOT IN ('8-0','7-0','6-0','5-0','0-8','0-7','0-6','0-5')
    """).fetchone()[0]
    contested_member = conn.execute("""
        SELECT COUNT(*) FROM norfolk_council_member_votes mv
        JOIN norfolk_council_votes v ON mv.vote_id = v.id
        WHERE LOWER(mv.member_name) LIKE ?
          AND mv.vote IN ('yes','no','abstain')
          AND v.vote_count NOT IN ('8-0','7-0','6-0','5-0','0-8','0-7','0-6','0-5','')
    """, (like,)).fetchone()[0]

    topic_rows = conn.execute("""
        SELECT COALESCE(e.topic, v.category, 'other') AS topic,
               COUNT(*) total,
               SUM(CASE WHEN mv.vote='yes' THEN 1 ELSE 0 END) yes_v,
               SUM(CASE WHEN mv.vote='no'  THEN 1 ELSE 0 END) no_v
        FROM norfolk_council_member_votes mv
        JOIN norfolk_council_votes v ON mv.vote_id = v.id
        LEFT JOIN norfolk_vote_enrichment e ON e.title = v.title
        WHERE LOWER(mv.member_name) LIKE ?
          AND mv.vote IN ('yes','no')
        GROUP BY COALESCE(e.topic, v.category, 'other')
        ORDER BY total DESC LIMIT 12
    """, (like,)).fetchall()

    dates = conn.execute(
        "SELECT MIN(meeting_date), MAX(meeting_date) FROM norfolk_council_member_votes "
        "WHERE LOWER(member_name) LIKE ?", (like,)
    ).fetchone()
    date_range = f"{dates[0]} – {dates[1]}" if dates and dates[0] else "2024–2026"

    dissent_pct = round(100 * len(dissents) / participated, 2) if participated else 0
    gate_pass = contested_body >= CONTESTED_VOTE_MIN

    # Sole-dissent detection
    sole_dissents = []
    for r in dissents:
        tally = r[4] or ""
        if tally and "-" in tally:
            parts = tally.split("-")
            if len(parts) == 2 and all(p.strip().isdigit() for p in parts):
                if min(int(parts[0]), int(parts[1])) == 1:
                    sole_dissents.append(r)

    lines: list[str] = [
        f"### {display} — Voting Factsheet (Norfolk City Council, {date_range})",
        "",
        "**Raw facts**",
        f"Votes recorded: {total}  |  Participated (non-absent): {participated}",
        f"YES: {yes_v} ({yes_rate}%)  |  NO: {no_v} ({no_rate}%)"
        f"  |  Abstain: {abs_v or 0}  |  Absent: {absent_v or 0}",
        "",
        "**Derived metrics**",
        f"YES rate: {yes_rate}%  |  NO rate: {no_rate}%",
        f"Dissent rate (voted against majority outcome): "
        f"{dissent_pct:.2f}% ({len(dissents)} of {participated})",
        f"Contested-vote participation: {contested_member} of {contested_body} "
        f"contested votes in dataset",
    ]

    if topic_rows:
        lines += ["", "**Per-topic record**"]
        lines.append(f"  {'Topic':<28} {'Total':>6}  {'YES':>5}  {'NO':>5}")
        for r in topic_rows:
            if r[1] < 3:
                continue
            lines.append(
                f"  {r[0]:<28} {r[1]:>6}  {r[2]:>5}  {r[3]:>5}"
            )

    if dissents:
        lines += ["", "**Notable dissents (voted against majority outcome)**"]
        for i, r in enumerate(dissents, 1):
            tally = r[4] or ""
            sole = False
            if tally and "-" in tally:
                parts = tally.split("-")
                if len(parts) == 2 and all(p.strip().isdigit() for p in parts):
                    sole = min(int(parts[0]), int(parts[1])) == 1
            flag = " [sole dissent]" if sole else ""
            desc = (r[6] or r[5] or "")[:72]
            lines.append(
                f"  {i}. {r[0]} | {r[1]} | {r[2].upper()} | {r[4]} {r[3]}{flag}"
            )
            if desc:
                lines.append(f"     {desc}")
    else:
        lines.append("**Notable dissents**: none — all votes aligned with majority outcome")

    # Interpretive signal (neutral behavioral flag, no motive)
    sole_note = (
        f"Anomalous: {len(sole_dissents)} sole-dissent vote(s) against majority. "
        if sole_dissents else ""
    )
    lines += [
        "",
        "**Interpretive signal**",
        f"{sole_note}Dissent rate: {dissent_pct:.2f}% ({len(dissents)} dissents / "
        f"{participated} participated). No motive or characterization is inferred.",
        "",
        "**Methodology note**",
        f"Body contested votes: {contested_body}  |  CONTESTED_VOTE_MIN: {CONTESTED_VOTE_MIN}",
        "Below threshold — comparative rankings suppressed; descriptive record only."
        if not gate_pass else
        "Threshold met — comparative analysis available.",
    ]

    # Lint interpretive lines (skip raw vote-data lines starting with numbering/spaces)
    linted: list[str] = []
    for ln in lines:
        stripped = ln.strip()
        is_raw_data = stripped[:3].replace(".", "").replace(" ", "").isdigit()
        if is_raw_data:
            linted.append(ln)
        else:
            try:
                linted.append(_lint_norfolk_output(ln))
            except ValueError as e:
                linted.append(f"  [SUPPRESSED — {e}]")
    return linted


def _add_norfolk_council_context(blocks: list[str], query: str, terms: list[str]) -> None:
    """Inject Norfolk City Council vote records.

    Fires when the query mentions Norfolk city council, a council member by
    last name with vote intent, or Norfolk local government actions.
    """
    q_lower = (query or "").lower()
    named_members = [n for n in _NORFOLK_MEMBER_NAMES if n in q_lower]
    triggered = bool(_NORFOLK_COUNCIL_TRIGGER.search(query or ""))

    # Also fire when a council member name appears with vote/council/donor/testimony intent
    if not triggered:
        member_hit = bool(named_members)
        action_intent = any(
            w in q_lower
            for w in ("vote", "voted", "votes", "voting", "council", "norfolk",
                      "donor", "fund", "money", "contribut", "financ", "receiv",
                      "paid", "backed", "hotel", "pathway", "developer", "corrupt",
                      "align", "interest",
                      "testif", "testimony", "constituent", "resident", "public",
                      "spoke", "said", "comment", "hearing", "oppos", "support",
                      "dissent", "pattern", "differently", "profile", "outlier",
                      "contrarian", "disagree", "record",
                      "tell me about", "who is", "background", "position",
                      "favor", "favors", "supports", "against", "what does", "what do",
                      "issue", "stance", "believe", "stand on")
        )
        triggered = member_hit and action_intent

    # Also fire for Norfolk + testimony/donor-adjacency intent (no member name needed)
    if not triggered and "norfolk" in q_lower:
        triggered = any(w in q_lower for w in (
            "testif", "testimony", "public comment", "public hearing",
            "who spoke", "spoke against", "spoke in support",
            "public oppos", "public sent", "residents oppos",
            "voted against public", "against the public", "public accountability",
            "donor", "overlap", "adjacen", "align", "sector", "who fund",
            "member", "council member",
            "sentiment", "opinion", "oppose most", "most opposed",
            "unpopular", "public feel", "community feel", "public mood",
            "what do people", "what do residents", "what does the public",
            "resistance", "pushback", "public resist", "public push",
            "norfolk oppos",
            "bloc", "coalition", "vote with", "votes with",
            "vote together", "votes together", "who votes with",
            "voting pattern", "most independent", "vote alike",
            "swing vote", "agree with", "alignment",
            "ward", "overrul", "overrod", "ignored", "which community",
            "split vote", "split the council", "votes split", "council split",
            "close vote", "contested", "most divided", "divided vote",
            "fault line", "controversial", "divided council",
            "most contentious", "tight vote", "most disputed",
            "who represents ward", "represents ward", "ward rep",
            "ward representative", "council member for ward",
            "who is ward", "who holds ward", "who sits on",
            "attend", "absence", "absent", "missed", "who misses",
            "most absent", "participation", "never absent",
            "listen", "responsive", "scorecard", "accountability score",
            "who listens", "who ignores", "votes with constituents",
            "last meeting", "recent meeting", "latest meeting",
            "what happened", "meeting recap", "council meeting",
            "trend", "over time", "growing", "increasing",
            "opposition trend", "more opposed", "getting worse",
            "superward", "super ward",
            "upcoming", "next meeting", "next council",
            "next session", "future meeting", "agenda",
        ))

    if not triggered:
        return

    try:
        conn = _connect("polls")
        if conn is None:
            return
        if not _table_exists(conn, "norfolk_council_votes"):
            conn.close()
            return

        lines: list[str] = ["## Norfolk City Council Vote Records"]

        # ── Routing: factsheet and body-summary modes ─────────────────────────
        _has_record_kw = any(kw in q_lower for kw in (
            "factsheet", "voting record", "full record",
        ))
        _has_show_kw = (
            any(kw in q_lower for kw in ("show me", "tell me about")) and "record" in q_lower
        )
        factsheet_mode = bool(named_members) and (_has_record_kw or _has_show_kw)

        body_summary_mode = (
            not named_members
            and any(kw in q_lower for kw in (
                "how does", "body summary", "consensus", "voting pattern",
                "overall voting", "as a body", "whole council",
                "agenda", "priorities", "priority", "focus", "what topics",
                "what does the council", "what does norfolk council",
                "overall agenda", "policy areas", "what kind", "what do they vote",
                "council stance", "council overall", "collectively",
            ))
        )

        if factsheet_mode:
            fs_lines: list[str] = ["## Norfolk City Council Vote Records", ""]
            for m in named_members[:2]:
                fs_lines += _norfolk_member_factsheet(conn, m)
                fs_lines.append("")
            if len(fs_lines) > 3:
                blocks.append("\n".join(fs_lines))
            conn.close()
            return

        if body_summary_mode:
            bs_lines = (
                ["## Norfolk City Council Vote Records", ""]
                + _norfolk_body_summary(conn)
            )
            blocks.append("\n".join(bs_lines))
            conn.close()
            return

        # ── Per-member vote summary ───────────────────────────────────────────
        # Normalize variant name forms across meetings (e.g. "Smigiel" vs "Smigiel Jr.")
        member_rows = conn.execute("""
            SELECT
                CASE
                    WHEN member_name LIKE 'Smigiel%' THEN 'Smigiel Jr.'
                    WHEN member_name LIKE 'Thomas%'  THEN 'Thomas Jr.'
                    ELSE member_name
                END AS norm_name,
                COUNT(*) AS total,
                SUM(CASE WHEN vote = 'yes'     THEN 1 ELSE 0 END) AS yes_v,
                SUM(CASE WHEN vote = 'no'      THEN 1 ELSE 0 END) AS no_v,
                SUM(CASE WHEN vote = 'abstain' THEN 1 ELSE 0 END) AS abstain_v,
                SUM(CASE WHEN vote = 'absent'  THEN 1 ELSE 0 END) AS absent_v
            FROM norfolk_council_member_votes
            GROUP BY norm_name
            ORDER BY no_v DESC, norm_name
        """).fetchall()

        if member_rows:
            lines += ["", "### Council Member Vote Summary"]
            lines.append(
                f"{'Member':<20} {'Total':>5} {'Yes':>5} {'No':>5} "
                f"{'Abstain':>8} {'Absent':>7}"
            )
            lines.append("-" * 58)
            for r in member_rows:
                lines.append(
                    f"{r[0]:<20} {r[1]:>5} {r[2]:>5} {r[3]:>5} {r[4]:>8} {r[5]:>7}"
                )


        # ── Recent votes matching query terms ─────────────────────────────────
        _NORFOLK_STOP_WORDS = {
            "norfolk", "city", "council", "vote", "votes", "voted", "voting",
            "how", "does", "what", "when", "who", "did", "member", "members",
        }
        topic_terms = [
            t for t in terms
            if t.lower() not in _NORFOLK_MEMBER_NAMES
            and t.lower() not in _NORFOLK_STOP_WORDS
            and len(t) > 3
        ]
        # Also include address numbers (3-5 digit tokens) from the raw query
        # so queries like "1511 Lea View" don't miss the record.
        addr_nums = re.findall(r"\b\d{3,5}\b", query or "")
        all_search = topic_terms + addr_nums
        if all_search:
            like_clauses = " OR ".join(
                "LOWER(v.title) LIKE ?" for _ in all_search
            )
            params = [f"%{t.lower()}%" for t in all_search]
            topic_rows = conn.execute(f"""
                SELECT v.meeting_date, v.agenda_item, v.title, v.vote_count,
                       v.result, v.votes_json, e.plain_english, e.topic,
                       e.state_tags, e.federal_tags
                FROM norfolk_council_votes v
                LEFT JOIN norfolk_vote_enrichment e ON v.title = e.title
                WHERE ({like_clauses})
                ORDER BY v.meeting_date DESC
                LIMIT 15
            """, params).fetchall()

            if topic_rows:
                lines += ["", f"### Votes matching query ({', '.join(topic_terms[:3] or addr_nums[:3])})"]
                for r in topic_rows:
                    member_votes = json.loads(r[5] or "{}")
                    vote_str = ", ".join(
                        f"{m}: {v}" for m, v in sorted(member_votes.items())
                    )
                    topic_tag = f" [{r[7]}]" if r[7] else ""
                    desc = r[6] or r[2][:80]
                    lines.append(f"{r[0]} | {r[1]} | {r[3]} {r[4]}{topic_tag}")
                    lines.append(f"  {desc}")
                    # Show state/federal cross-references when enriched
                    state_tags = json.loads(r[8] or "[]")
                    fed_tags   = json.loads(r[9] or "[]")
                    if state_tags:
                        lines.append(f"  State law context: {', '.join(state_tags)}")
                    if fed_tags:
                        lines.append(f"  Federal context: {', '.join(fed_tags)}")
                    if vote_str:
                        lines.append(f"  Votes: {vote_str}")

        # ── Member-specific voting history ────────────────────────────────────
        # named_members derived at function entry before trigger check
        if named_members:
            for member in named_members[:2]:
                member_votes_rows = conn.execute("""
                    SELECT mv.meeting_date, mv.agenda_item, mv.title, mv.vote, mv.result
                    FROM norfolk_council_member_votes mv
                    WHERE LOWER(mv.member_name) LIKE ?
                    ORDER BY mv.meeting_date DESC
                    LIMIT 20
                """, (f"%{member}%",)).fetchall()

                if member_votes_rows:
                    lines += ["", f"### {member.title()} — Recent Votes"]
                    for r in member_votes_rows:
                        lines.append(
                            f"  {r[0]} | {r[1]} | {r[3].upper():>7} | {r[4]} | {r[2][:65]}"
                        )

        # ── Donor × vote cross-reference ─────────────────────────────────────
        # Map council member last names to their SBE campaign finance records.
        # Shows top donor employer categories alongside their dissent pattern.
        _SBE_NAME_MAP = {
            "alexander": "Alexander", "clanton": "Clanton",
            "doyle": "Doyle",        "johnson": "Johnson",
            "mcgee": "McGee",        "paige": "Paige",
            "smigiel": "Smigiel",    "thomas": "Thomas",
            "royster": "Royster",    "mcclellan": "McClellan",
        }

        # Ward / Superward → current council representative (public record, Norfolk City Council)
        # Transitions: Riddick→Paige (Jan 2023, SW7), McClellan→Clanton + Royster→McGee (Jan 2025, W5/SW6)
        _WARD_REP: dict[int, str] = {
            1: "Smigiel Jr.",
            2: "Doyle",
            3: "Johnson",
            4: "Thomas Jr.",
            5: "Clanton",    # replaced McClellan Jan 2025
            6: "McGee",      # replaced Royster Jan 2025
            7: "Paige",      # replaced Riddick Jan 2023
        }
        _SEAT_LABEL: dict[int, str] = {
            1: "Ward 1", 2: "Ward 2", 3: "Ward 3",
            4: "Ward 4", 5: "Ward 5",
            6: "Superward 6", 7: "Superward 7",
        }
        # Reverse: vote-data name → (ward_num, seat_label)  — includes former reps
        _REP_WARD: dict[str, tuple[int, str]] = {
            "Smigiel Jr.": (1, "Ward 1"),
            "Doyle":        (2, "Ward 2"),
            "Johnson":      (3, "Ward 3"),
            "Thomas Jr.":   (4, "Ward 4"),
            "Clanton":      (5, "Ward 5"),
            "McClellan":    (5, "Ward 5"),      # former (–Dec 2024)
            "McGee":        (6, "Superward 6"),
            "Royster":      (6, "Superward 6"), # former (–Dec 2024)
            "Paige":        (7, "Superward 7"),
            "Riddick":      (7, "Superward 7"), # former (–Dec 2022)
            "Alexander":    (0, "Mayor (at-large)"),
        }

        donor_finance_trigger = (
            any(w in q_lower for w in ("donor", "fund", "money", "contribut", "financ",
                                       "who pays", "paid by", "backed by", "receiv"))
            or named_members
        )
        if donor_finance_trigger and _table_exists(conn, "norfolk_finance_summary"):
            # Use precomputed sector totals (name-disambiguated via build_norfolk_finance.py)
            target_members = named_members if named_members else list(_SBE_NAME_MAP.keys())
            donor_lines: list[str] = []
            for m in target_members[:4]:
                display = _SBE_NAME_MAP.get(m, m.title())
                summary = conn.execute(
                    "SELECT total_raised, top_sector, top_sector_pct, sector_json "
                    "FROM norfolk_finance_summary WHERE member_name=?",
                    (display,)
                ).fetchone()
                sectors = conn.execute(
                    "SELECT sector, total_amount, pct_of_total, donor_count "
                    "FROM norfolk_finance_totals WHERE member_name=? "
                    "ORDER BY total_amount DESC LIMIT 5",
                    (display,)
                ).fetchall()
                no_votes = conn.execute("""
                    SELECT mv.title, mv.meeting_date
                    FROM norfolk_council_member_votes mv
                    JOIN norfolk_council_votes v ON mv.vote_id = v.id
                    WHERE LOWER(mv.member_name) LIKE ?
                      AND mv.vote = 'no'
                      AND v.category = 'substantive'
                    ORDER BY mv.meeting_date DESC
                    LIMIT 8
                """, (f"%{m}%",)).fetchall()

                if summary or no_votes:
                    donor_lines.append(f"\n#### {display} — Donors & Dissent")
                    if summary and sectors:
                        total_raised = summary[0]
                        donor_lines.append(
                            f"Total raised: ${total_raised:,.0f}  "
                            f"(top sector: {summary[1]}, {summary[2]:.1f}%)"
                        )
                        donor_lines.append("Donor sectors:")
                        for s in sectors:
                            donor_lines.append(
                                f"  {s[0]:<18} ${s[1]:>9,.0f}  ({s[2]:>5.1f}%,  {s[3]} donors)"
                            )
                    if no_votes:
                        donor_lines.append("Dissenting (No) votes on substantive items:")
                        for r in no_votes:
                            donor_lines.append(f"  {r[1]} — {r[0][:70]}")

            if donor_lines:
                lines += ["", "### Campaign Finance × Voting Pattern"] + donor_lines

        # ── Donor-sector ↔ vote-topic adjacency (facts only, no causal inference) ──
        _adj_trigger = donor_finance_trigger or any(
            w in q_lower for w in ("align", "adjacen", "sector", "pattern", "overlap")
        )
        if _adj_trigger and _table_exists(conn, "norfolk_donor_vote_adjacency"):
            target_adj = named_members if named_members else list(_SBE_NAME_MAP.keys())
            adj_lines: list[str] = []
            for m in target_adj[:6]:
                display = _SBE_NAME_MAP.get(m, m.title())
                rows_adj = conn.execute("""
                    SELECT sector, sector_pct, top_topic, top_topic_delta,
                           top_topic_yes_pct, council_yes_pct, topic_vote_count
                    FROM norfolk_donor_vote_summary
                    WHERE member_name = ?
                    ORDER BY ABS(top_topic_delta) DESC
                    LIMIT 4
                """, (display,)).fetchall()
                if not rows_adj:
                    continue
                adj_lines.append(f"\n#### {display} — Donor Sector / Vote Topic Adjacency")
                adj_lines.append(
                    "Source: SBE contributions + council member votes joined on enriched topic. "
                    "Adjacency only — no causal inference drawn."
                )
                for r in rows_adj:
                    sector, sec_pct, topic, delta, m_yes, c_yes, n = r
                    sign = "+" if delta >= 0 else ""
                    adj_lines.append(
                        f"  {sec_pct:4.0f}% from {sector:<15s} | "
                        f"votes YES on {topic:<22s} {m_yes:.0f}% "
                        f"(council avg {c_yes:.0f}%, delta {sign}{delta:.0f}pp, n={n})"
                    )
            if adj_lines:
                lines += ["", "### Donor-Sector / Vote-Topic Adjacency"] + adj_lines

        # ── Per-member dissent profile (No-rate vs council avg by topic) ──────
        _dissent_trigger = (
            named_members
            or any(w in q_lower for w in (
                "dissent", "differently", "pattern", "contrarian",
                "disagree", "against", "oppose", "no vote", "outlier",
                "bloc", "coalition", "votes with", "votes together",
                "who votes with", "aligned with", "voting pattern",
                "most independent", "who disagrees", "lone vote",
            ))
        )
        if _dissent_trigger and _table_exists(conn, "norfolk_member_dissent"):
            target_dis = named_members if named_members else list(_SBE_NAME_MAP.keys())
            dis_lines: list[str] = []
            for m in target_dis[:6]:
                display = _SBE_NAME_MAP.get(m, m.title())
                vote_name = {"Smigiel": "Smigiel Jr.", "Thomas": "Thomas Jr."}.get(display, display)
                rows_dis = conn.execute("""
                    SELECT topic, member_no_pct, council_no_pct, delta_pp, member_no_count
                    FROM norfolk_member_dissent
                    WHERE member_name = ? AND ABS(delta_pp) >= 2
                    ORDER BY ABS(delta_pp) DESC LIMIT 5
                """, (vote_name,)).fetchall()
                if not rows_dis:
                    continue
                supports_nf = [(t, m, c, d, n) for t, m, c, d, n in rows_dis if d < 0]
                opposes_nf  = [(t, m, c, d, n) for t, m, c, d, n in rows_dis if d > 0]
                dis_lines.append(f"\n#### {display} — Issue Positions (vs council average)")
                if supports_nf:
                    dis_lines.append("  Tends to SUPPORT (votes Yes more than peers):")
                    for topic, m_no, c_no, delta, nos in supports_nf:
                        dis_lines.append(
                            f"    {topic:<22s}  Yes rate above avg by {abs(delta):.1f}pp "
                            f"(No only {m_no:.0f}% vs council {c_no:.0f}%)"
                        )
                if opposes_nf:
                    dis_lines.append("  Tends to OPPOSE (votes No more than peers):")
                    for topic, m_no, c_no, delta, nos in opposes_nf:
                        dis_lines.append(
                            f"    {topic:<22s}  No rate above avg by {delta:.1f}pp "
                            f"({m_no:.0f}% No vs council {c_no:.0f}%, {nos} No votes)"
                        )
                top_topic = opposes_nf[0][0] if opposes_nf else None
                # Signature No votes: 3 actual bills from the most distinctive topic
                if top_topic:
                    sig_rows = conn.execute("""
                        SELECT mv.meeting_date, mv.agenda_item,
                               cv.vote_count, e.plain_english, mv.title
                        FROM norfolk_council_member_votes mv
                        JOIN norfolk_vote_enrichment e ON e.title = mv.title
                        JOIN norfolk_council_votes cv
                          ON cv.agenda_item = mv.agenda_item
                         AND cv.meeting_date = mv.meeting_date
                        WHERE mv.member_name = ?
                          AND LOWER(mv.vote) = 'no'
                          AND e.topic = ?
                          AND mv.category = 'substantive'
                        ORDER BY mv.meeting_date DESC
                        LIMIT 3
                    """, (vote_name, top_topic)).fetchall()
                    if sig_rows:
                        dis_lines.append(f"  Signature No votes on {top_topic}:")
                        for date, item, vc, plain, title in sig_rows:
                            desc = (plain or title or "")[:80]
                            dis_lines.append(f"    {date} | {item} | {vc} — {desc}")
            if dis_lines:
                lines += ["", "### Member Dissent Profiles"] + dis_lines

        # ── Voting blocs (pairwise agreement; independent of dissent table) ───
        _bloc_trigger = any(w in q_lower for w in (
            "bloc", "coalition", "vote with", "votes with",
            "vote together", "votes together", "who votes with",
            "aligned with", "alignment", "agree with", "agreement",
            "voting pattern", "most independent", "who disagrees",
            "swing vote", "lone vote", "vote alike", "vote the same",
        ))
        if _bloc_trigger and _table_exists(conn, "norfolk_voting_blocs"):
            if named_members:
                for m in named_members[:2]:
                    display = _SBE_NAME_MAP.get(m, m.title())
                    vote_name = {"Smigiel": "Smigiel Jr.", "Thomas": "Thomas Jr."}.get(display, display)
                    allies = conn.execute("""
                        SELECT member_b, agreement_pct, shared_votes
                        FROM norfolk_voting_blocs WHERE member_a = ?
                        ORDER BY agreement_pct DESC LIMIT 4
                    """, (vote_name,)).fetchall()
                    contrarians = conn.execute("""
                        SELECT member_b, agreement_pct, shared_votes
                        FROM norfolk_voting_blocs WHERE member_a = ?
                        ORDER BY agreement_pct ASC LIMIT 3
                    """, (vote_name,)).fetchall()
                    if allies:
                        lines.append(f"\n#### {display} — Voting Alignment (substantive votes)")
                        lines.append("  Closest allies:")
                        for b, pct, n in allies:
                            lines.append(f"    {b:<22s}  {pct:.1f}% agreement ({n} shared votes)")
                        if contrarians:
                            lines.append("  Most frequent disagreements:")
                            for b, pct, n in contrarians:
                                lines.append(f"    {b:<22s}  {pct:.1f}% agreement ({n} shared votes)")
            else:
                tight = conn.execute("""
                    SELECT member_a, member_b, agreement_pct, shared_votes
                    FROM norfolk_voting_blocs WHERE member_a < member_b
                    ORDER BY agreement_pct DESC LIMIT 5
                """).fetchall()
                diverge = conn.execute("""
                    SELECT member_a, member_b, agreement_pct, shared_votes
                    FROM norfolk_voting_blocs WHERE member_a < member_b
                    ORDER BY agreement_pct ASC LIMIT 5
                """).fetchall()
                if tight:
                    lines.append("\n#### Norfolk Council — Voting Bloc Analysis (substantive votes)")
                    lines.append("  Tightest pairs (most aligned):")
                    for a, b, pct, n in tight:
                        lines.append(f"    {a} + {b}: {pct:.1f}% agreement ({n} votes)")
                    if diverge:
                        lines.append("  Most divergent pairs:")
                        for a, b, pct, n in diverge:
                            lines.append(f"    {a} + {b}: {pct:.1f}% agreement ({n} votes)")

        # ── Split-vote / contested-issue index ───────────────────────────────
        _split_trigger = any(w in q_lower for w in (
            "split vote", "split the council", "votes split", "council split",
            "close vote", "contested", "most divided", "divided vote",
            "fault line", "controversial", "5-3", "4-4", "6-2", "3-5", "2-6",
            "divided council", "most contentious", "tight vote",
            "most disputed", "biggest disagreement",
        ))
        if _split_trigger and _table_exists(conn, "norfolk_council_votes"):
            # Detect optional topic filter
            _SPLIT_TOPIC_MAP = [
                ("budget",               ["budget", "fiscal", "spending", "tax"]),
                ("short-term-rental",    ["short-term", "short term", "str", "airbnb", "vrbo", "rental"]),
                ("rezoning",             ["rezoning", "zoning", "rezone"]),
                ("housing",              ["housing", "affordable"]),
                ("public-safety",        ["public safety", "police", "fire"]),
                ("economic-development", ["economic development", "econ dev"]),
                ("schools",              ["school", "education"]),
                ("infrastructure",       ["infrastructure", "road", "water"]),
                ("environment",          ["environment", "environmental"]),
            ]
            _split_topic: str | None = None
            for _st, _skws in _SPLIT_TOPIC_MAP:
                if any(kw in q_lower for kw in _skws):
                    _split_topic = _st
                    break

            topic_clause = "AND e.topic = ?" if _split_topic else ""
            params_split: list = [_split_topic] if _split_topic else []
            split_rows = conn.execute(f"""
                SELECT cv.meeting_date, cv.agenda_item, cv.vote_count,
                       cv.result, e.plain_english, e.topic,
                       MIN(
                           CAST(SUBSTR(cv.vote_count, 1,
                               INSTR(cv.vote_count,'-')-1) AS INTEGER),
                           CAST(SUBSTR(cv.vote_count,
                               INSTR(cv.vote_count,'-')+1) AS INTEGER)
                       ) minority
                FROM norfolk_council_votes cv
                LEFT JOIN norfolk_vote_enrichment e ON e.title = cv.title
                WHERE cv.category IN ('substantive','consent')
                  AND INSTR(cv.vote_count,'-') > 0
                  AND MIN(
                      CAST(SUBSTR(cv.vote_count, 1,
                          INSTR(cv.vote_count,'-')-1) AS INTEGER),
                      CAST(SUBSTR(cv.vote_count,
                          INSTR(cv.vote_count,'-')+1) AS INTEGER)
                  ) >= 2
                  {topic_clause}
                ORDER BY minority DESC, cv.meeting_date DESC
                LIMIT 15
            """, params_split).fetchall()
            if split_rows:
                label = f" on {_split_topic.replace('-',' ').title()}" if _split_topic else ""
                lines.append(f"\n#### Most Contested Council Votes{label}")
                lines.append("  (minority dissent >= 2; ordered by closeness)")
                for date, item, vc, result, plain, topic, minority in split_rows:
                    desc = (plain or "")[:70]
                    topic_tag = f" [{topic}]" if topic and not _split_topic else ""
                    lines.append(f"  {date} | {item} | {vc} {result.upper()}{topic_tag}")
                    if desc:
                        lines.append(f"    {desc}")

        # ── Named-member No-vote history (from precomputed norfolk_split_votes) ──
        _named_split_trigger = (
            bool(named_members)
            and any(w in q_lower for w in (
                "voted against", "voted no", "no vote", "voted down", "split",
                "contested", "controversial", "opposed", "specific vote",
                "which vote", "example", "minority", "dissent", "record",
                "disagree", "against", "no on",
            ))
        )
        if _named_split_trigger and _table_exists(conn, "norfolk_split_votes"):
            _nm = named_members[0]
            _nm_rows = conn.execute("""
                SELECT meeting_date, topic, yes_count, no_count, no_voters, title
                FROM norfolk_split_votes
                WHERE no_voters LIKE ?
                ORDER BY meeting_date DESC
                LIMIT 8
            """, (f"%{_nm}%",)).fetchall()
            if _nm_rows:
                lines.append(f"\n#### {_nm.title()} — No votes on contested resolutions")
                for dt, topic, yes, no, voters, title in _nm_rows:
                    t = f"[{topic}]" if topic else "[other]"
                    lines.append(
                        f"  {dt}  {t:22s}  {yes}Y/{no}N  ({voters})  {title[:65]}"
                    )

        # ── Attendance / absence tracker ──────────────────────────────────────
        _attend_trigger = any(w in q_lower for w in (
            "attend", "absence", "absent", "miss", "missed", "show up",
            "skip", "skipped", "present", "participation",
            "who misses", "most absent", "never absent",
        ))
        if _attend_trigger and _table_exists(conn, "norfolk_council_member_votes"):
            att_rows = conn.execute("""
                SELECT member_name,
                       COUNT(*) total,
                       SUM(CASE WHEN LOWER(vote)='absent' THEN 1 ELSE 0 END) absent,
                       SUM(CASE WHEN LOWER(vote)='abstain' THEN 1 ELSE 0 END) abstain
                FROM norfolk_council_member_votes
                GROUP BY member_name
                ORDER BY absent DESC
            """).fetchall()
            if att_rows:
                lines.append("\n#### Council Attendance Record")
                lines.append("  Member               Total   Absent   Rate   Abstain")
                lines.append("  " + "-" * 55)
                for name, total, absent, abstain in att_rows:
                    rate = round(100 * absent / total, 1) if total else 0
                    ward_info = _REP_WARD.get(name, (None, None))
                    seat_tag = f"  ({ward_info[1]})" if ward_info[0] else ""
                    lines.append(
                        f"  {name:20s}  {total:5d}  {absent:6d}  {rate:5.1f}%  {abstain:7d}{seat_tag}"
                    )

        # ── Member-topic voting profile ───────────────────────────────────────
        _mtopic_trigger = (
            named_members
            and topic_terms
            and any(w in q_lower for w in (
                "vote on", "votes on", "voting on", "position on",
                "stance on", "record on", "how does", "how did",
            ))
        )
        if not _mtopic_trigger and named_members and topic_terms:
            _mtopic_trigger = True
        if _mtopic_trigger and _table_exists(conn, "norfolk_vote_enrichment"):
            for m in named_members[:2]:
                display = _SBE_NAME_MAP.get(m, m.title())
                vote_name = {"Smigiel": "Smigiel Jr.", "Thomas": "Thomas Jr."}.get(display, display)
                for topic_kw in topic_terms[:2]:
                    topic_label = topic_kw.replace("-", " ").title()
                    tp_rows = conn.execute("""
                        SELECT mv.vote, COUNT(*) n
                        FROM norfolk_council_member_votes mv
                        JOIN norfolk_vote_enrichment e ON e.title = mv.title
                        WHERE mv.category = 'substantive'
                          AND mv.member_name = ?
                          AND LOWER(e.topic) = LOWER(?)
                        GROUP BY mv.vote
                        ORDER BY n DESC
                    """, (vote_name, topic_kw)).fetchall()
                    if tp_rows:
                        total = sum(r[1] for r in tp_rows)
                        breakdown = ", ".join(f"{r[0]}={r[1]}" for r in tp_rows)
                        yes_n = next((r[1] for r in tp_rows if r[0].lower() == 'yes'), 0)
                        no_n = next((r[1] for r in tp_rows if r[0].lower() == 'no'), 0)
                        lines.append(
                            f"\n#### {vote_name} — {topic_label} Voting Record\n"
                            f"  {total} substantive {topic_label.lower()} votes: {breakdown}\n"
                            f"  YES rate: {round(100*yes_n/total,1)}% | NO rate: {round(100*no_n/total,1)}%"
                        )

        # ── Defensible donor-vote signals (schema-conformant) ────────────────
        # Schema: raw facts → derived metrics → at most one neutral signal.
        # NO causal/intent language. Banned: captured, bought, because donors,
        # in exchange, rewarded, influence, benefit directly, market protection.
        _BANNED_DONOR_TERMS = frozenset([
            "captured", "market protection", "bought", "because donors",
            "in exchange", "rewarded", "benefit directly",
            "paid to vote", "quid pro quo", "builders fund him",
            "because he opposes", "because she", "hotels benefit",
        ])

        def _lint_donor_signal(text: str) -> str:
            return _lint_norfolk_output(text)  # delegates to module-level extended lint

        # Build per-member baselines from member_rows (already queried above)
        _baselines: dict[str, dict] = {}
        for _br in (member_rows or []):
            _nm = str(_br[0]).lower()
            _tot = _br[1] or 1
            _baselines[_nm] = {
                "yes_rate": round(100.0 * _br[2] / _tot, 1),
                "no_rate":  round(100.0 * _br[3] / _tot, 1),
                "total":    _br[1],
            }

        def _b(name: str, field: str, default: str = "?") -> str:
            return str(_baselines.get(name, {}).get(field, default))

        # STR bloc check — are STR votes a council-wide pattern?
        _str_rows = conn.execute("""
            SELECT vote_count FROM norfolk_council_votes
            WHERE LOWER(title) LIKE '%short-term%' OR LOWER(title) LIKE '%short term%'
        """).fetchall()
        _str_total = len(_str_rows)
        _str_unanimous = sum(
            1 for r in _str_rows
            if r[0] and r[0].startswith(("8-0","0-8","7-0","0-7","6-0","0-6"))
        )
        _str_bloc_pct = round(100 * _str_unanimous / _str_total) if _str_total else 0
        _str_bloc_label = "LOW-SIGNAL" if _str_bloc_pct >= 85 else "INFORMATIVE"

        _DONOR_SIGNALS = [
            {
                "members": {"paige", "alexander"},
                "heading": "PAIGE + ALEXANDER — Hotel Industry Donations",
                "lines": [
                    _lint_donor_signal(
                        f"Raw facts: Paige — $5,500 from hotel operators (Norfolk Hotel Associates LLC). "
                        f"Alexander — $47,500 from hotel operators (Norfolk Hotel Associates LLC $25,000; "
                        f"Gold Key Resorts $15,000; Shamin Hotels $7,500)."
                    ),
                    f"Derived metrics: Paige baseline {_b('paige','yes_rate')}% YES "
                    f"({_b('paige','total')} votes); "
                    f"Alexander baseline {_b('alexander','yes_rate')}% YES "
                    f"({_b('alexander','total')} votes).",
                    f"STR vote record (2024–2026): {_str_total} STR-related votes; "
                    f"{_str_unanimous}/{_str_total} unanimous ({_str_bloc_pct}%). "
                    f"Feb 25 2025 zoning amendment removing STR as permitted use: 8-0 (whole council).",
                    f"Interpretive signal: {_str_bloc_label} — STR vote pattern is council-wide bloc "
                    f"({_str_bloc_pct}% unanimous). Hotel donations are adjacent fact; "
                    f"no discriminating deviation from majority or baseline in this dataset.",
                ],
            },
            {
                "members": {"clanton"},
                "heading": "CLANTON — Pathway Realty Group Donation",
                "lines": [
                    _lint_donor_signal(
                        "Raw facts: $2,500 from Pathway RG / Managing Member (Pathway Realty Group)."
                    ),
                    "Vote record: MARCH 25, 2025 PH-1 — Approved 2.28-acre city-owned land "
                    "purchase-and-development agreement. Clanton voted YES. Result: 7-1 passed "
                    "(87.5% council approval). Sole NO: Doyle (no Pathway donations on record).",
                    f"Derived metrics: Clanton baseline {_b('clanton','yes_rate')}% YES across "
                    f"{_b('clanton','total')} votes.",
                    "Interpretive signal: LOW-SIGNAL — Clanton's YES is consistent with baseline; "
                    "87.5% of council approved. No deviation from majority or baseline detected.",
                ],
            },
            {
                "members": {"doyle"},
                "heading": "DOYLE — Pathway NO Vote (no donor link; anomalous dissent)",
                "lines": [
                    "Raw facts: No Pathway Realty Group donations on record for Doyle.",
                    "Vote record: MARCH 25, 2025 PH-1 — Doyle voted NO (sole dissent in 7-1 vote).",
                    f"Derived metrics: Doyle baseline {_b('doyle','yes_rate')}% YES, "
                    f"{_b('doyle','no_rate')}% dissent rate.",
                    "Interpretive signal: ANOMALOUS — Doyle broke from her YES baseline and the "
                    "87.5% council majority on a split vote; no donor link to Pathway detected. "
                    "Ward 2 constituency context may be relevant (testimony records show Ward 2 "
                    "opposition on development items).",
                ],
            },
            {
                "members": {"alexander"},
                "heading": "ALEXANDER — Bonaventure Developer Donation",
                "lines": [
                    _lint_donor_signal(
                        "Raw facts: John Hyland / Bonaventure gave $105,000 to Alexander "
                        "(14% of $750,807 total raised; 3 contributions on Feb 15, 2023). "
                        "Donor base also includes Tidewater Builders Association PAC, "
                        "hotel operators, and real estate law firms."
                    ),
                    f"Derived metrics: Alexander baseline {_b('alexander','yes_rate')}% YES across "
                    f"{_b('alexander','total')} votes.",
                    "Interpretive signal: LOW-SIGNAL — Alexander's near-100% baseline makes any YES "
                    "vote indistinguishable from general pattern. Donation amount is notable; "
                    "no Bonaventure-specific vote items identified to assess deviation.",
                ],
            },
            {
                "members": {"royster"},
                "heading": "ROYSTER — Franklin Johnston Group Donation",
                "lines": [
                    "Raw facts: $24,500 from The Franklin Johnston Group (single largest donor, "
                    "~9% of total raised). Alexander also received $30,000 from Franklin Johnston.",
                    f"Derived metrics: Royster baseline {_b('royster','yes_rate')}% YES, "
                    f"{_b('royster','no_rate')}% dissent rate ({_b('royster','total')} votes; "
                    f"8 contested votes — marginal sample).",
                    "Interpretive signal: LOW-SIGNAL — vote sample (8 contested votes) is "
                    "insufficient for pattern claim; dissent rate makes any individual YES "
                    "indistinguishable from baseline. Developer donation is adjacent fact; "
                    "no discriminating vote pattern identified.",
                ],
            },
            {
                "members": {"smigiel"},
                "heading": "SMIGIEL JR. — Construction / Builder Donations",
                "lines": [
                    _lint_donor_signal(
                        "Raw facts: $14,500 combined from construction and homebuilder employers "
                        "(2021–2023) — $6,000 Equity Development Corporation, $3,000 Elysion, "
                        "$3,000 ConstructTech, $2,500 Breeden Company."
                    ),
                    f"Derived metrics: Smigiel Jr. baseline {_b('smigiel jr.','yes_rate')}% YES, "
                    f"{_b('smigiel jr.','no_rate')}% dissent rate "
                    f"({_b('smigiel jr.','total')} votes; 30 contested votes, all NO vs majority YES).",
                    "STR vote record: Smigiel Jr. cast 11 NO votes on STR permit items across "
                    "117 STR-related votes. Thomas Jr. — $0 construction donations on record — "
                    "cast 14 NO votes on the same category. Doyle, Paige, and Alexander each "
                    "cast 10 NO votes with no construction donors on record.",
                    "Interpretive signal: LOW-SIGNAL — dissent pattern (anti-spending, "
                    "limited-government) is council-wide and predates the donation period. "
                    "STR opposition is not unique to members with construction donors. "
                    "No discriminating deviation from baseline identified.",
                ],
            },
        ]

        corr_trigger = donor_finance_trigger or any(
            w in q_lower for w in (
                "corrupt", "align", "conflict", "interest",
                "who funds", "who paid", "hotel", "pathway",
                "bonaventure", "franklin johnston", "airbnb", "str",
                "short-term", "short term", "rental", "vacation rental", "vrbo",
            )
        )
        if corr_trigger:
            active_members = set(named_members) if named_members else set(_SBE_NAME_MAP.keys())
            corr_lines: list[str] = []
            for sig in _DONOR_SIGNALS:
                if sig["members"] & active_members:
                    corr_lines.append(f"\n**{sig['heading']}**")
                    for ln in sig["lines"]:
                        try:
                            corr_lines.append(f"  {_lint_donor_signal(ln)}")
                        except ValueError as _e:
                            corr_lines.append(f"  [SUPPRESSED: {_e}]")
            if corr_lines:
                lines += ["", "### Donor-Vote Adjacency Record (schema v2 — facts only, no causal inference)"]
                lines += corr_lines

        # ── Testimony / public comment lookup ────────────────────────────────
        import re as _re
        _ward_m = _re.search(r'\bward\s*([1-5])\b', q_lower)
        query_ward = int(_ward_m.group(1)) if _ward_m else None

        testimony_trigger = any(
            w in q_lower for w in (
                "testif", "oppos", "support", "spoke", "comment", "public",
                "who opposed", "who supported", "neighbor", "resident",
                "community", "hearing", "public hearing", "ward",
            )
        ) or topic_terms or named_members or query_ward
        if testimony_trigger and _table_exists(conn, "norfolk_council_testimony"):
            test_lines: list[str] = []

            _sentiment_intent = any(w in q_lower for w in (
                "sentiment", "oppose most", "public opinion", "community opinion",
                "what does norfolk oppose", "what do residents", "what does the public",
                "how does public", "community feel", "public feel",
                "most opposed", "least supported", "most unpopular",
                "public against", "what do people", "public mood",
                "resistance", "pushback", "public resist", "public push",
                "most oppos", "public oppos", "residents oppos", "norfolk oppos",
                "most public", "had the most",
            ))

            # Accountability mode — takes priority; no member/topic needed
            _acct_intent = any(w in q_lower for w in (
                "voted against", "ignored", "overrode", "passed despite",
                "accountability", "went against", "public say", "public sentiment",
                "vs outcome", "outcome vs", "public vs", "against the public",
                "council vs", "overruled",
            ))
            if _acct_intent:
                _opp_pass = conn.execute("""
                    SELECT COUNT(*) FROM (
                        SELECT t.meeting_date, t.agenda_item
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_votes v
                          ON v.agenda_item=t.agenda_item AND v.meeting_date=t.meeting_date
                        GROUP BY t.meeting_date, t.agenda_item
                        HAVING SUM(CASE WHEN t.stance='oppose' THEN 1 ELSE 0 END) >
                               SUM(CASE WHEN t.stance='support' THEN 1 ELSE 0 END)
                           AND LOWER(v.result) LIKE '%pass%'
                    )
                """).fetchone()[0]
                _opp_total = conn.execute("""
                    SELECT COUNT(*) FROM (
                        SELECT t.meeting_date, t.agenda_item
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_votes v
                          ON v.agenda_item=t.agenda_item AND v.meeting_date=t.meeting_date
                        GROUP BY t.meeting_date, t.agenda_item
                        HAVING SUM(CASE WHEN t.stance='oppose' THEN 1 ELSE 0 END) >
                               SUM(CASE WHEN t.stance='support' THEN 1 ELSE 0 END)
                    )
                """).fetchone()[0]
                _pct = round(100 * _opp_pass / _opp_total) if _opp_total else 0
                acc_rows = conn.execute("""
                    SELECT t.meeting_date, t.agenda_item, v.title,
                        SUM(CASE WHEN t.stance='oppose' THEN 1 ELSE 0 END) oppose,
                        SUM(CASE WHEN t.stance='support' THEN 1 ELSE 0 END) support,
                        v.result, v.vote_count, e.plain_english
                    FROM norfolk_council_testimony t
                    JOIN norfolk_council_votes v
                      ON v.agenda_item=t.agenda_item AND v.meeting_date=t.meeting_date
                    LEFT JOIN norfolk_vote_enrichment e ON e.title=v.title
                    GROUP BY t.meeting_date, t.agenda_item, v.title, v.result, v.vote_count
                    HAVING oppose > support AND LOWER(v.result) LIKE '%pass%'
                    ORDER BY oppose DESC
                    LIMIT 15
                """).fetchall()
                test_lines.append(
                    f"\n#### Public Opposition vs. Council Outcome\n"
                    f"Stat: {_opp_pass}/{_opp_total} items where public opposition exceeded "
                    f"support still passed ({_pct}%)"
                )
                for r in acc_rows:
                    desc = (r[7] or r[2] or "")[:70]
                    test_lines.append(
                        f"  {r[0]} | {r[1]} | {r[3]} oppose / {r[4]} support "
                        f"-> {r[5].upper()} {r[6]}"
                    )
                    if desc:
                        test_lines.append(f"    {desc}")

            # Public sentiment index — topic drill-down or overall rollup
            elif _sentiment_intent:
                # Detect if query targets a specific topic
                _TOPIC_KEYWORDS: list[tuple[str, list[str]]] = [
                    ("budget",               ["budget", "fiscal", "spending", "tax", "fy20"]),
                    ("schools",              ["school", "education", "student", "teacher", "classroom"]),
                    ("short-term-rental",    ["short-term", "short term", "airbnb", "vrbo",
                                              "vacation rental", "rental permit", " str "]),
                    ("housing",              ["housing", "affordable housing", "affordable home"]),
                    ("public-safety",        ["public safety", "police", "fire department",
                                              "crime", "911", "emergency service"]),
                    ("personnel",            ["personnel", "staff", "salary", "employee",
                                              "hiring", "firing", "layoff"]),
                    ("infrastructure",       ["infrastructure", "road", "water", "sewer",
                                              "stormwater", "sidewalk", "bridge"]),
                    ("rezoning",             ["rezoning", "zoning", "rezone", "land use"]),
                    ("economic-development", ["economic development", "econ dev", "developer",
                                              "development project", "redevelopment"]),
                    ("environment",          ["environment", "environmental", "climate",
                                              "flood", "green", "sustainability"]),
                ]
                _drill_topic: str | None = None
                for _t, _kws in _TOPIC_KEYWORDS:
                    if any(kw in q_lower for kw in _kws):
                        _drill_topic = _t
                        break

                if _drill_topic:
                    topic_label = _drill_topic.replace("-", " ").title()
                    drill_rows = conn.execute("""
                        SELECT e.plain_english, cv.agenda_item, cv.meeting_date,
                               SUM(CASE WHEN t.stance='oppose' THEN 1 ELSE 0 END) opp,
                               SUM(CASE WHEN t.stance='support' THEN 1 ELSE 0 END) sup,
                               COUNT(*) total, cv.result
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_votes cv
                          ON cv.agenda_item = t.agenda_item
                         AND cv.meeting_date = t.meeting_date
                        JOIN norfolk_vote_enrichment e ON e.title = cv.title
                        WHERE t.stance IN ('oppose','support')
                          AND e.topic = ?
                        GROUP BY cv.agenda_item, cv.meeting_date
                        HAVING total >= 2
                        ORDER BY opp DESC
                        LIMIT 12
                    """, (_drill_topic,)).fetchall()
                    if drill_rows:
                        test_lines.append(
                            f"\n#### {topic_label} Items — Public Opposition Ranking\n"
                            "(oppose/support testimony only; 'comment' stance excluded)"
                        )
                        for plain, item, date, opp, sup, total, result in drill_rows:
                            pct = round(100 * opp / total) if total else 0
                            test_lines.append(
                                f"  {date} | {item} | {opp} opp / {sup} sup "
                                f"({pct}% oppose) -> {(result or '').upper()}"
                            )
                            if plain:
                                test_lines.append(f"    {plain[:75]}")
                    else:
                        test_lines.append(
                            f"\n#### {topic_label} Items — Public Opposition Ranking\n"
                            f"No {topic_label.lower()} testimony records with "
                            "opposition/support stances found."
                        )
                else:
                    # No specific topic — show rollup across all categories
                    sent_rows = conn.execute("""
                        SELECT e.topic,
                               SUM(CASE WHEN t.stance='oppose' THEN 1 ELSE 0 END) opp,
                               SUM(CASE WHEN t.stance='support' THEN 1 ELSE 0 END) sup,
                               COUNT(*) total
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_votes cv
                          ON cv.agenda_item = t.agenda_item
                         AND cv.meeting_date = t.meeting_date
                        JOIN norfolk_vote_enrichment e ON e.title = cv.title
                        WHERE t.stance IN ('oppose','support')
                        GROUP BY e.topic
                        HAVING total >= 3
                        ORDER BY CAST(opp AS REAL) / total DESC
                    """).fetchall()
                    if sent_rows:
                        total_stance = conn.execute(
                            "SELECT COUNT(*) FROM norfolk_council_testimony "
                            "WHERE stance IN ('oppose','support')"
                        ).fetchone()[0]
                        test_lines.append(
                            f"\n#### Public Sentiment by Topic\n"
                            f"Based on {total_stance} stance-bearing testimony records "
                            f"(oppose/support only; 'comment' stance excluded as neutral)."
                        )
                        for topic, opp, sup, total in sent_rows:
                            pct = round(100 * opp / total) if total else 0
                            bar = "#" * (pct // 10) + "-" * (10 - pct // 10)
                            test_lines.append(
                                f"  {topic:<22s}  {bar}  {pct:3d}% oppose "
                                f"({opp} opp / {sup} sup, n={total})"
                            )

            # Ward-filtered query — fires when "ward N" appears in query
            elif query_ward:
                ward_stance_filter = ""
                ward_params: list = [query_ward]
                # Narrow by oppose/support if query signals a stance
                if any(w in q_lower for w in ("oppos", "against", "no vote", "fought")):
                    ward_stance_filter = "AND t.stance = 'oppose'"
                elif any(w in q_lower for w in ("support", "favor", "pro", "backed")):
                    ward_stance_filter = "AND t.stance = 'support'"
                else:
                    ward_stance_filter = "AND t.stance IN ('oppose','support')"
                # Add topic filter if present
                topic_ward_clause = ""
                if topic_terms:
                    topic_ward_clause = "AND (" + " OR ".join(
                        "LOWER(v.title) LIKE ? OR LOWER(e.plain_english) LIKE ?"
                        for _ in topic_terms
                    ) + ")"
                    ward_params += [p for t in topic_terms for p in (f"%{t.lower()}%", f"%{t.lower()}%")]
                ward_rows = conn.execute(f"""
                    SELECT t.speaker_name, t.stance, t.meeting_date,
                           t.agenda_item, t.ward, v.result, v.vote_count,
                           e.plain_english
                    FROM norfolk_council_testimony t
                    JOIN norfolk_council_votes v
                        ON v.meeting_date = t.meeting_date
                       AND v.agenda_item  = t.agenda_item
                    LEFT JOIN norfolk_vote_enrichment e ON e.title = v.title
                    WHERE t.ward = ?
                      {ward_stance_filter}
                      {topic_ward_clause}
                    ORDER BY t.meeting_date DESC
                    LIMIT 20
                """, ward_params).fetchall()
                if ward_rows:
                    label = f"Ward {query_ward}"
                    test_lines.append(f"\n#### Testimony from {label} residents")
                    for r in ward_rows:
                        desc = (r["plain_english"] or "")[:70]
                        test_lines.append(
                            f"  [{r['stance'].upper():>7}] {r['meeting_date']} "
                            f"{r['agenda_item']} ({r['vote_count']} {r['result']}) "
                            f"— {r['speaker_name']} ({r['speaker_addr'] if 'speaker_addr' in r.keys() else ''})"
                        )
                        if desc:
                            test_lines.append(f"           {desc}")
                else:
                    test_lines.append(f"\n#### No Ward {query_ward} testimony matched those criteria")

            # Topic-matched testimony
            elif topic_terms:
                like_clauses = " OR ".join(
                    "LOWER(v.title) LIKE ? OR LOWER(e.plain_english) LIKE ?"
                    for _ in topic_terms
                )
                params = [p for t in topic_terms for p in (f"%{t.lower()}%", f"%{t.lower()}%")]
                test_rows = conn.execute(f"""
                    SELECT t.speaker_name, t.stance, t.meeting_date,
                           t.agenda_item, t.ward, v.result, v.vote_count,
                           e.plain_english
                    FROM norfolk_council_testimony t
                    JOIN norfolk_council_votes v
                        ON v.meeting_date = t.meeting_date
                       AND v.agenda_item  = t.agenda_item
                    LEFT JOIN norfolk_vote_enrichment e ON e.title = v.title
                    WHERE ({like_clauses})
                      AND t.stance IN ('oppose','support','present')
                    ORDER BY t.meeting_date DESC
                    LIMIT 15
                """, params).fetchall()
                if test_rows:
                    test_lines.append("\n#### Public Testimony on matched items")
                    for r in test_rows:
                        desc = (r["plain_english"] or "")[:70]
                        ward_tag = f" Ward {r['ward']}" if r["ward"] else ""
                        test_lines.append(
                            f"  [{r['stance'].upper():>7}] {r['meeting_date']} "
                            f"{r['agenda_item']} ({r['vote_count']} {r['result']}) "
                            f"— {r['speaker_name']}{ward_tag}"
                        )
                        if desc:
                            test_lines.append(f"           {desc}")

            # Named-member query — show recent contested testimony
            elif named_members:
                mem_test = conn.execute("""
                    SELECT t.speaker_name, t.stance, t.meeting_date,
                           t.agenda_item, t.ward, v.result, v.vote_count,
                           e.plain_english
                    FROM norfolk_council_testimony t
                    JOIN norfolk_council_votes v
                        ON v.meeting_date = t.meeting_date
                       AND v.agenda_item  = t.agenda_item
                    LEFT JOIN norfolk_vote_enrichment e ON e.title = v.title
                    WHERE t.stance IN ('oppose','support')
                    ORDER BY t.meeting_date DESC
                    LIMIT 10
                """).fetchall()
                if mem_test:
                    test_lines.append("\n#### Recent contested testimony")
                    for r in mem_test:
                        desc = (r["plain_english"] or "")[:65]
                        ward_tag = f" Ward {r['ward']}" if r["ward"] else ""
                        test_lines.append(
                            f"  [{r['stance'].upper():>7}] {r['meeting_date']} "
                            f"{r['agenda_item']} ({r['vote_count']} {r['result']}) "
                            f"— {r['speaker_name']}{ward_tag}"
                        )
                        if desc:
                            test_lines.append(f"           {desc}")

            # ── Who represents ward X? ───────────────────────────────────────
            _ward_rep_query = any(w in q_lower for w in (
                "who represents ward", "represents ward", "who is ward",
                "council member for ward", "rep for ward", "ward rep",
                "ward representative", "representative for ward",
                "who sits on", "who holds ward", "who holds superward",
                "who is the rep", "who is my council",
            ))
            if _ward_rep_query:
                import re as _re
                _wm = _re.search(r'\b(?:super\s*ward|ward)\s*([1-7])\b', q_lower)
                if _wm:
                    _wn = int(_wm.group(1))
                    _wrep = _WARD_REP.get(_wn)
                    _wseat = _SEAT_LABEL.get(_wn, f"Ward {_wn}")
                    if _wrep:
                        test_lines.append(f"\n#### Norfolk {_wseat} Council Seat")
                        test_lines.append(f"  Current representative: **{_wrep}** ({_wseat})")
                        if _wn == 5:
                            test_lines.append("  (Clanton replaced Andria McClellan in January 2025)")
                        elif _wn == 6:
                            test_lines.append("  (McGee replaced Danica Royster in January 2025)")
                        elif _wn == 7:
                            test_lines.append("  (Paige replaced Paul Riddick in January 2023)")
                else:
                    # No specific ward number — show full map
                    test_lines.append("\n#### Norfolk City Council — Seat Assignments")
                    test_lines.append("  Mayor (at-large): Alexander")
                    for _wn, _wrep in _WARD_REP.items():
                        _wseat = _SEAT_LABEL.get(_wn, f"Ward {_wn}")
                        test_lines.append(f"  {_wseat}: {_wrep}")

            # Ward vs. council outcome — which wards get overruled most
            _ward_outcome_trigger = (
                ("ward" in q_lower and any(w in q_lower for w in (
                    "overrul", "overrod", "ignored", "ignore",
                    "outcome", "listened", "least listen",
                )))
                or any(w in q_lower for w in (
                    "which ward", "which community", "community ignored",
                    "most overruled", "ignored the most",
                ))
            )
            if _ward_outcome_trigger and _table_exists(conn, "norfolk_council_testimony"):
                ward_out = conn.execute("""
                    SELECT ward,
                           COUNT(*) items_with_opp,
                           SUM(passed) passed_despite,
                           SUM(oppose_n) total_opp
                    FROM (
                        SELECT t.ward, t.meeting_date, t.agenda_item,
                               MAX(CASE WHEN LOWER(v.result) LIKE '%pass%' THEN 1 ELSE 0 END) passed,
                               COUNT(*) oppose_n
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_votes v
                          ON v.agenda_item = t.agenda_item
                         AND v.meeting_date = t.meeting_date
                        WHERE t.ward IS NOT NULL AND t.stance = 'oppose'
                        GROUP BY t.ward, t.meeting_date, t.agenda_item
                    )
                    GROUP BY ward
                    HAVING items_with_opp >= 2
                    ORDER BY CAST(passed_despite AS REAL) / items_with_opp DESC
                """).fetchall()
                # Superward-level outcome (wards 6/7 via superward column)
                sw_out = conn.execute("""
                    SELECT superward,
                           COUNT(*) items_with_opp,
                           SUM(passed) passed_despite,
                           SUM(oppose_n) total_opp
                    FROM (
                        SELECT t.superward, t.meeting_date, t.agenda_item,
                               MAX(CASE WHEN LOWER(v.result) LIKE '%pass%' THEN 1 ELSE 0 END) passed,
                               COUNT(*) oppose_n
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_votes v
                          ON v.agenda_item = t.agenda_item
                         AND v.meeting_date = t.meeting_date
                        WHERE t.superward IS NOT NULL AND t.stance = 'oppose'
                        GROUP BY t.superward, t.meeting_date, t.agenda_item
                    )
                    GROUP BY superward
                    HAVING items_with_opp >= 2
                    ORDER BY CAST(passed_despite AS REAL) / items_with_opp DESC
                """).fetchall()
                all_out = [(w, c, p, o) for w, c, p, o in ward_out] + \
                          [(w, c, p, o) for w, c, p, o in sw_out]
                all_out.sort(key=lambda x: x[2] / x[1] if x[1] else 0, reverse=True)
                if all_out:
                    test_lines.append(
                        "\n#### Ward vs. Council Outcome\n"
                        "(Items where ward/superward residents testified in opposition — % that still passed)"
                    )
                    for ward, contested, passed, opp_n in all_out:
                        pct = round(100 * passed / contested) if contested else 0
                        bar = "#" * (pct // 10) + "-" * (10 - pct // 10)
                        seat = _SEAT_LABEL.get(ward, f"Ward {ward}")
                        rep  = _WARD_REP.get(ward)
                        rep_tag = f"  [{rep} — {seat} rep]" if rep else ""
                        test_lines.append(
                            f"  {seat:<14}  {bar}  {pct:3d}% overruled "
                            f"({passed}/{contested} items passed | {opp_n} oppose testimonies){rep_tag}"
                        )

            # ── Testimony-to-vote alignment per member ─────────────────────────
            _align_trigger = any(w in q_lower for w in (
                "listen", "responsive", "responsiveness", "represent",
                "votes with constituents", "votes with ward",
                "votes against constituents", "ignores ward",
                "alignment score", "scorecard", "accountability score",
                "who listens", "who ignores", "responsive to",
            ))
            if _align_trigger and _table_exists(conn, "norfolk_council_testimony"):
                _ward_to_rep_sql = {1: "Smigiel Jr.", 2: "Doyle", 3: "Johnson",
                                    4: "Thomas Jr.", 5: "Clanton"}
                _sw_to_rep_sql = {6: "McGee", 7: "Paige"}
                _align_parts: dict[str, list[int]] = {}
                for _aw, _arep in _ward_to_rep_sql.items():
                    rows_a = conn.execute("""
                        SELECT t.stance, mv.vote
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_member_votes mv
                          ON mv.agenda_item = t.agenda_item
                         AND mv.meeting_date = t.meeting_date
                        WHERE t.ward = ? AND t.stance IN ('support','oppose')
                          AND mv.member_name = ?
                    """, (_aw, _arep)).fetchall()
                    if rows_a:
                        aligned = sum(
                            1 for stance, vote in rows_a
                            if (stance == 'oppose' and vote.lower() == 'no')
                            or (stance == 'support' and vote.lower() == 'yes')
                        )
                        _align_parts[_arep] = [aligned, len(rows_a)]
                for _asw, _arep in _sw_to_rep_sql.items():
                    rows_a = conn.execute("""
                        SELECT t.stance, mv.vote
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_member_votes mv
                          ON mv.agenda_item = t.agenda_item
                         AND mv.meeting_date = t.meeting_date
                        WHERE t.superward = ? AND t.stance IN ('support','oppose')
                          AND mv.member_name = ?
                    """, (_asw, _arep)).fetchall()
                    if rows_a:
                        aligned = sum(
                            1 for stance, vote in rows_a
                            if (stance == 'oppose' and vote.lower() == 'no')
                            or (stance == 'support' and vote.lower() == 'yes')
                        )
                        _align_parts[_arep] = [aligned, len(rows_a)]
                align_rows = sorted(
                    [(k, v[0], v[1]) for k, v in _align_parts.items() if v[1] >= 3],
                    key=lambda x: x[1] / x[2], reverse=True,
                )
                if align_rows:
                    test_lines.append(
                        "\n#### Constituency Alignment Scorecard\n"
                        "(How often each ward/superward rep votes with their constituents' testimony stance)"
                    )
                    for name, aligned, total in align_rows:
                        pct = round(100 * aligned / total) if total else 0
                        ward_info = _REP_WARD.get(name, (None, None))
                        seat_tag = f" ({ward_info[1]})" if ward_info[0] else ""
                        bar = "#" * (pct // 10) + "-" * (10 - pct // 10)
                        test_lines.append(
                            f"  {name:16s}  {bar}  {pct:3d}% aligned "
                            f"({aligned}/{total} items){seat_tag}"
                        )

            # ── Meeting recap ─────────────────────────────────────────────────
            import re as _re2
            _meeting_trigger = any(w in q_lower for w in (
                "last meeting", "recent meeting", "latest meeting",
                "what happened", "meeting recap", "meeting summary",
                "council meeting", "agenda recap",
            ))
            _date_match = _re2.search(
                r'(january|february|march|april|may|june|july|august|'
                r'september|october|november|december)\s+\d{1,2},?\s*\d{4}',
                q_lower,
            )
            if (_meeting_trigger or _date_match) and _table_exists(conn, "norfolk_council_votes"):
                if _date_match:
                    target_date = _date_match.group(0).upper()
                    if "," not in target_date:
                        parts = target_date.rsplit(" ", 1)
                        target_date = parts[0] + ", " + parts[1] if len(parts) == 2 else target_date
                    mtg_rows = conn.execute("""
                        SELECT cv.agenda_item, cv.vote_count, cv.result,
                               e.plain_english, e.topic, cv.category
                        FROM norfolk_council_votes cv
                        LEFT JOIN norfolk_vote_enrichment e ON e.title = cv.title
                        WHERE cv.meeting_date = ?
                        ORDER BY cv.agenda_item
                    """, (target_date,)).fetchall()
                else:
                    latest = conn.execute("""
                        SELECT meeting_date FROM norfolk_council_votes
                        ORDER BY ROWID DESC LIMIT 1
                    """).fetchone()
                    if latest:
                        target_date = latest[0]
                        mtg_rows = conn.execute("""
                            SELECT cv.agenda_item, cv.vote_count, cv.result,
                                   e.plain_english, e.topic, cv.category
                            FROM norfolk_council_votes cv
                            LEFT JOIN norfolk_vote_enrichment e ON e.title = cv.title
                            WHERE cv.meeting_date = ?
                            ORDER BY cv.agenda_item
                        """, (target_date,)).fetchall()
                    else:
                        mtg_rows = []
                if mtg_rows:
                    sub = [r for r in mtg_rows if r[5] == 'substantive']
                    consent = [r for r in mtg_rows if r[5] == 'consent']
                    test_lines.append(f"\n#### Meeting Recap — {target_date}")
                    test_lines.append(
                        f"  {len(mtg_rows)} items total "
                        f"({len(sub)} substantive, {len(consent)} consent)"
                    )
                    for item, vc, result, plain, topic, cat in mtg_rows:
                        if cat == 'substantive':
                            desc = (plain or "")[:80]
                            topic_tag = f" [{topic}]" if topic else ""
                            test_lines.append(f"  {item}  {vc} {result}{topic_tag}")
                            if desc:
                                test_lines.append(f"    {desc}")

            # ── Upcoming agenda ────────────────────────────────────────────────
            _upcoming_trigger = any(w in q_lower for w in (
                "upcoming", "next meeting", "next council", "what's coming",
                "what is coming", "agenda", "scheduled", "future meeting",
                "when is the next", "next session",
            ))
            if _upcoming_trigger and _table_exists(conn, "norfolk_upcoming_agenda"):
                up_rows = conn.execute("""
                    SELECT meeting_date, item_ref, title, status, agenda_url
                    FROM norfolk_upcoming_agenda
                    ORDER BY meeting_date, item_ref
                """).fetchall()
                if up_rows:
                    from itertools import groupby as _groupby
                    test_lines.append("\n#### Upcoming Norfolk City Council Meetings")
                    by_date: dict[str, list] = {}
                    for date_s, ref, title, status, aurl in up_rows:
                        by_date.setdefault(date_s, []).append((ref, title, status, aurl))
                    for mtg_date, items in by_date.items():
                        pending = items[0][2] == 'pending'
                        if pending:
                            test_lines.append(f"  {mtg_date} — Formal Session (agenda not yet posted)")
                        else:
                            aurl = items[0][3] or ""
                            url_tag = f"  [agenda PDF]({aurl})" if aurl else ""
                            test_lines.append(f"  {mtg_date} — Formal Session{url_tag}")
                            for ref, title, _, _ in items:
                                ref_tag = f"{ref}: " if ref and ref != "AGENDA" else ""
                                test_lines.append(f"    {ref_tag}{title}")

            # ── Opposition trend over time ─────────────────────────────────────
            _trend_trigger = any(w in q_lower for w in (
                "trend", "over time", "growing", "increasing", "declining",
                "more opposed", "less opposed", "opposition trend",
                "changing", "shifted", "shift", "year over year",
                "historically", "compared to", "getting worse",
                "getting better", "more resistance", "less resistance",
            ))
            if _trend_trigger and _table_exists(conn, "norfolk_council_testimony"):
                trend_rows = conn.execute("""
                    SELECT SUBSTR(meeting_date, -4) yr,
                           SUM(CASE WHEN stance='oppose' THEN 1 ELSE 0 END) oppose,
                           SUM(CASE WHEN stance='support' THEN 1 ELSE 0 END) support,
                           SUM(CASE WHEN stance='comment' THEN 1 ELSE 0 END) comment,
                           COUNT(*) total
                    FROM norfolk_council_testimony
                    GROUP BY SUBSTR(meeting_date, -4)
                    ORDER BY yr
                """).fetchall()
                if trend_rows and len(trend_rows) >= 2:
                    test_lines.append("\n#### Public Testimony Trend by Year")
                    test_lines.append("  Year   Oppose  Support  Comment  Total  Opp%")
                    test_lines.append("  " + "-" * 50)
                    for yr, opp, sup, com, tot in trend_rows:
                        opp_pct = round(100 * opp / tot) if tot else 0
                        test_lines.append(
                            f"  {yr}   {opp:6d}  {sup:7d}  {com:7d}  {tot:5d}  {opp_pct:3d}%"
                        )
                    first_opp_pct = round(100 * trend_rows[0][1] / trend_rows[0][4]) if trend_rows[0][4] else 0
                    last_opp_pct = round(100 * trend_rows[-1][1] / trend_rows[-1][4]) if trend_rows[-1][4] else 0
                    direction = "rising" if last_opp_pct > first_opp_pct else "declining" if last_opp_pct < first_opp_pct else "stable"
                    test_lines.append(
                        f"  Trend: opposition share {direction} "
                        f"({first_opp_pct}% in {trend_rows[0][0]} → {last_opp_pct}% in {trend_rows[-1][0]})"
                    )

            # Influence summary note — dynamic stat replaces hardcoded version
            if test_lines:
                _inf_opp_pass = conn.execute("""
                    SELECT COUNT(*) FROM (
                        SELECT t.meeting_date, t.agenda_item
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_votes v
                          ON v.agenda_item=t.agenda_item AND v.meeting_date=t.meeting_date
                        GROUP BY t.meeting_date, t.agenda_item
                        HAVING SUM(CASE WHEN t.stance='oppose' THEN 1 ELSE 0 END) >
                               SUM(CASE WHEN t.stance='support' THEN 1 ELSE 0 END)
                           AND LOWER(v.result) LIKE '%pass%'
                    )
                """).fetchone()[0]
                _inf_opp_total = conn.execute("""
                    SELECT COUNT(*) FROM (
                        SELECT t.meeting_date, t.agenda_item
                        FROM norfolk_council_testimony t
                        JOIN norfolk_council_votes v
                          ON v.agenda_item=t.agenda_item AND v.meeting_date=t.meeting_date
                        GROUP BY t.meeting_date, t.agenda_item
                        HAVING SUM(CASE WHEN t.stance='oppose' THEN 1 ELSE 0 END) >
                               SUM(CASE WHEN t.stance='support' THEN 1 ELSE 0 END)
                    )
                """).fetchone()[0]
                _inf_pct = round(100 * _inf_opp_pass / _inf_opp_total) if _inf_opp_total else 96
                test_lines.insert(0, (
                    f"\n#### Testimony influence note: "
                    f"{_inf_pct}% of items where public opposition exceeded support still passed. "
                    "Doyle is the only member whose NO votes consistently match "
                    "opposition testimony from Ward 2 constituents."
                ))
                lines += ["", "### Public Testimony Records"] + test_lines

        conn.close()
        if len(lines) > 3:
            blocks.append("\n".join(lines))

    except Exception:
        pass


# ── Virginia Beach City Council context ──────────────────────────────────────

# Static roster — current term (2024 election), valid through at least 2026 primary.
# Used as fallback when vb_council_members table hasn't been seeded yet.
_VB_COUNCIL_ROSTER_FALLBACK: list[tuple] = [
    ("Robert M. \"Bobby\" Dyer", "Mayor",       0,  "mayorsoffice@vbgov.com"),
    ("David Hutcheson",          "District 1",  1,  "dhutcheson@vbgov.com"),
    ("Barbara Henley",           "District 2",  2,  "bhenley@vbgov.com"),
    ("Michael Berlucchi",        "District 3",  3,  "mberlucc@vbgov.com"),
    ("Dr. Amelia Ross-Hammond",  "District 4",  4,  "arosshammond@vbgov.com"),
    ("Rosemary Wilson",          "District 5",  5,  "rcwilson@vbgov.com"),
    ("Robert W. \"Worth\" Remick","District 6", 6,  "wremick@vbgov.com"),
    ("Cal \"Cash\" Jackson-Green","District 7", 7,  "cjacksongreen@vbgov.com"),
    ("Stacy Cummings",           "District 8",  8,  "stcummings@vbgov.com"),
    ("Joashua F. Schulman",      "District 9",  9,  "jschulman@vbgov.com"),
    ("Jennifer V. Rouse",        "District 10", 10, "jvrouse@vbgov.com"),
]

_VB_TRIGGER_RE = re.compile(
    r"virginia\s+beach.*(council|member|district|representative|agenda|meeting|upcoming|vote|voted|votes|mayor|appoint|ordinance|resolution|support|oppos|fund|donor|profile|background)"
    r"|vb\s+(city\s+)?council"
    r"|(council|member|district|representative|agenda|upcoming|ordinance|resolution).*virginia\s+beach"
    r"|berlucchi|hutcheson|cummings|jackson.green|remick|ross.hammond|schulman|dyer"
    r"|henley|rouse"
    r"|(wilson).*(vote|council|district|virginia|support|oppos|fund|profile)",
    re.IGNORECASE,
)

# Bare surnames for typo-tolerant matching (e.g. "Berluchhi" -> "berlucchi").
# Single-word only — hyphenated names (jackson-green, ross-hammond) are left to
# the exact-match paths above since word-tokenizing would split them anyway.
_VB_LASTNAMES_FUZZY = (
    "berlucchi", "hutcheson", "cummings", "henley", "remick",
    "schulman", "rouse", "wilson", "dyer",
)

# Source link for an individual VB Council agenda item — the city's own
# IQM2 portal page for that resolution (full text, staff report, full roll call).
_VB_LEGIFILE_URL = "https://virginiabeachva.iqm2.com/Citizens/Detail_LegiFile.aspx?ID={}"


def _vb_fuzzy_key_lookup(q_lower: str, keys) -> str | None:
    """Fuzzy-match query words against known VB council surnames/keys.

    Fallback only — exact substring checks should always run first.
    """
    words = re.findall(r"[a-z]{5,}", q_lower)
    keys = list(keys)
    for w in words:
        match = difflib.get_close_matches(w, keys, n=1, cutoff=0.75)
        if match:
            return match[0]
    return None


def _add_vb_council_context(blocks: list[str], query: str, terms: list[str]) -> None:
    q_lower = query.lower()

    if not _VB_TRIGGER_RE.search(query) and not _vb_fuzzy_key_lookup(q_lower, _VB_LASTNAMES_FUZZY):
        return

    # Member key maps — defined up front because the council-agenda trigger
    # below references _VB_FINANCE_KEY_MAP before the finance section runs.
    _VB_FINANCE_KEY_MAP = {
        "berlucchi": "Berlucchi", "dyer": "Dyer", "henley": "Henley",
        "hutcheson": "Hutcheson", "wilson": "Wilson", "remick": "Remick",
        "ross-hammond": "Ross-Hammond", "ross hammond": "Ross-Hammond",
        "schulman": "Schulman", "rouse": "Rouse", "cummings": "Cummings",
        "jackson-green": "Jackson-Green", "jackson green": "Jackson-Green",
    }
    _ALL_VB_FINANCE = ["Berlucchi", "Dyer", "Henley", "Hutcheson", "Wilson",
                       "Remick", "Ross-Hammond", "Schulman", "Rouse", "Cummings",
                       "Jackson-Green"]

    try:
        conn = _connect("polls")
        if conn is None:
            return
        lines: list[str] = ["## Virginia Beach City Council"]

        # ── member roster ──────────────────────────────────────────────────
        member_trigger = any(w in q_lower for w in (
            "member", "council", "who", "roster", "list", "all", "district",
            "represent", "mayor", "contact", "email",
        ))

        members: list[tuple] = []
        if _table_exists(conn, "vb_council_members"):
            members = conn.execute("""
                SELECT name, district, district_num, email
                FROM vb_council_members
                ORDER BY district_num
            """).fetchall()
        if not members:
            members = _VB_COUNCIL_ROSTER_FALLBACK

        if members and member_trigger:
            # Specific district lookup
            _dist_m = re.search(r"\bdistrict\s+(\d+)\b", q_lower)
            if _dist_m:
                _dn = int(_dist_m.group(1))
                _match = [(n, d, dn, e) for n, d, dn, e in members if dn == _dn]
                if _match:
                    n, d, dn, e = _match[0]
                    lines.append(f"\n#### Virginia Beach {d}")
                    lines.append(f"  Current representative: **{n}**")
                    if e:
                        lines.append(f"  Email: {e}")
                else:
                    lines.append(f"\n  No member found for District {_dn}")
            else:
                lines.append("\n#### Virginia Beach City Council — Member Roster")
                for name, district, dnum, email in members:
                    label = f"{district:<12}" if district != "Mayor" else "Mayor (at-large)"
                    e_str = f"  ({email})" if email else ""
                    lines.append(f"  {label}  {name}{e_str}")

        # ── upcoming agenda ────────────────────────────────────────────────
        agenda_trigger = any(w in q_lower for w in (
            "upcoming", "next meeting", "next council", "agenda",
            "coming up", "scheduled", "vote on", "ordinance", "resolution",
        ))

        if agenda_trigger and _table_exists(conn, "vb_upcoming_agenda"):
            ag_rows = conn.execute("""
                SELECT meeting_date, item_ref, category, title
                FROM vb_upcoming_agenda
                WHERE meeting_date >= date('now')
                ORDER BY meeting_date, item_ref
                LIMIT 30
            """).fetchall()

            if ag_rows:
                lines.append("\n#### Virginia Beach City Council — Upcoming Agenda Items")
                cur_date = ""
                for mdate, ref, cat, title in ag_rows:
                    if mdate != cur_date:
                        cur_date = mdate
                        lines.append(f"\n  {mdate} (Formal Session)")
                    short_title = title[:100] + ("…" if len(title) > 100 else "")
                    lines.append(f"    {ref:<6}  [{cat}] {short_title}")
            else:
                lines.append("\n  No upcoming agenda items posted yet — agendas typically appear 1-2 weeks before each meeting.")

        # ── overall council agenda / policy priorities ─────────────────────
        _council_agenda_trigger = (
            not any(k in q_lower for k in _VB_FINANCE_KEY_MAP)  # no specific member named
            and any(w in q_lower for w in (
                "agenda", "priorities", "priority", "focus", "what topics",
                "what does the council", "what does vb council",
                "what does virginia beach council", "overall agenda",
                "policy areas", "what kind", "what do they vote",
                "council stance", "council overall", "collectively",
                "what does the vb", "big issues", "main issues",
                "what are they working on", "what is the council working",
            ))
        )
        if _council_agenda_trigger and _table_exists(conn, "vb_member_dissent"):
            ovr = conn.execute("""
                SELECT COUNT(DISTINCT resolution_id) total,
                       SUM(CASE WHEN vote_no > 0 THEN 1 ELSE 0 END) contested,
                       MIN(meeting_date), MAX(meeting_date)
                FROM (
                    SELECT resolution_id,
                           MAX(vote_no) vote_no,
                           MIN(meeting_date) meeting_date
                    FROM vb_council_member_votes
                    GROUP BY resolution_id
                )
            """).fetchone()
            topic_agenda = conn.execute("""
                SELECT topic,
                       SUM(topic_vote_count) / COUNT(DISTINCT member_name) avg_votes,
                       AVG(council_no_pct) avg_no_pct
                FROM vb_member_dissent
                GROUP BY topic
                ORDER BY avg_votes DESC
            """).fetchall()
            if ovr and topic_agenda:
                total_r, contested_r, d_min, d_max = ovr
                consensus_pct = round(100 * (total_r - (contested_r or 0)) / total_r) if total_r else 0
                lines.append(f"\n#### Virginia Beach City Council — Legislative Agenda ({d_min} to {d_max})")
                lines.append(
                    f"Total resolutions: {total_r}  |  "
                    f"Contested (any No vote): {contested_r or 0} ({100 - consensus_pct}%)  |  "
                    f"Consensus: {consensus_pct}% pass without dissent"
                )
                lines.append("")
                lines.append(f"  {'Topic':<22} {'Est. votes':>10}  {'Avg No%':>8}  Stance")
                lines.append("  " + "-" * 58)
                for topic, avg_v, avg_no in topic_agenda:
                    stance = (
                        "Occasionally divided" if avg_no >= 1.5
                        else "Strong consensus" if avg_no >= 0.5
                        else "Near-unanimous"
                    )
                    lines.append(
                        f"  {topic:<22} {int(avg_v):>10,}  {avg_no:>7.1f}%  {stance}"
                    )
                lines.append(
                    "\nNote: Virginia Beach council is highly consensus-driven. "
                    "Most dissent appears on rezoning and governance items."
                )

        # ── vote data availability note ────────────────────────────────────
        vote_trigger = any(w in q_lower for w in (
            "vote", "voted", "votes", "voting", "yes", "no", "passed", "failed",
            "dissent", "opposed", "against", "absent", "record", "history",
            "profile", "background", "tell me about", "what does", "what do",
            "support", "supports", "supporting", "favor", "position",
        ))
        has_vote_table = _table_exists(conn, "vb_council_member_votes")
        _vote_section_added = False

        if vote_trigger and has_vote_table:
            # Named member query
            _vb_member_names = {
                "berlucchi": "Michael F. Berlucchi",
                "dyer": 'Robert M. "Bobby" Dyer',
                "cummings": "Stacy Cummings",
                "henley": "Barbara M. Henley",
                "hutcheson": "David Hutcheson",
                "jackson": "Cal",
                "jackson-green": "Cal",
                "remick": "Robert W.",
                "ross-hammond": "Dr. Amelia",
                "ross hammond": "Dr. Amelia",
                "rouse": "Jennifer Rouse",
                "schulman": "Joashua",
                "wilson": "Rosemary C. Wilson",
            }
            matched_member = None
            for key, partial in _vb_member_names.items():
                if key in q_lower:
                    row = conn.execute(
                        "SELECT DISTINCT member_name, district FROM vb_council_member_votes "
                        "WHERE member_name LIKE ?", (f"%{partial}%",)
                    ).fetchone()
                    if row:
                        matched_member = row[0]
                        break

            if not matched_member:
                fuzzy_key = _vb_fuzzy_key_lookup(q_lower, _vb_member_names.keys())
                if fuzzy_key:
                    row = conn.execute(
                        "SELECT DISTINCT member_name, district FROM vb_council_member_votes "
                        "WHERE member_name LIKE ?", (f"%{_vb_member_names[fuzzy_key]}%",)
                    ).fetchone()
                    if row:
                        matched_member = row[0]

            # Title / subject keyword search
            _kw_excl = {"virginia", "beach", "council", "vote", "voted", "how", "did",
                        "what", "the", "on", "at", "in", "of", "is", "are", "was"}
            search_terms = [t for t in terms if t.lower() not in _kw_excl and len(t) > 3]

            if matched_member:
                # Member voting record summary
                stats = conn.execute("""
                    SELECT COUNT(*) total,
                           SUM(CASE WHEN vote='Yes/Aye' THEN 1 ELSE 0 END) yes_v,
                           SUM(CASE WHEN vote='No/Nay'  THEN 1 ELSE 0 END) no_v,
                           SUM(CASE WHEN vote='Absent'  THEN 1 ELSE 0 END) absent,
                           MIN(meeting_date), MAX(meeting_date)
                    FROM vb_council_member_votes WHERE member_name=?
                """, (matched_member,)).fetchone()
                if stats and stats[0]:
                    total_v, yes_v, no_v, absent, d_min, d_max = stats
                    lines.append(f"\n#### VB Council — {matched_member} Vote Summary")
                    lines.append(f"  Period: {d_min} to {d_max}  ({total_v} recorded votes)")
                    lines.append(f"  Yes/Aye: {yes_v}   No/Nay: {no_v}   Absent: {absent}")
                    if total_v:
                        lines.append(f"  Dissent rate: {round(100*no_v/(yes_v+no_v) if yes_v+no_v else 0)}%  "
                                     f"Absence rate: {round(100*absent/total_v)}%")

                # Show their No votes
                no_rows = conn.execute("""
                    SELECT meeting_date, title, vote_yes, vote_no, resolution_id
                    FROM vb_council_member_votes
                    WHERE member_name=? AND vote='No/Nay'
                    ORDER BY meeting_date DESC LIMIT 10
                """, (matched_member,)).fetchall()
                if no_rows:
                    lines.append(f"\n  Recent No/Nay votes ({len(no_rows)} shown):")
                    for mdate, title, vy, vn, res_id in no_rows:
                        url = _VB_LEGIFILE_URL.format(res_id)
                        lines.append(f"    {mdate}  [{vy}Y/{vn}N]  [{title[:80]}]({url})")
                _vote_section_added = True

            elif search_terms:
                # Search by topic keywords
                like = "%" + "%".join(search_terms[:3]) + "%"
                item_rows = conn.execute("""
                    SELECT title, meeting_date, vote_yes, vote_no,
                           GROUP_CONCAT(member_name || '=' || vote, ' | ') votes_detail,
                           resolution_id
                    FROM vb_council_member_votes
                    WHERE title LIKE ?
                    GROUP BY resolution_id
                    ORDER BY meeting_date DESC LIMIT 5
                """, (like,)).fetchall()
                if item_rows:
                    lines.append(f"\n#### VB Council — Vote Search: {' '.join(search_terms[:3])!r}")
                    for title, mdate, vy, vn, detail, res_id in item_rows:
                        url = _VB_LEGIFILE_URL.format(res_id)
                        lines.append(f"\n  {mdate}  [{vy}Y/{vn}N]  [{title[:90]}]({url})")
                        if detail:
                            # Show No votes only
                            no_detail = [p for p in detail.split(" | ") if "No/Nay" in p]
                            if no_detail:
                                lines.append(f"    Dissent: {', '.join(no_detail)}")
                else:
                    lines.append(f"\n  No VB vote records found matching: {' '.join(search_terms[:3])}")
                _vote_section_added = True

        if has_vote_table and not _vote_section_added:
            # General summary — shown whenever no member/topic branch fired
            lines.append("\n#### VB Council — Most Contested Votes (most No votes)")
            contested = conn.execute("""
                SELECT title, meeting_date, vote_yes, vote_no, resolution_id
                FROM vb_council_member_votes
                GROUP BY resolution_id
                HAVING SUM(CASE WHEN vote='No/Nay' THEN 1 ELSE 0 END) >= 3
                ORDER BY SUM(CASE WHEN vote='No/Nay' THEN 1 ELSE 0 END) DESC
                LIMIT 8
            """).fetchall()
            for title, mdate, vy, vn, res_id in contested:
                url = _VB_LEGIFILE_URL.format(res_id)
                lines.append(f"  {mdate}  [{vy}Y/{vn}N]  [{title[:80]}]({url})")

        # ── campaign finance ──────────────────────────────────────────────
        # (_VB_FINANCE_KEY_MAP / _ALL_VB_FINANCE defined at function top)
        _vb_finance_trigger = any(w in q_lower for w in (
            "donor", "fund", "money", "contribut", "financ",
            "who pays", "paid by", "backed by", "receiv", "campaign", "raise",
            "profile", "background", "tell me about", "who is",
            "support", "supports", "what does", "what do",
        ))
        _named_vb_finance = list(dict.fromkeys(
            v for k, v in _VB_FINANCE_KEY_MAP.items() if k in q_lower
        ))
        if _vb_finance_trigger and _table_exists(conn, "vb_finance_summary"):
            _finance_targets = _named_vb_finance if _named_vb_finance else _ALL_VB_FINANCE
            _vb_donor_lines: list[str] = []
            for db_key in _finance_targets[:4]:
                summary = conn.execute(
                    "SELECT total_raised, top_sector, top_sector_pct, "
                    "undisclosed_count, undisclosed_amt, undisclosed_pct "
                    "FROM vb_finance_summary WHERE member_name=?", (db_key,)
                ).fetchone()
                sectors = conn.execute(
                    "SELECT sector, total_amount, pct_of_total, donor_count "
                    "FROM vb_finance_totals WHERE member_name=? "
                    "ORDER BY total_amount DESC LIMIT 5", (db_key,)
                ).fetchall()
                if summary or sectors:
                    _vb_donor_lines.append(f"\n#### {db_key} — Campaign Finance (SBE contributions)")
                    if summary and sectors:
                        _vb_donor_lines.append(
                            f"Total raised: ${summary[0]:,.0f}  "
                            f"(top sector: {summary[1]}, {summary[2]:.1f}%)"
                        )
                        _vb_donor_lines.append("Donor sectors:")
                        for s in sectors:
                            _vb_donor_lines.append(
                                f"  {s[0]:<18} ${s[1]:>9,.0f}  ({s[2]:>5.1f}%,  {s[3]} donors)"
                            )
                        unk_pct = summary[5] or 0
                        if unk_pct >= 1.0:
                            _vb_donor_lines.append(
                                f"  Transparency note: {unk_pct:.1f}% of contributions "
                                f"(${summary[4]:,.0f}, {summary[3]} records) list no employer "
                                f"or occupation — Virginia SBE does not verify or enforce "
                                f"disclosure of this field."
                            )
            if _vb_donor_lines:
                lines += ["", "### VB Council — Campaign Finance by Sector"] + _vb_donor_lines

        # ── Donor-sector ↔ vote-topic adjacency (facts only, no causal inference) ──
        _vb_adj_trigger = _vb_finance_trigger or any(
            w in q_lower for w in ("align", "adjacen", "sector", "pattern", "overlap",
                                   "topic", "interest", "conflict")
        )
        if _vb_adj_trigger and _table_exists(conn, "vb_donor_vote_summary"):
            _adj_targets = _named_vb_finance if _named_vb_finance else _ALL_VB_FINANCE
            _adj_lines: list[str] = []
            for db_key in _adj_targets[:4]:
                rows_adj = conn.execute("""
                    SELECT sector, sector_pct, top_topic, top_topic_delta,
                           top_topic_yes_pct, council_yes_pct, topic_vote_count
                    FROM vb_donor_vote_summary
                    WHERE member_name = ?
                    ORDER BY ABS(top_topic_delta) DESC
                    LIMIT 4
                """, (db_key,)).fetchall()
                if not rows_adj:
                    continue
                _adj_lines.append(f"\n#### {db_key} — Donor Sector / Vote Topic Adjacency")
                _adj_lines.append(
                    "Source: SBE contributions + VB council votes joined on topic. "
                    "Adjacency only — no causal inference drawn."
                )
                for r in rows_adj:
                    sector, sec_pct, topic, delta, m_yes, c_yes, n = r
                    sign = "+" if delta >= 0 else ""
                    _adj_lines.append(
                        f"  {sec_pct:4.0f}% from {sector:<15s} | "
                        f"votes YES on {topic:<20s} {m_yes:.0f}% "
                        f"(council avg {c_yes:.0f}%, delta {sign}{delta:.0f}pp, n={n})"
                    )
            if _adj_lines:
                lines += ["", "### VB Council — Donor Sector / Vote Topic Adjacency"] + _adj_lines

        # ── Per-member dissent profile (No-rate vs council avg by topic) ──
        _vb_dissent_trigger = (
            bool(_named_vb_finance)
            or any(w in q_lower for w in (
                "dissent", "pattern", "contrarian", "disagree", "against",
                "oppose", "no vote", "outlier", "independently", "bloc",
                "who votes against", "votes no",
            ))
        )
        if _vb_dissent_trigger and _table_exists(conn, "vb_member_dissent"):
            _dis_targets = _named_vb_finance if _named_vb_finance else _ALL_VB_FINANCE
            _dis_lines: list[str] = []
            for db_key in _dis_targets[:4]:
                # Resolve to full vote-table name via partial match
                full_name = conn.execute(
                    "SELECT DISTINCT member_name FROM vb_member_dissent "
                    "WHERE member_name LIKE ? LIMIT 1", (f"%{db_key.split('-')[0]}%",)
                ).fetchone()
                if not full_name:
                    continue
                vote_name = full_name[0]
                rows_dis = conn.execute("""
                    SELECT topic, member_no_pct, council_no_pct, delta_pp, member_no_count
                    FROM vb_member_dissent
                    WHERE member_name = ? AND ABS(delta_pp) >= 1 AND member_no_count >= 1
                    ORDER BY ABS(delta_pp) DESC LIMIT 5
                """, (vote_name,)).fetchall()
                if not rows_dis:
                    continue
                supports = [(t, m, c, d, n) for t, m, c, d, n in rows_dis if d < 0]
                opposes  = [(t, m, c, d, n) for t, m, c, d, n in rows_dis if d > 0]
                _dis_lines.append(f"\n#### {db_key} — Issue Positions (vs council average)")
                if supports:
                    _dis_lines.append("  Tends to SUPPORT (votes Yes more than peers):")
                    for topic, m_no, c_no, delta, nos in supports:
                        _dis_lines.append(
                            f"    {topic:<22s}  Yes rate above avg by {abs(delta):.1f}pp "
                            f"(No only {m_no:.0f}% vs council {c_no:.0f}%)"
                        )
                if opposes:
                    _dis_lines.append("  Tends to OPPOSE (votes No more than peers):")
                    for topic, m_no, c_no, delta, nos in opposes:
                        _dis_lines.append(
                            f"    {topic:<22s}  No rate above avg by {delta:.1f}pp "
                            f"({m_no:.0f}% No vs council {c_no:.0f}%, {nos} No votes)"
                        )
            if _dis_lines:
                lines += ["", "### VB Council — Issue Positions by Topic"] + _dis_lines

        # ── voting bloc / alignment ────────────────────────────────────────
        _bloc_trigger = any(w in q_lower for w in (
            "bloc", "align", "coalition", "faction", "together", "swing",
            "who votes with", "voting pattern", "pair", "outlier", "isolated",
            "agree", "disagree", "divide", "divided", "split",
        ))
        if _bloc_trigger and _table_exists(conn, "vb_voting_blocs"):
            # Detect named member for targeted lookup
            _bloc_member: str | None = None
            for _bk, _bpartial in {
                "berlucchi": "Berlucchi", "dyer": "Dyer", "henley": "Henley",
                "hutcheson": "Hutcheson", "wilson": "Wilson", "remick": "Remick",
                "ross-hammond": "Ross-Hammond", "schulman": "Schulman",
                "rouse": "Rouse", "cummings": "Cummings",
                "jackson-green": "Jackson-Green",
            }.items():
                if _bk in q_lower:
                    _brow = conn.execute(
                        "SELECT DISTINCT member_a FROM vb_voting_blocs WHERE member_a LIKE ?",
                        (f"%{_bpartial}%",)
                    ).fetchone()
                    if _brow:
                        _bloc_member = _brow[0]
                        break

            if _bloc_member:
                allies = conn.execute("""
                    SELECT member_b, agreement_pct, shared_votes
                    FROM vb_voting_blocs WHERE member_a = ?
                    ORDER BY agreement_pct DESC LIMIT 4
                """, (_bloc_member,)).fetchall()
                contrarians = conn.execute("""
                    SELECT member_b, agreement_pct, shared_votes
                    FROM vb_voting_blocs WHERE member_a = ?
                    ORDER BY agreement_pct ASC LIMIT 3
                """, (_bloc_member,)).fetchall()
                if allies:
                    lines.append(f"\n#### {_bloc_member} — Voting Alignment")
                    lines.append("  Closest allies:")
                    for b, pct, n in allies:
                        lines.append(f"    {b:<36s}  {pct:.1f}% agreement ({n} shared votes)")
                    if contrarians:
                        lines.append("  Most frequent disagreements:")
                        for b, pct, n in contrarians:
                            lines.append(f"    {b:<36s}  {pct:.1f}% agreement ({n} shared votes)")
            else:
                diverge = conn.execute("""
                    SELECT member_a, member_b, agreement_pct, shared_votes
                    FROM vb_voting_blocs WHERE member_a < member_b
                    ORDER BY agreement_pct ASC LIMIT 5
                """).fetchall()
                aligned = conn.execute("""
                    SELECT member_a, member_b, agreement_pct, shared_votes
                    FROM vb_voting_blocs WHERE member_a < member_b
                    ORDER BY agreement_pct DESC LIMIT 5
                """).fetchall()
                if diverge:
                    lines.append("\n#### VB Council — Voting Alignment (precomputed, all recorded votes)")
                    lines.append("  Most divergent pairs:")
                    for a, b, pct, n in diverge:
                        al = a.split()[-1].rstrip(",")
                        bl = b.split()[-1].rstrip(",")
                        lines.append(f"    {al} + {bl}: {pct:.1f}% agreement ({n} shared votes)")
                    if aligned:
                        lines.append("  Most aligned pairs:")
                        for a, b, pct, n in aligned:
                            al = a.split()[-1].rstrip(",")
                            bl = b.split()[-1].rstrip(",")
                            lines.append(f"    {al} + {bl}: {pct:.1f}% agreement ({n} shared votes)")

        # ── Split votes — specific non-unanimous examples ─────────────────────
        _split_trigger = (
            bool(_named_vb_finance)
            or any(w in q_lower for w in (
                "voted against", "voted no", "no vote", "voted down", "split",
                "contested", "controversial", "opposed", "specific vote",
                "which vote", "example", "minority",
            ))
        )
        if _split_trigger and _table_exists(conn, "vb_split_votes"):
            _split_lines: list[str] = []
            if _named_vb_finance:
                for _sv_key in _named_vb_finance[:2]:
                    _sv_last = _sv_key.split()[-1]
                    _sv_rows = conn.execute("""
                        SELECT meeting_date, topic, yes_count, no_count, no_voters, title, resolution_id
                        FROM vb_split_votes
                        WHERE no_voters LIKE ?
                        ORDER BY meeting_date DESC LIMIT 8
                    """, (f"%{_sv_last}%",)).fetchall()
                    if not _sv_rows:
                        continue
                    _sv_total = conn.execute(
                        "SELECT COUNT(*) FROM vb_split_votes WHERE no_voters LIKE ?",
                        (f"%{_sv_last}%",)
                    ).fetchone()[0]
                    _split_lines.append(
                        f"\n#### {_sv_key} — No votes on contested resolutions ({_sv_total} total)"
                    )
                    for _dt, _tp, _yes, _no, _voters, _title, _res_id in _sv_rows:
                        _t = f"[{_tp}]" if _tp else "[other]"
                        _url = _VB_LEGIFILE_URL.format(_res_id)
                        _split_lines.append(
                            f"  {_dt}  {_t:18s}  {_yes}Y/{_no}N  ({_voters})  [{_title[:72]}]({_url})"
                        )
            else:
                _split_lines.append("\n#### Most contested votes (highest No count):")
                for _dt, _tp, _yes, _no, _voters, _title, _res_id in conn.execute("""
                    SELECT meeting_date, topic, yes_count, no_count, no_voters, title, resolution_id
                    FROM vb_split_votes ORDER BY no_count DESC, meeting_date DESC LIMIT 8
                """).fetchall():
                    _t = f"[{_tp}]" if _tp else "[other]"
                    _url = _VB_LEGIFILE_URL.format(_res_id)
                    _split_lines.append(
                        f"  {_dt}  {_t:18s}  {_yes}Y/{_no}N  ({_voters})  [{_title[:72]}]({_url})"
                    )
                _split_lines.append("\n#### Most recent split votes:")
                for _dt, _tp, _yes, _no, _voters, _title, _res_id in conn.execute("""
                    SELECT meeting_date, topic, yes_count, no_count, no_voters, title, resolution_id
                    FROM vb_split_votes ORDER BY meeting_date DESC LIMIT 6
                """).fetchall():
                    _t = f"[{_tp}]" if _tp else "[other]"
                    _url = _VB_LEGIFILE_URL.format(_res_id)
                    _split_lines.append(
                        f"  {_dt}  {_t:18s}  {_yes}Y/{_no}N  ({_voters})  [{_title[:72]}]({_url})"
                    )
            if _split_lines:
                lines += ["", "### VB Council — Split Votes (Specific Examples)"] + _split_lines

        conn.close()
        if len(lines) > 1:
            blocks.append("\n".join(lines))

    except Exception:
        pass


# ── Scraped city-council contexts (Chesapeake, Portsmouth) ───────────────────
# Real per-member vote data scraped from each city's own records portal —
# neither city runs IQM2 like Norfolk/VB, so each has a dedicated scraper:
#   Chesapeake: scrape_chesapeake_council.py — Granicus ViewPublisher archive
#   Portsmouth: scrape_portsmouth_council.py — City Clerk Laserfiche WebLink
#     (scanned minutes read multimodally by Gemini; no text layer)
# Both write identically-shaped tables ({city}_council_members / _votes /
# _member_votes), so one config-driven builder serves both.
#
# Trigger design: requires the city name in the query (no bare-surname fuzzy
# matching like VB's) — members like Smith, Ward, King, Thomas, Bryant are
# common English words/first names that would false-positive without that
# context requirement.
#
# No chronological claims anywhere: Chesapeake meeting_date is free text with
# literal "unknown" values (~10% of rows); Portsmouth is MM/DD/YYYY, which
# text-sorts wrong across years. Labels avoid "recent"/"period" accordingly.

_CHESAPEAKE_TRIGGER_RE = re.compile(
    r"chesapeake.*(council|member|district|mayor|vote|voted|votes|voting|"
    r"agenda|meeting|ordinance|resolution|profile|background)"
    r"|(council|member|mayor|vote|voted|votes|agenda).*chesapeake",
    re.IGNORECASE,
)

_PORTSMOUTH_TRIGGER_RE = re.compile(
    r"portsmouth.*(council|member|district|mayor|vote|voted|votes|voting|"
    r"agenda|meeting|ordinance|resolution|profile|background)"
    r"|(council|member|mayor|vote|voted|votes|agenda).*portsmouth",
    re.IGNORECASE,
)

_NEWPORT_NEWS_TRIGGER_RE = re.compile(
    r"newport\s+news.*(council|member|district|mayor|vote|voted|votes|voting|"
    r"agenda|meeting|ordinance|resolution|profile|background)"
    r"|(council|member|mayor|vote|voted|votes|agenda).*newport\s+news",
    re.IGNORECASE,
)

_HAMPTON_TRIGGER_RE = re.compile(
    r"hampton.*(council|member|district|mayor|vote|voted|votes|voting|"
    r"agenda|meeting|ordinance|resolution|profile|background)"
    r"|(council|member|mayor|vote|voted|votes|agenda).*hampton",
    re.IGNORECASE,
)

_SUFFOLK_TRIGGER_RE = re.compile(
    r"suffolk.*(council|member|district|mayor|vote|voted|votes|voting|"
    r"agenda|meeting|ordinance|resolution|profile|background)"
    r"|(council|member|mayor|vote|voted|votes|agenda).*suffolk",
    re.IGNORECASE,
)

_COUNCIL_SCRAPE_CFGS: dict[str, dict] = {
    "chesapeake": {
        "display": "Chesapeake",
        "trigger": _CHESAPEAKE_TRIGGER_RE,
        # lowercase surname -> exact member_name in {prefix}_council_member_votes
        "member_map": {
            "west": "West", "ritter": "Ritter", "bunn": "Bunn",
            "jefferies": "Jefferies", "king": "King", "newins": "Newins",
            "smith": "Smith", "ward": "Ward", "whitaker": "Whitaker",
        },
        "source_note": (
            "Source: Chesapeake City Council meeting minutes, "
            "https://chesapeake.granicus.com/ViewPublisher.php?view_id=29"
        ),
    },
    "portsmouth": {
        "display": "Portsmouth",
        "trigger": _PORTSMOUTH_TRIGGER_RE,
        "member_map": {
            "glover": "Glover", "moody": "Moody", "hugel": "Hugel",
            "tillage": "Tillage", "bryant": "Bryant", "dodson": "Dodson",
            "thomas": "Thomas", "barnes": "Barnes", "lucas-burke": "Lucas-Burke",
            "lucas burke": "Lucas-Burke", "whitaker": "Whitaker",
        },
        "source_note": (
            "Source: Portsmouth City Council meeting minutes, City Clerk "
            "Laserfiche archive, https://www2.portsmouthva.gov/weblink7CCMinutes/"
        ),
    },
    "newport_news": {
        "display": "Newport News",
        "trigger": _NEWPORT_NEWS_TRIGGER_RE,
        # Unlike Chesapeake/Portsmouth, newport_news_council_member_votes
        # stores each member's FULL display name (scrape_newport_news_council.py
        # MEMBERS dict), not a bare surname — CivicWeb's Voting Records search
        # is member-scoped per full name, so that's what got captured.
        "member_map": {
            "long": "Cleon M. Long, P.E.", "bethany": "Curtis D. Bethany III",
            "jenkins": "David H. Jenkins", "woodbury": "Dr. Patricia P. Woodbury",
            "eley": "John R. Eley III", "harris": "Marcellus L. Harris III, D. Div.",
            "price": "McKinley L. Price, DDS", "jones": "Phillip Jones",
            "coleman": "Robert Coleman", "cherry": "Saundra N. Cherry, D. Min.",
            "scott": "Sharon P. Scott, MPA", "vick": "Tina L. Vick",
        },
        "source_note": (
            "Source: Newport News City Council Attendance & Voting Records, "
            "https://nngov.civicweb.net/Portal/VotingRecords.aspx"
        ),
    },
    "hampton": {
        "display": "Hampton",
        "trigger": _HAMPTON_TRIGGER_RE,
        # Bare surnames, matching Chesapeake/Portsmouth — Gemini extraction
        # was prompted to key votes by last name only (scrape_hampton_council.py).
        # Includes historical members (Tuck, Hobbs) alongside the current
        # roster (Gray, Brown, Bowman, Campbell, Ferebee, Harper, Mugler) —
        # council membership changed between the scraped 2023 data and today.
        "member_map": {
            "gray": "Gray", "brown": "Brown", "bowman": "Bowman",
            "campbell": "Campbell", "ferebee": "Ferebee", "harper": "Harper",
            "mugler": "Mugler", "tuck": "Tuck", "hobbs": "Hobbs",
        },
        "source_note": (
            "Source: Hampton City Council Notice of Action meeting minutes, "
            "https://hampton.legistar.com/Calendar.aspx"
        ),
    },
    "suffolk": {
        "display": "Suffolk",
        "trigger": _SUFFOLK_TRIGGER_RE,
        # Bare surnames as extracted by scrape_suffolk_council.py. Includes
        # historical members (Fawcett, Goldberg, Milteer) alongside the
        # current roster — the scraped range spans 2020-2026.
        "member_map": {
            "johnson": "Johnson", "bennett": "Bennett", "williams": "Williams",
            "fawcett": "Fawcett", "ward": "Ward", "duman": "Duman",
            "goldberg": "Goldberg", "wright": "Wright", "milteer": "Milteer",
            "butler barlow": "Butler Barlow", "butler-barlow": "Butler Barlow",
            "barlow": "Butler Barlow",
        },
        "source_note": (
            "Source: Suffolk City Council agenda documents, "
            "https://www.suffolkva.us/AgendaCenter. "
            "Note: Suffolk's documents only name individual members on split "
            "votes; unanimous outcomes are recorded at the tally level (e.g. "
            "'Approved 8-0') with no per-member attribution, so per-member "
            "counts here reflect only explicitly named votes (mostly dissents "
            "and abstentions), NOT a member's full voting record."
        ),
    },
}

_COUNCIL_KW_EXCL_BASE = {
    "council", "vote", "voted", "votes", "voting", "how", "did",
    "what", "the", "on", "at", "in", "of", "is", "are", "was", "city",
    "who", "list", "roster", "member", "members", "mayor", "does", "show",
}


def _council_vote_data_available(prefix: str) -> bool:
    """True when a scraped-council builder has data to serve.

    Deployment-state guard: these tables exist in the locally-scraped
    polls.db but ship to production via the version-gated seed — a production
    disk seeded before the scrape has none of them. In that state the roster
    fallback must keep listing council members.
    """
    try:
        conn = _connect("polls")
        if conn is None:
            return False
        try:
            table = f"{prefix}_council_members"
            if not _table_exists(conn, table):
                return False
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0
        finally:
            conn.close()
    except Exception:
        return False


# Kept under the old name — callers/tests reference it directly.
def _chesapeake_vote_data_available() -> bool:
    return _council_vote_data_available("chesapeake")


def _vote_date_spellings(iso_date: str) -> list[str]:
    """The five scraped cities' vote tables store meeting_date in whatever
    format their source used ('Jun 13, 2023' Chesapeake, '8/14/2024' Hampton,
    'Jan 14 2020' Newport News, '06/09/2026' Portsmouth, ISO Suffolk).
    Given an ISO date from an *_upcoming_agenda table, return every spelling
    to try when joining against a vote table."""
    try:
        d = datetime.strptime(iso_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return [iso_date]
    mon_abbr = d.strftime("%b")
    mon_full = d.strftime("%B")
    return list({
        iso_date,
        f"{mon_abbr} {d.day:02d}, {d.year}", f"{mon_abbr} {d.day}, {d.year}",
        f"{mon_full} {d.day:02d}, {d.year}", f"{mon_full} {d.day}, {d.year}",
        f"{mon_abbr} {d.day:02d} {d.year}", f"{mon_abbr} {d.day} {d.year}",
        f"{d.month:02d}/{d.day:02d}/{d.year}", f"{d.month}/{d.day}/{d.year}",
    })


def _add_scraped_council_context(
    blocks: list[str], query: str, terms: list[str], prefix: str,
    user_lat: float | None = None, user_lng: float | None = None,
) -> None:
    cfg = _COUNCIL_SCRAPE_CFGS[prefix]
    q_lower = (query or "").lower()
    if not cfg["trigger"].search(query or ""):
        return

    display = cfg["display"]
    t_members = f"{prefix}_council_members"
    t_votes = f"{prefix}_council_votes"
    t_member_votes = f"{prefix}_council_member_votes"

    try:
        conn = _connect("polls")
        if conn is None:
            return
        lines: list[str] = [f"## {display} City Council"]
        has_votes = _table_exists(conn, t_member_votes)

        # ── member roster ──────────────────────────────────────────────────
        member_trigger = any(w in q_lower for w in (
            "member", "council", "who", "roster", "list", "all", "mayor", "contact", "email",
        ))
        if member_trigger and _table_exists(conn, t_members):
            members = conn.execute(f"""
                SELECT name, district, email FROM {t_members}
                ORDER BY district_num
            """).fetchall()
            if members:
                lines.append(f"\n#### {display} City Council — Member Roster")
                for name, district, email in members:
                    e_str = f"  ({email})" if email else ""
                    lines.append(f"  {district:<28}  {name}{e_str}")

        # ── upcoming agenda ─────────────────────────────────────────────────
        t_agenda = f"{prefix}_upcoming_agenda"
        agenda_trigger = any(w in q_lower for w in (
            "upcoming", "next meeting", "next council", "agenda",
            "coming up", "scheduled", "how do i speak", "speak at",
            "public comment", "sign up", "participate",
        ))
        if agenda_trigger and _table_exists(conn, t_agenda):
            ag_rows = conn.execute(f"""
                SELECT meeting_date, item_ref, title, category, agenda_url
                FROM {t_agenda}
                WHERE meeting_date >= date('now')
                ORDER BY meeting_date, item_ref
                LIMIT 30
            """).fetchall()
            if ag_rows:
                lines.append(f"\n#### {display} City Council — Upcoming Agenda Items")
                cur_date, agenda_url = "", None
                for mdate, ref, title, category, url in ag_rows:
                    if mdate != cur_date:
                        cur_date = mdate
                        agenda_url = url
                        lines.append(f"\n  {mdate}")
                    short_title = title[:100] + ("…" if len(title) > 100 else "")
                    lines.append(f"    {ref:<6}  [{category}] {short_title}")
                if agenda_url:
                    lines.append(f"\n  [Full agenda PDF]({agenda_url})")
                try:
                    how_to = conn.execute(f"""
                        SELECT how_to_participate FROM {t_agenda}
                        WHERE meeting_date >= date('now') AND how_to_participate IS NOT NULL
                        ORDER BY meeting_date LIMIT 1
                    """).fetchone()
                    if how_to and how_to[0]:
                        lines.append(f"\n  How to participate: {how_to[0]}")
                except sqlite3.OperationalError:
                    pass
            else:
                lines.append(
                    f"\n  No upcoming agenda items posted yet for {display} — "
                    "agendas typically appear 1-2 weeks before each meeting."
                )

        # ── "what's proposed near me?" ──────────────────────────────────────
        # Only fires when the query both reads as a proximity question AND
        # either names a numbered street address in the query text, or the
        # caller passed the user's own on-file address (resolved once via
        # /api/find-district at page load, threaded through as user_lat/
        # user_lng) -- extract_address() refuses a bare street name (no
        # house number) since that geocodes to an arbitrary point along the
        # road, not the resident's actual parcel. An address typed in the
        # query always wins over the on-file location (covers "near my old
        # place at X" style overrides).
        _near_trigger = any(w in q_lower for w in (
            "near me", "near my", "close to me", "close to my", "next to my",
            "by my house", "by my home", "affect my neighborhood", "around my",
            "near ",
        ))
        user_addr = extract_address(query) if _near_trigger else None
        near_lat = near_lng = None
        near_label = None
        if user_addr:
            geocoded = geocode_lite(f"{user_addr}, {display}, VA")
            if geocoded and display.lower() in (geocoded["matched_address"] or "").lower():
                near_lat, near_lng, near_label = geocoded["lat"], geocoded["lng"], user_addr
            elif geocoded:
                lines.append(
                    f"\n  Couldn't confirm {user_addr!r} is in {display} "
                    f"(geocoded to {geocoded['matched_address']!r}) — skipping the nearby-items lookup."
                )
            else:
                lines.append(f"\n  Couldn't geocode {user_addr!r} — skipping the nearby-items lookup.")
        elif _near_trigger and user_lat is not None and user_lng is not None:
            near_lat, near_lng, near_label = user_lat, user_lng, "your saved address"

        if near_lat is not None and _table_exists(conn, t_agenda):
            nearby_rows = conn.execute(f"""
                SELECT meeting_date, item_ref, title, category, lat, lng
                FROM {t_agenda} WHERE lat IS NOT NULL
            """).fetchall()
            within: list[tuple[float, tuple]] = []
            for mdate, ref, title, category, ilat, ilng in nearby_rows:
                dist = haversine_miles(near_lat, near_lng, ilat, ilng)
                if dist <= 3.0:
                    within.append((dist, (mdate, ref, title, category)))
            within.sort(key=lambda x: x[0])

            lines.append(f"\n#### {display} City Council — Proposed Near {near_label}")
            if within:
                for dist, (mdate, ref, title, category) in within[:10]:
                    short_title = title[:100] + ("…" if len(title) > 100 else "")
                    lines.append(f"  {dist:.1f} mi  {mdate}  {ref:<6} [{category}]  {short_title}")
            else:
                lines.append(
                    "  Nothing with a geocoded address found within 3 miles in the "
                    "agenda items currently on record for this city."
                )

        # ── named-member vote summary ──────────────────────────────────────
        matched_member = next(
            (full for key, full in cfg["member_map"].items() if key in q_lower), None
        )
        _vote_section_added = False

        if matched_member and has_votes:
            stats = conn.execute(f"""
                SELECT COUNT(*), SUM(vote='yes'), SUM(vote='no'), SUM(vote='absent')
                FROM {t_member_votes} WHERE member_name=?
            """, (matched_member,)).fetchone()
            if stats and stats[0]:
                total_v, yes_v, no_v, absent = stats
                lines.append(f"\n#### {display} Council — {matched_member} Vote Summary")
                lines.append(f"  {total_v} recorded votes")
                lines.append(f"  Yes: {yes_v or 0}   No: {no_v or 0}   Absent: {absent or 0}")

                no_rows = conn.execute(f"""
                    SELECT meeting_date, title, result FROM {t_member_votes}
                    WHERE member_name=? AND vote='no' ORDER BY meeting_date DESC LIMIT 10
                """, (matched_member,)).fetchall()
                if no_rows:
                    lines.append(f"\n  No votes on record ({len(no_rows)} of {no_v or 0} shown):")
                    for mdate, title, result in no_rows:
                        lines.append(f"    {mdate}  [{result}]  {title[:90]}")
                _vote_section_added = True

        # ── recent meeting outcomes (agenda → recorded-vote link) ──────────
        # Agenda rows are retained 45 days past the meeting so "what happened
        # with that rezoning?" can be answered by joining them to the vote
        # tables. Exact item_ref match first (Suffolk/Hampton share ref
        # spaces with their vote tables), then fuzzy title match (Chesapeake/
        # NN/Portsmouth use different item labels across sources).
        outcome_trigger = any(w in q_lower for w in (
            "what happened", "happened", "outcome", "outcomes", "result",
            "results", "passed", "failed", "last meeting", "recent meeting",
            "previous meeting", "recap", "did the council", "did council",
            "approved", "denied",
        ))
        if outcome_trigger and has_votes and _table_exists(conn, t_agenda):
            o_date_row = conn.execute(f"""
                SELECT MAX(meeting_date) FROM {t_agenda}
                WHERE meeting_date < date('now')
            """).fetchone()
            o_date = o_date_row[0] if o_date_row else None
            if o_date:
                o_items = conn.execute(f"""
                    SELECT item_ref, title, category FROM {t_agenda}
                    WHERE meeting_date = ? AND category != 'procedural'
                    ORDER BY CAST(item_ref AS INTEGER), item_ref LIMIT 18
                """, (o_date,)).fetchall()
                spellings = _vote_date_spellings(o_date)
                ph = ",".join("?" for _ in spellings)
                v_rows = conn.execute(f"""
                    SELECT id, agenda_item, title, result, vote_count
                    FROM {t_votes} WHERE meeting_date IN ({ph})
                """, spellings).fetchall()
                if o_items:
                    lines.append(f"\n#### {display} Council — {o_date} Meeting: What Happened")
                    if not v_rows:
                        lines.append(
                            "  Recorded votes for this meeting are not in the database "
                            "yet (vote records typically lag the meeting). Agenda items were:"
                        )
                    used_vote_ids: set[int] = set()
                    for a_ref, a_title, a_cat in o_items:
                        match = None
                        for vid, v_item, v_title, v_result, v_count in v_rows:
                            if vid not in used_vote_ids and \
                                    (v_item or "").strip().lower() == (a_ref or "").strip().lower():
                                match = (vid, v_result, v_count)
                                break
                        if match is None and v_rows:
                            best_ratio, best_cand = 0.0, None
                            for vid, v_item, v_title, v_result, v_count in v_rows:
                                if vid in used_vote_ids:
                                    continue
                                r = difflib.SequenceMatcher(
                                    None, (a_title or "").lower()[:120],
                                    (v_title or "").lower()[:120],
                                ).ratio()
                                if r > best_ratio:
                                    best_ratio, best_cand = r, (vid, v_result, v_count)
                            if best_ratio >= 0.55:
                                match = best_cand
                        short_t = a_title[:90] + ("…" if len(a_title) > 90 else "")
                        if match:
                            vid, v_result, v_count = match
                            used_vote_ids.add(vid)
                            lines.append(f"  {a_ref:<8} [{v_count}, {v_result}]  {short_t}")
                            no_voters = conn.execute(f"""
                                SELECT member_name FROM {t_member_votes}
                                WHERE vote_id=? AND vote='no'
                            """, (vid,)).fetchall()
                            if no_voters:
                                lines.append(f"           Dissent: {', '.join(r[0] for r in no_voters)}")
                        else:
                            tag = "  (no recorded outcome found)" if v_rows else ""
                            lines.append(f"  {a_ref:<8} {short_t}{tag}")
                    _vote_section_added = True

        # ── topic search ────────────────────────────────────────────────────
        # Exclude every word of the display name ("newport news" -> both
        # "newport" and "news"), not just the underscored table prefix —
        # _keywords() tokenizes the query into separate words.
        _kw_excl = _COUNCIL_KW_EXCL_BASE | set(display.lower().split()) | {prefix}
        search_terms = [t for t in terms if t.lower() not in _kw_excl and len(t) > 3]

        if not matched_member and search_terms and has_votes:
            like = "%" + "%".join(search_terms[:3]) + "%"
            item_rows = conn.execute(f"""
                SELECT DISTINCT cv.meeting_date, cv.title, cv.vote_count, cv.result, cv.id
                FROM {t_votes} cv
                WHERE cv.title LIKE ? ORDER BY cv.meeting_date DESC LIMIT 5
            """, (like,)).fetchall()
            if item_rows:
                lines.append(f"\n#### {display} Council — Vote Search: {' '.join(search_terms[:3])!r}")
                for mdate, title, vcount, result, vid in item_rows:
                    lines.append(f"\n  {mdate}  [{vcount}, {result}]  {title[:100]}")
                    no_voters = conn.execute(f"""
                        SELECT member_name FROM {t_member_votes}
                        WHERE vote_id=? AND vote='no'
                    """, (vid,)).fetchall()
                    if no_voters:
                        lines.append(f"    Dissent: {', '.join(r[0] for r in no_voters)}")
                _vote_section_added = True

        # ── most contested votes (default when no member/topic matched) ────
        if has_votes and not _vote_section_added:
            contested = conn.execute(f"""
                SELECT cv.meeting_date, cv.title, cv.vote_count, cv.result,
                       SUM(mv.vote='no') AS no_count
                FROM {t_votes} cv
                JOIN {t_member_votes} mv ON mv.vote_id = cv.id
                GROUP BY cv.id
                HAVING no_count >= 2
                ORDER BY no_count DESC, cv.meeting_date DESC
                LIMIT 8
            """).fetchall()
            if contested:
                lines.append(f"\n#### {display} Council — Most Contested Votes (most No votes)")
                for mdate, title, vcount, result, no_count in contested:
                    lines.append(f"  {mdate}  [{vcount}, {result}]  {title[:90]}")

        conn.close()
        if len(lines) > 1:
            lines.append(f"\n{cfg['source_note']}")
            blocks.append("\n".join(lines))

    except Exception:
        pass


def _add_chesapeake_council_context(
    blocks: list[str], query: str, terms: list[str],
    user_lat: float | None = None, user_lng: float | None = None,
) -> None:
    _add_scraped_council_context(blocks, query, terms, "chesapeake", user_lat, user_lng)


def _add_portsmouth_council_context(
    blocks: list[str], query: str, terms: list[str],
    user_lat: float | None = None, user_lng: float | None = None,
) -> None:
    _add_scraped_council_context(blocks, query, terms, "portsmouth", user_lat, user_lng)


def _add_newport_news_council_context(
    blocks: list[str], query: str, terms: list[str],
    user_lat: float | None = None, user_lng: float | None = None,
) -> None:
    _add_scraped_council_context(blocks, query, terms, "newport_news", user_lat, user_lng)


def _add_hampton_council_context(
    blocks: list[str], query: str, terms: list[str],
    user_lat: float | None = None, user_lng: float | None = None,
) -> None:
    _add_scraped_council_context(blocks, query, terms, "hampton", user_lat, user_lng)


def _add_suffolk_council_context(
    blocks: list[str], query: str, terms: list[str],
    user_lat: float | None = None, user_lng: float | None = None,
) -> None:
    _add_scraped_council_context(blocks, query, terms, "suffolk", user_lat, user_lng)


_NN_PC_TRIGGER_RE = re.compile(
    r"newport\s+news.*(planning\s+commission|rezon|cup\b|conditional\s+use|zoning)"
    r"|(planning\s+commission|rezon|cup\b|conditional\s+use).*newport\s+news",
    re.IGNORECASE,
)


def _add_newport_news_pc_context(
    blocks: list[str], query: str, terms: list[str],
    user_lat: float | None = None, user_lng: float | None = None,
) -> None:
    """Newport News Planning Commission agenda -- rezoning/CUP cases are
    decided here in substance, typically a month before City Council's own
    vote on the Commission's recommendation. Not part of the generic
    _COUNCIL_SCRAPE_CFGS system since PC has no member roster or vote
    history, just upcoming agenda items."""
    if not _NN_PC_TRIGGER_RE.search(query or ""):
        return

    q_lower = (query or "").lower()
    t_agenda = "newport_news_pc_upcoming_agenda"

    try:
        conn = _connect("polls")
        if conn is None or not _table_exists(conn, t_agenda):
            return
        lines: list[str] = ["## Newport News Planning Commission"]

        agenda_trigger = any(w in q_lower for w in (
            "upcoming", "next meeting", "agenda", "coming up", "scheduled",
            "rezon", "cup", "conditional use", "hearing",
        ))
        if agenda_trigger:
            ag_rows = conn.execute(f"""
                SELECT meeting_date, item_ref, title, category, agenda_url, council_hearing_date
                FROM {t_agenda}
                WHERE meeting_date >= date('now') AND category != 'procedural'
                ORDER BY meeting_date, item_ref
                LIMIT 30
            """).fetchall()
            if ag_rows:
                lines.append("\n#### Newport News Planning Commission — Upcoming Agenda Items")
                cur_date, agenda_url = "", None
                for mdate, ref, title, category, url, hearing_date in ag_rows:
                    if mdate != cur_date:
                        cur_date = mdate
                        agenda_url = url
                        lines.append(f"\n  {mdate}")
                    short_title = title[:100] + ("…" if len(title) > 100 else "")
                    tag = f"  [to Council: {hearing_date}]" if hearing_date else ""
                    lines.append(f"    {ref:<14} [{category}] {short_title}{tag}")
                if agenda_url:
                    lines.append(f"\n  [Full agenda PDF]({agenda_url})")
                lines.append(
                    "\n  Note: Planning Commission makes a recommendation; City Council casts "
                    "the final vote, typically about a month later, on the date shown above "
                    "when stated."
                )
            else:
                lines.append(
                    "\n  No upcoming Planning Commission agenda items posted yet — "
                    "agendas typically appear 1-2 weeks before each meeting."
                )

        # ── "what's proposed near me?" (same pattern as the council builders) ─
        near_trigger = any(w in q_lower for w in (
            "near me", "near my", "close to me", "close to my", "next to my",
            "by my house", "by my home", "affect my neighborhood", "around my",
            "near ",
        ))
        user_addr = extract_address(query) if near_trigger else None
        near_lat = near_lng = None
        near_label = None
        if user_addr:
            geocoded = geocode_lite(f"{user_addr}, Newport News, VA")
            if geocoded and "newport news" in (geocoded["matched_address"] or "").lower():
                near_lat, near_lng, near_label = geocoded["lat"], geocoded["lng"], user_addr
            elif geocoded:
                lines.append(
                    f"\n  Couldn't confirm {user_addr!r} is in Newport News "
                    f"(geocoded to {geocoded['matched_address']!r}) — skipping the nearby-items lookup."
                )
            else:
                lines.append(f"\n  Couldn't geocode {user_addr!r} — skipping the nearby-items lookup.")
        elif near_trigger and user_lat is not None and user_lng is not None:
            near_lat, near_lng, near_label = user_lat, user_lng, "your saved address"

        if near_lat is not None:
            nearby_rows = conn.execute(f"""
                SELECT meeting_date, item_ref, title, category, council_hearing_date, lat, lng
                FROM {t_agenda} WHERE lat IS NOT NULL
            """).fetchall()
            within: list[tuple[float, tuple]] = []
            for mdate, ref, title, category, hearing_date, ilat, ilng in nearby_rows:
                dist = haversine_miles(near_lat, near_lng, ilat, ilng)
                if dist <= 3.0:
                    within.append((dist, (mdate, ref, title, category, hearing_date)))
            within.sort(key=lambda x: x[0])

            lines.append(f"\n#### Newport News Planning Commission — Proposed Near {near_label}")
            if within:
                for dist, (mdate, ref, title, category, hearing_date) in within[:10]:
                    short_title = title[:100] + ("…" if len(title) > 100 else "")
                    tag = f"  [to Council: {hearing_date}]" if hearing_date else ""
                    lines.append(f"  {dist:.1f} mi  {mdate}  {ref:<14} [{category}]  {short_title}{tag}")
            else:
                lines.append(
                    "  Nothing with a geocoded address found within 3 miles in the "
                    "Planning Commission items currently on record."
                )

        conn.close()
        if len(lines) > 1:
            lines.append(
                "\nSource: Newport News Planning Commission agenda documents, "
                "https://nngov.civicweb.net/Portal/MeetingTypeList.aspx"
            )
            blocks.append("\n".join(lines))

    except Exception:
        pass


# ── Chesapeake / Suffolk / Hampton / Portsmouth Planning Commission ────────
# Shared builder (unlike Council, which has _COUNCIL_SCRAPE_CFGS for member
# rosters/vote history too) since PC has no member roster or vote history --
# just upcoming agenda items, same shape as Newport News PC above. Only
# Portsmouth's table has a council_hearing_date column (its scraper
# extracts that cross-reference; the other 3 don't since it wasn't observed
# in their real agenda documents) -- handled with a column-existence check
# rather than assuming it's there.

_PC_TRIGGER_RES = {
    "chesapeake": re.compile(
        r"chesapeake.*(planning\s+commission|rezon|cup\b|conditional\s+use|zoning)"
        r"|(planning\s+commission|rezon|cup\b|conditional\s+use).*chesapeake",
        re.IGNORECASE,
    ),
    "suffolk": re.compile(
        r"suffolk.*(planning\s+commission|rezon|cup\b|conditional\s+use|zoning)"
        r"|(planning\s+commission|rezon|cup\b|conditional\s+use).*suffolk",
        re.IGNORECASE,
    ),
    "hampton": re.compile(
        r"hampton.*(planning\s+commission|rezon|use\s+permit|zoning)"
        r"|(planning\s+commission|rezon|use\s+permit).*hampton",
        re.IGNORECASE,
    ),
    "portsmouth": re.compile(
        r"portsmouth.*(planning\s+commission|rezon|use\s+permit|zoning)"
        r"|(planning\s+commission|rezon|use\s+permit).*portsmouth",
        re.IGNORECASE,
    ),
}
_PC_DISPLAY = {
    "chesapeake": "Chesapeake", "suffolk": "Suffolk",
    "hampton": "Hampton", "portsmouth": "Portsmouth",
}
_PC_SOURCE = {
    "chesapeake": "Chesapeake Planning Commission agenda documents, https://chesapeake.granicus.com/ViewPublisher.php?view_id=35",
    "suffolk": "Suffolk Planning Commission agenda documents, https://www.suffolkva.us/AgendaCenter",
    "hampton": "Hampton Planning Commission agenda documents, https://hampton.legistar.com/Calendar.aspx",
    "portsmouth": "Portsmouth Planning Commission agenda documents, https://www.portsmouthva.gov/planning-commission-agendas-minutes",
}


def _add_pc_agenda_context(
    blocks: list[str], query: str, terms: list[str], prefix: str,
    user_lat: float | None = None, user_lng: float | None = None,
) -> None:
    if not _PC_TRIGGER_RES[prefix].search(query or ""):
        return

    display = _PC_DISPLAY[prefix]
    q_lower = (query or "").lower()
    t_agenda = f"{prefix}_pc_upcoming_agenda"

    try:
        conn = _connect("polls")
        if conn is None or not _table_exists(conn, t_agenda):
            return
        lines: list[str] = [f"## {display} Planning Commission"]

        has_hearing_col = "council_hearing_date" in {
            row[1] for row in conn.execute(f"PRAGMA table_info({t_agenda})")
        }
        hearing_col_sql = "council_hearing_date" if has_hearing_col else "NULL AS council_hearing_date"

        agenda_trigger = any(w in q_lower for w in (
            "upcoming", "next meeting", "agenda", "coming up", "scheduled",
            "rezon", "cup", "conditional use", "use permit", "hearing",
        ))
        if agenda_trigger:
            ag_rows = conn.execute(f"""
                SELECT meeting_date, item_ref, title, category, agenda_url, {hearing_col_sql}
                FROM {t_agenda}
                WHERE meeting_date >= date('now') AND category != 'procedural'
                ORDER BY meeting_date, item_ref
                LIMIT 30
            """).fetchall()
            if ag_rows:
                lines.append(f"\n#### {display} Planning Commission — Upcoming Agenda Items")
                cur_date, agenda_url = "", None
                for mdate, ref, title, category, url, hearing_date in ag_rows:
                    if mdate != cur_date:
                        cur_date = mdate
                        agenda_url = url
                        lines.append(f"\n  {mdate}")
                    short_title = title[:100] + ("…" if len(title) > 100 else "")
                    tag = f"  [to Council: {hearing_date}]" if hearing_date else ""
                    lines.append(f"    {ref:<16} [{category}] {short_title}{tag}")
                if agenda_url:
                    lines.append(f"\n  [Full agenda PDF]({agenda_url})")
                lines.append(
                    "\n  Note: Planning Commission makes a recommendation; City Council casts "
                    "the final vote, typically weeks to a month later."
                )
            else:
                lines.append(
                    f"\n  No upcoming Planning Commission agenda items posted yet for {display} — "
                    "agendas typically appear a short time before each meeting."
                )

        near_trigger = any(w in q_lower for w in (
            "near me", "near my", "close to me", "close to my", "next to my",
            "by my house", "by my home", "affect my neighborhood", "around my",
            "near ",
        ))
        user_addr = extract_address(query) if near_trigger else None
        near_lat = near_lng = None
        near_label = None
        if user_addr:
            geocoded = geocode_lite(f"{user_addr}, {display}, VA")
            if geocoded and display.lower() in (geocoded["matched_address"] or "").lower():
                near_lat, near_lng, near_label = geocoded["lat"], geocoded["lng"], user_addr
            elif geocoded:
                lines.append(
                    f"\n  Couldn't confirm {user_addr!r} is in {display} "
                    f"(geocoded to {geocoded['matched_address']!r}) — skipping the nearby-items lookup."
                )
            else:
                lines.append(f"\n  Couldn't geocode {user_addr!r} — skipping the nearby-items lookup.")
        elif near_trigger and user_lat is not None and user_lng is not None:
            near_lat, near_lng, near_label = user_lat, user_lng, "your saved address"

        if near_lat is not None:
            nearby_rows = conn.execute(f"""
                SELECT meeting_date, item_ref, title, category, {hearing_col_sql}, lat, lng
                FROM {t_agenda} WHERE lat IS NOT NULL
            """).fetchall()
            within: list[tuple[float, tuple]] = []
            for mdate, ref, title, category, hearing_date, ilat, ilng in nearby_rows:
                dist = haversine_miles(near_lat, near_lng, ilat, ilng)
                if dist <= 3.0:
                    within.append((dist, (mdate, ref, title, category, hearing_date)))
            within.sort(key=lambda x: x[0])

            lines.append(f"\n#### {display} Planning Commission — Proposed Near {near_label}")
            if within:
                for dist, (mdate, ref, title, category, hearing_date) in within[:10]:
                    short_title = title[:100] + ("…" if len(title) > 100 else "")
                    tag = f"  [to Council: {hearing_date}]" if hearing_date else ""
                    lines.append(f"  {dist:.1f} mi  {mdate}  {ref:<16} [{category}]  {short_title}{tag}")
            else:
                lines.append(
                    "  Nothing with a geocoded address found within 3 miles in the "
                    "Planning Commission items currently on record."
                )

        conn.close()
        if len(lines) > 1:
            lines.append(f"\nSource: {_PC_SOURCE[prefix]}")
            blocks.append("\n".join(lines))

    except Exception:
        pass


def _add_chesapeake_pc_context(
    blocks: list[str], query: str, terms: list[str],
    user_lat: float | None = None, user_lng: float | None = None,
) -> None:
    _add_pc_agenda_context(blocks, query, terms, "chesapeake", user_lat, user_lng)


def _add_suffolk_pc_context(
    blocks: list[str], query: str, terms: list[str],
    user_lat: float | None = None, user_lng: float | None = None,
) -> None:
    _add_pc_agenda_context(blocks, query, terms, "suffolk", user_lat, user_lng)


def _add_hampton_pc_context(
    blocks: list[str], query: str, terms: list[str],
    user_lat: float | None = None, user_lng: float | None = None,
) -> None:
    _add_pc_agenda_context(blocks, query, terms, "hampton", user_lat, user_lng)


def _add_portsmouth_pc_context(
    blocks: list[str], query: str, terms: list[str],
    user_lat: float | None = None, user_lng: float | None = None,
) -> None:
    _add_pc_agenda_context(blocks, query, terms, "portsmouth", user_lat, user_lng)
