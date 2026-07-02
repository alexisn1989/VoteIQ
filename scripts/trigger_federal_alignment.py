#!/usr/bin/env python3
"""
trigger_federal_alignment.py

Called by the Render cron job "voteiq-federal-alignment-monthly".
POSTs /api/admin/ingest-federal-alignment on the running web service so the ingest runs inside the
container that owns /var/data/polls.db (and any API keys the script needs).

Required env vars (set in Render dashboard):
  VOTEIQ_SERVICE_URL  — e.g. https://voteiq.io
  ADMIN_TOKEN         — must match the web service's ADMIN_TOKEN env var
"""

import json
import os
import sys
import urllib.error
import urllib.request

SERVICE_URL = os.environ.get("VOTEIQ_SERVICE_URL", "").rstrip("/")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

if not SERVICE_URL:
    print("ERROR: VOTEIQ_SERVICE_URL is not set.", file=sys.stderr)
    sys.exit(1)

url = f"{SERVICE_URL}/api/admin/ingest-federal-alignment"
print(f"POST {url}")

headers = {}
if ADMIN_TOKEN:
    headers["Authorization"] = f"Bearer {ADMIN_TOKEN}"
headers["Content-Length"] = "0"

req = urllib.request.Request(url, data=b"", headers=headers, method="POST")

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
