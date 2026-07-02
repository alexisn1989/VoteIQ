import sqlite3

def _load_virginia_candidates() -> dict[str, str]:
    DB_PATH = r'c:\Users\Alexis\OneDrive\Desktop\Vriginia_api_election\polls.db'
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT c.name, b.fec_candidate_id
            FROM congress_members c
            JOIN bioguide_fec_bridge b ON c.bioguide_id = b.bioguide_id
            WHERE c.state = 'Virginia' AND b.fec_candidate_id IS NOT NULL
            ORDER BY c.name
        """).fetchall()
        return {row["name"]: row["fec_candidate_id"] for row in rows}
    finally:
        conn.close()

candidates = _load_virginia_candidates()
print(f'Loaded {len(candidates)} VA candidates from bridge table:')
for name, fec_id in sorted(candidates.items()):
    print(f'  {name:35} -> {fec_id}')
