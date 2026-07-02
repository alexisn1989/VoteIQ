import requests
import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv('OPENSTATES_API_KEY')
BASE = 'https://v3.openstates.org'

print('Testing exact parameters from build_openstates_db.py...')
print()

# Exact params from the script
params = {
    "jurisdiction": "Virginia",
    "session": "2026",
    "per_page": 20,
    "page": 1,
    "include": ["sponsorships", "abstracts", "votes", "actions", "documents", "versions", "amendments"],
}

print(f'Params: {params}')
print()

try:
    r = requests.get(
        f'{BASE}/bills',
        params=params,
        headers={'X-API-KEY': API_KEY, 'User-Agent': 'VoteIQ/1.0'},
        timeout=60
    )
    print(f'Status: {r.status_code}')
    print(f'URL: {r.url}')
    if r.status_code != 200:
        print(f'Error: {r.text[:300]}')
    else:
        print(f'Success! Found {r.json().get("pagination", {}).get("count", 0)} bills on this page')
except Exception as e:
    print(f'Exception: {e}')
