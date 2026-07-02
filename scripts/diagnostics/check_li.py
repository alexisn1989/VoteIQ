import sqlite3
conn = sqlite3.connect("legislative_intelligence.db")

print("=== Table counts ===")
for t in ["legislators", "va_bills", "va_votes", "donor_sector_totals", "va_sbe_contributions"]:
    n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {n:,}")

print("\n=== Top fundraisers (all cycles) ===")
for r in conn.execute("""
    SELECT l.name, l.party, SUM(dst.total_amount) as total, COUNT(DISTINCT dst.sector) as sectors
    FROM donor_sector_totals dst
    JOIN legislators l ON l.lis_id = dst.legislator_id
    GROUP BY l.name, l.party ORDER BY total DESC LIMIT 10
""").fetchall():
    print(f"  {r[0]:<30} {r[1]:<12} ${r[2]:>12,.0f}  {r[3]} sectors")

print("\n=== Top individual donors (mega/major) ===")
for r in conn.execute("""
    SELECT contributor_name, employer, SUM(amount) as total, COUNT(*) as txn,
           donor_tier, candidate_name
    FROM va_sbe_contributions
    WHERE donor_tier IN ('Mega Donor','Major Institutional')
    GROUP BY contributor_name, candidate_name
    ORDER BY total DESC LIMIT 10
""").fetchall():
    print(f"  {r[0]:<35} -> {r[5]:<25} ${r[2]:>10,.0f}  [{r[4]}]")

print("\n=== Triangle sample: top legislator ===")
top = conn.execute("""
    SELECT l.name FROM donor_sector_totals dst
    JOIN legislators l ON l.lis_id = dst.legislator_id
    GROUP BY l.name ORDER BY SUM(dst.total_amount) DESC LIMIT 1
""").fetchone()[0]
print(f"  {top}")
for r in conn.execute("""
    SELECT sector, total_amount, donor_count, cycle
    FROM donor_sector_totals dst
    JOIN legislators l ON l.lis_id = dst.legislator_id
    WHERE l.name = ? ORDER BY total_amount DESC LIMIT 8
""", (top,)).fetchall():
    print(f"    {r[0]:<22} ${r[1]:>10,.0f}  {r[2]:>4} donors  cycle {r[3]}")

conn.close()
