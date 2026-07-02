import sqlite3

conn = sqlite3.connect(r'c:\Users\Alexis\OneDrive\Desktop\Vriginia_api_election\polls.db')
cur = conn.cursor()

print('FEC contributions by candidate:')
print('=' * 100)
candidates = cur.execute('''
    SELECT candidate_name, COUNT(*) as count,
           SUM(amount) as total_amount,
           MIN(contribution_date) as earliest,
           MAX(contribution_date) as latest
    FROM fec_individual_contributions
    GROUP BY candidate_name
    ORDER BY total_amount DESC NULLS LAST
''').fetchall()

for cand_name, cnt, total, earliest, latest in candidates:
    if total is None:
        total_fmt = 'NULL'
    else:
        total_fmt = f'${total:,.0f}'
    print(f'{cand_name:25} | {cnt:6} | {total_fmt:15} | {earliest} to {latest}')

conn.close()
