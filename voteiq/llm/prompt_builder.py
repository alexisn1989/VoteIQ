from voteiq.config.prompts import ANALYST_INSTRUCTIONS


def build_analyst_prompt(name: str, config: dict, analyst_type: str,
                         classified: list, profile: dict,
                         committees: list, defections: list,
                         donor_shift: list = None,
                         geography: list = None,
                         effectiveness: dict = None) -> str:

    sector_lines = "\n".join(
        f"  {c['label']} {c['sector']}: "
        f"${c['total']:,.0f} | {c['donor_count']} donors | "
        f"${c['avg_donation']:,.0f} avg | {c['concentration']}"
        for c in classified
    ) or "  No sector data available"

    limit_context = (
        f"Contribution limit: ${profile['contribution_limit']:,}"
        if profile["has_limits"]
        else "NO CONTRIBUTION LIMITS — unlimited giving allowed"
    )

    profile_lines = (
        f"  Level:               {profile['level']}\n"
        f"  {limit_context}\n"
        f"  Total raised:        ${profile['total_raised']:,.0f}\n"
        f"  Total donors:        {profile['total_donors']:,}\n"
        f"  Large-donor money:   {profile['large_donor_pct']:.1f}%\n"
        f"  Small-dollar base:   {profile['small_dollar_pct']:.1f}%"
    )

    shift_lines = ""
    if donor_shift:
        cycles: dict = {}
        for row in donor_shift:
            cycle, sector, total, count, avg = row[0], row[1], row[2], row[3], row[4]
            cycles.setdefault(cycle, []).append(
                f"    {sector}: ${total:,.0f} ({count} donors, ${(avg or 0):,.0f} avg)"
            )
        shift_lines = "\nDONOR SHIFT ACROSS CYCLES:\n"
        for cycle, lines in cycles.items():
            shift_lines += f"  {cycle}:\n" + "\n".join(lines) + "\n"

    geo_lines = ""
    if geography:
        geo_lines = "\nDONOR GEOGRAPHY:\n" + "\n".join(
            f"  {row[0]}, {row[1]}: ${row[3]:,.0f} ({row[2]} donations) — {row[4]}"
            for row in geography[:10]
        )

    eff_lines = ""
    if effectiveness:
        eff_lines = (
            f"\nLEGISLATIVE EFFECTIVENESS:\n"
            f"  Bills introduced: {effectiveness.get('bills_introduced', 0)}\n"
            f"  Bills passed:     {effectiveness.get('bills_passed', 0)}\n"
            f"  Pass rate:        {effectiveness.get('pass_rate', 0):.1f}%\n"
            f"  Bipartisan bills: {effectiveness.get('bipartisan', 0)}\n"
            f"  Avg co-sponsors:  {effectiveness.get('avg_cosponsors', 0):.1f}"
        )

    defection_lines = (
        "\n".join(
            f"  - {d['bill']}: Voted {d['vote']} | "
            f"Party: {d['party_majority']} | {d['classification']}"
            for d in defections
        ) if defections else "  No notable defections found"
    )

    committee_lines = "\n".join(f"  - {c}" for c in committees) or "  None provided"

    instructions = ANALYST_INSTRUCTIONS.get(analyst_type, ANALYST_INSTRUCTIONS["triangle"])

    limit_note = (
        f"Anyone at ${config['contribution_limit']:,} maxed out legally"
        if config.get("contribution_limit")
        else "No limits apply here — unlimited giving is legal"
    )

    return f"""
You are a nonpartisan civic intelligence analyst for VoteIQ,
covering Virginia politics at {config['level']} level.

{config['context']}

LEGISLATOR: {name}
ANALYST TYPE: {analyst_type.upper()}

FUNDING PROFILE:
{profile_lines}

SECTOR BREAKDOWN:
(🔴 Near-max / Large-donor money | 🟡 Mixed donor base | 🟢 Small-dollar base | ⚫ Industry-linked money)
{sector_lines}
{shift_lines}
{geo_lines}
{eff_lines}

COMMITTEE ASSIGNMENTS:
{committee_lines}

PARTY DEFECTIONS:
{defection_lines}

ANALYSIS INSTRUCTIONS:
{instructions}

CRITICAL DISTINCTION:
- Federal donors max at ${config.get('contribution_limit', 'unlimited')}
- {limit_note}
- Always note this context when interpreting donation sizes

FORMAT:

## Funding Profile
[Small-dollar base vs large-donor money — clearly separated. Use the safer labels.]

## {'Career Arc' if analyst_type == 'donor_shift' else 'Sector Analysis'}
[Key findings with level-appropriate context. No inferred motive.]

## Committee Alignment
[Industry-linked or large-donor sectors with committee overlap — flagged 🚨]
[Small-dollar sectors with overlap — noted ✓]

## {'Geographic Profile' if analyst_type == 'geography' else 'Defection Analysis'}
[Level-appropriate findings]

## Alignment Rating
[Low / Medium / High — one sentence explanation]

## VoteIQ Finding
[Single most newsworthy nonpartisan sentence]

---
Correlation does not imply causation.
Data: {config['data_source']} | {config['vote_source']}
"""
