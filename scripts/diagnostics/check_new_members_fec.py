import sqlite3

conn = sqlite3.connect(r'c:\Users\Alexis\OneDrive\Desktop\Vriginia_api_election\polls.db')
cur = conn.cursor()

print('FEC contributions by candidate (current):')
print('=' * 100)
candidates = cur.execute('''
    SELECT candidate_name, COUNT(*) as count,
           SUM(amount) as total_amount
    FROM fec_individual_contributions
    GROUP BY candidate_name
    ORDER BY total_amount DESC NULLS LAST
''').fetchall()

for cand_name, cnt, total in candidates:
    total_fmt = f'${total:,.0f}' if total else 'NULL'
    print(f'{cand_name:35} | {cnt:6} contributions | {total_fmt}')

print()
print('New members from bridge table fix:')
new_members = [
    ('McGuire, John J.', 'H0VA07133'),
    ('Subramanyam, Suhas', 'H4VA10279'),
    ('Vindman, Eugene Simon', 'H4VA07234'),
    ('Walkinshaw, James R.', 'H6VA11066'),
]

found = set()
for cand_name, cnt, total in candidates:
    found.add(cand_name)

for name, fec_id in new_members:
    status = 'FOUND' if name in found else 'MISSING'
    print(f'  {name:35} ({fec_id}) -> {status}')

print()
print('Total rows in FEC table:', cur.execute('SELECT COUNT(*) FROM fec_individual_contributions').fetchone()[0])

conn.close()
