#!/usr/bin/env python3
"""
trigger_va_polls_gemini.py

Called by the Render cron job "voteiq-polls-gemini".
POSTs to /api/admin/ingest-polls on the running web service so the
ingest runs inside the container that has access to /var/data/polls.db.

Required env vars (set in Render dashboard):
  VOTEIQ_SERVICE_URL  — e.g. https://voteiq.io
  ADMIN_TOKEN         — must match the web service's ADMIN_TOKEN env var
  GEMINI_API_KEY       — must be set on the web service (checked there, not here)
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

url = f"{SERVICE_URL}/api/admin/ingest-polls?sources=fivethirtyeight,votehub,news&use_gemini=true"
print(f"POST {url}")

headers = {"Content-Length": "0"}
if ADMIN_TOKEN:
    headers["Authorization"] = f"Bearer {ADMIN_TOKEN}"

req = urllib.request.Request(url, data=b"", headers=headers, method="POST")

try:
    with urllib.request.urlopen(req, timeout=500) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        print(f"Status: {resp.status}")
        print(f"ok={data.get('ok')}  returncode={data.get('returncode')}")
        out = (data.get("stdout") or "")[-1000:]
        err = (data.get("stderr") or "")[-500:]
        if out:
            print(f"stdout: {out}")
        if err and not data.get("ok"):
            print(f"stderr: {err}")
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
