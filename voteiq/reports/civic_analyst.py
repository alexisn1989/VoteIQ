# reports/civic_analyst.py
# The run_analyst() function — coordinates everything.
# Single entry point for all VoteIQ analyst reports.
# Called by API routes and scripts.

import logging
from config.finance_rules import (
    get_config,
    LevelConfig,
)
from db import federal, virginia
from analysis.classify import classify_all
from llm.claude_client import (
    generate_report,
    generate_chat_response,
    build_triangle_prompt,
    build_donor_shift_prompt,
    build_geography_prompt,
    build_network_prompt,
    build_loyalty_prompt,
    build_effectiveness_prompt,
)

logger = logging.getLogger(__name__)


# ── ANALYST TYPES ─────────────────────────────────────────────────────────────

MEMBER_ANALYST_TYPES = [
    "triangle",
    "donor_shift",
    "geography",
    "loyalty",
    "effectiveness",
]

NETWORK_ANALYST_TYPES = ["network"]


# ── DATA LOADER ───────────────────────────────────────────────────────────────

def load_legislator_data(member_id: str,
                          level: str,
                          cycle,
                          analyst_type: str) -> dict:
    """
    Load all raw data needed for a given
    analyst type and level.

    Returns raw_data dict passed to
    classify_all() and generate_report().
    """
    db = federal if level == "federal" else virginia

    # Always load sector totals
    raw_data = {
        "sector_totals": db.get_sector_totals(
                            member_id, cycle
                         ),
        "votes":         [],
        "bills":         [],
        "defections":    [],
        "mega_donors":   [],
        "donor_shift":   [],
        "geography":     [],
        "loyalty":       [],
        "effectiveness": {},
        "subjects":      [],
    }

    # Load additional data per analyst type
    if analyst_type == "triangle":
        raw_data["votes"]      = db.get_votes(
                                    member_id
                                 )
        raw_data["bills"]      = db.get_sponsored_bills(
                                    member_id
                                 )
        raw_data["defections"] = db.get_party_defections(
                                    member_id
                                 )

        # Mega donors for state level only
        if level == "state":
            raw_data["mega_donors"] = (
                virginia.get_mega_donors(member_id, cycle)
            )

    elif analyst_type == "donor_shift":
        raw_data["donor_shift"] = db.get_donor_shift(
                                     member_id
                                  )

    elif analyst_type == "geography":
        raw_data["geography"] = db.get_donor_geography(
                                   member_id, cycle
                                )

    elif analyst_type == "loyalty":
        raw_data["loyalty"] = db.get_loyalty_scores(
                                 member_id
                              )
        raw_data["votes"]   = db.get_votes(member_id)

    elif analyst_type == "effectiveness":
        raw_data["bills"]         = db.get_sponsored_bills(
                                       member_id
                                    )
        raw_data["effectiveness"] = db.get_effectiveness_stats(
                                       member_id
                                    )
        raw_data["subjects"]      = db.get_bill_subjects(
                                       member_id
                                    ) if level == "federal" \
                                    else []

    return raw_data


# ── MEMBER LOOKUP ─────────────────────────────────────────────────────────────

def lookup_member(member_id: str,
                   level: str) -> dict:
    """
    Look up member by ID.
    Returns member dict or empty dict if not found.
    """
    db = federal if level == "federal" else virginia

    if level == "federal":
        member = db.get_member(member_id)
    else:
        member = db.get_legislator(member_id)

    return member or {}


def lookup_member_by_name(name: str,
                           level: str) -> dict:
    """
    Look up member by name (fuzzy).
    Returns member dict or empty dict if not found.
    """
    db = federal if level == "federal" else virginia

    if level == "federal":
        member = db.get_member_by_name(name)
    else:
        member = db.get_legislator_by_name(name)

    return member or {}


# ── COMMITTEE LOADER ──────────────────────────────────────────────────────────

# Committee assignments are not yet in the DB.
# Stored here as a lookup until Legistar
# and Congress.gov committee endpoints are wired.

FEDERAL_COMMITTEES = {
    "K000400": [
        {"committee": "Armed Services",    "role": "Member",    "source": "House.gov", "as_of": "2026-05-18"},
        {"committee": "Natural Resources", "role": "Member",    "source": "House.gov", "as_of": "2026-05-18"},
        {"committee": "Veterans' Affairs", "role": "Chairwoman (Oversight & Investigations)", "source": "House.gov", "as_of": "2026-05-18"},
    ],
    "S000185": [
        {"committee": "Education and the Workforce",    "role": "Member", "source": "House.gov", "as_of": "2026-05-18"},
        {"committee": "Transportation and Infrastructure", "role": "Member", "source": "House.gov", "as_of": "2026-05-18"},
    ],
    "W000804": [
        {"committee": "Armed Services",    "role": "Member", "source": "House.gov", "as_of": "2026-05-18"},
        {"committee": "Natural Resources", "role": "Member", "source": "House.gov", "as_of": "2026-05-18"},
    ],
    "W000805": [
        {"committee": "Banking, Housing, and Urban Affairs", "role": "Member",       "source": "Senate.gov", "as_of": "2026-05-18"},
        {"committee": "Finance",                             "role": "Member",       "source": "Senate.gov", "as_of": "2026-05-18"},
        {"committee": "Rules and Administration",            "role": "Member",       "source": "Senate.gov", "as_of": "2026-05-18"},
        {"committee": "Senate Select Committee on Intelligence", "role": "Vice Chairman", "source": "Senate.gov", "as_of": "2026-05-18"},
        {"committee": "Budget",                              "role": "Member",       "source": "Senate.gov", "as_of": "2026-05-18"},
    ],
    "K000384": [
        {"committee": "Armed Services",    "role": "Member", "source": "Senate.gov", "as_of": "2026-05-18"},
        {"committee": "Budget",            "role": "Member", "source": "Senate.gov", "as_of": "2026-05-18"},
        {"committee": "Foreign Relations", "role": "Member", "source": "Senate.gov", "as_of": "2026-05-18"},
        {"committee": "Judiciary",         "role": "Member", "source": "Senate.gov", "as_of": "2026-05-18"},
    ],
    "M001220": [
        {"committee": "Agriculture",                "role": "Member", "source": "House.gov", "as_of": "2026-05-18"},
        {"committee": "Science, Space, and Technology", "role": "Member", "source": "House.gov", "as_of": "2026-05-18"},
    ],
    "B001292": [
        {"committee": "Ways and Means",             "role": "Member", "source": "House.gov", "as_of": "2026-05-18"},
        {"committee": "Science, Space, and Technology", "role": "Member", "source": "House.gov", "as_of": "2026-05-18"},
    ],
    "G000568": [
        {"committee": "Energy and Commerce", "role": "Member", "source": "House.gov", "as_of": "2026-05-18"},
    ],
    "G000591": [
        {"committee": "Budget",                      "role": "Member", "source": "House.gov", "as_of": "2026-05-18"},
        {"committee": "Education and the Workforce", "role": "Member", "source": "House.gov", "as_of": "2026-05-18"},
        {"committee": "Rules",                       "role": "Member", "source": "House.gov", "as_of": "2026-05-18"},
    ],
    "C001118": [
        {"committee": "Appropriations", "role": "Member", "source": "House.gov", "as_of": "2026-05-18"},
    ],
}


def get_committees(member_id: str,
                   level: str,
                   cycle) -> list:
    """
    Return committee assignments for a member.
    Federal: uses FEDERAL_COMMITTEES lookup.
    State: returns empty list until
           Legistar integration is live.
    """
    if level == "federal":
        return FEDERAL_COMMITTEES.get(member_id, [])
    return []


# ── MINORITY PARTY DETECTOR ───────────────────────────────────────────────────

# Keyed by (level, congress_number, chamber).
# Update each new congress.
MINORITY_PARTIES_BY_CONTEXT = {
    ("federal", 119, "house"):  {"D"},
    ("federal", 119, "senate"): {"D"},
}


def is_minority_party(member: dict,
                       level: str) -> bool:
    """
    Detect if member is in minority party.
    Used by loyalty analyst for context.
    """
    party   = (member.get("party") or "").strip()
    congress = member.get("congress", 119)
    chamber  = (member.get("chamber") or "").lower()
    key = (level, congress, chamber)
    return party in MINORITY_PARTIES_BY_CONTEXT.get(key, set())


# ── NO DATA RESPONSE ──────────────────────────────────────────────────────────

def no_data_response(name: str,
                      level: str,
                      cycle) -> str:
    """
    Return a clean response when no data
    is available for a member.
    """
    return (
        f"No {level} campaign finance data found "
        f"for {name} in the {cycle} cycle. "
        f"This may be a data gap in the current "
        f"pipeline. Try a different cycle or "
        f"check back after the next data update.\n\n"
        f"For assistance: hello@voteiq.io\n\n"
        f"---\n"
        f"Correlation does not imply causation.\n"
        f"VoteIQ displays publicly available "
        f"data only."
    )


# ── MAIN ANALYST RUNNER ───────────────────────────────────────────────────────

def run_analyst(name: str,
                member_id: str,
                level: str        = "federal",
                analyst_type: str = "triangle",
                cycle             = None) -> dict:
    """
    Main entry point for all VoteIQ analyst reports.

    Coordinates:
    1. get_config()          — level rules
    2. lookup_member()       — member validation
    3. load_legislator_data()— raw DB data
    4. classify_all()        — labeled sectors
    5. build_*_prompt()      — Claude prompt
    6. generate_report()     — Claude output
                               + guardrails

    Returns dict:
    {
        "status":   "success" | "no_data" | "error"
        "report":   str
        "profile":  dict
        "member":   dict
        "config":   str
        "analyst":  str
        "cycle":    any
    }

    Usage:
        result = run_analyst(
            name         = "Jennifer Kiggans",
            member_id    = "K000400",
            level        = "federal",
            analyst_type = "triangle",
            cycle        = 2024,
        )

        if result["status"] == "success":
            print(result["report"])
    """
    logger.info(
        f"Running {analyst_type} analyst "
        f"for {name} ({level}, cycle {cycle})"
    )

    # 1. Get config
    try:
        config = get_config(level)
    except ValueError as e:
        logger.error(f"Config error: {e}")
        return {
            "status":  "error",
            "report":  str(e),
            "profile": {},
            "member":  {},
            "config":  level,
            "analyst": analyst_type,
            "cycle":   cycle,
        }

    # Default cycle per level
    if cycle is None:
        cycle = 2024 if level == "federal" else "2023"

    # 2. Validate analyst type
    if analyst_type not in MEMBER_ANALYST_TYPES:
        return {
            "status":  "error",
            "report":  (
                f"Unknown analyst type '{analyst_type}'. "
                f"Available: {MEMBER_ANALYST_TYPES}"
            ),
            "profile": {},
            "member":  {},
            "config":  config.level,
            "analyst": analyst_type,
            "cycle":   cycle,
        }

    # 3. Look up member
    member = lookup_member(member_id, level)
    if not member:
        member = {"name": name}

    # 4. Load raw data
    try:
        raw_data = load_legislator_data(
            member_id    = member_id,
            level        = level,
            cycle        = cycle,
            analyst_type = analyst_type,
        )
    except Exception as e:
        logger.error(f"Data load error: {e}")
        return {
            "status":  "error",
            "report":  f"Data load failed: {e}",
            "profile": {},
            "member":  member,
            "config":  config.level,
            "analyst": analyst_type,
            "cycle":   cycle,
        }

    # 5. Check data availability
    if not raw_data.get("sector_totals"):
        logger.warning(
            f"No finance data for {name} "
            f"({level}, cycle {cycle})"
        )
        return {
            "status":  "no_data",
            "report":  no_data_response(
                           name, level, cycle
                       ),
            "profile": {},
            "member":  member,
            "config":  config.level,
            "analyst": analyst_type,
            "cycle":   cycle,
        }

    # 6. Classify all data
    classified = classify_all(raw_data, config)

    # 7. Get committee assignments
    committees = get_committees(member_id, level, cycle)

    # 8. Build prompt per analyst type
    try:
        if analyst_type == "triangle":
            prompt = build_triangle_prompt(
                name       = name,
                config     = config,
                classified = classified,
                committees = committees,
                cycle      = cycle,
            )
            tier = "full"

        elif analyst_type == "donor_shift":
            prompt = build_donor_shift_prompt(
                name         = name,
                config       = config,
                donor_shift  = raw_data["donor_shift"],
                committees   = committees,
            )
            tier = "shift"

        elif analyst_type == "geography":
            prompt = build_geography_prompt(
                name      = name,
                config    = config,
                geography = raw_data["geography"],
                cycle     = cycle,
            )
            tier = "analysis"

        elif analyst_type == "loyalty":
            minority = is_minority_party(member, level)
            prompt = build_loyalty_prompt(
                name           = name,
                config         = config,
                loyalty        = raw_data["loyalty"],
                minority_party = minority,
            )
            tier = "analysis"

        elif analyst_type == "effectiveness":
            prompt = build_effectiveness_prompt(
                name            = name,
                config          = config,
                effectiveness   = raw_data["effectiveness"],
                subjects        = raw_data["subjects"],
                classified      = classified,
                cycle           = cycle,
            )
            tier = "analysis"

        else:
            # Network analyst handled separately
            # via run_network_analyst()
            raise ValueError(
                f"Analyst type '{analyst_type}' "
                f"requires run_network_analyst()"
            )

    except Exception as e:
        logger.error(f"Prompt build error: {e}")
        return {
            "status":  "error",
            "report":  f"Prompt build failed: {e}",
            "profile": classified.get("profile", {}),
            "member":  member,
            "config":  config.level,
            "analyst": analyst_type,
            "cycle":   cycle,
        }

    # 9. Generate report
    report = generate_report(
        prompt   = prompt,
        config   = config,
        raw_data = raw_data,
        tier     = tier,
    )

    logger.info(
        f"Report generated for {name} "
        f"({analyst_type})"
    )

    return {
        "status":  "success",
        "report":  report,
        "profile": classified.get("profile", {}),
        "member":  member,
        "config":  config.level,
        "analyst": analyst_type,
        "cycle":   cycle,
    }


# ── NETWORK ANALYST ───────────────────────────────────────────────────────────

def run_network_analyst(sector: str,
                         level: str  = "state",
                         cycle       = None) -> dict:
    """
    Industry network analyst.
    Shows all legislators ranked by sector funding.
    Separate from run_analyst() — no member_id needed.

    Usage:
        result = run_network_analyst(
            sector = "Real Estate",
            level  = "state",
            cycle  = "2023",
        )
    """
    config = get_config(level)

    if cycle is None:
        cycle = 2024 if level == "federal" else "2023"

    db = federal if level == "federal" else virginia

    # Load network data
    network = db.get_industry_network(sector, cycle)

    if not network:
        return {
            "status":  "no_data",
            "report":  (
                f"No {sector} sector data found "
                f"for {level} level, cycle {cycle}."
            ),
            "sector":  sector,
            "config":  config.level,
            "cycle":   cycle,
        }

    # Build and generate
    prompt = build_network_prompt(
        sector  = sector,
        config  = config,
        network = network,
        cycle   = cycle,
    )

    raw_data = {
        "sector_totals": network,
        "network":       network,
        "votes":         None,
        "bills":         None,
    }

    report = generate_report(
        prompt   = prompt,
        config   = config,
        raw_data = raw_data,
        tier     = "network",
    )

    return {
        "status":  "success",
        "report":  report,
        "sector":  sector,
        "config":  config.level,
        "cycle":   cycle,
    }


# ── CHAT HANDLER ──────────────────────────────────────────────────────────────

def handle_chat(user_message: str,
                member_id: str    = None,
                level: str        = "federal",
                cycle             = None,
                history: list     = None) -> str:
    """
    Handle a conversational chat message.
    Returns short response by default.
    Full analyst fires only on explicit trigger.

    Usage:
        response = handle_chat(
            user_message = "Tell me about Kiggans",
            member_id    = "K000400",
            level        = "federal",
        )
    """
    config = get_config(level)

    if cycle is None:
        cycle = 2024 if level == "federal" else "2023"

    # Build context from available data
    if member_id:
        db      = federal if level == "federal" \
                  else virginia
        context = db.check_data_available(
                      member_id, cycle
                  )
    else:
        context = {}

    # Check for full report trigger phrases
    FULL_REPORT_TRIGGERS = [
        "show me the full report",
        "run the analyst",
        "give me the analysis",
        "full breakdown",
        "show me the data",
        "full report",
        "run analysis",
        "complete report",
        "full analysis",
    ]

    message_lower = user_message.lower()
    triggered = any(
        trigger in message_lower
        for trigger in FULL_REPORT_TRIGGERS
    )

    if triggered and member_id:
        # Fire full triangle analyst
        member = lookup_member(member_id, level)
        name   = (
            member.get("name")
            or context.get("legislator", {}).get("name")
            or "This legislator"
        )
        result = run_analyst(
            name         = name,
            member_id    = member_id,
            level        = level,
            analyst_type = "triangle",
            cycle        = cycle,
        )
        return result["report"]

    # Default — short conversational response
    return generate_chat_response(
        user_message = user_message,
        context      = context,
        config       = config,
        history      = history,
    )
