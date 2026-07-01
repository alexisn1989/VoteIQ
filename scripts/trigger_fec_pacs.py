#!/usr/bin/env python3
"""
trigger_fec_pacs.py

Called by the Render cron job "voteiq-fec-weekly".
GETs /api/admin/ingest-fec-pacs on the running web service so the FEC
PAC/industry ingest runs inside the container that owns /var/data/polls.db
and FEC_API_KEY. Replaces the old startup thread in main.py, which re-ran
a 30-minute crawl on every deploy while data was stale.

The endpoint is non-blocking: it kicks off the ingest as a background task
and returns immediately, so this script only confirms the ingest started.

Required env vars (set in Render dashboard):
  VOTEIQ_SERVICE_URL  — e.g. https://voteiq.io
  ADMIN_TOKEN         — must match the web service's ADMIN_TOKEN env var

Optional:
  FEC_CYCLE           — restrict to one cycle (default: all cycles)
"""

import json
import os
import sys
import urllib.error
import urllib.request

SERVICE_URL = os.environ.get("VOTEIQ_SERVICE_URL", "").rstrip("/")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
FEC_CYCLE = os.environ.get("FEC_CYCLE", "")

if not SERVICE_URL:
    print("ERROR: VOTEIQ_SERVICE_URL is not set.", file=sys.stderr)
    sys.exit(1)

url = f"{SERVICE_URL}/api/admin/ingest-fec-pacs"
if FEC_CYCLE:
    url += f"?cycle={FEC_CYCLE}"
print(f"GET {url}")

headers = {}
if ADMIN_TOKEN:
    headers["Authorization"] = f"Bearer {ADMIN_TOKEN}"

req = urllib.request.Request(url, headers=headers, method="GET")

try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        print(f"Status: {resp.status}")
        print(f"ok={data.get('ok')}  message={data.get('message') or data.get('error')}")
        sys.exit(0 if data.get("ok") else 1)

except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", errors="replace")
    print(f"HTTP {exc.code}: {body[:500]}", file=sys.stderr)
    sys.exit(1)
except urllib.error.URLError as exc:
    print(f"Connection error: {exc.reason}", file=sys.stderr)
    sys.exit(1)
except Exception as exc:
    print(f"Unexpected error: {exc}", file=sys.stderr)
    sys.exit(1)
