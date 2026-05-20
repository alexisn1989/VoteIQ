# config/finance_rules.py
# Central configuration for federal and Virginia state
# campaign finance rules, thresholds, and context.
# All analyst modules import from here.

from dataclasses import dataclass, field
from typing import Optional


# ── CONTRIBUTION THRESHOLDS ───────────────────────────────────────────────────

@dataclass
class DonorThresholds:
    """
    Donation size thresholds for concentration classification.
    Federal and state use different values due to contribution limits.
    """
    small_dollar:       float   # avg < this = Small-dollar base
    mixed_lower:        float   # avg >= this = Mixed donor base
    industry_linked:    float   # avg >= this = Industry-linked money
    large_donor:        float   # avg >= this = Large-donor money
    mega_donor:         float   # avg >= this = Mega-donor money
    major_institutional: float  # avg >= this = Major institutional money


FEDERAL_THRESHOLDS = DonorThresholds(
    small_dollar        = 200,
    mixed_lower         = 200,
    industry_linked     = 1_000,
    large_donor         = 2_000,
    mega_donor          = 3_300,    # at/near FEC individual limit
    major_institutional = 3_300,
)

VIRGINIA_THRESHOLDS = DonorThresholds(
    small_dollar        = 200,
    mixed_lower         = 200,
    industry_linked     = 1_000,
    large_donor         = 5_000,
    mega_donor          = 10_000,   # Virginia-specific — no legal cap
    major_institutional = 50_000,   # Party committees, large entities
)


# ── CONTRIBUTION LIMITS ───────────────────────────────────────────────────────

@dataclass
class ContributionLimits:
    """
    Legal contribution limits per cycle.
    None means no limit exists in law.
    """
    individual:         Optional[float]
    pac:                Optional[float]
    party_committee:    Optional[float]
    corporate_direct:   Optional[float]
    note:               str


FEDERAL_LIMITS = ContributionLimits(
    individual          = 3_300,
    pac                 = 5_000,
    party_committee     = 41_300,
    corporate_direct    = None,     # prohibited entirely
    note = (
        "Federal law prohibits direct corporate contributions. "
        "Individual limit is $3,300 per candidate per election. "
        "A donor at $3,300 has reached the legal maximum. "
        "Super PAC and 501(c)(4) spending not reflected in "
        "Schedule A data."
    )
)

VIRGINIA_LIMITS = ContributionLimits(
    individual          = None,     # no limit
    pac                 = None,     # no limit
    party_committee     = None,     # no limit
    corporate_direct    = None,     # no limit — corporations may give
    note = (
        "Virginia has no contribution limits for state candidates. "
        "Individuals, corporations, PACs, unions, and party committees "
        "may give unlimited amounts. "
        "Virginia is one of the least restrictive campaign finance "
        "environments in the United States. "
        "Large donations are legal under Virginia law."
    )
)


# ── DONOR CONCENTRATION LABELS ────────────────────────────────────────────────

# Maps internal classification keys to
# public-facing plain-English labels.
# These labels are legally safer than
# 'Corporate' or 'Grassroots'.

DONOR_LABELS = {
    "small_dollar":       "Small-dollar base",
    "mixed":              "Mixed donor base",
    "industry_linked":    "Industry-linked money",
    "large_donor":        "Large-donor money",
    "mega_donor":         "Mega-donor money",
    "major_institutional":"Major institutional money",
    "near_max":           "Near-max donor money",    # federal only
    "party_committee":    "Party committee money",
    "unclassified":       "Unclassified donors",
}

DONOR_ICONS = {
    "small_dollar":        "🟢",
    "mixed":               "🟡",
    "industry_linked":     "🔴",
    "large_donor":         "🔴",
    "mega_donor":          "🔴",
    "major_institutional": "⚫",
    "near_max":            "🔴",
    "party_committee":     "⚫",
    "unclassified":        "⬜",
}


# ── INDUSTRY SECTORS ──────────────────────────────────────────────────────────

# Canonical sector list used across
# both federal and state classification.

SECTORS = [
    "Defense",
    "Finance",
    "Real Estate",
    "Healthcare",
    "Legal",
    "Energy",
    "Technology",
    "Education",
    "Agriculture",
    "Transportation",
    "Labor/Union",
    "Hospitality",
    "Manufacturing",
    "Retail",
    "Ideological",
    "Party Committee",
    "Retired",
    "Self-Employed",
    "Homemaker",
    "Other",
]

# Employer keyword → sector mapping
# Used by classify.py for both FEC and VA SBE data
EMPLOYER_SECTOR_MAP = {
    # Defense
    "huntington ingalls":           "Defense",
    "huntington ingalls industries":"Defense",
    "hii":                          "Defense",
    "booz allen":                   "Defense",
    "booz allen hamilton":          "Defense",
    "raytheon":                     "Defense",
    "lockheed":                     "Defense",
    "lockheed martin":              "Defense",
    "general dynamics":             "Defense",
    "northrop":                     "Defense",
    "northrop grumman":             "Defense",
    "leidos":                       "Defense",
    "saic":                         "Defense",
    "bae systems":                  "Defense",
    "l3harris":                     "Defense",
    "harris corporation":           "Defense",
    "mantech":                      "Defense",
    "caci":                         "Defense",

    # Finance
    "bank of america":              "Finance",
    "wells fargo":                  "Finance",
    "jpmorgan":                     "Finance",
    "jp morgan":                    "Finance",
    "goldman sachs":                "Finance",
    "morgan stanley":               "Finance",
    "navy federal":                 "Finance",
    "usaa":                         "Finance",
    "capital one":                  "Finance",
    "truist":                       "Finance",
    "pnc":                          "Finance",
    "insurance":                    "Finance",
    "financial":                    "Finance",
    "investment":                   "Finance",
    "securities":                   "Finance",
    "asset management":             "Finance",
    "hedge fund":                   "Finance",
    "private equity":               "Finance",
    "venture capital":              "Finance",

    # Real Estate
    "real estate":                  "Real Estate",
    "realty":                       "Real Estate",
    "properties":                   "Real Estate",
    "development":                  "Real Estate",
    "developer":                    "Real Estate",
    "homebuilder":                  "Real Estate",
    "mortgage":                     "Real Estate",
    "title company":                "Real Estate",
    "property management":          "Real Estate",

    # Healthcare
    "sentara":                      "Healthcare",
    "bon secours":                  "Healthcare",
    "vcu health":                   "Healthcare",
    "childrens hospital":           "Healthcare",
    "hospital":                     "Healthcare",
    "health system":                "Healthcare",
    "medical":                      "Healthcare",
    "physician":                    "Healthcare",
    "doctor":                       "Healthcare",
    "dentist":                      "Healthcare",
    "pharmacy":                     "Healthcare",
    "pharma":                       "Healthcare",
    "biotech":                      "Healthcare",

    # Legal
    "attorney":                     "Legal",
    "lawyer":                       "Legal",
    "law firm":                     "Legal",
    "law office":                   "Legal",
    "esquire":                      "Legal",
    "counsel":                      "Legal",
    "legal":                        "Legal",

    # Energy
    "dominion":                     "Energy",
    "dominion energy":              "Energy",
    "appalachian power":            "Energy",
    "exxon":                        "Energy",
    "chevron":                      "Energy",
    "bp ":                          "Energy",
    "shell":                        "Energy",
    "solar":                        "Energy",
    "wind energy":                  "Energy",
    "utility":                      "Energy",
    "electric":                     "Energy",
    "pipeline":                     "Energy",
    "oil":                          "Energy",
    "gas company":                  "Energy",

    # Technology
    "amazon":                       "Technology",
    "google":                       "Technology",
    "microsoft":                    "Technology",
    "oracle":                       "Technology",
    "ibm":                          "Technology",
    "meta":                         "Technology",
    "apple":                        "Technology",
    "software":                     "Technology",
    "technology":                   "Technology",
    "tech ":                        "Technology",
    "cyber":                        "Technology",
    "data ":                        "Technology",

    # Education
    "university":                   "Education",
    "college":                      "Education",
    "school":                       "Education",
    "professor":                    "Education",
    "teacher":                      "Education",
    "faculty":                      "Education",
    "superintendent":               "Education",
    "principal":                    "Education",

    # Agriculture
    "farm":                         "Agriculture",
    "farming":                      "Agriculture",
    "agriculture":                  "Agriculture",
    "agribusiness":                 "Agriculture",
    "crop":                         "Agriculture",
    "livestock":                    "Agriculture",

    # Transportation
    "airline":                      "Transportation",
    "aviation":                     "Transportation",
    "trucking":                     "Transportation",
    "logistics":                    "Transportation",
    "shipping":                     "Transportation",
    "railroad":                     "Transportation",
    "transit":                      "Transportation",
    "port ":                        "Transportation",

    # Labor/Union
    "union":                        "Labor/Union",
    "afl-cio":                      "Labor/Union",
    "teamsters":                    "Labor/Union",
    "seiu":                         "Labor/Union",
    "ufcw":                         "Labor/Union",
    "ibew":                         "Labor/Union",
    "labor":                        "Labor/Union",

    # Hospitality
    "hotel":                        "Hospitality",
    "restaurant":                   "Hospitality",
    "hospitality":                  "Hospitality",
    "resort":                       "Hospitality",
    "casino":                       "Hospitality",
    "tourism":                      "Hospitality",

    # Manufacturing
    "manufacturing":                "Manufacturing",
    "factory":                      "Manufacturing",
    "industrial":                   "Manufacturing",
    "steel":                        "Manufacturing",
    "auto":                         "Manufacturing",
    "automotive":                   "Manufacturing",

    # Retail
    "retail":                       "Retail",
    "store":                        "Retail",
    "shop":                         "Retail",
    "walmart":                      "Retail",
    "target":                       "Retail",

    # Retired / Self
    "retired":                      "Retired",
    "self-employed":                "Self-Employed",
    "self employed":                "Self-Employed",
    "consultant":                   "Self-Employed",
    "homemaker":                    "Homemaker",
    "housewife":                    "Homemaker",
    "none":                         "Other",
    "n/a":                          "Other",
}


# ── LEVEL CONFIGURATIONS ──────────────────────────────────────────────────────

@dataclass
class LevelConfig:
    """
    Complete configuration for a government level.
    Imported by all analyst modules.
    """
    level:          str
    thresholds:     DonorThresholds
    limits:         ContributionLimits
    data_source:    str
    vote_source:    str
    bill_source:    str
    db_type:        str             # "postgres" or "sqlite"
    db_path:        Optional[str]   # sqlite path if applicable
    cycle_format:   str             # "int" or "str"
    context:        str             # injected into Claude prompt


FEDERAL_CONFIG = LevelConfig(
    level           = "Federal",
    thresholds      = FEDERAL_THRESHOLDS,
    limits          = FEDERAL_LIMITS,
    data_source     = "FEC Schedule A filings",
    vote_source     = "Congress.gov / clerk.house.gov",
    bill_source     = "Congress.gov",
    db_type         = "sqlite",
    db_path         = "polls.db",
    cycle_format    = "int",
    context         = f"""
FEDERAL CONTEXT:
{FEDERAL_LIMITS.note}
Individual donor sector totals reflect the industries
where donors are employed — not direct corporate contributions.
Use: 'industry-linked individual donations' not 'corporate donations.'
Anyone giving ${FEDERAL_THRESHOLDS.mega_donor:,.0f} has reached
the individual legal maximum for this cycle.
""",
)

VIRGINIA_CONFIG = LevelConfig(
    level           = "Virginia State",
    thresholds      = VIRGINIA_THRESHOLDS,
    limits          = VIRGINIA_LIMITS,
    data_source     = "Virginia SBE filings 1999–present",
    vote_source     = "Virginia LIS",
    bill_source     = "Virginia LIS",
    db_type         = "sqlite",
    db_path         = "legislative_intelligence.db",
    cycle_format    = "str",
    context         = f"""
VIRGINIA STATE CONTEXT:
{VIRGINIA_LIMITS.note}
Donors giving ${VIRGINIA_THRESHOLDS.mega_donor:,.0f}+
are classified as Mega-donor money.
Donors giving ${VIRGINIA_THRESHOLDS.major_institutional:,.0f}+
are classified as Major institutional money.
Party caucus committees (e.g. Virginia House Democratic Caucus)
are the largest single donors in many Virginia races —
this is legal party targeting activity, not improper influence.
OccupationOrTypeOfBusiness and NameOfEmployer fields
from SBE filings are used for sector classification.
""",
)


# ── CYCLE-SPECIFIC FEDERAL LIMITS ────────────────────────────────────────────

FEDERAL_LIMITS_BY_CYCLE: dict[int, int] = {
    2024: 3_300,
    2026: 3_500,
}


def get_federal_limit(cycle: int) -> int:
    """Return the per-candidate individual contribution limit for a given cycle."""
    return FEDERAL_LIMITS_BY_CYCLE.get(cycle, 3_300)


# ── CONFIG ACCESSOR ───────────────────────────────────────────────────────────

def get_config(level: str) -> LevelConfig:
    """
    Return the correct config for a given level.

    Usage:
        config = get_config("federal")
        config = get_config("state")
    """
    level = level.lower().strip()

    if level in ("federal", "fed", "us", "congress"):
        return FEDERAL_CONFIG

    if level in ("state", "virginia", "va"):
        return VIRGINIA_CONFIG

    raise ValueError(
        f"Unknown level '{level}'. "
        f"Use 'federal' or 'state'."
    )


# ── GUARDRAIL CONSTANTS ───────────────────────────────────────────────────────

# Forbidden language — never appears in Claude output
# Enforced by guardrails.py

FORBIDDEN_TERMS = [
    "bought",
    "controlled",
    "owned",
    "payoff",
    "corrupt",
    "corruption",
    "bribery",
    "quid pro quo",
    "dark money politician",
    "pay to play",
    "bought and paid",
    "in the pocket",
    "on the payroll",
]

# Required phrases — must appear in every report
REQUIRED_PHRASES = [
    "correlation does not imply causation",
]

# Standard disclaimer appended to every report
OUTPUT_DISCLAIMER = """
---
Correlation does not imply causation.
VoteIQ displays publicly available data only.
All findings reflect statistical patterns in public records.
No claim of improper influence, motive, or intent is made.
Data: {data_source} | {vote_source}
Corrections: alexisnieuwenhuys89@gmail.com
"""


def get_disclaimer(config: LevelConfig) -> str:
    """Return formatted disclaimer for a given config."""
    return OUTPUT_DISCLAIMER.format(
        data_source = config.data_source,
        vote_source = config.vote_source,
    )
