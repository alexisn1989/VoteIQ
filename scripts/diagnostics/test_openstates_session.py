import requests
import os
import json
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv('OPENSTATES_API_KEY')
BASE = 'https://v3.openstates.org'

print('Testing different session parameters...')
print()

sessions_to_try = ['2026', '2025', '2024', None]

for session in sessions_to_try:
    params = {
        'jurisdiction': 'Virginia',
        'per_page': 1
    }
    if session:
        params['session'] = session

    print(f'Testing with session={session}')
    try:
        r = requests.get(
            f'{BASE}/bills',
            params=params,
            headers={'X-API-KEY': API_KEY, 'User-Agent': 'VoteIQ/1.0'},
            timeout=10
        )
        print(f'  Status: {r.status_code}')
        if r.status_code == 200:
            data = r.json()
            total = data.get('pagination', {}).get('total_items', 0)
            print(f'  Found {total} bills')
        else:
            print(f'  Error: {r.status_code} - {r.text[:100]}')
    except Exception as e:
        print(f'  Exception: {e}')
    print()
