from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_HERE    = Path(__file__).resolve()
BASE_DIR = _HERE.parent.parent.parent   # config/ -> voteiq/ -> project root

POLLS_DB        = str(BASE_DIR / "polls.db")
LEG_DB          = str(BASE_DIR / "legislative_intelligence.db")
FEDERAL_DB_PATH = POLLS_DB

# ── CONTRIBUTION LIMIT SCHEDULE ───────────────────────────────────────────────

FEDERAL_LIMITS_BY_CYCLE: dict[int, int] = {
    2020: 2800,
    2022: 2900,
    2024: 3300,
    2026: 3500,
}


def get_federal_limit(cycle: int) -> int:
    return FEDERAL_LIMITS_BY_CYCLE.get(cycle, 3300)


# ── TYPED CONFIG ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ContributionLimits:
    individual: Optional[int]   # None = Virginia (no cap)
    pac:        Optional[int]   # None = Virginia (no cap)


@dataclass
class LevelConfig:
    level:               str
    limits:              ContributionLimits
    context:             str
    data_source:         str
    vote_source:         str
    bill_source:         str
    cycles:              list
    db_path:             str
    grassroots_threshold: int = 200
    mega_donor:          int = 10_000
    major_donor:         int = 50_000


def get_config(level: str, cycle: int | str | None = None) -> LevelConfig:
    """
    Return a fully-populated LevelConfig for 'federal' or 'state'.
    Pass cycle to get the correct federal contribution limit.
    """
    if level == "federal":
        limit = get_federal_limit(int(cycle)) if cycle else 3300
        ctx = (
            f"FEDERAL CONTEXT:\n"
            f"- Contribution limit: ${limit:,} per individual per cycle\n"
            f"- PAC limit: $5,000 per cycle\n"
            f"- No corporate direct contributions allowed\n"
            f"- Anyone giving ${limit:,} is at the legal maximum\n"
            f"- Dark money through 501c4s not visible in this data\n"
            f"- Super PAC money is separate from individual contributions"
        )
        return LevelConfig(
            level        = "Federal",
            limits       = ContributionLimits(individual=limit, pac=5000),
            context      = ctx,
            data_source  = "FEC Schedule A individual contributions",
            vote_source  = "Congress.gov",
            bill_source  = "Congress.gov",
            cycles       = [2024, 2022, 2020, 2026],
            db_path      = POLLS_DB,
        )
    else:
        return LevelConfig(
            level        = "Virginia State",
            limits       = ContributionLimits(individual=None, pac=None),
            context      = (
                "VIRGINIA STATE CONTEXT:\n"
                "- Virginia has ZERO contribution limits\n"
                "- Individuals and corporations can give unlimited amounts\n"
                "- Party caucus committees can move millions between candidates\n"
                "- This makes Virginia one of the most donor-friendly states\n"
                "- A single donor giving $50,000+ is a major institutional donor\n"
                "- Federal donors max at $3,300; Virginia donors have no ceiling\n"
                "- Party war chests are the largest single donors in many races"
            ),
            data_source  = "Virginia SBE filings",
            vote_source  = "Virginia LIS / OpenStates",
            bill_source  = "Virginia LIS / OpenStates",
            cycles       = ["2025", "2023", "2021", "2019", "2017"],
            db_path      = LEG_DB,
        )


def get_disclaimer(config: LevelConfig) -> str:
    from voteiq.config.prompts import LEGAL_DISCLAIMER
    return LEGAL_DISCLAIMER


# ── LEGACY DICT CONFIGS (backward-compat for existing code) ───────────────────

FEDERAL_CONFIG = {
    "level":                "Federal",
    "contribution_limit":   3300,
    "grassroots_threshold": 200,
    "mixed_threshold":      1000,
    "corporate_threshold":  1000,
    "data_source":          "FEC Schedule A individual contributions",
    "vote_source":          "Congress.gov",
    "bill_source":          "Congress.gov",
    "cycles":               [2024, 2022, 2020, 2026],
    "db_path":              POLLS_DB,
    "context":              "",
}

STATE_CONFIG = {
    "level":                "Virginia State",
    "contribution_limit":   None,
    "grassroots_threshold": 200,
    "mixed_threshold":      1000,
    "corporate_threshold":  1000,
    "mega_donor":           10_000,
    "major_donor":          50_000,
    "data_source":          "Virginia SBE filings",
    "vote_source":          "Virginia LIS / OpenStates",
    "bill_source":          "Virginia LIS / OpenStates",
    "cycles":               ["2025", "2023", "2021", "2019", "2017"],
    "db_path":              LEG_DB,
    "context": (
        "\nVIRGINIA STATE CONTEXT:\n"
        "- Virginia has ZERO contribution limits\n"
        "- Individuals and corporations can give unlimited amounts\n"
        "- Party caucus committees can move millions between candidates\n"
        "- This makes Virginia one of the most donor-friendly states in America\n"
        "- A single donor giving $50,000+ is a major institutional donor\n"
        "- Compare carefully: federal donors max at $3,300; Virginia donors have no ceiling\n"
        "- Party war chests (House Democratic Caucus, etc.)\n"
        "  are the largest single donors in many races\n"
    ),
}
