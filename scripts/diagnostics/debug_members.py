import sqlite3

conn = sqlite3.connect(r'c:\Users\Alexis\OneDrive\Desktop\Vriginia_api_election\polls.db')
cur = conn.cursor()

print('All congress_members:')
cur.execute("SELECT bioguide_id, name, party, state FROM congress_members ORDER BY state, name")
rows = cur.fetchall()
for bid, name, party, state in rows:
    print(f'  {bid:10} {name:30} {party:3} {state}')

print()
print('States in congress_members:')
cur.execute("SELECT DISTINCT state FROM congress_members ORDER BY state")
states = cur.fetchall()
for (state,) in states:
    print(f'  {state}')

conn.close()
