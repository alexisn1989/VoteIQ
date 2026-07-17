"""Usage tracking — counts queries per user, agent, tier, and period.

Tracks daily/monthly usage limits and provides query counts for enforcement.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Database path — adjust based on your deployment
_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "voteiq.db"


class UsageTracker:
    """Track and enforce usage limits per user/agent/tier."""

    def __init__(self, db_path: str | Path = _DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize usage tracking tables if they don't exist."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                tier TEXT NOT NULL,
                vertical TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Usage tracking table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usage (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                vertical TEXT NOT NULL,
                tier TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                tokens_used INTEGER,
                query_type TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        # Create index for efficient lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_usage_user_agent_date
            ON usage(user_id, agent, timestamp)
        """)

        conn.commit()
        conn.close()

    def record_query(
        self,
        user_id: str,
        agent: str,
        vertical: str,
        tier: str,
        tokens_used: int = 0,
        query_type: str = "standard",
    ) -> str:
        """Record a query usage event.

        Args:
            user_id: User ID
            agent: Agent name (e.g., 'analyst')
            vertical: Vertical (e.g., 'civic')
            tier: User's tier (e.g., 'pro')
            tokens_used: Approximate tokens used
            query_type: Type of query (e.g., 'bulk', 'realtime')

        Returns:
            Usage record ID
        """
        import uuid

        usage_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO usage (id, user_id, agent, vertical, tier, timestamp, tokens_used, query_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (usage_id, user_id, agent, vertical, tier, timestamp, tokens_used, query_type))

        conn.commit()
        conn.close()

        return usage_id

    def get_daily_usage(
        self,
        user_id: str,
        agent: str,
        date: datetime | None = None,
    ) -> int:
        """Get daily query count for a user/agent.

        Args:
            user_id: User ID
            agent: Agent name
            date: Date to check (defaults to today UTC)

        Returns:
            Number of queries used today
        """
        if date is None:
            date = datetime.utcnow()

        # Get start and end of day in UTC
        day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*) FROM usage
            WHERE user_id = ? AND agent = ?
              AND timestamp >= ? AND timestamp < ?
        """, (user_id, agent, day_start.isoformat(), day_end.isoformat()))

        count = cursor.fetchone()[0]
        conn.close()

        return count

    def get_monthly_usage(
        self,
        user_id: str,
        agent: str,
        date: datetime | None = None,
    ) -> int:
        """Get monthly query count for a user/agent.

        Args:
            user_id: User ID
            agent: Agent name
            date: Date to check (defaults to current month UTC)

        Returns:
            Number of queries used this month
        """
        if date is None:
            date = datetime.utcnow()

        # Get start of month and end of month in UTC
        month_start = date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if date.month == 12:
            month_end = month_start.replace(year=month_start.year + 1, month=1)
        else:
            month_end = month_start.replace(month=month_start.month + 1)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*) FROM usage
            WHERE user_id = ? AND agent = ?
              AND timestamp >= ? AND timestamp < ?
        """, (user_id, agent, month_start.isoformat(), month_end.isoformat()))

        count = cursor.fetchone()[0]
        conn.close()

        return count

    def get_usage(
        self,
        user_id: str,
        agent: str,
        period: str = "monthly",
    ) -> int:
        """Get usage count for a user/agent in the specified period.

        Args:
            user_id: User ID
            agent: Agent name
            period: 'daily' or 'monthly'

        Returns:
            Number of queries used in period
        """
        if period == "daily":
            return self.get_daily_usage(user_id, agent)
        elif period == "monthly":
            return self.get_monthly_usage(user_id, agent)
        else:
            return 0

    def get_user_usage_summary(
        self,
        user_id: str,
        vertical: str,
        tier: str,
    ) -> dict:
        """Get complete usage summary for a user in a vertical.

        Args:
            user_id: User ID
            vertical: Vertical (e.g., 'civic')
            tier: User's tier

        Returns:
            Dict with usage for all agents available to this tier
        """
        from voteiq.config.tiers import get_available_agents, get_limit_period

        agents = get_available_agents(tier, vertical)
        summary = {}

        for agent in agents:
            period = get_limit_period(tier, vertical, agent) or "monthly"
            usage = self.get_usage(user_id, agent, period)
            summary[agent] = {
                "usage": usage,
                "period": period,
            }

        return summary

    def reset_daily_usage(self, user_id: str, agent: str):
        """Clear daily usage for a user/agent (for testing).

        Args:
            user_id: User ID
            agent: Agent name
        """
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=1)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM usage
            WHERE user_id = ? AND agent = ?
              AND timestamp >= ? AND timestamp < ?
        """, (user_id, agent, today.isoformat(), tomorrow.isoformat()))

        conn.commit()
        conn.close()

    def reset_monthly_usage(self, user_id: str, agent: str):
        """Clear monthly usage for a user/agent (for testing).

        Args:
            user_id: User ID
            agent: Agent name
        """
        now = datetime.utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if now.month == 12:
            month_end = month_start.replace(year=month_start.year + 1, month=1)
        else:
            month_end = month_start.replace(month=month_start.month + 1)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM usage
            WHERE user_id = ? AND agent = ?
              AND timestamp >= ? AND timestamp < ?
        """, (user_id, agent, month_start.isoformat(), month_end.isoformat()))

        conn.commit()
        conn.close()


# Singleton instance
_tracker = None


def get_usage_tracker() -> UsageTracker:
    """Get or create the usage tracker singleton."""
    global _tracker
    if _tracker is None:
        _tracker = UsageTracker()
    return _tracker
