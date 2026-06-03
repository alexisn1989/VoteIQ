"""
build_donor_bills_rag.py

Generates JSONL chunks for ChromaDB ingestion covering Virginia donor-bill
correlation data. Output: donor_bills_rag.jsonl

Run:
    python build_donor_bills_rag.py
    python ingest.py --file donor_bills_rag.jsonl

Chunk types:
  donor_trend        — per-donor cycle history + spike narrative (top 300 donors)
  legislator_finance — per-legislator campaign finance sector profile (168)
  donor_vote_align   — per-legislator donor-vote alignment score (139)
  cycle_narrative    — per-year investment story (2013-2026)
  lobbyist_crossref  — per-principal lobbyist × donation summary (top 100)
  sector_summary     — per-sector donor landscape
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
POLLS_DB = BASE_DIR / "polls.db"
OUT_FILE = BASE_DIR / "donor_bills_rag.jsonl"

DISCLAIMER = (
    "Public-record data from Virginia SBE campaign finance filings. "
    "Correlation does not imply causation. No motive or intent is inferred."
)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(POLLS_DB)
    c.row_factory = sqlite3.Row
    return c


def _fmt(n: float | None) -> str:
    if n is None:
        return "$0"
    if abs(n) >= 1_000_000:
        return f"${n/1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"${n/1_000:.0f}K"
    return f"${n:.0f}"


# ── 1. Per-donor trend narratives ─────────────────────────────────────────────

def build_donor_trend_chunks(conn: sqlite3.Connection) -> list[dict]:
    """One narrative chunk per significant donor (top 300 by total giving)."""
    donors = conn.execute("""
        SELECT donor_key, canonical_name, variant_names,
               first_cycle, last_cycle, total_all_cycles, active_cycles
        FROM donor_entity_map
        WHERE total_all_cycles >= 100000
        ORDER BY total_all_cycles DESC
        LIMIT 300
    """).fetchall()

    chunks = []
    for d in donors:
        key   = d["donor_key"]
        name  = d["canonical_name"]
        total = d["total_all_cycles"]

        cycles = conn.execute("""
            SELECT election_cycle, cycle_parity, total_amount,
                   pct_change_prior, ratio_vs_mean, is_spike
            FROM donor_cycle_trends
            WHERE donor_key = ?
            ORDER BY election_cycle
        """, (key,)).fetchall()

        if not cycles:
            continue

        odd_amts  = [c["total_amount"] for c in cycles if c["cycle_parity"] == "odd"]
        even_amts = [c["total_amount"] for c in cycles if c["cycle_parity"] == "even"]
        odd_mean  = sum(odd_amts)  / len(odd_amts)  if odd_amts  else 0
        even_mean = sum(even_amts) / len(even_amts) if even_amts else 0

        spike_yrs = [c["election_cycle"] for c in cycles if c["is_spike"]]
        ctx_map: dict[str, list[str]] = {}
        if spike_yrs:
            ph = ",".join("?" for _ in spike_yrs)
            for row in conn.execute(
                f"SELECT election_cycle, title, donor_sector FROM cycle_context "
                f"WHERE election_cycle IN ({ph}) AND significance='high' "
                f"ORDER BY election_cycle, donor_sector",
                spike_yrs,
            ):
                ctx_map.setdefault(row["election_cycle"], []).append(
                    f"{row['title'][:65]}"
                )

        # Variant names
        try:
            variants = json.loads(d["variant_names"] or "[]")
        except Exception:
            variants = []
        also_known = (
            f" (also known as: {', '.join(variants[:3])})"
            if len(variants) > 1 else ""
        )

        lines = [
            f"Virginia Campaign Finance — Donor Trend Profile: {name}{also_known}",
            f"Total donated to Virginia state campaigns ({d['first_cycle']}–{d['last_cycle']}): {_fmt(total)}",
            f"Active in {d['active_cycles']} election cycles.",
            f"Typical giving pattern: {_fmt(odd_mean)} in state-election years (odd years, when VA elects all 140 legislative seats + governor), "
            f"{_fmt(even_mean)} in federal-election years (even years).",
            f"{DISCLAIMER}",
            "",
            "Year-by-year giving history:",
        ]

        for c in cycles:
            base  = odd_mean if c["cycle_parity"] == "odd" else even_mean
            yr    = "state-election yr" if c["cycle_parity"] == "odd" else "federal-election yr"
            ratio = c["total_amount"] / base if base else 0
            pct_s = f", up {c['pct_change_prior']:.0f}% from prior comparable cycle" \
                    if c["pct_change_prior"] else ""

            if c["is_spike"]:
                ctx_titles = "; ".join(ctx_map.get(c["election_cycle"], [])[:2])
                ctx_str = f" Active legislation that session: {ctx_titles}." if ctx_titles else ""
                lines.append(
                    f"  {c['election_cycle']} ({yr}): {_fmt(c['total_amount'])} "
                    f"[NOTABLY ELEVATED — {ratio:.1f}x their typical {yr} giving{pct_s}].{ctx_str}"
                )
            elif ratio >= 1.5:
                lines.append(
                    f"  {c['election_cycle']} ({yr}): {_fmt(c['total_amount'])} "
                    f"[elevated — {ratio:.1f}x typical{pct_s}]"
                )
            else:
                lines.append(f"  {c['election_cycle']} ({yr}): {_fmt(c['total_amount'])}")

        if spike_yrs:
            lines.append(
                f"\nSpike cycles (giving notably above their own baseline): {', '.join(spike_yrs)}."
            )

        chunks.append({
            "chunk_id":    f"donor_trend_{key}",
            "text":        "\n".join(lines),
            "source_type": "donor_trend",
            "donor_key":   key,
            "donor_name":  name,
            "total_raised": total,
            "spike_cycles": ", ".join(spike_yrs),
            "first_cycle": d["first_cycle"],
            "last_cycle":  d["last_cycle"],
        })

    return chunks


# ── 2. Per-legislator campaign finance profile ────────────────────────────────

def build_legislator_finance_chunks(conn: sqlite3.Connection) -> list[dict]:
    """One chunk per legislator summarising their donor sector profile."""
    rows = conn.execute("""
        SELECT legislator_id, name, chamber, district, party, source,
               latest_cycle, total_raised, top_sector, top_sector_pct,
               by_sector_json, top_donors_json, cycles_json
        FROM campaign_finance_summary
        WHERE by_sector_json IS NOT NULL
        ORDER BY total_raised DESC
    """).fetchall()

    chunks = []
    for r in rows:
        try:
            sectors = json.loads(r["by_sector_json"] or "[]")
        except Exception:
            sectors = []
        try:
            top_donors = json.loads(r["top_donors_json"] or "[]")
        except Exception:
            top_donors = []
        try:
            cycles = json.loads(r["cycles_json"] or "[]")
        except Exception:
            cycles = []

        source_label = "Virginia SBE (state campaign finance)" \
            if r["source"] == "va_sbe" else "FEC (federal campaign finance)"
        office = f"{r['chamber'] or 'Legislature'} District {r['district'] or '?'}"

        lines = [
            f"Virginia Campaign Finance Profile — {r['name']}",
            f"Office: {office} | Party: {r['party'] or 'Unknown'} | Source: {source_label}",
            f"Latest cycle: {r['latest_cycle']} | Total raised: {_fmt(r['total_raised'])}",
            f"Top donor sector: {r['top_sector']} ({r['top_sector_pct'] or '?'}% of total)",
            f"Active cycles: {', '.join(str(c) for c in (cycles or []))}",
            f"{DISCLAIMER}",
            "",
        ]

        if sectors:
            lines.append("Fundraising by sector (top sources):")
            for s in sectors[:8]:
                pct = round(s.get("total", 0) / r["total_raised"] * 100, 1) \
                      if r["total_raised"] else 0
                lines.append(
                    f"  {s.get('sector','?')}: {_fmt(s.get('total'))} "
                    f"from {s.get('donor_count',0):,} donors ({pct}%)"
                )

        if top_donors:
            lines.append("")
            lines.append("Top individual donors:")
            for d in top_donors[:5]:
                emp = d.get("employer", "")
                sec = d.get("sector", "")
                tag = f" [{emp}]" if emp else (f" [{sec}]" if sec else "")
                lines.append(
                    f"  {d.get('contributor_name','?')}{tag}: "
                    f"{_fmt(d.get('total'))} over {d.get('count',1)} contributions"
                )

        chunks.append({
            "chunk_id":      f"leg_finance_{r['source']}_{r['legislator_id']}",
            "text":          "\n".join(lines),
            "source_type":   "legislator_finance",
            "legislator_id": r["legislator_id"],
            "name":          r["name"],
            "chamber":       r["chamber"] or "",
            "party":         r["party"] or "",
            "top_sector":    r["top_sector"] or "",
            "total_raised":  r["total_raised"] or 0,
        })

    return chunks


# ── 3. Donor-vote alignment chunks ────────────────────────────────────────────

def build_donor_vote_alignment_chunks(conn: sqlite3.Connection) -> list[dict]:
    """One chunk per legislator showing how their votes track their donor sector."""
    rows = conn.execute("""
        SELECT legislator_id, name, party, chamber,
               top_donor_sector, top_sector_amt,
               sector_yes_rate, other_yes_rate, alignment_delta,
               sector_vote_count, other_vote_count, by_sector_json
        FROM donor_vote_alignment
        WHERE alignment_delta IS NOT NULL
        ORDER BY ABS(alignment_delta) DESC
    """).fetchall()

    chunks = []
    for r in rows:
        try:
            by_sector = json.loads(r["by_sector_json"] or "[]")
        except Exception:
            by_sector = []

        delta = r["alignment_delta"]
        if delta is None:
            continue

        # Plain-English interpretation
        sector = r["top_donor_sector"] or "Unknown"
        if delta >= 15:
            interp = (
                f"Strong alignment: {r['name']} votes YES on {sector} bills "
                f"{delta:.1f} percentage points more often than on other bills."
            )
        elif delta >= 5:
            interp = (
                f"Moderate alignment: {r['name']} votes YES on {sector} bills "
                f"{delta:.1f} percentage points more often than on other bills."
            )
        elif delta <= -15:
            interp = (
                f"Contrary pattern: Despite receiving significant {sector} funding, "
                f"{r['name']} votes YES on {sector} bills "
                f"{abs(delta):.1f} percentage points LESS often than on other bills."
            )
        elif delta <= -5:
            interp = (
                f"Slight contrary pattern: {r['name']} votes YES on {sector} bills "
                f"{abs(delta):.1f} percentage points less than their overall average."
            )
        else:
            interp = (
                f"No clear alignment: {r['name']}'s vote pattern on {sector} bills "
                f"is close to their overall average (delta: {delta:+.1f}pp)."
            )

        lines = [
            f"Virginia Donor-Vote Alignment — {r['name']}",
            f"Party: {r['party'] or 'Unknown'} | Chamber: {r['chamber'] or 'Unknown'}",
            f"Top donor sector: {sector} (total received: {_fmt(r['top_sector_amt'])})",
            f"",
            f"Vote analysis (based on contested floor votes, 2024-2026 sessions):",
            f"  YES rate on {sector} bills: {r['sector_yes_rate']:.1f}% "
            f"(based on {r['sector_vote_count']} votes)",
            f"  YES rate on all other bills: {r['other_yes_rate']:.1f}% "
            f"(based on {r['other_vote_count']} votes)",
            f"  Alignment delta: {delta:+.1f} percentage points",
            f"",
            interp,
            f"",
            f"{DISCLAIMER}",
        ]

        if by_sector:
            lines.append("")
            lines.append("YES rates by sector (all contested votes):")
            for s in sorted(by_sector, key=lambda x: -(x.get("total", 0))):
                yr = s.get("yes_rate")
                yr_s = f"{yr:.1f}%" if yr is not None else "n/a"
                lines.append(
                    f"  {s['sector']}: {yr_s} YES rate "
                    f"({s.get('yes',0)}/{s.get('total',0)} votes)"
                )

        chunks.append({
            "chunk_id":      f"dva_{r['legislator_id']}",
            "text":          "\n".join(lines),
            "source_type":   "donor_vote_align",
            "legislator_id": r["legislator_id"],
            "name":          r["name"],
            "chamber":       r["chamber"] or "",
            "party":         r["party"] or "",
            "top_sector":    sector,
            "alignment_delta": delta,
        })

    return chunks


# ── 4. Year-by-year investment narrative ──────────────────────────────────────

def build_cycle_narrative_chunks(conn: sqlite3.Connection) -> list[dict]:
    """One chunk per election year covering the dominant legislative fights and donor spikes."""
    # Hardcoded narratives for 2013-2021 (pre-structured-bill-data era)
    HARDCODED_NARRATIVES = {
        "2013": (
            "2013 Virginia Legislative Session — Investment Signal\n"
            "Key legislation: The Rate Freeze Act (SB1349/HB2690) froze SCC biennial review "
            "of Dominion Virginia Power base rates through 2022, preventing potential $1B+ in "
            "customer refunds.\n"
            "Donor spike: Dominion-affiliated PACs donated approximately $4.6M in the 2013 "
            "cycle — roughly 7x their 2012 baseline. Contributions went to 159 legislators "
            "in the sessions preceding and during the rate-freeze vote.\n"
            "Also active: Uranium mining moratorium debate; competing energy and mining interests."
        ),
        "2015": (
            "2015 Virginia Legislative Session — Investment Signal\n"
            "Key legislation: Rate freeze extension — Dominion's protection from SCC oversight "
            "continued despite the company earning $300M above its authorized return.\n"
            "Donor spike: Dominion-affiliated PACs donated $3.2M in 2015 — 7x their 2014 "
            "even-year baseline.\n"
            "Also active: Consumer lending regulation; payday loan reform bills."
        ),
        "2017": (
            "2017 Virginia Legislative Session — Investment Signal\n"
            "Key legislation: Grid Transformation and Security Act (HB2291/SB1416) authorized "
            "Dominion and Appalachian Power to invest $4.3B in grid modernization while "
            "shielding spending from SCC prudency review. Included Virginia Beach offshore wind pilot.\n"
            "Context: Governor race (Northam vs. Gillespie) drove total cycle spending to $202M — "
            "highest odd-year total to that point."
        ),
        "2019": (
            "2019 Virginia Legislative Session — Investment Signal\n"
            "Key legislation: Pre-legislative negotiations for what became the Virginia Clean "
            "Economy Act. Democrats won control of both chambers in November 2019, shifting "
            "the energy regulatory landscape significantly.\n"
            "Counter-donor emergence: Clean Virginia Fund (founded 2018 by Michael Bills) "
            "ramped to $410K in 2019, specifically to counter Dominion's legislative influence.\n"
            "Also active: Casino gaming authorization bills; ABC privatization proposals."
        ),
        "2020": (
            "2020 Virginia Legislative Session — Investment Signal\n"
            "Key legislation: Virginia Clean Economy Act (VCEA) signed April 2020. Requires "
            "100% clean electricity by 2045 (Dominion) / 2050 (Appalachian Power).\n"
            "Casino referendum enabling legislation (HB4/SB36): authorized casino referenda "
            "in Bristol, Danville, Norfolk, Portsmouth, and Richmond. MGM, Caesars, Hard Rock "
            "among largest even-year institutional donors.\n"
            "Dominion even-year spend elevated to $1.4M (1.1x even-year mean) as VCEA moved "
            "through session. Clean Virginia Fund at $1.2M (first major even-year spend)."
        ),
        "2021": (
            "2021 Virginia Legislative Session — Investment Signal\n"
            "Key legislation: Coastal Virginia Offshore Wind (CVOW) 2.6 GW project regulatory "
            "approvals; SCC rate review proceedings under VCEA framework.\n"
            "Record spending: Total VA campaign finance reached $385M in 2021 — highest "
            "odd-year total (Youngkin vs. McAuliffe governor race).\n"
            "Dominion: $6.1M — highest odd-year total to that point (2.5x 2019 odd-year).\n"
            "Clean Virginia Fund: $5.1M — matched Dominion nearly dollar-for-dollar in "
            "what became a direct counter-spending campaign."
        ),
    }

    chunks = []

    # Hardcoded narrative chunks
    for yr, narrative in HARDCODED_NARRATIVES.items():
        # Add top donors for that year
        top_donors = conn.execute("""
            SELECT canonical_name, total_amount, cycle_parity, is_spike
            FROM donor_cycle_trends
            WHERE election_cycle = ? AND is_individual = 0 AND total_amount >= 50000
            ORDER BY total_amount DESC LIMIT 10
        """, (yr,)).fetchall()

        text = narrative
        if top_donors:
            text += "\n\nTop institutional donors this cycle:\n"
            for d in top_donors:
                spike_s = " [SPIKE]" if d["is_spike"] else ""
                text += f"  {d['canonical_name']}: {_fmt(d['total_amount'])}{spike_s}\n"

        text += f"\n{DISCLAIMER}"

        chunks.append({
            "chunk_id":    f"cycle_narrative_{yr}",
            "text":        text,
            "source_type": "cycle_narrative",
            "cycle":       yr,
            "parity":      "odd" if int(yr) % 2 == 1 else "even",
        })

    # Bill-matched narrative for 2022-2026
    for yr in ["2022", "2023", "2024", "2025", "2026"]:
        ctx_rows = conn.execute("""
            SELECT donor_sector, title, description, bill_number, significance
            FROM cycle_context
            WHERE election_cycle = ? AND significance = 'high'
            ORDER BY donor_sector, significance DESC
            LIMIT 20
        """, (yr,)).fetchall()

        top_donors = conn.execute("""
            SELECT canonical_name, total_amount, is_spike
            FROM donor_cycle_trends
            WHERE election_cycle = ? AND is_individual = 0 AND total_amount >= 100000
            ORDER BY total_amount DESC LIMIT 12
        """, (yr,)).fetchall()

        if not ctx_rows and not top_donors:
            continue

        parity = "odd" if int(yr) % 2 == 1 else "even"
        yr_type = "state-election year" if parity == "odd" else "federal-election year"

        lines = [
            f"{yr} Virginia Legislative Session — Donor & Bill Correlation Summary",
            f"({yr_type})",
            "",
        ]

        if ctx_rows:
            by_sector: dict[str, list[str]] = {}
            for r in ctx_rows:
                by_sector.setdefault(r["donor_sector"] or "General", []).append(
                    r["title"][:80]
                )
            lines.append("Key legislation by sector:")
            for sector, titles in by_sector.items():
                lines.append(f"  {sector}:")
                for t in titles[:4]:
                    lines.append(f"    - {t}")

        if top_donors:
            lines.append("")
            lines.append("Top institutional donors this cycle:")
            for d in top_donors:
                spike_s = " [NOTABLY ELEVATED vs. this donor's baseline]" if d["is_spike"] else ""
                lines.append(f"  {d['canonical_name']}: {_fmt(d['total_amount'])}{spike_s}")

        lines.append(f"\n{DISCLAIMER}")

        chunks.append({
            "chunk_id":    f"cycle_narrative_{yr}",
            "text":        "\n".join(lines),
            "source_type": "cycle_narrative",
            "cycle":       yr,
            "parity":      parity,
        })

    return chunks


# ── 5. Lobbyist cross-reference chunks ────────────────────────────────────────

def build_lobbyist_chunks(conn: sqlite3.Connection) -> list[dict]:
    """One chunk per significant lobbying principal."""
    principals = conn.execute("""
        SELECT principal_name, COUNT(DISTINCT lobbyist_name) as lobbyist_count,
               MIN(year_range) as first_year, MAX(year_range) as last_year,
               GROUP_CONCAT(DISTINCT lobbyist_name) as lobbyist_names
        FROM lobbyist_registrations
        GROUP BY principal_name
        HAVING lobbyist_count >= 2
        ORDER BY lobbyist_count DESC
        LIMIT 100
    """).fetchall()

    chunks = []
    for p in principals:
        name       = p["principal_name"]
        n_lobbyists = p["lobbyist_count"]
        lobbyists   = (p["lobbyist_names"] or "").split(",")[:8]

        # Try to find donation data
        total_donated = 0
        n_recipients  = 0
        donation_row = conn.execute("""
            SELECT SUM(amount) as total, COUNT(DISTINCT candidate_name) as n
            FROM va_cf_schedule_a
            WHERE last_or_company LIKE ?
        """, (f"%{name[:20]}%",)).fetchone()
        if donation_row and donation_row["total"]:
            total_donated = donation_row["total"]
            n_recipients  = donation_row["n"]

        lines = [
            f"Virginia Lobbying Registration — {name}",
            f"Registered lobbyists: {n_lobbyists} ({', '.join(l.strip() for l in lobbyists[:5])}{'...' if len(lobbyists) > 5 else ''})",
            f"Active years on record: {p['first_year']} – {p['last_year']}",
        ]
        if total_donated:
            lines.append(
                f"Campaign donations to VA legislators: {_fmt(total_donated)} "
                f"across {n_recipients:,} recipients (Virginia SBE records)"
            )
            lines.append(
                f"Combined lobbying + campaign finance presence: {name} has both "
                f"registered lobbyists and documented campaign contributions to "
                f"Virginia legislators, representing dual-channel influence."
            )
        lines.append(f"{DISCLAIMER}")

        chunks.append({
            "chunk_id":       f"lobbyist_{re.sub(r'[^a-z0-9]', '_', name.lower())[:40]}",
            "text":           "\n".join(lines),
            "source_type":    "lobbyist_crossref",
            "principal_name": name,
            "lobbyist_count": n_lobbyists,
            "total_donated":  round(total_donated, 2),
        })

    return chunks


# ── 6. Sector summary chunks ──────────────────────────────────────────────────

def build_sector_summary_chunks(conn: sqlite3.Connection) -> list[dict]:
    """One chunk per sector summarising donor activity and legislative correlation."""
    SECTORS = [
        ("Energy/Utilities", ["dominion", "appalachian", "electric", "energy", "solar", "grid"]),
        ("Healthcare",       ["health", "hospital", "medical", "pharma", "nurse"]),
        ("Alcohol/Gambling", ["casino", "gaming", "beer", "wine", "spirits", "cannabis"]),
        ("Finance",          ["bank", "financ", "insur", "credit", "loan"]),
        ("Agriculture",      ["farm", "agri", "crop", "livestock"]),
        ("Transportation",   ["transit", "highway", "toll", "rideshare"]),
        ("Housing",          ["hous", "rent", "zoning", "landlord"]),
        ("Labor",            ["seiu", "union", "afscme", "labor", "worker"]),
    ]

    chunks = []
    for sector_name, kws in SECTORS:
        # Top donors in this sector from campaign_finance_summary
        top_legs = conn.execute("""
            SELECT name, chamber, party, total_raised, top_sector
            FROM campaign_finance_summary
            WHERE top_sector LIKE ?
            ORDER BY total_raised DESC LIMIT 8
        """, (f"%{sector_name.split('/')[0]}%",)).fetchall()

        # Alignment stats for this sector
        align_rows = conn.execute("""
            SELECT name, party, sector_yes_rate, other_yes_rate, alignment_delta
            FROM donor_vote_alignment
            WHERE top_donor_sector = ?
            ORDER BY ABS(alignment_delta) DESC LIMIT 6
        """, (sector_name,)).fetchall()

        # Spike history
        spikes = conn.execute("""
            SELECT canonical_name, election_cycle, total_amount, ratio_vs_mean
            FROM donor_cycle_trends
            WHERE is_spike = 1 AND is_individual = 0
              AND lower(canonical_name) LIKE ?
            ORDER BY total_amount DESC LIMIT 5
        """, (f"%{kws[0]}%",)).fetchall()

        lines = [
            f"Virginia Campaign Finance — {sector_name} Sector Overview",
            f"",
            f"This document covers the {sector_name} sector's donor activity, "
            f"legislative correlation, and voting patterns in Virginia (2012–2026).",
            f"{DISCLAIMER}",
            "",
        ]

        if top_legs:
            lines.append(f"Legislators whose top donor sector is {sector_name}:")
            for l in top_legs:
                lines.append(
                    f"  {l['name']} ({l['party'] or '?'}, {l['chamber'] or '?'}): "
                    f"{_fmt(l['total_raised'])} total raised"
                )

        if align_rows:
            lines.append("")
            lines.append(f"Donor-vote alignment for {sector_name}-funded legislators:")
            for a in align_rows:
                delta = a["alignment_delta"]
                dir_s = "more" if delta >= 0 else "less"
                lines.append(
                    f"  {a['name']} ({a['party'] or '?'}): votes YES on {sector_name} bills "
                    f"{abs(delta):.1f}pp {dir_s} often than their baseline "
                    f"({a['sector_yes_rate']:.1f}% vs {a['other_yes_rate']:.1f}%)"
                )

        if spikes:
            lines.append("")
            lines.append(f"Notable {sector_name} donor spending spikes:")
            for s in spikes:
                lines.append(
                    f"  {s['canonical_name']} in {s['election_cycle']}: "
                    f"{_fmt(s['total_amount'])} ({s['ratio_vs_mean']:.1f}x their typical giving)"
                )

        chunks.append({
            "chunk_id":    f"sector_summary_{re.sub(r'[^a-z0-9]', '_', sector_name.lower())}",
            "text":        "\n".join(lines),
            "source_type": "sector_summary",
            "sector":      sector_name,
        })

    return chunks


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    conn = _conn()
    all_chunks: list[dict] = []

    print("Building donor trend chunks...")
    c1 = build_donor_trend_chunks(conn)
    all_chunks.extend(c1)
    print(f"  {len(c1)} donor trend chunks")

    print("Building legislator finance chunks...")
    c2 = build_legislator_finance_chunks(conn)
    all_chunks.extend(c2)
    print(f"  {len(c2)} legislator finance chunks")

    print("Building donor-vote alignment chunks...")
    c3 = build_donor_vote_alignment_chunks(conn)
    all_chunks.extend(c3)
    print(f"  {len(c3)} donor-vote alignment chunks")

    print("Building cycle narrative chunks...")
    c4 = build_cycle_narrative_chunks(conn)
    all_chunks.extend(c4)
    print(f"  {len(c4)} cycle narrative chunks")

    print("Building lobbyist cross-reference chunks...")
    c5 = build_lobbyist_chunks(conn)
    all_chunks.extend(c5)
    print(f"  {len(c5)} lobbyist chunks")

    print("Building sector summary chunks...")
    c6 = build_sector_summary_chunks(conn)
    all_chunks.extend(c6)
    print(f"  {len(c6)} sector summary chunks")

    conn.close()

    print(f"\nTotal chunks: {len(all_chunks)}")
    print(f"Writing to {OUT_FILE}...")
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        for chunk in all_chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    # Stats
    sizes = [len(c["text"].split()) for c in all_chunks]
    print(f"Done. Word counts: min={min(sizes)} avg={sum(sizes)//len(sizes)} max={max(sizes)}")
    print(f"\nIngest with: python ingest.py --file {OUT_FILE.name}")

    # Preview
    print("\nSample chunks:")
    for chunk in all_chunks[:2]:
        print(f"\n--- {chunk['chunk_id']} ({chunk['source_type']}) ---")
        print(chunk["text"][:400])
        print("...")


if __name__ == "__main__":
    main()
