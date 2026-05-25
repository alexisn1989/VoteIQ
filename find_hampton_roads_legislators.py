"""
Find and list all Hampton Roads area state legislators
Hampton Roads includes: Virginia Beach, Norfolk, Newport News, Williamsburg, and surrounding areas
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
print("HAMPTON ROADS STATE LEGISLATORS")
print("=" * 90)

# Known Hampton Roads area state senators and delegates
# Based on district assignments for Virginia Beach, Norfolk, Newport News, Williamsburg areas

hampton_roads_legislators = {
    'Senators': [
        'Kiggans',  # Virginia Beach
        'Marsden',  # Norfolk/Virginia Beach
        'Deeds',    # Eastern area
        'Locke',    # Norfolk area
        'McPike',   # Peninsula area
        'Barker',   # Virginia Beach
    ],
    'Delegates': [
        'Luria',    # Virginia Beach/Norfolk
        'Hunt',     # Virginia Beach
        'Kiggans',  # Virginia Beach (also delegate)
        'Anderson', # Norfolk/Virginia Beach
        'Bulova',   # Arlington (not Hampton Roads, remove)
        'Sickles',  # Arlington (not Hampton Roads, remove)
        'Cole',     # Peninsula
        'Hodges',   # Virginia Beach
        'Helmer',   # Peninsula
        'Hope',     # Virginia Beach
        'Seibold',  # Virginia Beach
        'Stolle',   # Virginia Beach area
        'Yoon',     # Virginia Beach
        'Hackworth',# Peninsula
        'Bennett-Parker', # Peninsula
        'Garrett',  # Peninsula
        'Campbell', # Peninsula
        'Nolan',    # Peninsula
    ]
}

print("\nSearching for Hampton Roads legislators in databases...\n")

# Search in bills database for bill introducers
va_cursor.execute("SELECT DISTINCT Patron_name FROM bills ORDER BY Patron_name")
all_patrons = [row[0] for row in va_cursor.fetchall()]

# Match against known names
found_legislators = {
    'Senators': [],
    'Delegates': [],
}

print("MATCHING AGAINST BILL INTRODUCERS:\n")

for legislator in hampton_roads_legislators['Senators']:
    for patron in all_patrons:
        if legislator.lower() in patron.lower():
            found_legislators['Senators'].append(patron)
            print(f"Senator: {patron}")

            # Get their bills
            va_cursor.execute("""
                SELECT COUNT(*) FROM bills WHERE Patron_name = ?
            """, (patron,))
            bills = va_cursor.fetchone()[0]
            print(f"  Bills introduced: {bills}\n")
            break

for legislator in hampton_roads_legislators['Delegates']:
    for patron in all_patrons:
        if legislator.lower() in patron.lower():
            found_legislators['Delegates'].append(patron)
            print(f"Delegate: {patron}")

            # Get their bills
            va_cursor.execute("""
                SELECT COUNT(*) FROM bills WHERE Patron_name = ?
            """, (patron,))
            bills = va_cursor.fetchone()[0]
            print(f"  Bills introduced: {bills}\n")
            break

# Search in campaign finance database
print("\n" + "=" * 90)
print("MATCHING AGAINST CAMPAIGN FINANCE DATA:\n")

polls_cursor.execute("""
    SELECT DISTINCT candidate_name
    FROM va_cf_schedule_a
    ORDER BY candidate_name
""")

all_candidates = [row[0] for row in polls_cursor.fetchall()]

hampton_roads_with_funding = {
    'Senators': [],
    'Delegates': [],
}

# Match senators
for legislator_name in found_legislators['Senators']:
    for candidate in all_candidates:
        if legislator_name.lower() in candidate.lower() or candidate.lower() in legislator_name.lower():
            polls_cursor.execute("""
                SELECT SUM(amount), COUNT(*) FROM va_cf_schedule_a
                WHERE candidate_name = ?
            """, (candidate,))
            result = polls_cursor.fetchone()
            total = result[0] if result and result[0] else 0
            count = result[1] if result and result[1] else 0

            if total > 100000:  # Only if they have significant funding
                hampton_roads_with_funding['Senators'].append({
                    'name': candidate,
                    'patron_name': legislator_name,
                    'total_raised': total,
                    'contributions': count,
                })
                print(f"Senator: {candidate}")
                print(f"  Raised: ${total:,.0f} from {count} contributions\n")
            break

# Match delegates
for legislator_name in found_legislators['Delegates']:
    for candidate in all_candidates:
        if legislator_name.lower() in candidate.lower() or candidate.lower() in legislator_name.lower():
            polls_cursor.execute("""
                SELECT SUM(amount), COUNT(*) FROM va_cf_schedule_a
                WHERE candidate_name = ?
            """, (candidate,))
            result = polls_cursor.fetchone()
            total = result[0] if result and result[0] else 0
            count = result[1] if result and result[1] else 0

            if total > 100000:  # Only if they have significant funding
                hampton_roads_with_funding['Delegates'].append({
                    'name': candidate,
                    'patron_name': legislator_name,
                    'total_raised': total,
                    'contributions': count,
                })
                print(f"Delegate: {candidate}")
                print(f"  Raised: ${total:,.0f} from {count} contributions\n")
            break

# Summary
print("\n" + "=" * 90)
print("SUMMARY: HAMPTON ROADS LEGISLATORS WITH CAMPAIGN FINANCE DATA")
print("=" * 90)

total_senators = len(hampton_roads_with_funding['Senators'])
total_delegates = len(hampton_roads_with_funding['Delegates'])

print(f"\nState Senators: {total_senators}")
for leg in hampton_roads_with_funding['Senators']:
    print(f"  - {leg['name']}: ${leg['total_raised']:,.0f}")

print(f"\nHouse Delegates: {total_delegates}")
for leg in hampton_roads_with_funding['Delegates']:
    print(f"  - {leg['name']}: ${leg['total_raised']:,.0f}")

print(f"\nTotal Hampton Roads Legislators with Funding Data: {total_senators + total_delegates}")

va_conn.close()
polls_conn.close()

print("\n" + "=" * 90)
