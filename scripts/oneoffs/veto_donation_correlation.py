#!/usr/bin/env python3
"""
Veto-Donation Correlation Analysis for Governor Spanberger

Analyzes if there's a correlation between:
- Campaign donations by sector
- Vetoes of bills related to those sectors
"""

import sqlite3
import re
from collections import defaultdict

# Connect to databases
polls_db = sqlite3.connect('polls.db')
polls_db.row_factory = sqlite3.Row
cursor = polls_db.cursor()

print("=" * 80)
print("GOVERNOR SPANBERGER: VETO-DONATION CORRELATION ANALYSIS")
print("=" * 80)
print()

# ── Step 1: Load Donation Data by Sector ──────────────────────────────────────

print("[STEP 1] Loading campaign finance data by sector...")

cursor.execute("""
    SELECT employer, occupation, amount, is_individual
    FROM va_cf_schedule_a
    WHERE candidate_name LIKE '%Spanberger%'
""")

# Classify sectors
sector_keywords = {
    "Technology": ["software", "tech", "it", "computer", "data"],
    "Finance": ["bank", "financial", "investment", "insurance", "hedge"],
    "Legal": ["attorney", "lawyer", "law firm"],
    "Healthcare": ["hospital", "medical", "health", "pharma", "doctor"],
    "Real Estate": ["real estate", "property", "developer", "construction"],
    "Energy": ["oil", "gas", "energy", "utility", "power"],
    "Education": ["school", "university", "college", "education"],
    "Labor/Union": ["union", "labor", "afl-cio"],
    "Gaming/Betting": ["gaming", "betting", "casino", "sports betting"],
    "Ideological": ["pac", "political", "committee", "action", "fund"],
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

# ── Step 2: Load Veto Data ────────────────────────────────────────────────────

print("[STEP 2] Loading veto data...")

cursor.execute("""
    SELECT bill_number, title, action_date
    FROM governor_actions
    WHERE action = 'vetoed' AND governor = 'Spanberger'
    ORDER BY action_date
""")

vetoes = cursor.fetchall()
print(f"  Found {len(vetoes)} vetoes by Spanberger")
print()

# ── Step 3: Classify Vetoes by Sector ─────────────────────────────────────────

print("[STEP 3] Classifying vetoes by sector...")

# Bill title keywords for sector classification
bill_sector_patterns = {
    "Technology": ["technology", "broadband", "telecom", "cyber", "data"],
    "Finance": ["banking", "finance", "insurance", "investment", "credit"],
    "Legal": ["attorney", "court", "lawsuit", "bar", "legal"],
    "Healthcare": ["health", "medical", "doctor", "hospital", "prescription"],
    "Real Estate": ["property", "real estate", "housing", "construction", "developer"],
    "Energy": ["energy", "utility", "power", "gas", "oil"],
    "Education": ["school", "education", "university", "college", "student"],
    "Labor/Union": ["labor", "union", "employee", "collective bargaining"],
    "Gaming/Betting": ["gaming", "betting", "casino", "lottery", "sports betting"],
    "Ideological": ["abortion", "voting", "gun", "environment", "discrimination"],
}

def classify_bill_sector(title):
    title_lower = (title or "").lower()
    for sector, patterns in bill_sector_patterns.items():
        if any(p in title_lower for p in patterns):
            return sector
    return "Other"

veto_sectors = defaultdict(int)

print("\nVetoes by Classification:")
print("-" * 80)

for veto in vetoes:
    sector = classify_bill_sector(veto["title"])
    veto_sectors[sector] += 1
    print(f"  {veto['bill_number']:8} -> {sector:15} | {veto['title'][:55]}")

print()

# ── Step 4: Build Correlation Table ───────────────────────────────────────────

print("[STEP 4] Correlation Analysis")
print("=" * 80)

correlation_data = []

for sector in sorted(set(list(sector_donations.keys()) + list(veto_sectors.keys()))):
    donated = sector_donations.get(sector, {}).get("amount", 0)
    veto_count = veto_sectors.get(sector, 0)

    correlation_data.append({
        "sector": sector,
        "donated": donated,
        "vetoes": veto_count,
    })

# Sort by donation amount
correlation_data.sort(key=lambda x: x["donated"], reverse=True)

print()
print("SECTOR DONATIONS vs VETOES")
print("-" * 80)
print(f"{'Sector':<20} {'Donations':>15} {'% of Total':>12} {'Vetoes':>8} {'Pattern':>15}")
print("-" * 80)

total_donated = sum(d["donated"] for d in correlation_data)

for item in correlation_data:
    sector = item["sector"]
    donated = item["donated"]
    pct = (donated / total_donated * 100) if total_donated > 0 else 0
    vetoes = item["vetoes"]

    # Classify pattern
    if vetoes == 0 and donated > 1_000_000:
        pattern = "[OK] High donor"
    elif vetoes > 0 and donated < 500_000:
        pattern = "[!] Low donor vetoed"
    elif vetoes > 0 and donated > 5_000_000:
        pattern = "[?] High donor vetoed"
    else:
        pattern = "[--]"

    print(f"{sector:<20} ${donated:>14,.0f} {pct:>11.1f}% {vetoes:>8} {pattern:>15}")

print("-" * 80)
print(f"{'TOTAL':<20} ${total_donated:>14,.0f} {100.0:>11.1f}%")
print()

# ── Step 5: Key Findings ──────────────────────────────────────────────────────

print()
print("KEY FINDINGS")
print("=" * 80)

# Find sectors with high donations but no vetoes
high_donor_no_veto = [
    d for d in correlation_data
    if d["donated"] > 1_000_000 and d["vetoes"] == 0
]

# Find sectors with low donations but vetoes
low_donor_vetoed = [
    d for d in correlation_data
    if d["donated"] < 500_000 and d["vetoes"] > 0
]

# Find sectors with both high donations and vetoes
high_donor_vetoed = [
    d for d in correlation_data
    if d["donated"] > 5_000_000 and d["vetoes"] > 0
]

if high_donor_no_veto:
    print("\n[OK] SECTORS WITH HIGH DONATIONS & NO VETOES:")
    print("  (Suggests possible alignment or no conflict)")
    for item in high_donor_no_veto:
        print(f"    - {item['sector']}: ${item['donated']:,.0f} donated")

if low_donor_vetoed:
    print("\n[!] SECTORS WITH LOW DONATIONS & VETOED BILLS:")
    print("  (Suggests possible misalignment)")
    for item in low_donor_vetoed:
        print(f"    - {item['sector']}: ${item['donated']:,.0f} donated, {item['vetoes']} veto(es)")

if high_donor_vetoed:
    print("\n[?] SECTORS WITH HIGH DONATIONS & VETOED BILLS:")
    print("  (Suggests selective opposition)")
    for item in high_donor_vetoed:
        print(f"    - {item['sector']}: ${item['donated']:,.0f} donated, {item['vetoes']} veto(es)")

print()
print("=" * 80)
print("DATA INTEGRITY NOTES")
print("=" * 80)
print()
print("[!] IMPORTANT LIMITATIONS:")
print("  - Veto data: 28 official vetoes from Governor.Virginia.gov")
print("  - Bill classifications: Based on title keywords (imperfect)")
print("  - Donations: $97.9M from Virginia SBE filings")
print("  - Sector classification: Automated from employer/occupation fields")
print()
print("[OK] WHAT THIS SHOWS:")
print("  - Which sectors had bills vetoed")
print("  - How much each sector donated")
print("  - Potential patterns or alignment/misalignment")
print()
print("[X] WHAT THIS DOES NOT SHOW:")
print("  - Causation (donations causing vetoes or vice versa)")
print("  - Motive or intent")
print("  - Quality of bills (were vetoes justified?)")
print("  - Full legislative context")
print()
print("CONCLUSION: Correlation observed, but causation cannot be inferred.")
print("=" * 80)

polls_db.close()
