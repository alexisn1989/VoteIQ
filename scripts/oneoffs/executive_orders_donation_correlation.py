#!/usr/bin/env python3
"""
Executive Orders-Donation Correlation Analysis for Governor Spanberger

Analyzes if there's a correlation between:
- Campaign donations by sector
- Executive orders issued benefiting those sectors
"""

import sqlite3
from collections import defaultdict

polls_db = sqlite3.connect('polls.db')
polls_db.row_factory = sqlite3.Row
cursor = polls_db.cursor()

print("=" * 80)
print("GOVERNOR SPANBERGER: EXECUTIVE ORDERS-DONATION CORRELATION")
print("=" * 80)
print()

# ── Step 1: Load Donation Data by Sector ──────────────────────────────────────

print("[STEP 1] Loading campaign finance data by sector...")

cursor.execute("""
    SELECT employer, occupation, amount
    FROM va_cf_schedule_a
    WHERE candidate_name LIKE '%Spanberger%'
""")

sector_keywords = {
    "Technology": ["software", "tech", "it", "computer", "data"],
    "Finance": ["bank", "financial", "investment", "insurance"],
    "Legal": ["attorney", "lawyer", "law firm"],
    "Healthcare": ["hospital", "medical", "health", "pharma"],
    "Real Estate": ["real estate", "property", "developer"],
    "Energy": ["oil", "gas", "energy", "utility"],
    "Education": ["school", "university", "college"],
    "Labor/Union": ["union", "labor", "afl-cio"],
    "Environment": ["environmental", "green", "conservation"],
    "Ideological": ["pac", "political", "committee"],
}

def classify_sector(employer, occupation):
    combined = f"{employer} {occupation}".lower()
    for sector, keywords in sector_keywords.items():
        if any(k in combined for k in keywords):
            return sector
    return "Other"

sector_donations = defaultdict(lambda: {"amount": 0, "count": 0})

for row in cursor.fetchall():
    sector = classify_sector(row["employer"], row["occupation"])
    sector_donations[sector]["amount"] += row["amount"]
    sector_donations[sector]["count"] += 1

print(f"  Found {len(sector_donations)} sectors with donations")
print()

# ── Step 2: Load Governor Actions (Pending, Signed, Amended) ───────────────────

print("[STEP 2] Loading executive actions and orders...")

cursor.execute("""
    SELECT action, COUNT(*) as count
    FROM governor_actions
    WHERE governor = 'Spanberger'
    GROUP BY action
""")

actions_summary = cursor.fetchall()
print("\nExecutive Actions Summary:")
for row in actions_summary:
    print(f"  {row['action']:20} : {row['count']:3} actions")

print()

# ── Step 3: Classify Actions by Sector ────────────────────────────────────────

print("[STEP 3] Classifying actions by sector...")

bill_sector_patterns = {
    "Technology": ["technology", "broadband", "telecom", "cyber", "data"],
    "Finance": ["banking", "finance", "insurance", "credit"],
    "Legal": ["attorney", "court", "justice"],
    "Healthcare": ["health", "medical", "hospital", "prescription"],
    "Real Estate": ["property", "housing", "construction"],
    "Energy": ["energy", "utility", "power", "renewable"],
    "Education": ["school", "education", "university", "student"],
    "Labor/Union": ["labor", "union", "employee", "wage"],
    "Environment": ["environment", "climate", "pollution", "water"],
    "Ideological": ["voting", "abortion", "gun", "rights"],
}

def classify_bill_sector(title):
    title_lower = (title or "").lower()
    for sector, patterns in bill_sector_patterns.items():
        if any(p in title_lower for p in patterns):
            return sector
    return "Other"

cursor.execute("""
    SELECT action, title, bill_number
    FROM governor_actions
    WHERE governor = 'Spanberger'
    ORDER BY action, action_date
""")

action_sectors = defaultdict(lambda: defaultdict(int))

for row in cursor.fetchall():
    sector = classify_bill_sector(row["title"])
    action_sectors[row["action"]][sector] += 1

print("\nSample Executive Actions by Sector:")
print("-" * 80)

cursor.execute("""
    SELECT action, title, bill_number
    FROM governor_actions
    WHERE governor = 'Spanberger'
    LIMIT 15
""")

for row in cursor.fetchall():
    sector = classify_bill_sector(row["title"])
    print(f"  [{row['action']:10}] {row['bill_number']:8} -> {sector:15}")

print()

# ── Step 4: Consolidate Correlation ───────────────────────────────────────────

print("[STEP 4] Correlation Analysis")
print("=" * 80)

# Get all actions by sector
all_sector_actions = defaultdict(int)
for action_type, sectors in action_sectors.items():
    for sector, count in sectors.items():
        all_sector_actions[sector] += count

correlation_data = []

for sector in sorted(set(list(sector_donations.keys()) + list(all_sector_actions.keys()))):
    donated = sector_donations.get(sector, {}).get("amount", 0)
    action_count = all_sector_actions.get(sector, 0)

    correlation_data.append({
        "sector": sector,
        "donated": donated,
        "actions": action_count,
    })

correlation_data.sort(key=lambda x: x["donated"], reverse=True)

print()
print("SECTOR DONATIONS vs EXECUTIVE ACTIONS/ORDERS")
print("-" * 80)
print(f"{'Sector':<20} {'Donations':>15} {'% of Total':>12} {'Actions':>8} {'Pattern':>15}")
print("-" * 80)

total_donated = sum(d["donated"] for d in correlation_data)

for item in correlation_data:
    sector = item["sector"]
    donated = item["donated"]
    pct = (donated / total_donated * 100) if total_donated > 0 else 0
    actions = item["actions"]

    # Classify pattern
    if actions > 2 and donated > 5_000_000:
        pattern = "[OK] Strong support"
    elif actions > 2 and donated < 500_000:
        pattern = "[!] Policy priority"
    elif actions == 0 and donated > 1_000_000:
        pattern = "[?] No action taken"
    else:
        pattern = "[--]"

    print(f"{sector:<20} ${donated:>14,.0f} {pct:>11.1f}% {actions:>8} {pattern:>15}")

print("-" * 80)
print(f"{'TOTAL':<20} ${total_donated:>14,.0f} {100.0:>11.1f}%")
print()

# ── Step 5: Summary ───────────────────────────────────────────────────────────

print()
print("SUMMARY")
print("=" * 80)

strong_support = [d for d in correlation_data if d["actions"] > 2 and d["donated"] > 5_000_000]
policy_priority = [d for d in correlation_data if d["actions"] > 2 and d["donated"] < 500_000]
no_action = [d for d in correlation_data if d["actions"] == 0 and d["donated"] > 1_000_000]

if strong_support:
    print("\n[OK] SECTORS WITH HIGH DONATIONS & MULTIPLE ACTIONS:")
    for item in strong_support:
        print(f"    - {item['sector']}: ${item['donated']:,.0f} + {item['actions']} action(s)")

if policy_priority:
    print("\n[!] SECTORS WITH ACTIONS BUT LOW DONATIONS:")
    print("  (Policy-driven, not donor-driven)")
    for item in policy_priority:
        print(f"    - {item['sector']}: ${item['donated']:,.0f} + {item['actions']} action(s)")

if no_action:
    print("\n[?] SECTORS WITH DONATIONS BUT NO ACTIONS:")
    for item in no_action:
        print(f"    - {item['sector']}: ${item['donated']:,.0f}")

print()
print("=" * 80)
print("NOTE: Executive actions include signed, amended, pending, and overridden actions")
print("=" * 80)

polls_db.close()
