import sqlite3

conn = sqlite3.connect(r'c:\Users\Alexis\OneDrive\Desktop\Vriginia_api_election\polls.db')
cur = conn.cursor()

print('Campaign Finance Summary (Federal members with FEC data):')
print('=' * 80)
rows = cur.execute('''
    SELECT name, total_raised, top_sector
    FROM campaign_finance_summary
    WHERE source = 'fec'
    ORDER BY total_raised DESC
''').fetchall()

for name, raised, sector in rows:
    print(f'{name:30} | ${raised:>12,.0f} | {sector}')

print()
print(f'Total: {len(rows)} federal members with FEC data')
print()
print('Sample: Kiggans finance data')
kg = cur.execute('''
    SELECT name, total_raised, top_sector
    FROM campaign_finance_summary
    WHERE name LIKE '%Kiggans%'
''').fetchone()
if kg:
    print(f'  Name: {kg[0]}')
    print(f'  Total raised: ${kg[1]:,.0f}')
    print(f'  Top sector: {kg[2]}')
else:
    print('  Not found')

conn.close()
