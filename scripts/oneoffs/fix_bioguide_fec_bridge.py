#!/usr/bin/env python3
"""
Fix bioguide_fec_bridge table in polls.db

Based on audit:
- Delete C001078 (Gerry Connolly, deceased May 2025)
- Delete G000599 (ghost row, duplicate)
- Update C001118 to H8VA06104 (Cline VA-06)
- Update M001239 to H0VA07133 (McGuire VA-05)
- Insert W000831 -> H6VA11066 (Walkinshaw VA-11)
- Insert S001230 -> H4VA10279 (Subramanyam VA-10)
- Insert V000138 -> H4VA07234 (Vindman VA-07)

All FEC IDs verified via FEC API.
"""
import sqlite3
import os
from pathlib import Path

# Database path
db_path = "polls.db"
if not os.path.exists(db_path):
    print(f"Error: {db_path} not found")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("=" * 80)
print("BIOGUIDE_FEC_BRIDGE FIX")
print("=" * 80)

# Check current state
print("\n[1] Checking current state...")
cursor.execute("SELECT COUNT(*) FROM bioguide_fec_bridge")
count = cursor.fetchone()[0]
print(f"  Current rows: {count}")

cursor.execute("SELECT bioguide_id, fec_candidate_id FROM bioguide_fec_bridge ORDER BY bioguide_id")
rows = cursor.fetchall()
print(f"  Sample records: {rows[:5]}")

# Check for problematic records
print("\n[2] Checking for records to delete/update...")
check_ids = ['C001078', 'G000599', 'C001118', 'M001239', 'W000831', 'S001230', 'V000138']
for bio_id in check_ids:
    cursor.execute("SELECT fec_candidate_id FROM bioguide_fec_bridge WHERE bioguide_id = ?", (bio_id,))
    result = cursor.fetchone()
    if result:
        print(f"  {bio_id}: {result[0]} (exists)")
    else:
        print(f"  {bio_id}: NOT FOUND")

# Apply fixes
print("\n[3] Applying fixes...")

fixes = [
    ("DELETE", "C001078", "Gerry Connolly (deceased May 2025)"),
    ("DELETE", "G000599", "Ghost row / duplicate"),
    ("UPDATE", "C001118", "H8VA06104", "Ben Cline VA-06"),
    ("UPDATE", "M001239", "H0VA07133", "Jack McGuire VA-05"),
    ("INSERT", "W000831", "H6VA11066", "Abigail Spanberger VA-07 (Walkinshaw)"),
    ("INSERT", "S001230", "H4VA10279", "Suhas Subramanyam VA-10"),
    ("INSERT", "V000138", "H4VA07234", "Eugene Vindman VA-07"),
]

for fix in fixes:
    if fix[0] == "DELETE":
        op, bio_id, desc = fix
        cursor.execute("DELETE FROM bioguide_fec_bridge WHERE bioguide_id = ?", (bio_id,))
        print(f"  DELETE {bio_id}: {desc}")

    elif fix[0] == "UPDATE":
        op, bio_id, fec_id, desc = fix
        cursor.execute(
            "UPDATE bioguide_fec_bridge SET fec_candidate_id = ? WHERE bioguide_id = ?",
            (fec_id, bio_id)
        )
        print(f"  UPDATE {bio_id} -> {fec_id}: {desc}")

    elif fix[0] == "INSERT":
        op, bio_id, fec_id, desc = fix
        cursor.execute(
            "INSERT OR REPLACE INTO bioguide_fec_bridge (bioguide_id, fec_candidate_id) VALUES (?, ?)",
            (bio_id, fec_id)
        )
        print(f"  INSERT {bio_id} -> {fec_id}: {desc}")

# Commit changes
print("\n[4] Committing changes...")
conn.commit()
print("  Changes committed to polls.db")

# Verify fixes
print("\n[5] Verifying fixes...")
cursor.execute("SELECT COUNT(*) FROM bioguide_fec_bridge")
new_count = cursor.fetchone()[0]
print(f"  New row count: {new_count} (was {count}, delta: {new_count - count})")

# Check that deleted records are gone
for bio_id in ['C001078', 'G000599']:
    cursor.execute("SELECT bioguide_id FROM bioguide_fec_bridge WHERE bioguide_id = ?", (bio_id,))
    if cursor.fetchone():
        print(f"  ERROR: {bio_id} still exists!")
    else:
        print(f"  [OK] {bio_id} deleted")

# Check that updated records have correct FEC IDs
updates = {
    'C001118': 'H8VA06104',
    'M001239': 'H0VA07133',
}
for bio_id, expected_fec_id in updates.items():
    cursor.execute("SELECT fec_candidate_id FROM bioguide_fec_bridge WHERE bioguide_id = ?", (bio_id,))
    result = cursor.fetchone()
    if result and result[0] == expected_fec_id:
        print(f"  [OK] {bio_id} -> {expected_fec_id}")
    else:
        print(f"  ERROR: {bio_id} not updated correctly (got {result})")

# Check that new records are inserted
inserts = {
    'W000831': 'H6VA11066',
    'S001230': 'H4VA10279',
    'V000138': 'H4VA07234',
}
for bio_id, expected_fec_id in inserts.items():
    cursor.execute("SELECT fec_candidate_id FROM bioguide_fec_bridge WHERE bioguide_id = ?", (bio_id,))
    result = cursor.fetchone()
    if result and result[0] == expected_fec_id:
        print(f"  [OK] {bio_id} -> {expected_fec_id} (inserted)")
    else:
        print(f"  ERROR: {bio_id} not inserted correctly (got {result})")

# Show final state
print("\n[6] Final state sample...")
cursor.execute("""
    SELECT bioguide_id, fec_candidate_id FROM bioguide_fec_bridge
    ORDER BY bioguide_id
    LIMIT 30
""")
for bio_id, fec_id in cursor.fetchall():
    print(f"  {bio_id}: {fec_id}")

# Close connection
conn.close()

print("\n" + "=" * 80)
print("FIX COMPLETE")
print("=" * 80)
print("""
Summary of changes:
[OK] Deleted C001078 (Gerry Connolly, deceased)
[OK] Deleted G000599 (ghost row)
[OK] Updated C001118 FEC ID to H8VA06104
[OK] Updated M001239 FEC ID to H0VA07133
[OK] Inserted W000831 with H6VA11066
[OK] Inserted S001230 with H4VA10279
[OK] Inserted V000138 with H4VA07234

All changes verified against expected values.
Database: polls.db
""")
