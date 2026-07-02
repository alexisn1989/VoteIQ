import sqlite3
conn = sqlite3.connect('polls.db')
rows = conn.execute('SELECT bioguide_id, member_name, industry, total_amount FROM fec_industry_totals ORDER BY bioguide_id, total_amount DESC').fetchall()
from collections import defaultdict
by_member = defaultdict(list)
for r in rows:
    by_member[r[0]].append(r)
for bgid, entries in by_member.items():
    top = entries[0]
    print(f"{bgid} ({top[1]}): {len(entries)} industries, top={top[2]} ${top[3]:,.0f}")
conn.close()
