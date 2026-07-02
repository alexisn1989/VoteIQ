import sqlite3

# Check polls.db
print('Tables in polls.db containing congress/member:')
conn = sqlite3.connect(r'c:\Users\Alexis\OneDrive\Desktop\Vriginia_api_election\polls.db')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = cur.fetchall()
for (tbl,) in tables:
    if 'congress' in tbl.lower() or 'member' in tbl.lower():
        print(f'  {tbl}')
        count = cur.execute(f'SELECT COUNT(*) FROM {tbl}').fetchone()[0]
        print(f'    Rows: {count}')

        # Sample columns
        cur.execute(f'PRAGMA table_info({tbl})')
        cols = cur.fetchall()
        col_names = [c[1] for c in cols]
        print(f'    Columns: {col_names[:5]}')

conn.close()
