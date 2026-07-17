"""Tier enforcement middleware — checks agent access and usage limits.

This middleware enforces subscription tier access control:
- Checks if user's tier can access the requested agent
- Tracks and enforces daily/monthly usage limits
- Returns detailed error messages for tier/limit violations
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException, status

from voteiq.config.tiers import (
    Tier,
    Vertical,
    can_access_agent,
    get_agent_limit,
    get_limit_period,
)


class TierCheckException(HTTPException):
    """Raised when user's tier cannot access an agent."""

    def __init__(self, tier: str, vertical: str, agent: str):
        self.tier = tier
        self.vertical = vertical
        self.agent = agent
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Agent '{agent}' is not available in {vertical.upper()} chat on {tier.upper()} tier. "
            f"Please upgrade your subscription or choose a different agent.",
        )


class UsageLimitException(HTTPException):
    """Raised when user has exceeded their usage limit."""

    def __init__(self, agent: str, limit: int, period: str, vertical: str):
        self.agent = agent
        self.limit = limit
        self.period = period
        self.vertical = vertical
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"You've reached your {period} limit for the {agent} agent "
            f"in {vertical} chat ({limit}/{limit} queries used). "
            f"Your limit resets on the first day of next {period}. "
            f"Upgrade to a higher tier for increased limits.",
        )


def check_agent_access(
    tier: str,
    vertical: str,
    agent: str,
) -> bool:
    """Check if a user can access an agent.

    Args:
        tier: User's subscription tier (e.g., 'free', 'pro')
        vertical: Chat vertical (e.g., 'civic', 'news')
        agent: Agent name (e.g., 'analyst', 'news_monitor')

    Returns:
        True if access allowed

    Raises:
        TierCheckException: If user's tier cannot access agent
    """
    if not can_access_agent(tier, vertical, agent):
        raise TierCheckException(tier, vertical, agent)
    return True


def check_usage_limit(
    tier: str,
    vertical: str,
    agent: str,
    current_usage: int,
) -> bool:
    """Check if user is within usage limits.

    Args:
        tier: User's subscription tier
        vertical: Chat vertical
        agent: Agent name
        current_usage: Number of queries already used in current period

    Returns:
        True if under limit

    Raises:
        UsageLimitException: If user has exceeded their limit
    """
    limit = get_agent_limit(tier, vertical, agent)

    # Unlimited agents never hit the limit
    if limit == "unlimited":
        return True

    # Check if we have a numeric limit
    if isinstance(limit, int):
        if current_usage >= limit:
            period = get_limit_period(tier, vertical, agent) or "monthly"
            raise UsageLimitException(agent, limit, period, vertical)

    return True


def get_remaining_queries(
    tier: str,
    vertical: str,
    agent: str,
    current_usage: int,
) -> Optional[int]:
    """Get the number of queries remaining for a user.

    Args:
        tier: User's subscription tier
        vertical: Chat vertical
        agent: Agent name
        current_usage: Number of queries already used in current period

    Returns:
        Number of remaining queries, or None if unlimited
    """
    limit = get_agent_limit(tier, vertical, agent)

    if limit == "unlimited":
        return None

    if isinstance(limit, int):
        return max(0, limit - current_usage)

    return None


def format_usage_message(
    tier: str,
    vertical: str,
    agent: str,
    current_usage: int,
) -> str:
    """Format a user-friendly message about their usage.

    Args:
        tier: User's subscription tier
        vertical: Chat vertical
        agent: Agent name
        current_usage: Number of queries already used in current period

    Returns:
        Human-readable usage message
    """
    limit = get_agent_limit(tier, vertical, agent)
    period = get_limit_period(tier, vertical, agent) or "monthly"

    if limit == "unlimited":
        return f"✓ Unlimited {agent} queries ({period})"

    if isinstance(limit, int):
        remaining = max(0, limit - current_usage)
        return f"{remaining}/{limit} {agent} queries remaining ({period})"

    return f"Unknown limit for {agent}"


def calculate_reset_date(period: str) -> datetime:
    """Calculate when the usage limit resets.

    Args:
        period: 'daily' or 'monthly'

    Returns:
        Datetime of next reset
    """
    now = datetime.utcnow()

    if period == "daily":
        # Reset at midnight UTC tomorrow
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    elif period == "monthly":
        # Reset on the first of next month at midnight UTC
        if now.month == 12:
            return now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            return now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)

    return now  # Fallback


def tier_check_middleware(
    tier: str,
    vertical: str,
    agent: str,
    current_usage: int = 0,
) -> dict:
    """Complete tier check: access + usage limits.

    Args:
        tier: User's subscription tier
        vertical: Chat vertical
        agent: Agent name
        current_usage: Current query count in this period

    Returns:
        Dictionary with access info and usage details

    Raises:
        TierCheckException: If tier cannot access agent
        UsageLimitException: If user exceeded usage limit
    """
    # Check tier access
    check_agent_access(tier, vertical, agent)

    # Check usage limits
    check_usage_limit(tier, vertical, agent, current_usage)

    # Return usage info
    limit = get_agent_limit(tier, vertical, agent)
    period = get_limit_period(tier, vertical, agent) or "monthly"
    remaining = get_remaining_queries(tier, vertical, agent, current_usage)

    return {
        "access_granted": True,
        "tier": tier,
        "vertical": vertical,
        "agent": agent,
        "limit": limit,
        "period": period,
        "current_usage": current_usage,
        "remaining_queries": remaining,
        "reset_date": calculate_reset_date(period).isoformat(),
        "usage_message": format_usage_message(tier, vertical, agent, current_usage),
    }
