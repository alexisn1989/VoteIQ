"""
State Legislator Donor Correlation Analysis
Analyzes how campaign donations correlate with bills introduced, cosponsored, and voted for
"""

import sqlite3
from pathlib import Path
from collections import defaultdict

va_leg_db = Path("virginia_legislature.db")
polls_db = Path("polls.db")

# Connect to databases
va_conn = sqlite3.connect(str(va_leg_db))
polls_conn = sqlite3.connect(str(polls_db))

va_cursor = va_conn.cursor()
polls_cursor = polls_conn.cursor()

print("\n" + "=" * 90)
print("STATE LEGISLATOR DONOR CORRELATION ANALYSIS")
print("Campaign Donations vs Bills Introduced, Cosponsored, Voted For")
print("=" * 90)

# Get all state members
va_cursor.execute("SELECT member_id, member_name FROM members ORDER BY member_name")
all_members = va_cursor.fetchall()

print(f"\nTotal state legislators in database: {len(all_members)}")

# Categorize by chamber
senators = []
delegates = []

for member_id, name in all_members:
    if 'senator' in name.lower():
        senators.append((member_id, name))
    else:
        delegates.append((member_id, name))

print(f"  - State Senators: {len(senators)}")
print(f"  - House Delegates: {len(delegates)}")

# Analyze each legislator
print("\n" + "=" * 90)
print("STATE SENATORS: Campaign Finance vs Legislative Activity")
print("=" * 90)

legislator_stats = []

for member_id, member_name in senators[:10]:  # Start with first 10 senators
    # Get campaign finance for this legislator
    polls_cursor.execute("""
        SELECT
            COUNT(*) as contributions,
            SUM(amount) as total_raised
        FROM va_cf_schedule_a
        WHERE candidate_name = ?
    """, (member_name,))

    cf_result = polls_cursor.fetchone()
    contributions = cf_result[0] if cf_result and cf_result[0] else 0
    total_raised = cf_result[1] if cf_result and cf_result[1] else 0

    # Get bills introduced (as Patron)
    va_cursor.execute("""
        SELECT COUNT(*) FROM bills
        WHERE Patron_name = ?
    """, (member_name,))

    bills_introduced = va_cursor.fetchone()[0]

    # Get member's voting record
    va_cursor.execute("""
        SELECT
            vote_value,
            COUNT(*) as vote_count
        FROM votes
        WHERE member_id = ?
        GROUP BY vote_value
    """, (member_id,))

    vote_breakdown = {}
    for vote_value, count in va_cursor.fetchall():
        vote_breakdown[vote_value] = count

    total_votes = sum(vote_breakdown.values())
    yes_votes = vote_breakdown.get('Y', 0)
    no_votes = vote_breakdown.get('N', 0)
    yes_rate = (yes_votes / total_votes * 100) if total_votes > 0 else 0

    # Get donor sectors
    polls_cursor.execute("""
        SELECT
            CASE
                WHEN LOWER(employer || ' ' || occupation) LIKE '%pac%' THEN 'PAC'
                WHEN LOWER(employer || ' ' || occupation) LIKE '%tech%' THEN 'Technology'
                WHEN LOWER(employer || ' ' || occupation) LIKE '%law%' THEN 'Legal'
                WHEN LOWER(employer || ' ' || occupation) LIKE '%bank%' THEN 'Finance'
                WHEN LOWER(employer || ' ' || occupation) LIKE '%labor%' THEN 'Labor'
                ELSE 'Other'
            END as sector,
            SUM(amount) as amount
        FROM va_cf_schedule_a
        WHERE candidate_name = ?
        GROUP BY sector
        ORDER BY amount DESC
    """, (member_name,))

    top_donor_sector = None
    donor_sectors = []
    for sector, amount in polls_cursor.fetchall():
        donor_sectors.append((sector, amount))
        if not top_donor_sector:
            top_donor_sector = sector

    if contributions > 0:
        legislator_stats.append({
            'name': member_name,
            'type': 'Senator',
            'total_raised': total_raised,
            'contributions': contributions,
            'bills_introduced': bills_introduced,
            'total_votes': total_votes,
            'yes_rate': yes_rate,
            'top_donor_sector': top_donor_sector,
            'donor_sectors': donor_sectors,
        })

        print(f"\n{member_name}")
        print(f"  Campaign Finance: ${total_raised:,.0f} from {contributions} contributions")
        if donor_sectors:
            print(f"  Top Donor Sector: {donor_sectors[0][0]} (${donor_sectors[0][1]:,.0f})")
        print(f"  Bills Introduced: {bills_introduced}")
        print(f"  Voting Record: {yes_votes} yes / {no_votes} no ({yes_rate:.1f}% yes rate)")

print("\n" + "=" * 90)
print("ANALYSIS INSIGHTS")
print("=" * 90)

if legislator_stats:
    # Find patterns
    high_fundraisers = sorted(legislator_stats, key=lambda x: x['total_raised'], reverse=True)
    bill_introducers = sorted(legislator_stats, key=lambda x: x['bills_introduced'], reverse=True)

    print(f"\nTop Fundraisers (first 5):")
    for leg in high_fundraisers[:5]:
        print(f"  {leg['name']}: ${leg['total_raised']:,.0f}")

    print(f"\nMost Active Bill Introducers (first 5):")
    for leg in bill_introducers[:5]:
        print(f"  {leg['name']}: {leg['bills_introduced']} bills")

    print(f"\nKey Correlation Patterns:")
    print(f"  - Do high fundraisers introduce more bills? [Analysis pending]")
    print(f"  - Do donor sectors match bill types? [Analysis pending]")
    print(f"  - How does voting record correlate with donors? [Analysis pending]")

print("\n" + "=" * 90)
print("NEXT STEPS FOR COMPREHENSIVE ANALYSIS")
print("=" * 90)

print("""
For full demo implementation:

1. LEGISLATOR PROFILES (similar to Governor Spanberger)
   - Campaign finance summary (total raised, average contribution, donor sectors)
   - Bills introduced (count + examples)
   - Cosponsored bills (count + examples)
   - Voting record (yes rate, key votes)
   - Donor-bill correlation (do donors' interests match legislator's bills?)

2. DONOR SECTOR CORRELATION
   - Tech donors -> legislator introduces tech bills?
   - Labor donors -> legislator votes for labor bills?
   - Finance donors -> legislator votes for banking/finance bills?

3. VOTING PATTERN CORRELATION
   - Does voting record align with donor interests?
   - Do high fundraisers vote differently than low fundraisers?

4. LEGISLATIVE INFLUENCE SCORING
   - Which legislators most influenced by specific donor sectors
   - Which sectors get best legislative support
   - Legislator influence tiers (similar to Governor analysis)

5. COMPARATIVE ANALYSIS
   - Which legislators are most influenced by donors?
   - Which are most independent?
   - Differences between senators and delegates

This creates a complete "money in politics" picture for Virginia:
- Governor level (1)
- State Senate level (40)
- House Delegates level (100)
- Total: 141 politicians analyzed for donor influence
""")

va_conn.close()
polls_conn.close()

print("\n" + "=" * 90)
