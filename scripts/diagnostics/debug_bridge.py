import sqlite3

conn = sqlite3.connect(r'c:\Users\Alexis\OneDrive\Desktop\Vriginia_api_election\polls.db', timeout=30)
cur = conn.cursor()

print('Congress members from VA:')
va_members = cur.execute("SELECT bioguide_id, name, state FROM congress_members WHERE state = 'VA' ORDER BY name").fetchall()
print(f'  Found {len(va_members)} members')
for bid, name, state in va_members[:5]:
    print(f'    {bid:10} {name:25} {state}')

print()
print('Bridge table entries:')
bridge = cur.execute('SELECT bioguide_id, fec_candidate_id FROM bioguide_fec_bridge ORDER BY bioguide_id').fetchall()
print(f'  Found {len(bridge)} entries')
for bid, fec_id in bridge[:10]:
    print(f'    {bid:10} -> {fec_id}')

print()
print('Bridge entries that match congress_members:')
matches = cur.execute("""
    SELECT c.bioguide_id, c.name, b.fec_candidate_id
    FROM congress_members c
    JOIN bioguide_fec_bridge b ON c.bioguide_id = b.bioguide_id
    WHERE c.state = 'VA'
    ORDER BY c.name
""").fetchall()
print(f'  Found {len(matches)} matches')
for bid, name, fec_id in matches:
    print(f'    {bid:10} {name:25} {fec_id}')

conn.close()
