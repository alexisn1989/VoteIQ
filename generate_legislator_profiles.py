"""
Generate top 15 state legislator profiles with full donor-bill correlation analysis
"""

import sqlite3
from pathlib import Path
from collections import defaultdict

va_leg_db = Path("virginia_legislature.db")
polls_db = Path("polls.db")

va_conn = sqlite3.connect(str(va_leg_db))
polls_conn = sqlite3.connect(str(polls_db))

va_cursor = va_conn.cursor()
polls_cursor = polls_conn.cursor()

print("\n" + "=" * 90)
print("GENERATING TOP 15 STATE LEGISLATOR PROFILES")
print("=" * 90)

# Get top 15 state legislators by fundraising (excluding governors/statewide officials)
polls_cursor.execute("""
    SELECT
        candidate_name,
        SUM(amount) as total_raised,
        COUNT(*) as contributions
    FROM va_cf_schedule_a
    WHERE candidate_name NOT LIKE '%Youngkin%'
        AND candidate_name NOT LIKE '%Northam%'
        AND candidate_name NOT LIKE '%McAuliffe%'
        AND candidate_name NOT LIKE '%Sears%'
        AND candidate_name NOT LIKE '%Miyares%'
        AND candidate_name NOT LIKE '%Cuccinelli%'
        AND candidate_name NOT LIKE '%Gillespie%'
        AND candidate_name NOT LIKE '%Herring%'
        AND candidate_name NOT LIKE '%Spanberger%'
    GROUP BY candidate_name
    HAVING SUM(amount) > 1000000
        AND candidate_name NOT LIKE '%Tester%'
        AND candidate_name NOT LIKE '%Test%'
    ORDER BY total_raised DESC
    LIMIT 15
""")

top_legislators = polls_cursor.fetchall()

print(f"\nTop 15 State Legislators by Campaign Fundraising:\n")

for i, (name, total_raised, contributions) in enumerate(top_legislators, 1):
    print(f"{i}. {name}")
    print(f"   Raised: ${total_raised:,.0f} from {contributions} contributions")
    print(f"   Avg contribution: ${total_raised/contributions:,.2f}")
    print()

print("\n" + "=" * 90)
print("ANALYZING EACH LEGISLATOR...")
print("=" * 90)

# Analyze each legislator
profiles = []

for name, total_raised, contributions in top_legislators:
    print(f"\nAnalyzing: {name}")

    # Get donor sectors
    polls_cursor.execute("""
        SELECT
            CASE
                WHEN LOWER(employer || ' ' || occupation) LIKE '%pac%' THEN 'PAC/Committee'
                WHEN LOWER(employer || ' ' || occupation) LIKE '%tech%' THEN 'Technology'
                WHEN LOWER(employer || ' ' || occupation) LIKE '%law%' THEN 'Legal'
                WHEN LOWER(employer || ' ' || occupation) LIKE '%bank%' THEN 'Finance'
                WHEN LOWER(employer || ' ' || occupation) LIKE '%labor%' THEN 'Labor/Union'
                WHEN LOWER(employer || ' ' || occupation) LIKE '%health%' THEN 'Healthcare'
                WHEN LOWER(employer || ' ' || occupation) LIKE '%real estate%' THEN 'Real Estate'
                ELSE 'Other'
            END as sector,
            SUM(amount) as amount,
            COUNT(*) as count
        FROM va_cf_schedule_a
        WHERE candidate_name = ?
        GROUP BY sector
        ORDER BY amount DESC
    """, (name,))

    donor_sectors = polls_cursor.fetchall()

    # Try to match with bills introduced (name variations)
    name_parts = name.split()
    last_name = name_parts[-1] if name_parts else name

    va_cursor.execute("""
        SELECT COUNT(*) FROM bills WHERE Patron_name = ?
    """, (last_name,))

    bills_introduced = va_cursor.fetchone()[0]

    # Get sample bills
    va_cursor.execute("""
        SELECT Bill_id, Bill_description FROM bills
        WHERE Patron_name = ?
        LIMIT 5
    """, (last_name,))

    sample_bills = va_cursor.fetchall()

    # Try to get voting record (fuzzy match on name)
    va_cursor.execute("""
        SELECT member_id FROM members WHERE member_name LIKE ?
    """, (f"%{last_name}%",))

    member_result = va_cursor.fetchone()
    votes_data = None

    if member_result:
        member_id = member_result[0]
        va_cursor.execute("""
            SELECT
                SUM(CASE WHEN vote = 'Y' THEN 1 ELSE 0 END) as yes_votes,
                SUM(CASE WHEN vote = 'N' THEN 1 ELSE 0 END) as no_votes,
                COUNT(*) as total_votes
            FROM votes
            WHERE member_id = ?
        """, (member_id,))

        votes_data = va_cursor.fetchone()

    # Store profile data
    profile = {
        'name': name,
        'total_raised': total_raised,
        'contributions': contributions,
        'avg_contribution': total_raised / contributions if contributions > 0 else 0,
        'donor_sectors': donor_sectors,
        'bills_introduced': bills_introduced,
        'sample_bills': sample_bills,
        'votes': votes_data,
    }

    profiles.append(profile)
    print(f"  Donor sectors: {len(donor_sectors)}")
    print(f"  Bills introduced: {bills_introduced}")
    if votes_data:
        print(f"  Voting record: {votes_data[0]} yes / {votes_data[1]} no")

# Generate markdown profiles
print("\n" + "=" * 90)
print("GENERATING MARKDOWN PROFILES...")
print("=" * 90)

for i, profile in enumerate(profiles, 1):
    name = profile['name']
    filename = f"legislator_profile_{i:02d}_{name.replace(' ', '_').replace('.', '')}.md"

    print(f"\nGenerating: {filename}")

    # Build markdown content
    md = f"""
## Campaign Finance Profile: {name}

### Financial Summary
- **Total Raised**: ${profile['total_raised']:,.2f}
- **Total Contributions**: {profile['contributions']:,}
- **Average Contribution**: ${profile['avg_contribution']:,.2f}

### Donor Sectors (Top Funding Sources)
| Sector | Amount | Donors | % |
|--------|--------|--------|-----|
"""

    total = profile['total_raised']
    for sector, amount, count in profile['donor_sectors'][:10]:
        pct = (amount / total * 100) if total > 0 else 0
        md += f"| {sector} | ${amount:,.0f} | {count:,} | {pct:.1f}% |\n"

    md += f"""

### Legislative Activity Summary
- **Bills Introduced**: {profile['bills_introduced']} bills
"""

    if profile['votes'] and profile['votes'][0] is not None:
        yes, no, total = profile['votes']
        yes_rate = (yes / total * 100) if total > 0 else 0
        md += f"- **Voting Record**: {yes:,} yes / {no:,} no ({yes_rate:.1f}% yes rate)\n"
        md += f"- **Total Votes**: {total:,}\n"

    if profile['sample_bills']:
        md += f"""

### Sample Bills Introduced
"""
        for bill_id, description in profile['sample_bills']:
            short_desc = description[:60] if len(description) > 60 else description
            md += f"- {bill_id}: {short_desc}\n"

    md += f"""

---

### [PRO/NEWSPAPER FEATURE] Donor-Legislative Correlation Analysis

*This detailed analysis is available in:*
- **Pro Section** - Full legislative intelligence and voting patterns
- **Newspaper** - Investigative journalism on donations and legislative outcomes

*Features in those sections:*
- **Donor-Bill Correlation**: Do their campaign donors match their bill activity?
- **Voting Pattern Analysis**: How does voting record align with donor interests?
- **Influence Tier**: Is this legislator highly influenced by donors or independent?
- **Donor Sector Impact**: Which sectors get legislative support from this politician?
- **Comparative Analysis**: How does their donor influence compare to peers?

This analysis reveals the relationship between campaign contributions and legislative outcomes.

---
**Source**: Virginia State Board of Elections + Virginia Legislature Database
**Data Current**: May 25, 2026
"""

    # Write to file
    with open(filename, 'w') as f:
        f.write(md)

print("\n" + "=" * 90)
print(f"GENERATED {len(profiles)} LEGISLATOR PROFILES")
print("=" * 90)

print("\nFiles created:")
for i, profile in enumerate(profiles, 1):
    name = profile['name']
    filename = f"legislator_profile_{i:02d}_{name.replace(' ', '_').replace('.', '')}.md"
    print(f"  {i}. {filename}")

va_conn.close()
polls_conn.close()

print("\n" + "=" * 90)
print("NEXT: Update these profiles with full correlation analysis for Pro/Newspaper")
print("=" * 90)
