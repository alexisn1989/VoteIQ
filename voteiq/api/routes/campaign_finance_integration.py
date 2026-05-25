"""
Campaign Finance Integration for Chat.py
Integrates Virginia SBE and FEC campaign finance data into VoteIQ responses.

Add this to chat.py routes to enable automatic campaign finance display
for candidates like Governor Spanberger.
"""

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class CampaignFinanceData:
    """Campaign finance data for a candidate"""
    candidate_name: str
    total_raised: float
    total_contributions: int
    average_contribution: float
    donor_sectors: Dict[str, Dict]  # sector -> {amount, count, pct}
    top_individual_donors: List[tuple]
    top_pac_donors: List[tuple]


class CampaignFinanceService:
    """Service for retrieving campaign finance data"""

    def __init__(self, db_path: str = "polls.db"):
        self.db_path = Path(db_path)

    def search_candidate(self, name: str) -> Optional[str]:
        """Search for candidate name in database (exact or partial match)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        # Try exact match first
        cursor.execute(
            "SELECT DISTINCT candidate_name FROM va_cf_schedule_a WHERE candidate_name = ?",
            (name,)
        )
        result = cursor.fetchone()
        if result:
            conn.close()
            return result[0]

        # Try partial match
        cursor.execute(
            "SELECT DISTINCT candidate_name FROM va_cf_schedule_a WHERE candidate_name LIKE ?",
            (f"%{name}%",)
        )
        results = cursor.fetchall()
        conn.close()

        if results:
            return results[0][0]
        return None

    def get_campaign_finance(self, candidate_name: str) -> Optional[CampaignFinanceData]:
        """Get complete campaign finance profile for a candidate"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        # Get totals
        cursor.execute("""
            SELECT COUNT(*) as contributions, SUM(amount) as total
            FROM va_cf_schedule_a
            WHERE candidate_name = ?
        """, (candidate_name,))

        result = cursor.fetchone()
        if not result or result[1] is None:
            conn.close()
            return None

        total_contributions, total_raised = result
        avg_contribution = total_raised / total_contributions if total_contributions > 0 else 0

        # Get donor sectors
        donor_sectors = self._get_donor_sectors(cursor, candidate_name, total_raised)

        # Get top individual donors
        top_individual_donors = self._get_top_individual_donors(cursor, candidate_name, 10)

        # Get top PAC donors
        top_pac_donors = self._get_top_pac_donors(cursor, candidate_name, 10)

        conn.close()

        return CampaignFinanceData(
            candidate_name=candidate_name,
            total_raised=total_raised,
            total_contributions=total_contributions,
            average_contribution=avg_contribution,
            donor_sectors=donor_sectors,
            top_individual_donors=top_individual_donors,
            top_pac_donors=top_pac_donors,
        )

    def _get_donor_sectors(self, cursor, candidate_name: str, total_raised: float) -> Dict:
        """Get donor breakdown by sector"""
        # Sector keywords for classification
        sector_keywords = {
            "Technology": ["software", "tech", "it", "computer", "data", "tech company"],
            "Finance": ["bank", "financial", "investment", "insurance", "hedge", "private equity"],
            "Legal": ["attorney", "lawyer", "law firm"],
            "Healthcare": ["hospital", "medical", "health", "pharma", "doctor"],
            "Real Estate": ["real estate", "property", "developer", "construction"],
            "Energy": ["oil", "gas", "energy", "utility", "power"],
            "Defense": ["defense", "military", "aerospace"],
            "Labor/Union": ["union", "labor", "afl-cio"],
            "PAC/Committee": ["pac", "political", "committee", "action"],
        }

        def classify_sector(employer: str, occupation: str) -> str:
            combined = f"{employer} {occupation}".lower()
            for sector, keywords in sector_keywords.items():
                if any(k in combined for k in keywords):
                    return sector
            return "Individual/Other"

        # Get all contributions
        cursor.execute("""
            SELECT employer, occupation, amount, is_individual
            FROM va_cf_schedule_a
            WHERE candidate_name = ?
            ORDER BY amount DESC
        """, (candidate_name,))

        sectors = {}
        for employer, occupation, amount, is_individual in cursor.fetchall():
            sector = classify_sector(employer, occupation)

            if sector not in sectors:
                sectors[sector] = {"amount": 0, "count": 0}

            sectors[sector]["amount"] += amount
            sectors[sector]["count"] += 1

        # Add percentages
        for sector in sectors:
            sectors[sector]["pct"] = (sectors[sector]["amount"] / total_raised * 100) if total_raised > 0 else 0

        return sectors

    def _get_top_individual_donors(self, cursor, candidate_name: str, limit: int = 10) -> List[tuple]:
        """Get top individual donors"""
        donor_totals = {}

        cursor.execute("""
            SELECT first_name, last_or_company, SUM(amount) as total
            FROM va_cf_schedule_a
            WHERE candidate_name = ? AND is_individual = 1
            GROUP BY first_name, last_or_company
            ORDER BY total DESC
            LIMIT ?
        """, (candidate_name, limit))

        results = []
        for first, last, total in cursor.fetchall():
            name = f"{first} {last}".strip() if first else last
            results.append((name, total))

        return results

    def _get_top_pac_donors(self, cursor, candidate_name: str, limit: int = 10) -> List[tuple]:
        """Get top PAC/Committee donors"""
        cursor.execute("""
            SELECT last_or_company, SUM(amount) as total
            FROM va_cf_schedule_a
            WHERE candidate_name = ? AND is_individual = 0
            GROUP BY last_or_company
            ORDER BY total DESC
            LIMIT ?
        """, (candidate_name, limit))

        return cursor.fetchall()


def format_campaign_finance_response(data: CampaignFinanceData) -> str:
    """Format campaign finance data as markdown for chat response"""

    response = f"""
## Campaign Finance Profile: {data.candidate_name}

### Financial Summary
- **Total Raised**: ${data.total_raised:,.2f}
- **Total Contributions**: {data.total_contributions:,}
- **Average Contribution**: ${data.average_contribution:,.2f}

### Donor Sectors (Top Funding Sources)
| Sector | Amount | Donors | % |
|--------|--------|--------|-----|
"""

    for sector, info in sorted(data.donor_sectors.items(),
                               key=lambda x: x[1]['amount'],
                               reverse=True):
        response += f"| {sector} | ${info['amount']:,.0f} | {info['count']:,} | {info['pct']:.1f}% |\n"

    response += f"""
### Top Individual Donors (Top 10)
"""

    for i, (name, amount) in enumerate(data.top_individual_donors, 1):
        response += f"{i}. **{name}** — ${amount:,.0f}\n"

    response += f"""
### Top PAC/Committee Donors (Top 10)
"""

    for i, (name, amount) in enumerate(data.top_pac_donors, 1):
        response += f"{i}. **{name}** — ${amount:,.0f}\n"

    response += """
---
**Source**: Virginia State Board of Elections
**Data Current**: May 25, 2026
"""

    return response


# ============================================================================
# CHAT.PY INTEGRATION CODE
# ============================================================================

INTEGRATION_CODE = """
# Add these imports to chat.py
from voteiq.api.routes.campaign_finance_integration import (
    CampaignFinanceService,
    format_campaign_finance_response,
)

# Initialize service (in app startup)
campaign_finance_service = CampaignFinanceService()

# Add this route to chat.py
@app.route('/api/candidate/<candidate_name>/campaign-finance', methods=['GET'])
def get_campaign_finance(candidate_name):
    '''Get campaign finance data for a candidate'''
    try:
        # Search for candidate
        found_name = campaign_finance_service.search_candidate(candidate_name)
        if not found_name:
            return {
                'status': 'not_found',
                'message': f'No campaign finance data found for {candidate_name}'
            }, 404

        # Get finance data
        data = campaign_finance_service.get_campaign_finance(found_name)
        if not data:
            return {
                'status': 'no_data',
                'message': f'No contribution data available for {found_name}'
            }, 404

        # Return as dict
        return {
            'status': 'success',
            'candidate_name': data.candidate_name,
            'total_raised': data.total_raised,
            'total_contributions': data.total_contributions,
            'average_contribution': data.average_contribution,
            'donor_sectors': data.donor_sectors,
            'top_individual_donors': data.top_individual_donors,
            'top_pac_donors': data.top_pac_donors,
        }, 200
    except Exception as e:
        return {'status': 'error', 'message': str(e)}, 500

# Add this to the analyst agent prompt:
'''
CAMPAIGN FINANCE INTEGRATION:
If user asks about campaign contributions, donor sources, or fundraising:

1. Check campaign_finance_service.search_candidate(name)
2. If found: Get full profile with get_campaign_finance()
3. Format response using format_campaign_finance_response()
4. Append campaign finance section to analyst response

Example queries that should trigger campaign finance lookup:
- "Where did Spanberger get her campaign funding?"
- "Who are the top donors to [candidate]?"
- "What sectors fund [candidate]'s campaign?"
- "Show me [candidate]'s fundraising by donor type"
'''
"""

print(INTEGRATION_CODE)
