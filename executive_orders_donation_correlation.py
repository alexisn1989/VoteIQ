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

# ── Step 2: Load Executive Orders and Governor Actions ────────────────────────

print("[STEP 2] Loading executive orders and governor actions...")

# Executive orders
cursor.execute("SELECT COUNT(*) FROM executive_orders WHERE governor = 'Spanberger'")
exec_orders_count = cursor.fetchone()[0]

# Governor actions (vetoes, signed bills, etc.)
cursor.execute("""
    SELECT action, COUNT(*) as count
    FROM governor_actions
    WHERE governor = 'Spanberger'
    GROUP BY action
""")

actions_summary = cursor.fetchall()
print(f"\nExecutive Orders: {exec_orders_count} orders")
print("Governor Actions Summary:")
for row in actions_summary:
    print(f"  {row[0]:20} : {row[1]:3} actions")

print()

# ── Step 3: Classify Actions by Sector ────────────────────────────────────────

print("[STEP 3] Classifying executive orders and governor actions by sector...")

sector_patterns = {
    "Technology": ["technology", "broadband", "telecom", "cyber", "data"],
    "Finance": ["banking", "finance", "insurance", "credit", "business"],
    "Legal": ["attorney", "court", "justice", "legal"],
    "Healthcare": ["health", "medical", "hospital", "prescription"],
    "Real Estate": ["property", "housing", "construction", "real estate"],
    "Energy": ["energy", "utility", "power", "renewable", "clean energy"],
    "Education": ["school", "education", "university", "student"],
    "Labor/Union": ["labor", "union", "employee", "wage", "worker"],
    "Environment": ["environment", "climate", "pollution", "water", "conservation"],
    "Ideological": ["voting", "abortion", "gun", "rights", "discrimination"],
}

def classify_sector(title):
    title_lower = (title or "").lower()
    for sector, patterns in sector_patterns.items():
        if any(p in title_lower for p in patterns):
            return sector
    return "Other"

# Classify executive orders by sector
exec_order_sectors = defaultdict(int)
cursor.execute("""
    SELECT title, sector
    FROM executive_orders
    WHERE governor = 'Spanberger'
    ORDER BY issue_date
""")

print("\nExecutive Orders by Sector:")
print("-" * 80)

for row in cursor.fetchall():
    # Use the provided sector if available, otherwise classify from title
    sector = row[1] if row[1] and row[1] != "Other" else classify_sector(row[0])
    exec_order_sectors[sector] += 1
    print(f"  [EXEC_ORDER] {row[0][:55]:55} -> {sector:15}")

# Classify governor actions (vetoes, signed bills) by sector
action_sectors = defaultdict(lambda: defaultdict(int))
cursor.execute("""
    SELECT action, title
    FROM governor_actions
    WHERE governor = 'Spanberger'
    ORDER BY action_date
""")

print("\nGovernor Actions by Sector (sample, showing 10):")
print("-" * 80)

for i, row in enumerate(cursor.fetchall()):
    if i < 10:
        sector = classify_sector(row[1])
        print(f"  [{row[0]:10}] {row[1][:50]:50} -> {sector:15}")
    sector = classify_sector(row[1])
    action_sectors[row[0]][sector] += 1

if len(cursor.fetchall()) > 10:
    print(f"  ... and more")

print()

# ── Step 4: Consolidate Correlation ───────────────────────────────────────────

print("[STEP 4] Correlation Analysis")
print("=" * 80)

# Combine executive orders and governor actions
all_sector_actions = defaultdict(int)

# Add executive orders
for sector, count in exec_order_sectors.items():
    all_sector_actions[sector] += count

# Add governor actions (vetoes, signed bills, etc.)
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
print("SECTOR DONATIONS vs EXECUTIVE ORDERS + GOVERNOR ACTIONS")
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
    if actions >= 2 and donated > 5_000_000:
        pattern = "[OK] Strong align"
    elif actions >= 2 and donated < 500_000:
        pattern = "[!] Policy focus"
    elif actions == 0 and donated > 1_000_000:
        pattern = "[?] No action"
    elif actions >= 1 and donated > 0:
        pattern = "[--] Mixed"
    else:
        pattern = "[--]"

    print(f"{sector:<20} ${donated:>14,.0f} {pct:>11.1f}% {actions:>8} {pattern:>15}")

print("-" * 80)
print(f"{'TOTAL':<20} ${total_donated:>14,.0f} {100.0:>11.1f}%")
print(f"Note: Actions include {exec_order_sectors.__len__()} exec orders + governor actions")
print()

# ── Step 5: Summary ───────────────────────────────────────────────────────────

print()
print("KEY FINDINGS")
print("=" * 80)

strong_align = [d for d in correlation_data if d["actions"] >= 2 and d["donated"] > 5_000_000]
policy_focus = [d for d in correlation_data if d["actions"] >= 2 and d["donated"] < 500_000]
no_action = [d for d in correlation_data if d["actions"] == 0 and d["donated"] > 1_000_000]

if strong_align:
    print("\n[OK] SECTORS WITH HIGH DONATIONS & EXECUTIVE ACTION:")
    print("  (Strong alignment between donor base and policy action)")
    for item in strong_align:
        print(f"    - {item['sector']}: ${item['donated']:,.0f} donated, {item['actions']} action(s)")

if policy_focus:
    print("\n[!] SECTORS WITH EXECUTIVE ACTION & LOW DONATIONS:")
    print("  (Policy-driven initiatives regardless of donor support)")
    for item in policy_focus:
        print(f"    - {item['sector']}: ${item['donated']:,.0f} donated, {item['actions']} action(s)")

if no_action:
    print("\n[?] SECTORS WITH HIGH DONATIONS & NO ACTION:")
    print("  (Major donors with no corresponding executive action)")
    for item in no_action:
        print(f"    - {item['sector']}: ${item['donated']:,.0f}")

print()
print("=" * 80)
print("ANALYSIS SCOPE")
print("=" * 80)
print(f"  - Executive Orders: {exec_order_sectors.__len__()} orders issued")
total_gov_actions = sum(len(v) for v in action_sectors.values())
print(f"  - Governor Actions: {total_gov_actions} vetoes/signed bills/other")
print(f"  - Campaign Donations: ${total_donated:,.0f} from {len(sector_donations)} sectors")
print()
print("INTERPRETATION NOTES:")
print("  - Actions include: executive orders, vetoes, signed bills, amended bills")
print("  - Correlation analysis shows pattern of alignment (or lack thereof)")
print("  - Causation cannot be inferred from correlation alone")
print("=" * 80)

polls_db.close()
