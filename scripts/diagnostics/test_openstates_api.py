import requests
import os
import json
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv('OPENSTATES_API_KEY')
BASE = 'https://v3.openstates.org'

print('Testing OpenStates API...')
print(f'API Key: {API_KEY[:20]}...')
print()

# Test 1: Get jurisdiction info
print('Test 1: Get Virginia jurisdiction info')
try:
    r = requests.get(
        f'{BASE}/jurisdictions',
        headers={'X-API-KEY': API_KEY, 'User-Agent': 'VoteIQ/1.0'},
        timeout=10
    )
    print(f'Status: {r.status_code}')
    if r.status_code == 200:
        jurisdictions = r.json()
        va_juris = [j for j in jurisdictions if j.get('name', '').lower() == 'virginia']
        if va_juris:
            print(f'Virginia jurisdiction found:')
            print(json.dumps(va_juris[0], indent=2))
        else:
            print('Virginia not found. Available jurisdictions:')
            for j in jurisdictions[:5]:
                print(f'  - {j.get("name")} ({j.get("classification")})')
except Exception as e:
    print(f'Error: {e}')

print()

# Test 2: Try a simple bills request with minimal params
print('Test 2: Simple bills request (no session)')
try:
    r = requests.get(
        f'{BASE}/bills',
        params={
            'jurisdiction': 'Virginia',
            'per_page': 1
        },
        headers={'X-API-KEY': API_KEY, 'User-Agent': 'VoteIQ/1.0'},
        timeout=10
    )
    print(f'Status: {r.status_code}')
    if r.status_code == 200:
        data = r.json()
        print(f'Found {data.get("pagination", {}).get("total_items", 0)} bills')
    else:
        print(f'Error response: {r.text[:200]}')
except Exception as e:
    print(f'Error: {e}')
