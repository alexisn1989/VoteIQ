# ── openstates_va.db schema reference ─────────────────────────────────────────
# bills:  bill_id | session | title | subjects | sponsors | result | openstates_url
# votes:  id | bill_id | session | vote_date | chamber | motion | result
#         voter_name | option | party | district
#           option values : 'yes' / 'no' / 'not voting' / 'abstain'
#           chamber values: 'Senate' / 'House'
# JOIN:   votes.bill_id = bills.bill_id AND votes.session = bills.session
# ──────────────────────────────────────────────────────────────────────────────

import sqlite3

conn = sqlite3.connect('openstates_va.db')
cursor = conn.cursor()

# Totals by chamber
cursor.execute("""
    SELECT v.chamber, v.option, COUNT(*) as cnt
    FROM votes v
    JOIN bills b ON v.bill_id = b.bill_id AND v.session = b.session
    WHERE b.bill_id = 'HB665'
    GROUP BY v.chamber, v.option
    ORDER BY v.chamber, v.option
""")
rows = cursor.fetchall()

senate = {r[1]: r[2] for r in rows if r[0] == 'Senate'}
house  = {r[1]: r[2] for r in rows if r[0] == 'House'}

print("=== HB665 — House of Delegates Vote ===")
print(f"  YES:        {house.get('yes', 0)}")
print(f"  NO:         {house.get('no', 0)}")
print(f"  Not voting: {house.get('not voting', 0)}")

print("\n=== HB665 — State Senate Vote ===")
print(f"  YES:        {senate.get('yes', 0)}")
print(f"  NO:         {senate.get('no', 0)}")
print(f"  Not voting: {senate.get('not voting', 0)}")

# Motion detail per chamber
print("\n=== Breakdown by motion ===")
cursor.execute("""
    SELECT v.chamber, v.motion, v.option, COUNT(*) as cnt
    FROM votes v
    JOIN bills b ON v.bill_id = b.bill_id AND v.session = b.session
    WHERE b.bill_id = 'HB665'
    GROUP BY v.chamber, v.motion, v.option
    ORDER BY v.chamber, v.motion, v.option
""")
for chamber, motion, option, cnt in cursor.fetchall():
    print(f"  [{chamber}] {motion} | {option}: {cnt}")

conn.close()
