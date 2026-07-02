#!/usr/bin/env python3
"""
Signed Bills-Donation Correlation Analysis for Governor Spanberger

Analyzes if there's a correlation between:
- Campaign donations by sector
- Bills signed into law from those sectors
"""

import sqlite3
from collections import defaultdict

polls_db = sqlite3.connect('polls.db')
polls_db.row_factory = sqlite3.Row
cursor = polls_db.cursor()

print("=" * 80)
print("GOVERNOR SPANBERGER: SIGNED BILLS-DONATION CORRELATION ANALYSIS")
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
    "Real Estate": ["real estate", "property", "developer", "construction"],
    "Energy": ["oil", "gas", "energy", "utility", "power"],
    "Education": ["school", "university", "college"],
    "Labor/Union": ["union", "labor", "afl-cio"],
    "Gaming/Betting": ["gaming", "betting", "casino"],
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

# ── Step 2: Load Signed Bills ─────────────────────────────────────────────────

print("[STEP 2] Loading signed bills...")

cursor.execute("""
    SELECT bill_number, title, action_date
    FROM governor_actions
    WHERE action = 'signed' AND governor = 'Spanberger'
    ORDER BY action_date
""")

signed_bills = cursor.fetchall()
print(f"  Found {len(signed_bills)} bills signed by Spanberger")
print()

# ── Step 3: Classify Signed Bills by Sector ───────────────────────────────────

print("[STEP 3] Classifying signed bills by sector...")

bill_sector_patterns = {
    "Technology": ["technology", "broadband", "telecom", "cyber"],
    "Finance": ["banking", "finance", "insurance"],
    "Legal": ["attorney", "court", "lawsuit"],
    "Healthcare": ["health", "medical", "prescription"],
    "Real Estate": ["property", "housing", "construction"],
    "Energy": ["energy", "utility", "power"],
    "Education": ["school", "education", "university"],
    "Labor/Union": ["labor", "union", "employee"],
    "Gaming/Betting": ["gaming", "betting", "casino"],
    "Ideological": ["abortion", "voting", "gun", "environment"],
}

def classify_bill_sector(title):
    title_lower = (title or "").lower()
    for sector, patterns in bill_sector_patterns.items():
        if any(p in title_lower for p in patterns):
            return sector
    return "Other"

signed_sectors = defaultdict(int)

print("\nSigned Bills by Classification (showing first 20):")
print("-" * 80)

for i, bill in enumerate(signed_bills[:20]):
    sector = classify_bill_sector(bill["title"])
    signed_sectors[sector] += 1
    print(f"  {bill['bill_number']:8} -> {sector:15} | {bill['title'][:50]}")

# Count remaining
for bill in signed_bills[20:]:
    sector = classify_bill_sector(bill["title"])
    signed_sectors[sector] += 1

print(f"  ... and {len(signed_bills) - 20} more")
print()

# ── Step 4: Build Correlation Table ───────────────────────────────────────────

print("[STEP 4] Correlation Analysis")
print("=" * 80)

correlation_data = []

for sector in sorted(set(list(sector_donations.keys()) + list(signed_sectors.keys()))):
    donated = sector_donations.get(sector, {}).get("amount", 0)
    signed_count = signed_sectors.get(sector, 0)

    correlation_data.append({
        "sector": sector,
        "donated": donated,
        "signed": signed_count,
    })

correlation_data.sort(key=lambda x: x["donated"], reverse=True)

print()
print("SECTOR DONATIONS vs SIGNED BILLS")
print("-" * 80)
print(f"{'Sector':<20} {'Donations':>15} {'% of Total':>12} {'Bills':>8} {'Pattern':>15}")
print("-" * 80)

total_donated = sum(d["donated"] for d in correlation_data)

for item in correlation_data:
    sector = item["sector"]
    donated = item["donated"]
    pct = (donated / total_donated * 100) if total_donated > 0 else 0
    signed = item["signed"]

    # Classify pattern
    if signed > 0 and donated > 5_000_000:
        pattern = "[OK] High donor bills"
    elif signed > 0 and donated < 500_000:
        pattern = "[!] Low donor bills"
    elif signed == 0 and donated > 1_000_000:
        pattern = "[?] No bills signed"
    else:
        pattern = "[--]"

    print(f"{sector:<20} ${donated:>14,.0f} {pct:>11.1f}% {signed:>8} {pattern:>15}")

print("-" * 80)
print(f"{'TOTAL':<20} ${total_donated:>14,.0f} {100.0:>11.1f}%")
print()

# ── Step 5: Key Findings ──────────────────────────────────────────────────────

print()
print("KEY FINDINGS")
print("=" * 80)

high_donor_bills_signed = [
    d for d in correlation_data
    if d["donated"] > 5_000_000 and d["signed"] > 0
]

low_donor_bills_signed = [
    d for d in correlation_data
    if d["donated"] < 500_000 and d["signed"] > 0
]

high_donor_no_bills = [
    d for d in correlation_data
    if d["donated"] > 1_000_000 and d["signed"] == 0
]

if high_donor_bills_signed:
    print("\n[OK] SECTORS WITH HIGH DONATIONS & BILLS SIGNED:")
    print("  (Suggests strong sector support for legislation)")
    for item in high_donor_bills_signed:
        print(f"    - {item['sector']}: ${item['donated']:,.0f} donated, {item['signed']} bill(s) signed")

if low_donor_bills_signed:
    print("\n[!] SECTORS WITH LOW DONATIONS & BILLS SIGNED:")
    print("  (Suggests policy priority over donor base)")
    for item in low_donor_bills_signed:
        print(f"    - {item['sector']}: ${item['donated']:,.0f} donated, {item['signed']} bill(s) signed")

if high_donor_no_bills:
    print("\n[?] SECTORS WITH HIGH DONATIONS & NO BILLS SIGNED:")
    print("  (Suggests blocked legislation or no bills proposed)")
    for item in high_donor_no_bills:
        print(f"    - {item['sector']}: ${item['donated']:,.0f} donated")

print()
print("=" * 80)
print("DATA NOTES: {0} bills signed, {1} sectors analyzed".format(
    len(signed_bills), len(signed_sectors)))
print("=" * 80)

polls_db.close()
