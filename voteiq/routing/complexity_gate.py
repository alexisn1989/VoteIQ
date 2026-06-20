"""
VoteIQ Pro Tier — Query Complexity Gate
Routes queries to claude-sonnet-4-6 (default) or claude-opus-4-6 (escalation)
based on detected query complexity.

Place this module at: voteiq/routing/complexity_gate.py

Usage:
    from voteiq.routing.complexity_gate import get_model_for_query
    model = get_model_for_query(query, retrieved_sources)
"""

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Model constants — update here if model strings change
# ---------------------------------------------------------------------------

MODEL_DEFAULT = "claude-sonnet-4-6"       # Pro default (~95% of queries)
MODEL_ESCALATE = "claude-opus-4-6"        # Complex analysis escalation (~5%)


# ---------------------------------------------------------------------------
# Complexity signals
# ---------------------------------------------------------------------------

# Keywords that indicate cross-session trend analysis
CROSS_SESSION_PATTERNS = [
    r"\b(over|across|between|compare|trend|history|since|through)\b.{0,40}\b(session|year|term)\b",
    r"\b(20\d{2}).{0,20}(20\d{2})\b",       # two year references in one query
    r"\b(last|past|previous)\s+\d+\s+(session|year)s?\b",
]

# Keywords that indicate multi-legislator comparison
MULTI_LEGISLATOR_PATTERNS = [
    r"\b(compare|versus|vs\.?|between|both|all|each)\b.{0,60}\b(senator|delegate|legislator|member|representative)\b",
    r"\b(how\s+do|how\s+did)\b.{0,40}\b(they|them|legislators|members)\b",
]

# Keywords that indicate cross-domain synthesis
CROSS_DOMAIN_PATTERNS = [
    r"\b(donor|donat|contribution|campaign\s+finance|PAC|money|fund)\b.{0,80}\b(vote|voted|bill|legislat)\b",
    r"\b(lobbyist|lobbying)\b.{0,80}\b(vote|voted|bill|contribution|donat)\b",
    r"\b(vote|voted)\b.{0,80}\b(donor|donat|contribution|PAC|money)\b",
]

# Keywords that indicate multi-bill analysis
MULTI_BILL_PATTERNS = [
    r"\b(bills?|legislation|measures?)\b.{0,30}\b(both|all|multiple|several|compare|related)\b",
    r"\bHB\d+.{0,30}HB\d+\b",              # two bill numbers in one query
    r"\bSB\d+.{0,30}SB\d+\b",
    r"\bHB\d+.{0,30}SB\d+\b",
]


# ---------------------------------------------------------------------------
# Source threshold
# ---------------------------------------------------------------------------

# Escalate if query requires synthesis across this many retrieved sources
SOURCE_ESCALATION_THRESHOLD = 4


# ---------------------------------------------------------------------------
# Dataclass for routing decision
# ---------------------------------------------------------------------------

@dataclass
class RoutingDecision:
    model: str
    reason: str
    escalated: bool
    signals: list[str]


# ---------------------------------------------------------------------------
# Core routing function
# ---------------------------------------------------------------------------

def get_model_for_query(
    query: str,
    retrieved_source_count: int = 0,
    legislator_count: int = 0,
) -> RoutingDecision:
    """
    Determine which model to use for a VoteIQ Pro query.

    Args:
        query:                  Raw user query string
        retrieved_source_count: Number of distinct source types retrieved
                                (votes, CF, lobbying, fiscal, etc.)
        legislator_count:       Number of distinct legislators detected
                                in the query or retrieved context

    Returns:
        RoutingDecision with model string, reason, and matched signals
    """
    query_lower = query.lower()
    signals = []

    # --- Signal 1: Multi-legislator comparison ---
    if legislator_count >= 3:
        signals.append(f"multi_legislator: {legislator_count} legislators detected")

    if not signals or legislator_count < 3:
        for pattern in MULTI_LEGISLATOR_PATTERNS:
            if re.search(pattern, query_lower):
                signals.append(f"multi_legislator_language: '{pattern}'")
                break

    # --- Signal 2: Cross-session trend ---
    for pattern in CROSS_SESSION_PATTERNS:
        if re.search(pattern, query_lower):
            signals.append(f"cross_session: '{pattern}'")
            break

    # --- Signal 3: Cross-domain synthesis ---
    for pattern in CROSS_DOMAIN_PATTERNS:
        if re.search(pattern, query_lower):
            signals.append(f"cross_domain: '{pattern}'")
            break

    # --- Signal 4: Multi-bill analysis ---
    for pattern in MULTI_BILL_PATTERNS:
        if re.search(pattern, query_lower):
            signals.append(f"multi_bill: '{pattern}'")
            break

    # --- Signal 5: Source count threshold ---
    if retrieved_source_count >= SOURCE_ESCALATION_THRESHOLD:
        signals.append(f"source_count: {retrieved_source_count} sources")

    # --- Route decision ---
    if signals:
        return RoutingDecision(
            model=MODEL_ESCALATE,
            reason=f"Escalated to Opus: {len(signals)} complexity signal(s) detected",
            escalated=True,
            signals=signals,
        )

    return RoutingDecision(
        model=MODEL_DEFAULT,
        reason="Routed to Sonnet: no complexity signals detected",
        escalated=False,
        signals=[],
    )


# ---------------------------------------------------------------------------
# Source type counter helper
# ---------------------------------------------------------------------------

def count_retrieved_source_types(retrieved_context: dict) -> int:
    """
    Count distinct source types present in the retrieved context.
    Each key below counts as one source type.

    Args:
        retrieved_context: Dict of retrieved data keyed by source type

    Returns:
        Integer count of distinct source types present
    """
    SOURCE_KEYS = [
        "votes",
        "campaign_finance",
        "lobbying",
        "fiscal_notes",
        "bill_text",
        "governor_actions",
        "committee_actions",
        "legislator_profiles",
    ]
    return sum(1 for key in SOURCE_KEYS if retrieved_context.get(key))


# ---------------------------------------------------------------------------
# Legislator count helper
# ---------------------------------------------------------------------------

def count_legislators_in_query(query: str, name_index: list[str]) -> int:
    """
    Count how many known legislator names appear in the query.

    Args:
        query:      Raw user query string
        name_index: List of canonical legislator name strings
                    from va_name_index table

    Returns:
        Integer count of matched legislator names
    """
    query_lower = query.lower()
    matched = [
        name for name in name_index
        if name.lower() in query_lower
    ]
    return len(matched)


# ---------------------------------------------------------------------------
# Integration example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Example usage — replace with real retrieved context and name index

    test_queries = [
        # Should route to Sonnet
        "How did Louise Lucas vote on HB67?",
        "What is the legislative history of SB100 in the 2026 session?",
        "Which energy sector lobbyists were registered in 2026?",

        # Should escalate to Opus
        "Compare how Aaron Rouse, Louise Lucas, and Lionell Spruill voted on energy bills across the 2024 and 2026 sessions",
        "How do PAC contributions to delegates correlate with their votes on offshore wind legislation?",
        "Show me all bills where lobbyist activity and campaign finance donations overlap with floor vote patterns",
        "Compare HB67 and SB200 across votes, donors, and lobbying registrations",
    ]

    # Simulate a name index (in production, load from va_name_index table)
    mock_name_index = [
        "Louise Lucas", "Aaron Rouse", "Lionell Spruill",
        "Michael Feggans", "Barry Knight", "Nadarius Clark",
    ]

    print(f"{'Query':<70} {'Model':<25} {'Escalated'}")
    print("-" * 110)

    for q in test_queries:
        legislator_count = count_legislators_in_query(q, mock_name_index)
        decision = get_model_for_query(
            query=q,
            retrieved_source_count=0,
            legislator_count=legislator_count,
        )
        truncated = q[:67] + "..." if len(q) > 70 else q
        print(f"{truncated:<70} {decision.model:<25} {decision.escalated}")
        if decision.signals:
            for signal in decision.signals:
                print(f"  → {signal}")
