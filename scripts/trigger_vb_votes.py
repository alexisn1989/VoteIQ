#!/usr/bin/env python3
"""
trigger_vb_votes.py

Called by the Render cron job "voteiq-vb-votes".
POSTs to /api/admin/ingest-vb-votes on the running web service so the
ingest runs inside the container that has access to /var/data/polls.db.

Required env vars (set in Render dashboard):
  VOTEIQ_SERVICE_URL  — e.g. https://voteiq-api.onrender.com
  ADMIN_TOKEN         — must match the web service's ADMIN_TOKEN env var
"""

import os
import sys
import urllib.request
import urllib.error
import json

SERVICE_URL = os.environ.get("VOTEIQ_SERVICE_URL", "").rstrip("/")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

if not SERVICE_URL:
    print("ERROR: VOTEIQ_SERVICE_URL is not set.", file=sys.stderr)
    sys.exit(1)

url = f"{SERVICE_URL}/api/admin/ingest-vb-votes"
print(f"POST {url}")

headers = {"Content-Length": "0"}
if ADMIN_TOKEN:
    headers["Authorization"] = f"Bearer {ADMIN_TOKEN}"

req = urllib.request.Request(url, data=b"", headers=headers, method="POST")

try:
    with urllib.request.urlopen(req, timeout=960) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        print(f"Status: {resp.status}")
        for script, result in data.get("scripts", {}).items():
            ok  = result.get("ok", "?")
            out = (result.get("stdout") or "")[-400:]
            err = (result.get("stderr") or "")[-200:]
            print(f"\n  [{script}]  ok={ok}")
            if out:
                print(f"    stdout: {out}")
            if err and not ok:
                print(f"    stderr: {err}")
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
