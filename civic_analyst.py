"""
civic_analyst.py
Unified analyst for state and federal legislators.
Config from config/, data from db/, guardrails from analysis/.
"""

import argparse
from anthropic import Anthropic

from config.finance_rules import (
    get_config, get_disclaimer, get_federal_limit,
    DONOR_LABELS, DONOR_ICONS, LevelConfig,
)
from config.prompts import (
    SYSTEM_PROMPT, get_prompt,
    format_sector_lines, format_committee_lines,
    format_defection_lines, format_cycle_lines,
    format_geography_lines, format_network_lines,
    format_loyalty_lines,
    format_shared_donor_lines, format_alignment_lines,
)
from db.federal import (
    get_sector_totals  as fed_sectors,
    get_donor_shift    as fed_shift,
    get_donor_geography as fed_geo,
    get_votes          as fed_votes,
    get_party_defections as fed_defections,
    get_sponsored_bills as fed_bills,
    get_effectiveness_stats as fed_effectiveness,
    get_industry_network as fed_network,
    get_loyalty_scores as fed_loyalty,
    get_member, get_member_by_name,
    get_bioguide_id,
    get_shared_donors_named as fed_shared_donors,
    get_vote_alignment_matrix as fed_alignment,
)
from db.virginia import (
    get_sector_totals  as va_sectors,
    get_donor_shift    as va_shift,
    get_donor_geography as va_geo,
    get_votes          as va_votes,
    get_party_defections as va_defections,
    get_sponsored_bills as va_bills,
    get_effectiveness_stats as va_effectiveness,
    get_industry_network as va_network,
    get_loyalty_scores as va_loyalty,
    get_legislator, get_legislator_by_name,
)
from analysis.guardrails import run_guardrails, safe_output

client = Anthropic()

# ── CLASSIFIER ────────────────────────────────────────────────────────────────

_LARGE_DONOR_LABELS = frozenset({
    "Near-max donor money", "Industry-linked money",
    "Large-donor money", "Mega-donor money", "Major institutional money",
})


def classify_sectors(rows: list, cfg: LevelConfig,
                     cycle: int = None) -> list:
    t         = cfg.thresholds
    has_limit = cfg.limits.individual is not None
    limit     = get_federal_limit(cycle) if has_limit and cycle else (
                cfg.limits.individual or 0)

    classified = []
    for row in rows:
        sector = row[0] or ""
        total  = float(row[1] or 0)
        count  = int(row[2] or 0)
        avg    = float(row[3] or 0)
        if not count:
            continue

        if has_limit:
            if avg >= limit * 0.8:
                key = "near_max"
            elif avg >= t.industry_linked:
                key = "industry_linked"
            elif avg >= t.mixed_lower:
                key = "mixed"
            else:
                key = "small_dollar"
        else:
            if avg >= t.major_institutional:
                key = "major_institutional"
            elif avg >= t.large_donor:
                key = "large_donor"
            elif avg >= t.industry_linked:
                key = "industry_linked"
            elif avg >= t.mixed_lower:
                key = "mixed"
            else:
                key = "small_dollar"

        classified.append({
            "sector":        sector,
            "total":         total,
            "donor_count":   count,
            "avg_donation":  avg,
            "concentration": DONOR_LABELS[key],
            "label":         DONOR_LABELS[key],
            "icon":          DONOR_ICONS[key],
        })

    return classified


def summarize_profile(classified: list) -> dict:
    total_dollars = sum(c["total"] for c in classified)
    total_donors  = sum(c["donor_count"] for c in classified)
    small_dollars = sum(
        c["total"] for c in classified
        if c["label"] == "Small-dollar base"
    )
    large_dollars = sum(
        c["total"] for c in classified
        if c["label"] in _LARGE_DONOR_LABELS
    )
    return {
        "total_raised":    total_dollars,
        "total_donors":    total_donors,
        "large_donor_pct": (large_dollars / total_dollars * 100)
                           if total_dollars else 0.0,
        "small_dollar_pct":(small_dollars / total_dollars * 100)
                           if total_dollars else 0.0,
    }


# ── PROMPT KWARGS BUILDER ─────────────────────────────────────────────────────

def _build_kwargs(name: str, cfg: LevelConfig, cycle,
                  classified: list, profile: dict,
                  committees: list, defections: list,
                  shift: list, geo: list, effectiveness: dict,
                  network: list, loyalty: list,
                  sector: str = "", limit: int = None) -> dict:

    contribution_limit = (
        f"${limit:,} per individual per cycle"
        if limit else "No legal limit (Virginia state)"
    )

    # Geography percentages from raw geo rows
    # row format: (city, state, donations, total, origin)
    geo_total = sum(float(r[3] or 0) for r in geo) if geo else 0
    in_state_dollars  = sum(float(r[3] or 0) for r in geo
                            if r[4] == "In State") if geo else 0
    out_state_dollars = sum(float(r[3] or 0) for r in geo
                            if r[4] == "Out of State") if geo else 0
    local_pct       = (in_state_dollars / geo_total * 100) if geo_total else 0.0
    out_state_pct   = (out_state_dollars / geo_total * 100) if geo_total else 0.0
    out_district_pct = 0.0  # district-level data not yet available

    # Defection rows from DB have different shape than display dicts
    defection_dicts = []
    for d in defections:
        if isinstance(d, dict):
            defection_dicts.append(d)
        else:
            defection_dicts.append({
                "bill":          d[0] if len(d) > 0 else "",
                "vote":          d[1] if len(d) > 1 else "",
                "party_majority": d[2] if len(d) > 2 else "",
                "classification": d[4] if len(d) > 4 else "",
            })

    # Minority party flag (119th Congress: R majority)
    minority_party = "Yes (minority party)" if False else "No"

    return {
        # Identity
        "name":               name,
        "level":              cfg.level,
        "level_context":      cfg.context.strip(),
        "cycle":              cycle,
        "sector":             sector,
        # Contribution context
        "contribution_limit": contribution_limit,
        # Profile numbers
        "total_raised":       profile["total_raised"],
        "total_donors":       profile["total_donors"],
        "large_donor_pct":    profile["large_donor_pct"],
        "small_dollar_pct":   profile["small_dollar_pct"],
        # Formatted sections
        "sector_lines":       format_sector_lines(classified),
        "committee_lines":    format_committee_lines(committees),
        "defection_lines":    format_defection_lines(defection_dicts),
        "cycle_lines":        format_cycle_lines(shift),
        "geography_lines":    format_geography_lines(geo),
        "network_lines":      format_network_lines(network),
        "loyalty_lines":      format_loyalty_lines(loyalty),
        # Geography percentages
        "local_pct":          local_pct,
        "out_district_pct":   out_district_pct,
        "out_state_pct":      out_state_pct,
        # Effectiveness
        "bills_introduced":   effectiveness.get("bills_introduced", 0),
        "bills_passed":       effectiveness.get("bills_passed", 0),
        "pass_rate":          effectiveness.get("pass_rate", 0.0),
        "bipartisan_bills":   effectiveness.get("bipartisan_bills", 0),
        "avg_cosponsors":     effectiveness.get("avg_cosponsors", 0.0),
        "subject_lines":      "  No subject data available",
        # Loyalty
        "minority_party":     minority_party,
        # Disclaimer
        "disclaimer":         get_disclaimer(cfg).strip(),
    }


# ── MEMBER LOOKUP ─────────────────────────────────────────────────────────────

def lookup_member(member_id: str, level: str) -> dict:
    """Return member/legislator dict or empty dict if not found."""
    if level == "federal":
        return get_member(member_id) or get_member_by_name(member_id) or {}
    else:
        return get_legislator(member_id) or get_legislator_by_name(member_id) or {}


# ── MAIN ANALYST RUNNER ───────────────────────────────────────────────────────

def run_analyst(name: str, member_id: str,
                level: str = "federal",
                analyst_type: str = "triangle",
                cycle=None,
                committees: list = None,
                defections: list = None,
                sector: str = "") -> str:
    """
    Run any analyst type for federal or state legislators.

    Federal member_id: FEC candidate_id (e.g. H2VA02064)
    State member_id:   LIS ID
    """
    cfg = get_config(level)

    if cycle is None:
        cycle = 2024 if level == "federal" else "2023"

    print(f"\n{'='*60}")
    print(f"{cfg.level} — {analyst_type.upper()}")
    print(f"  {name}  |  Cycle: {cycle}")
    print("="*60)

    # ── Gather data ───────────────────────────────────────────
    if level == "federal":
        candidate_id = member_id
        bioguide_id  = get_bioguide_id(candidate_id)
        congress     = 119
        int_cycle    = int(cycle)
        limit        = get_federal_limit(int_cycle)

        sectors      = fed_sectors(candidate_id, int_cycle)
        shift        = fed_shift(candidate_id) if analyst_type == "donor_shift" else []
        geo          = fed_geo(candidate_id, int_cycle) if analyst_type == "geography" else []
        votes        = fed_votes(bioguide_id, congress) if bioguide_id else []
        auto_defects = fed_defections(bioguide_id, congress) if bioguide_id else []
        bills        = fed_bills(bioguide_id, congress) if bioguide_id else []
        effectiveness = (fed_effectiveness(bioguide_id, congress)
                         if analyst_type == "effectiveness" and bioguide_id else {})
        network      = fed_network(sector, int_cycle) if analyst_type == "network" else []
        loyalty      = fed_loyalty(bioguide_id) if analyst_type == "loyalty" and bioguide_id else []

        # Override context with cycle-specific limit
        from dataclasses import replace
        cfg = replace(cfg, context=f"""
FEDERAL CONTEXT:
{cfg.limits.note}
Individual donor sector totals reflect the industries
where donors are employed — not direct corporate contributions.
Use: 'industry-linked individual donations' not 'corporate donations.'
Anyone giving ${limit:,} has reached the individual legal
maximum for the {cycle} cycle.
""")

    else:
        lis_id       = member_id
        limit        = None
        int_cycle    = None

        sectors      = va_sectors(lis_id, cycle)
        shift        = va_shift(lis_id) if analyst_type == "donor_shift" else []
        geo          = va_geo(lis_id, cycle) if analyst_type == "geography" else []
        votes        = va_votes(lis_id)
        auto_defects = va_defections(lis_id)
        bills        = va_bills(lis_id)
        effectiveness = va_effectiveness(lis_id) if analyst_type == "effectiveness" else {}
        network      = va_network(sector, cycle) if analyst_type == "network" else []
        loyalty      = va_loyalty(lis_id) if analyst_type == "loyalty" else []

    final_defections = defections if defections is not None else auto_defects

    # ── Classify + summarize ──────────────────────────────────
    classified = classify_sectors(sectors, cfg, int_cycle)
    profile    = summarize_profile(classified)

    # ── Build prompt ──────────────────────────────────────────
    kwargs = _build_kwargs(
        name=name, cfg=cfg, cycle=cycle,
        classified=classified, profile=profile,
        committees=committees or [],
        defections=final_defections,
        shift=shift, geo=geo,
        effectiveness=effectiveness,
        network=network, loyalty=loyalty,
        sector=sector, limit=limit,
    )

    prompt = get_prompt(analyst_type).format(**kwargs)

    # ── Call Claude ───────────────────────────────────────────
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_output = response.content[0].text

    # ── Guardrails ────────────────────────────────────────────
    raw_data = {
        "sector_totals": sectors,
        "votes":         votes,
        "bills":         bills,
    }
    result = run_guardrails(raw_output, cfg, raw_data=raw_data)
    report = safe_output(result)

    if result.warnings:
        print(f"  [{len(result.warnings)} guardrail warnings]")

    print(report.encode("ascii", errors="replace").decode("ascii"))
    return report


# ── BLOC ANALYST ─────────────────────────────────────────────────────────────

def run_bloc_analyst(
    bioguide_ids:   list,
    cycle:          int = 2024,
    congress:       int = 119,
    min_candidates: int = 2,
) -> str:
    """
    Bloc analysis: shared donors + pairwise vote alignment
    for a group of federal members.

    bioguide_ids: list of congressional bioguide IDs
    Returns guardrail-cleaned Claude report.
    """
    if not bioguide_ids or len(bioguide_ids) < 2:
        return "Bloc analysis requires at least two member IDs."

    bioguide_ids = list(dict.fromkeys(bioguide_ids))
    cfg          = get_config("federal")

    # Resolve display names
    members      = [get_member(bid) or {"name": bid, "party": "?", "chamber": "?"} for bid in bioguide_ids]
    member_names = "\n".join(
        f"  - {m.get('name', bid)} ({m.get('party', '?')}, {m.get('chamber', '?')})"
        for m, bid in zip(members, bioguide_ids)
    )

    print(f"\n{'='*60}")
    print(f"FEDERAL — BLOC ANALYSIS")
    print(f"  Cycle: {cycle} | Congress: {congress}")
    print(f"  Members: {', '.join(m.get('name', bid) for m, bid in zip(members, bioguide_ids))}")
    print("="*60)

    # Gather data
    shared_donors = fed_shared_donors(bioguide_ids, cycle, min_candidates)
    alignment     = fed_alignment(bioguide_ids, congress)

    # Format prompt
    disclaimer = get_disclaimer(cfg).strip()
    prompt     = get_prompt("bloc").format(
        cycle              = cycle,
        member_names       = member_names,
        shared_donor_lines = format_shared_donor_lines(shared_donors),
        alignment_lines    = format_alignment_lines(alignment),
        disclaimer         = disclaimer,
    )

    # Call Claude
    response   = client.messages.create(
        model      = "claude-sonnet-4-6",
        max_tokens = 1500,
        system     = SYSTEM_PROMPT,
        messages   = [{"role": "user", "content": prompt}],
    )
    raw_output = response.content[0].text

    # Guardrails
    raw_data = {
        "sector_totals": shared_donors,  # data quality check — non-empty if donors found
        "votes":         alignment,
        "bills":         [],
    }
    result = run_guardrails(raw_output, cfg, raw_data=raw_data)
    report = safe_output(result)

    if result.warnings:
        print(f"  [{len(result.warnings)} guardrail warnings]")

    print(report.encode("ascii", errors="replace").decode("ascii"))
    return report


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VoteIQ Civic Analyst")
    parser.add_argument("--name",    required=True,
                        help="Legislator display name")
    parser.add_argument("--id",      required=True,
                        help="FEC candidate_id (federal) or lis_id (state)")
    parser.add_argument("--level",   default="federal",
                        choices=["federal", "state"])
    parser.add_argument("--type",    default="triangle",
                        choices=["triangle", "donor_shift", "geography",
                                 "network", "effectiveness", "loyalty",
                                 "bloc"])
    parser.add_argument("--cycle",   default=None,
                        help="Cycle year (2024 for federal, 2023 for state)")
    parser.add_argument("--sector",  default="",
                        help="Sector name (network analyst only)")
    parser.add_argument("--committees", nargs="*", default=[])
    parser.add_argument("--bloc-ids", nargs="*", default=[],
                        help="Additional bioguide IDs for bloc analysis")
    args = parser.parse_args()

    cycle = int(args.cycle) if args.cycle and args.level == "federal" else args.cycle

    if args.type == "bloc":
        # Combine --id with any --bloc-ids
        all_ids = [args.id] + (args.bloc_ids or [])
        run_bloc_analyst(
            bioguide_ids   = all_ids,
            cycle          = int(cycle) if cycle else 2024,
        )
    else:
        run_analyst(
            name=args.name,
            member_id=args.id,
            level=args.level,
            analyst_type=args.type,
            cycle=cycle,
            sector=args.sector,
            committees=args.committees,
        )
