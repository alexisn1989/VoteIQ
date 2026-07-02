import requests
import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv('OPENSTATES_API_KEY')
BASE = 'https://v3.openstates.org'

print('Testing include parameters...')
print()

include_values = [
    [],  # No includes
    ['sponsorships'],
    ['votes'],
    ['actions'],
    ['documents'],
    ['sponsorships', 'votes'],
    ['sponsorships', 'abstracts', 'votes', 'actions', 'documents', 'versions', 'amendments'],  # Original
]

for includes in include_values:
    params = {
        'jurisdiction': 'Virginia',
        'session': '2026',
        'per_page': 1
    }
    if includes:
        for inc in includes:
            if 'include' not in params:
                params['include'] = []
            params['include'] = includes

    print(f'Testing with include={includes}')
    try:
        r = requests.get(
            f'{BASE}/bills',
            params=params if 'include' not in params else {'jurisdiction': 'Virginia', 'session': '2026', 'per_page': 1, **{'include': includes}},
            headers={'X-API-KEY': API_KEY, 'User-Agent': 'VoteIQ/1.0'},
            timeout=10
        )
        print(f'  Status: {r.status_code}')
        if r.status_code != 200:
            print(f'  Error: {r.text[:150]}')
    except Exception as e:
        print(f'  Exception: {e}')
    print()
