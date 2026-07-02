"""
build_legislator_narratives.py

Pre-generates plain-English civic profiles for every VA legislator,
combining campaign finance, donor-vote alignment, bill sponsorship,
and lobbyist cross-reference data into a readable narrative paragraph.

Stores results in polls.db: legislator_narratives

Usage:
    python build_legislator_narratives.py
    python build_legislator_narratives.py --source va_sbe    # state only
    python build_legislator_narratives.py --source fec       # federal only

Re-run any time the underlying data changes (after new SBE ingest,
after build_donor_vote_alignment.py, after build_sponsor_finance_analysis.py).
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
POLLS_DB = BASE_DIR / "polls.db"

DISCLAIMER = (
    "All figures are from public records. "
    "Correlation does not imply causation or intent."
)

# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS legislator_narratives (
    legislator_id    TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    chamber          TEXT,
    district         TEXT,
    party            TEXT,
    source           TEXT,          -- 'va_sbe' or 'fec'
    narrative        TEXT,          -- full multi-paragraph profile
    narrative_short  TEXT,          -- 1-2 sentence summary for tooltips
    key_stats_json   TEXT,          -- JSON of the key numbers used
    updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_ln_name    ON legislator_narratives(name);
CREATE INDEX IF NOT EXISTS idx_ln_chamber ON legislator_narratives(chamber);
"""


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


# ── Money formatter ───────────────────────────────────────────────────────────

def _fmt(n: float | None, *, short: bool = False) -> str:
    if n is None or n == 0:
        return "$0"
    if abs(n) >= 1_000_000:
        return f"${n/1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"${n/1_000:.0f}K" if short else f"${n:,.0f}"
    return f"${n:.0f}"


# ── Alignment language ────────────────────────────────────────────────────────

def _alignment_sentence(
    name: str, sector: str,
    sector_yes: float, other_yes: float, delta: float,
    vote_count: int,
) -> str:
    """Plain-English sentence about vote alignment."""
    direction = "more" if delta >= 0 else "less"
    abs_delta = abs(delta)

    if abs_delta < 3:
        return (
            f"{name} voted YES on {sector} bills {sector_yes:.1f}% of the time "
            f"— essentially the same as their overall average of {other_yes:.1f}%, "
            f"showing no clear voting pattern linked to their donor sector "
            f"(based on {vote_count} contested votes)."
        )
    elif delta >= 15:
        return (
            f"{name} voted YES on {sector} bills {sector_yes:.1f}% of the time, "
            f"which is {abs_delta:.1f} percentage points higher than their "
            f"overall average of {other_yes:.1f}% — a notably strong alignment "
            f"with their top funding sector (based on {vote_count} contested votes)."
        )
    elif delta >= 5:
        return (
            f"{name} voted YES on {sector} bills {sector_yes:.1f}% of the time, "
            f"{abs_delta:.1f} percentage points above their overall average of "
            f"{other_yes:.1f}% (based on {vote_count} contested votes)."
        )
    elif delta <= -15:
        return (
            f"Despite {sector} being their top funding sector, {name} voted YES "
            f"on {sector} bills {sector_yes:.1f}% of the time — {abs_delta:.1f} "
            f"percentage points BELOW their overall average of {other_yes:.1f}%. "
            f"This contrary pattern (based on {vote_count} contested votes) "
            f"suggests the donor relationship did not translate to consistent "
            f"floor support."
        )
    else:
        return (
            f"{name} voted YES on {sector} bills {sector_yes:.1f}% of the time, "
            f"{abs_delta:.1f}pp {direction} than their overall average of "
            f"{other_yes:.1f}% (based on {vote_count} contested votes)."
        )


# ── Narrative builder ─────────────────────────────────────────────────────────

def _build_narrative(
    leg_id: str, name: str, chamber: str, district: str, party: str,
    source: str, cfs: sqlite3.Row, conn: sqlite3.Connection,
) -> tuple[str, str, dict]:
    """
    Returns (narrative, narrative_short, key_stats_dict).
    """
    party_label = {"R": "Republican", "D": "Democrat",
                   "I": "Independent"}.get(party or "", party or "")
    chamber_label = chamber or "state legislator"
    district_label = f"District {district}" if district else ""
    office = f"{chamber_label} {district_label}".strip()

    # ── Finance data ──────────────────────────────────────────────────────────
    total_raised = cfs["total_raised"] or 0
    top_sector   = cfs["top_sector"] or "Unknown"
    top_pct      = cfs["top_sector_pct"] or 0
    latest_cycle = cfs["latest_cycle"] or ""

    try:
        sectors = json.loads(cfs["by_sector_json"] or "[]")
    except Exception:
        sectors = []
    try:
        top_donors = json.loads(cfs["top_donors_json"] or "[]")
    except Exception:
        top_donors = []
    try:
        cycles = json.loads(cfs["cycles_json"] or "[]")
    except Exception:
        cycles = []

    # ── Vote alignment ────────────────────────────────────────────────────────
    dva = conn.execute("""
        SELECT sector_yes_rate, other_yes_rate, alignment_delta,
               sector_vote_count, other_vote_count, top_donor_sector
        FROM donor_vote_alignment
        WHERE legislator_id = ?
    """, (leg_id,)).fetchone()

    # ── Sponsorship ───────────────────────────────────────────────────────────
    spon_rows = conn.execute("""
        SELECT sector, bills_sponsored, total_from_sector_donors, alignment_label
        FROM sponsor_donor_correlation
        WHERE legislator_name = ? AND alignment_label IN ('strong','moderate','weak')
        ORDER BY bills_sponsored DESC
    """, (name,)).fetchall()

    # ── Lobbyist cross-reference ──────────────────────────────────────────────
    lob_data: list[dict] = []
    if top_donors:
        for d in top_donors[:3]:
            donor_name = d.get("contributor_name") or d.get("employer") or ""
            if not donor_name:
                continue
            brand = re.sub(r"\b(llc|inc|pac|corp|co|ltd|political action committee"
                           r"|political committee|fund|association)\b\.?",
                           "", donor_name.lower()).strip()
            brand_words = [w for w in brand.split() if len(w) > 3][:2]
            if not brand_words:
                continue
            brand_key = " ".join(brand_words)
            n_lob = conn.execute("""
                SELECT COUNT(DISTINCT lobbyist_name)
                FROM lobbyist_registrations
                WHERE lower(principal_name) LIKE ?
            """, (f"%{brand_key}%",)).fetchone()[0]
            if n_lob > 0:
                lob_data.append({
                    "donor": donor_name,
                    "lobbyists": n_lob,
                    "total": d.get("total", 0),
                })

    # ── Build paragraphs ──────────────────────────────────────────────────────
    paras: list[str] = []

    # P1: Identity + funding profile
    cycle_range = ""
    if cycles:
        cycle_range = f" across {len(cycles)} election cycles ({min(cycles)}-{max(cycles)})"
    p1 = (
        f"{name} is a {party_label} {office}. "
        f"According to Virginia SBE campaign finance records, they raised "
        f"{_fmt(total_raised)}{cycle_range}. "
    )
    if top_sector and top_pct:
        p1 += (
            f"Their largest funding sector was {top_sector}, accounting for "
            f"{top_pct:.0f}% of total contributions."
        )
    if len(sectors) >= 2:
        s2 = sectors[1]
        s2_pct = round(s2.get("total", 0) / total_raised * 100, 0) if total_raised else 0
        p1 += (
            f" The second-largest sector was {s2.get('sector','?')} "
            f"at {s2_pct:.0f}%."
        )
    paras.append(p1)

    # P2: Top donors
    if top_donors:
        donor_strs = []
        for d in top_donors[:4]:
            dname = d.get("contributor_name") or d.get("employer") or ""
            damt  = d.get("total", 0)
            dsec  = d.get("sector") or ""
            if dname and damt:
                tag = f" ({dsec})" if dsec else ""
                donor_strs.append(f"{dname}{tag} — {_fmt(damt)}")
        if donor_strs:
            paras.append(
                f"Top recorded donors to {name}: "
                + "; ".join(donor_strs) + "."
            )

    # P3: Vote alignment
    if dva and dva["alignment_delta"] is not None:
        dva_sector  = dva["top_donor_sector"] or top_sector
        sec_yes     = dva["sector_yes_rate"]
        oth_yes     = dva["other_yes_rate"]
        delta       = dva["alignment_delta"]
        vote_count  = dva["sector_vote_count"]
        paras.append(
            _alignment_sentence(name, dva_sector, sec_yes, oth_yes, delta, vote_count)
        )

    # P4: Sponsorship pattern
    if spon_rows:
        spon_parts = []
        for row in spon_rows[:3]:
            sec   = row["sector"]
            n_b   = row["bills_sponsored"]
            funds = row["total_from_sector_donors"]
            label = row["alignment_label"]
            strength = {"strong": "strongly aligned", "moderate": "moderately aligned",
                        "weak": "weakly aligned"}.get(label, "")
            amt_str = f", receiving {_fmt(funds)} from {sec} donors in those cycles" \
                      if funds > 0 else ""
            spon_parts.append(
                f"{n_b} {sec} bill{'s' if n_b != 1 else ''} ({strength}{amt_str})"
            )
        paras.append(
            f"Sponsorship pattern: {name} has introduced " +
            "; ".join(spon_parts) + "."
        )

    # P5: Lobbyist connections
    if lob_data:
        lob_parts = [
            f"{d['donor']} ({d['lobbyists']} registered VA lobbyist{'s' if d['lobbyists'] != 1 else ''})"
            for d in lob_data
        ]
        paras.append(
            f"Donors with registered Virginia lobbyists: " +
            "; ".join(lob_parts) + ". "
            f"Registered lobbyists and campaign contributions represent separate "
            f"but often overlapping channels of political engagement."
        )

    # P6: Disclaimer
    paras.append(DISCLAIMER)

    narrative = "\n\n".join(paras)

    # Short version: first 2 sentences of P1 + alignment if present
    short_parts = [p1.strip()]
    if dva and dva["alignment_delta"] is not None and abs(dva["alignment_delta"]) >= 5:
        dva_sector = dva["top_donor_sector"] or top_sector
        delta = dva["alignment_delta"]
        dir_s = "above" if delta > 0 else "below"
        short_parts.append(
            f"Their YES-vote rate on {dva_sector} bills ({dva['sector_yes_rate']:.1f}%) "
            f"is {abs(delta):.1f}pp {dir_s} their overall average."
        )
    narrative_short = " ".join(short_parts)

    key_stats = {
        "total_raised": total_raised,
        "top_sector": top_sector,
        "top_sector_pct": top_pct,
        "latest_cycle": latest_cycle,
        "sector_yes_rate": dva["sector_yes_rate"] if dva else None,
        "alignment_delta": dva["alignment_delta"] if dva else None,
        "n_top_donors": len(top_donors),
        "n_lobbyist_donors": len(lob_data),
        "n_sponsored_sectors": len(spon_rows),
    }

    return narrative, narrative_short, key_stats


# ── Main ──────────────────────────────────────────────────────────────────────

def main(source_filter: str | None = None) -> None:
    conn = sqlite3.connect(POLLS_DB, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_tables(conn)

    source_clause = ""
    source_params: list = []
    if source_filter:
        source_clause = "AND source = ?"
        source_params.append(source_filter)

    legislators = conn.execute(f"""
        SELECT legislator_id, name, chamber, district, party, source,
               total_raised, top_sector, top_sector_pct, latest_cycle,
               by_sector_json, top_donors_json, cycles_json
        FROM campaign_finance_summary
        WHERE by_sector_json IS NOT NULL
          {source_clause}
        ORDER BY total_raised DESC
    """, source_params).fetchall()

    print(f"Building narratives for {len(legislators)} legislators…")
    now = datetime.now(timezone.utc).isoformat()
    written = skipped = 0

    for leg in legislators:
        leg_id  = leg["legislator_id"]
        name    = leg["name"] or ""
        chamber = leg["chamber"] or ""
        district = str(leg["district"] or "")
        party   = leg["party"] or ""
        source  = leg["source"] or ""

        if not name:
            skipped += 1
            continue

        try:
            narrative, short, stats = _build_narrative(
                leg_id, name, chamber, district, party, source, leg, conn
            )
        except Exception as e:
            print(f"  WARN {name}: {e}")
            skipped += 1
            continue

        conn.execute("""
            INSERT INTO legislator_narratives
                (legislator_id, name, chamber, district, party, source,
                 narrative, narrative_short, key_stats_json, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(legislator_id) DO UPDATE SET
                name             = excluded.name,
                chamber          = excluded.chamber,
                district         = excluded.district,
                party            = excluded.party,
                source           = excluded.source,
                narrative        = excluded.narrative,
                narrative_short  = excluded.narrative_short,
                key_stats_json   = excluded.key_stats_json,
                updated_at       = excluded.updated_at
        """, (
            leg_id, name, chamber, district, party, source,
            narrative, short, json.dumps(stats), now,
        ))
        written += 1
        if written % 20 == 0:
            conn.commit()
            print(f"  {written}/{len(legislators)} done…")

    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM legislator_narratives").fetchone()[0]
    print(f"\nDone. {written} written, {skipped} skipped. "
          f"{total} total rows in legislator_narratives.")

    # Preview sample
    print("\nSample narrative:")
    sample = conn.execute("""
        SELECT name, narrative_short FROM legislator_narratives
        WHERE alignment_delta IS NOT NULL OR 1=1
        ORDER BY ABS(COALESCE(
            (SELECT alignment_delta FROM donor_vote_alignment WHERE legislator_id=legislator_narratives.legislator_id),
            0
        )) DESC LIMIT 1
    """).fetchone()
    if not sample:
        sample = conn.execute(
            "SELECT name, narrative_short FROM legislator_narratives LIMIT 1"
        ).fetchone()
    if sample:
        print(f"\n  {sample['name']}:")
        print(f"  {sample['narrative_short']}")

    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--source", choices=["va_sbe", "fec"], default=None,
                   help="Filter to state or federal legislators only")
    args = p.parse_args()
    main(args.source)
