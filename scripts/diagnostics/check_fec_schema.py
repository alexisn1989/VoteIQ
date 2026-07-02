import sqlite3

conn = sqlite3.connect(r'c:\Users\Alexis\OneDrive\Desktop\Vriginia_api_election\polls.db')
cur = conn.cursor()

print('FEC individual contributions table schema:')
cur.execute('PRAGMA table_info(fec_individual_contributions)')
for row in cur.fetchall():
    print(f'  {row[1]:30} {row[2]:15}')

print()
print('Sample FEC contribution record:')
cur.execute("""
    SELECT
        candidate_name,
        contributor_name,
        contributor_employer,
        amount,
        contribution_date,
        employer_sector
    FROM fec_individual_contributions
    WHERE amount IS NOT NULL
    LIMIT 1
""")
row = cur.fetchone()
if row:
    print(f'  Candidate: {row[0]}')
    print(f'  Contributor: {row[1]}')
    print(f'  Employer: {row[2]}')
    print(f'  Amount: {row[3]}')
    print(f'  Date: {row[4]}')
    print(f'  Sector: {row[5]}')
else:
    print('  No rows with amount data found')

print()
print('Check for NULL amounts:')
cur.execute("""
    SELECT COUNT(*) as null_count, COUNT(amount) as non_null_count
    FROM fec_individual_contributions
""")
null_count, non_null = cur.fetchone()
total = null_count + non_null
print(f'  Total rows: {total}')
print(f'  With amount: {non_null}')
print(f'  Without amount (NULL): {null_count - non_null if null_count > non_null else "0"}')

conn.close()
