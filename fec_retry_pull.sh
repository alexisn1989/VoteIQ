#!/bin/bash
# Wait for FEC rate limit to clear, then run full pull
KEY=$(grep '^FEC_API_KEY=' .env | cut -d= -f2)
echo "Waiting for FEC rate limit to clear..."
until python -c "
import requests
r = requests.get('https://api.open.fec.gov/v1/candidates/search/', 
    params={'q':'Kiggans','state':'VA','per_page':1,'api_key':'$KEY'}, timeout=15)
exit(0 if r.status_code != 429 else 1)
" 2>/dev/null; do
    echo "  still rate limited — sleeping 60s"
    sleep 60
done
echo "Rate limit cleared. Starting full pull..."
export FEC_API_KEY=$KEY
python -u fec_employer_pipeline.py --all --cycle 2024
echo "Done."
python -c "
import sqlite3
conn = sqlite3.connect('polls.db')
rows = conn.execute('SELECT candidate_name, COUNT(*) FROM fec_individual_contributions GROUP BY candidate_name ORDER BY 2 DESC').fetchall()
for r in rows: print(r)
print('TOTAL:', conn.execute('SELECT COUNT(*) FROM fec_individual_contributions').fetchone()[0])
conn.close()
"
