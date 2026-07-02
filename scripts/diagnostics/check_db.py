import sqlite3
conn = sqlite3.connect('polls.db')
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print('Tables:', tables)
for t in ['congress_members', 'congress_bills', 'congress_votes', 'fec_industry_totals']:
    if t in tables:
        n = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        print(f'  {t}: {n} rows')
    else:
        print(f'  {t}: MISSING')
conn.close()
