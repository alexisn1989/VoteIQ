"""
State Legislator Bill Sponsorship Analysis
Analyzes bills introduced/sponsored by Virginia state legislators
"""

import sqlite3
from pathlib import Path

va_leg_db = Path("virginia_legislature.db")
polls_db = Path("polls.db")

va_conn = sqlite3.connect(str(va_leg_db))
polls_conn = sqlite3.connect(str(polls_db))

va_cursor = va_conn.cursor()
polls_cursor = polls_conn.cursor()

print("\n" + "=" * 90)
print("VIRGINIA LEGISLATOR BILL SPONSORSHIP ANALYSIS")
print("Which bills do legislators introduce and support?")
print("=" * 90)

# Get bills by patron (introducer)
va_cursor.execute("""
    SELECT
        Patron_name,
        COUNT(*) as bills_count,
        GROUP_CONCAT(Bill_description, ' | ') as sample_bills
    FROM bills
    GROUP BY Patron_name
    ORDER BY bills_count DESC
    LIMIT 30
""")

print("\nTOP 30 BILL INTRODUCERS (2026):")
print("-" * 90)

all_legislators = []
for patron, count, sample_bills in va_cursor.fetchall():
    sample = sample_bills.split(' | ')[0] if sample_bills else "No bills"
    sample = sample[:60] if len(sample) > 60 else sample
    print(f"\n{patron}: {count} bills introduced")
    print(f"  Sample: {sample}")

    all_legislators.append({
        'name': patron,
        'bills_introduced': count,
    })

# Check for legislators with campaign finance data
print("\n" + "=" * 90)
print("MATCHING LEGISLATORS WITH CAMPAIGN FINANCE DATA")
print("=" * 90)

# Get all unique legislator candidates from campaign finance
polls_cursor.execute("""
    SELECT
        candidate_name,
        COUNT(*) as contributions,
        SUM(amount) as total
    FROM va_cf_schedule_a
    WHERE candidate_name NOT LIKE '%Spanberger%'
    GROUP BY candidate_name
    ORDER BY total DESC
    LIMIT 50
""")

print("\nTop state legislators by campaign fundraising:")
print("-" * 90)

for candidate, contrib, total in polls_cursor.fetchall():
    if total and total > 1000:  # Only show significant fundraisers
        avg = total / contrib if contrib > 0 else 0
        print(f"\n{candidate}")
        print(f"  Raised: ${total:,.0f} from {contrib} contributions")
        print(f"  Average: ${avg:,.2f} per contribution")

# Analyze voting patterns
print("\n" + "=" * 90)
print("VOTING PATTERN ANALYSIS")
print("=" * 90)

va_cursor.execute("""
    SELECT
        member_id,
        member_name,
        SUM(CASE WHEN vote_value = 'Y' THEN 1 ELSE 0 END) as yes_votes,
        SUM(CASE WHEN vote_value = 'N' THEN 1 ELSE 0 END) as no_votes,
        COUNT(*) as total_votes
    FROM votes
    GROUP BY member_id, member_name
    ORDER BY total_votes DESC
    LIMIT 20
""")

print("\nMost active voters (top 20):")
print("-" * 90)

for member_id, member_name, yes, no, total in va_cursor.fetchall():
    yes_rate = (yes / total * 100) if total > 0 else 0
    print(f"{member_name}: {yes} yes / {no} no ({yes_rate:.0f}% yes rate)")

# Analyze bill categories
print("\n" + "=" * 90)
print("BILL CATEGORIZATION BY TOPIC")
print("=" * 90)

bill_categories = {
    'Education': ['education', 'school', 'university', 'college', 'student'],
    'Healthcare': ['health', 'medical', 'hospital', 'pharmacy', 'doctor'],
    'Labor': ['labor', 'wage', 'employment', 'worker', 'union'],
    'Environment': ['environment', 'conservation', 'renewable', 'climate'],
    'Transportation': ['transportation', 'highway', 'road', 'vehicle'],
    'Housing': ['housing', 'property', 'real estate', 'residential'],
    'Finance/Banking': ['bank', 'financial', 'insurance', 'payment'],
    'Technology': ['technology', 'broadband', 'digital', 'data'],
    'Criminal Justice': ['criminal', 'court', 'sentence', 'prison'],
}

print("\nBills introduced by category:")
print("-" * 90)

for category, keywords in bill_categories.items():
    keyword_filter = " OR ".join([f"Bill_description LIKE '%{kw}%'" for kw in keywords])
    va_cursor.execute(f"""
        SELECT COUNT(DISTINCT Bill_id) FROM bills
        WHERE {keyword_filter}
    """)
    count = va_cursor.fetchone()[0]
    if count > 0:
        print(f"  {category}: {count} bills")

# Key insight
print("\n" + "=" * 90)
print("WHAT THIS ENABLES FOR YOUR DEMO")
print("=" * 90)

print("""
With this legislator data, you can create:

1. LEGISLATOR PROFILES (per senator/delegate)
   - Total bills introduced (sponsorship activity)
   - Bills by category (policy focus)
   - Voting record (yes rate, abstentions)
   - Campaign donors (top sectors)
   - CORRELATION: Do their campaign donors match their bill activity?

2. DONOR-BILL CORRELATION
   Example: Legislator X
   - Top donors: Technology sector ($X), Finance ($Y), Labor ($Z)
   - Bills introduced: 15 tech bills, 3 finance bills, 0 labor bills
   - INSIGHT: Introduced bills match donor interests (tech), ignored labor donors

3. COMPARATIVE LEGISLATOR ANALYSIS
   - Which legislators are most influenced by specific donors?
   - Which are most independent (diverse donors, diverse bills)?
   - Which have controversial patterns (high funding, limited bill output)?

4. VOTING RECORD VS DONORS
   - Does legislator's voting record align with donors?
   - Do high-tech donors' legislators vote for tech bills?
   - Do labor donors' legislators vote for labor protections?

5. LEGISLATOR INFLUENCE TIERS (like Governor analysis)
   - Tier A: High fundraisers introducing related bills
   - Tier B: Moderate fundraisers with some donor alignment
   - Tier C: Low fundraisers, independent voting
   - Pattern analysis: Which tier has most influence?

This turns your demo into:
- Governor: Veto power analysis (what gets blocked)
- Senators (40): Bill introduction + donor correlation
- Delegates (100): Bill introduction + donor correlation
- Total: 141 politicians with money-politics transparency

This is VERY valuable for voters, journalists, advocacy groups.
""")

va_conn.close()
polls_conn.close()

print("=" * 90)
EOF
