"""
llm/claude_client.py
Sends data to Claude API and returns cleaned reports.
Single entry point for all Claude calls in VoteIQ.
Applies guardrails before every response reaches the user.
"""
import logging
from anthropic import Anthropic

from voteiq.config.finance_rules import LevelConfig, get_disclaimer
from voteiq.config.prompts import (
    SYSTEM_PROMPT,
    CHAT_PROMPT,
    get_prompt,
    format_sector_lines,
    format_committee_lines,
    format_defection_lines,
    format_cycle_lines,
    format_geography_lines,
    format_network_lines,
    format_loyalty_lines,
)
from voteiq.analysis.guardrails import (
    run_guardrails,
    safe_output,
    FALLBACK_RESPONSE,
)

logger = logging.getLogger(__name__)

# ── CLIENT ────────────────────────────────────────────────────────────────────

_client: Anthropic | None = None


def get_client() -> Anthropic:
    """Shared Anthropic client — lazy-initialized on first call."""
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


# ── MODEL CONSTANTS ───────────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-6"

MAX_TOKENS: dict[str, int] = {
    "summary":    300,
    "analysis":   800,
    "full":      1500,
    "network":   1000,
    "shift":     1200,
}


# ── BASE CALLER ───────────────────────────────────────────────────────────────

def _call_claude(prompt: str,
                 system: str = SYSTEM_PROMPT,
                 max_tokens: int = 800) -> str:
    """
    Raw Claude API call.
    Returns text response or empty string on error.
    Internal — always call through generate_report() or generate_chat_response().
    """
    try:
        msg = get_client().messages.create(
            model      = MODEL,
            max_tokens = max_tokens,
            system     = system,
            messages   = [{"role": "user", "content": prompt}],
        )
        return msg.content[0].text
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return ""


# ── REPORT GENERATOR ──────────────────────────────────────────────────────────

def generate_report(prompt: str,
                    config: LevelConfig,
                    raw_data: dict,
                    tier: str = "full") -> str:
    """
    Generate a civic analyst report and apply guardrails.

    Usage:
        report = generate_report(
            prompt   = build_triangle_prompt(...),
            config   = get_config("federal", cycle=2024),
            raw_data = {"sectors": classified["sectors"], "votes": votes},
            tier     = "full",
        )
    """
    raw_output = _call_claude(
        prompt     = prompt,
        system     = SYSTEM_PROMPT,
        max_tokens = MAX_TOKENS.get(tier, 1500),
    )

    if not raw_output:
        logger.warning("Claude returned empty response")
        return FALLBACK_RESPONSE

    result = run_guardrails(
        output   = raw_output,
        config   = config,
        raw_data = raw_data,
        mode     = "report",
    )

    if result.violations:
        logger.error(f"Guardrail violations: {result.violations}")
    if result.warnings:
        logger.info(f"Guardrail warnings: {result.warnings}")

    return safe_output(result)


# ── CHAT RESPONSE GENERATOR ───────────────────────────────────────────────────

def generate_chat_response(user_message: str,
                            context: dict,
                            config: LevelConfig,
                            history: list | None = None) -> str:
    """
    Generate a conversational chat response.

    context: dict from check_data_available(), includes:
      member, cycle, has_finance, has_votes, has_bills,
      sectors, votes, bills, defections
    history: list of prior {role, content} messages for multi-turn
    """
    context_lines = _build_context_summary(context)

    prompt = f"AVAILABLE DATA:\n{context_lines}\n\nUSER MESSAGE:\n{user_message}"

    messages: list[dict] = []
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": prompt})

    try:
        msg = get_client().messages.create(
            model      = MODEL,
            max_tokens = MAX_TOKENS["summary"],
            system     = CHAT_PROMPT,
            messages   = messages,
        )
        raw_output = msg.content[0].text
    except Exception as e:
        logger.error(f"Claude chat error: {e}")
        return FALLBACK_RESPONSE

    if not raw_output:
        return FALLBACK_RESPONSE

    result = run_guardrails(
        output   = raw_output,
        config   = config,
        raw_data = context,
        mode     = "chat",
    )

    return safe_output(result)


# ── PROMPT ASSEMBLERS ─────────────────────────────────────────────────────────

def build_triangle_prompt(name: str,
                           config: LevelConfig,
                           classified: dict,
                           committees: list,
                           cycle) -> str:
    """
    classified = build_classified(sector_totals, config, defections)
      {profile, sectors, defections}
    """
    profile = classified["profile"]
    sectors = classified["sectors"]
    defects = classified["defections"]

    return get_prompt("triangle").format(
        level              = config.level,
        level_context      = config.context,
        name               = name,
        cycle              = cycle,
        contribution_limit = (
            f"${config.limits.individual:,} per individual per cycle"
            if config.limits.individual
            else "No limit — Virginia state (unlimited giving legal)"
        ),
        total_raised       = profile["total_raised"],
        total_donors       = profile["total_donors"],
        corporate_pct      = profile["corporate_pct"],
        grassroots_pct     = profile["grassroots_pct"],
        sector_lines       = format_sector_lines(sectors),
        committee_lines    = format_committee_lines(committees),
        defection_lines    = format_defection_lines(defects),
        disclaimer         = _get_disclaimer(config),
    )


def build_donor_shift_prompt(name: str,
                              config: LevelConfig,
                              donor_shift: list,
                              committees: list) -> str:
    return get_prompt("donor_shift").format(
        level           = config.level,
        level_context   = config.context,
        name            = name,
        cycle_lines     = format_cycle_lines(donor_shift),
        committee_lines = format_committee_lines(committees),
        disclaimer      = _get_disclaimer(config),
    )


def build_geography_prompt(name: str,
                            config: LevelConfig,
                            geography: list,
                            cycle) -> str:
    total      = sum(float(r[3] or 0) for r in geography)
    in_state   = sum(
        float(r[3] or 0) for r in geography if (r[4] or "") == "In State"
    )
    out_state  = sum(
        float(r[3] or 0) for r in geography if (r[4] or "") == "Out of State"
    )
    in_state_pct    = (in_state  / total * 100) if total else 0.0
    out_state_pct   = (out_state / total * 100) if total else 0.0
    out_district_pct = max(0.0, 100.0 - in_state_pct - out_state_pct)

    return get_prompt("geography").format(
        level            = config.level,
        level_context    = config.context,
        name             = name,
        cycle            = cycle,
        geography_lines  = format_geography_lines(geography),
        in_state_pct     = in_state_pct,
        out_district_pct = out_district_pct,
        out_state_pct    = out_state_pct,
        disclaimer       = _get_disclaimer(config),
    )


def build_network_prompt(sector: str,
                          config: LevelConfig,
                          network: list,
                          cycle) -> str:
    return get_prompt("network").format(
        level         = config.level,
        level_context = config.context,
        sector        = sector,
        cycle         = cycle,
        network_lines = format_network_lines(network),
        disclaimer    = _get_disclaimer(config),
    )


def build_loyalty_prompt(name: str,
                          config: LevelConfig,
                          loyalty: list,
                          minority_party: bool = False) -> str:
    return get_prompt("loyalty").format(
        level          = config.level,
        level_context  = config.context,
        name           = name,
        loyalty_lines  = format_loyalty_lines(loyalty),
        minority_party = (
            "Yes — minority party voting patterns apply"
            if minority_party else "No"
        ),
        disclaimer     = _get_disclaimer(config),
    )


def build_effectiveness_prompt(name: str,
                                config: LevelConfig,
                                effectiveness: dict,
                                subjects: list,
                                classified: dict,
                                cycle) -> str:
    subject_lines = (
        "\n".join(f"  - {row[0]}: {row[1]} bills" for row in subjects[:10])
        if subjects
        else "  No subject data available"
    )
    return get_prompt("effectiveness").format(
        level            = config.level,
        level_context    = config.context,
        name             = name,
        cycle            = cycle,
        bills_introduced = effectiveness.get("bills_introduced", 0),
        bills_passed     = effectiveness.get("bills_passed", 0),
        pass_rate        = effectiveness.get("pass_rate", 0.0),
        bipartisan_bills = effectiveness.get("bipartisan_bills", 0),
        avg_cosponsors   = effectiveness.get("avg_cosponsors", 0.0),
        subject_lines    = subject_lines,
        sector_lines     = format_sector_lines(classified["sectors"]),
        disclaimer       = _get_disclaimer(config),
    )


# ── CONTEXT SUMMARY ───────────────────────────────────────────────────────────

def _build_context_summary(context: dict) -> str:
    """
    Build plain-English data availability summary for chat context injection.

    context keys:
      member / legislator — dict with name, party, chamber, state
      cycle, has_finance, finance_sectors
      has_votes, vote_count
      has_bills, bill_count
      has_mega_donors, mega_donor_count
      has_party_money
      votes, bills, defections (raw data lists — not summarised here)
    """
    member  = context.get("member") or context.get("legislator") or {}
    name    = member.get("name", "This legislator")
    cycle   = context.get("cycle", "latest")
    chamber = member.get("chamber", "")
    party   = member.get("party", "")
    state   = member.get("state", "Virginia")

    lines = [
        f"Legislator: {name}",
        f"Party: {party}"   if party   else "",
        f"Chamber: {chamber}" if chamber else "",
        f"State: {state}",
        f"Cycle: {cycle}",
        "",
    ]

    if context.get("has_finance"):
        lines.append(
            f"✓ Campaign finance data ({context.get('finance_sectors', 0)} sectors)"
        )
    else:
        lines.append("✗ No campaign finance data")

    if context.get("has_votes"):
        lines.append(
            f"✓ Voting record ({context.get('vote_count', 0):,} votes)"
        )
    else:
        lines.append("✗ No voting record data")

    if context.get("has_bills"):
        lines.append(
            f"✓ Bill sponsorship ({context.get('bill_count', 0)} bills)"
        )
    else:
        lines.append("✗ No bill sponsorship data")

    if context.get("has_mega_donors"):
        lines.append(
            f"✓ Mega-donor data ({context.get('mega_donor_count', 0)} donors)"
        )

    if context.get("has_party_money"):
        lines.append("✓ Party committee money detected")

    return "\n".join(l for l in lines if l != "")


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _get_disclaimer(config: LevelConfig) -> str:
    return get_disclaimer(config).strip()
