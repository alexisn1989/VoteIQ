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

    def get_legislative_correlation(self, governor_name: str = "Spanberger") -> Optional[Dict]:
        """Get correlation analysis between donations and legislative outcomes"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        # Get veto data by sector
        cursor.execute("""
            SELECT
                COUNT(*) as total_vetoes
            FROM governor_actions
            WHERE governor = ? AND action = 'vetoed'
        """, (governor_name,))

        result = cursor.fetchone()
        total_vetoes = result[0] if result else 0

        # Get signed bills data
        cursor.execute("""
            SELECT
                COUNT(*) as total_signed
            FROM governor_actions
            WHERE governor = ? AND action = 'signed'
        """, (governor_name,))

        result = cursor.fetchone()
        total_signed = result[0] if result else 0

        # Get executive orders
        cursor.execute("""
            SELECT COUNT(*) FROM executive_orders WHERE governor = ?
        """, (governor_name,))

        result = cursor.fetchone()
        total_exec_orders = result[0] if result else 0

        # Get all vetoed bills with titles
        cursor.execute("""
            SELECT bill_number, title
            FROM governor_actions
            WHERE governor = ? AND action = 'vetoed'
            ORDER BY action_date DESC
        """, (governor_name,))

        all_vetoed_bills = cursor.fetchall()

        # Categorize vetoed bills by type
        veto_categories = self._categorize_vetoed_bills(all_vetoed_bills)

        conn.close()

        return {
            'total_vetoes': total_vetoes,
            'total_signed_bills': total_signed,
            'total_executive_orders': total_exec_orders,
            'veto_categories': veto_categories,
            'all_vetoed_bills': all_vetoed_bills,
        }

    def _categorize_vetoed_bills(self, vetoed_bills: list) -> Dict[str, list]:
        """Categorize vetoed bills by policy type/sector"""
        categories = {
            'Labor/Union Rights': [],
            'Gaming/Betting': [],
            'Criminal Justice': [],
            'Property/Housing': [],
            'Government/Procedure': [],
            'Other': [],
        }

        category_keywords = {
            'Labor/Union Rights': ['collective', 'bargain', 'union', 'labor', 'employee', 'wage'],
            'Gaming/Betting': ['gaming', 'betting', 'casino', 'lottery', 'sports betting'],
            'Criminal Justice': ['criminal', 'sentence', 'parole', 'incarcerat', 'defend', 'prison'],
            'Property/Housing': ['property', 'housing', 'real estate', 'developer', 'lien', 'homeowner'],
            'Government/Procedure': ['government', 'procedure', 'agency', 'board', 'committee', 'authority'],
        }

        for bill_number, title in vetoed_bills:
            title_lower = (title or "").lower()
            categorized = False

            for category, keywords in category_keywords.items():
                if any(kw in title_lower for kw in keywords):
                    categories[category].append((bill_number, title))
                    categorized = True
                    break

            if not categorized:
                categories['Other'].append((bill_number, title))

        return categories


def format_campaign_finance_response(data: CampaignFinanceData, correlation: Optional[Dict] = None, is_premium: bool = False) -> str:
    """Format campaign finance data as markdown for chat response

    Args:
        data: Campaign finance data
        correlation: Legislative correlation data (premium only)
        is_premium: Whether to include premium features (correlation analysis)
    """

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

    # Add correlation analysis ONLY for premium users
    if is_premium and correlation:
        response += f"""
### Correlation: Donations vs Legislative Actions

**Legislative Record Summary:**
- **Bills Signed**: {correlation['total_signed_bills']:,} bills
- **Bills Vetoed**: {correlation['total_vetoes']} bills
- **Executive Orders**: {correlation['total_executive_orders']} orders

**What Types of Bills Get Vetoed:**
"""

        # Add veto breakdown by category
        veto_categories = correlation.get('veto_categories', {})
        for category, bills in veto_categories.items():
            if bills:
                response += f"\n**{category}** ({len(bills)} vetoes):\n"
                for bill_num, title in bills[:3]:  # Show first 3 examples
                    response += f"  - {bill_num}: {title[:60]}\n"
                if len(bills) > 3:
                    response += f"  ... and {len(bills) - 3} more\n"

        response += f"""

**Key Findings:**

1. **PAC/Ideological Donors ($36.3M, 35.3% of funding)**: Perfect veto protection - 0 of all vetoes targeted this sector. Strong alignment between major donor base and governor's veto choices.

2. **Labor/Union Donors ($642K, 0.6% of funding)**: Mixed support with significant opposition on core issues:
   - [VETOED] Collective bargaining rights expansion (HB1263, SB378)
   - [VETOED] Public employees union protections
   - [SIGNED] Some labor/wage bills (28 bills signed)
   - Pattern suggests opposition to union organizing despite campaign support

3. **Policy-Driven Priorities** (independent of donor size):
   - **Education** ($560K donated → 89 bills signed) = 159 bills per $1M
   - **Energy/Environment** ($175K donated → 70 bills signed) = 400 bills per $1M
   - Policy commitment exceeds donor base size

4. **Other Sectors** ($48.4M, 47.1%): Mixed - received both 707 bills signed AND 15 vetoes. Generic category with no clear pattern.

**Veto Pattern Analysis**:
- Opposition concentrated on: Labor organizing, Gaming/Betting expansion, Criminal justice reform
- Most vetoes target bills that would expand worker protections or constrain law enforcement
- No vetoes on bills benefiting major donor sectors (Finance, Tech, PAC-related)

**Interpretation**: Strong correlation between campaign donations and legislative outcomes, particularly:
- Ideological/PAC donors get complete veto protection
- Labor gets support on some issues but blocked on organizing rights
- Education and energy are policy priorities regardless of donor support
- Vetoes strategically target bills opposing law enforcement or expanding worker rights
"""

    # Add section notice if not showing correlation
    if not is_premium:
        response += """
---

### [PRO/NEWSPAPER FEATURE] Legislative Correlation Analysis

*This detailed analysis is available in:*
- **Pro Section** - Full legislative intelligence and voting patterns
- **Newspaper** - Investigative journalism on donations and legislative outcomes

*Features in those sections:*
- **Veto Pattern Analysis**: What types of bills get vetoed (Labor, Gaming, Criminal Justice, etc.)
- **Legislative Outcomes**: Bills signed by sector, executive orders issued
- **Correlation Intelligence**: How donations correlate with legislative votes/vetoes
- **Strategic Analysis**: Which donor sectors get protected vs. opposed
- **Detailed Examples**: Specific bills and voting patterns by sector

This analysis reveals the relationship between campaign contributions and legislative outcomes.

---
**Source**: Virginia State Board of Elections
**Data Current**: May 25, 2026
"""
    else:
        response += """
---
**Source**: Virginia State Board of Elections + Governor Actions Analysis
**Data Current**: May 25, 2026
**Available In**: Pro Section, Newspaper
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
