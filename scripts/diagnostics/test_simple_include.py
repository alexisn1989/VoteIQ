import requests
import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv('OPENSTATES_API_KEY')
BASE = 'https://v3.openstates.org'

print('Testing include parameters...')
print()

# Test 1: No includes
print('Test 1: No includes')
r = requests.get(
    f'{BASE}/bills',
    params={'jurisdiction': 'Virginia', 'session': '2026', 'per_page': 1},
    headers={'X-API-KEY': API_KEY, 'User-Agent': 'VoteIQ/1.0'},
    timeout=10
)
print(f'  Status: {r.status_code}')
print()

# Test 2: Single include
print('Test 2: Single include=sponsorships')
r = requests.get(
    f'{BASE}/bills',
    params={'jurisdiction': 'Virginia', 'session': '2026', 'per_page': 1, 'include': 'sponsorships'},
    headers={'X-API-KEY': API_KEY, 'User-Agent': 'VoteIQ/1.0'},
    timeout=10
)
print(f'  Status: {r.status_code}')
print()

# Test 3: Multiple includes (requests will convert list to multiple params)
print('Test 3: Multiple includes')
params = {
    'jurisdiction': 'Virginia',
    'session': '2026',
    'per_page': 1,
    'include': ['sponsorships', 'votes', 'actions']
}
r = requests.get(
    f'{BASE}/bills',
    params=params,
    headers={'X-API-KEY': API_KEY, 'User-Agent': 'VoteIQ/1.0'},
    timeout=10
)
print(f'  Status: {r.status_code}')
print(f'  URL: {r.url}')
