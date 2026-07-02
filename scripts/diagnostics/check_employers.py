import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
conn = sqlite3.connect('polls.db')
conn.row_factory = sqlite3.Row

rows = conn.execute("""
    SELECT employer, ROUND(SUM(amount),0) as total, COUNT(*) as cnt
    FROM va_cf_schedule_a
    WHERE is_individual=1 AND amount>0 AND employer IS NOT NULL AND employer != ''
    GROUP BY lower(trim(employer))
    ORDER BY total DESC LIMIT 30
""").fetchall()
for r in rows:
    emp = (r["employer"] or "")[:50]
    print(f"{emp:<50}  ${r['total']:>12,.0f}  ({r['cnt']} donors)")
conn.close()
