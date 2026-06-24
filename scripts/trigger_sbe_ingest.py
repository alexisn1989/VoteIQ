#!/usr/bin/env python3
"""
trigger_sbe_ingest.py

Called by the Render cron job "voteiq-sbe-monthly".
POSTs to /api/admin/ingest-sbe-local on the running web service so the
download runs inside the container that has access to /var/data/polls.db.

Required env vars (set in Render dashboard):
  VOTEIQ_SERVICE_URL  — e.g. https://voteiq.io
  ADMIN_TOKEN         — must match the web service's UPLOAD_ADMIN_TOKEN env var

Optional:
  SBE_YEARS           — space-separated years to refresh (default: all available)
                        e.g. "2025 2026" to only pull recent cycles
"""

import os
import sys
import urllib.request
import urllib.error
import json

SERVICE_URL = os.environ.get("VOTEIQ_SERVICE_URL", "").rstrip("/")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
SBE_YEARS   = os.environ.get("SBE_YEARS", "").strip()

if not SERVICE_URL:
    print("ERROR: VOTEIQ_SERVICE_URL is not set.", file=sys.stderr)
    sys.exit(1)

url = f"{SERVICE_URL}/api/admin/ingest-sbe-local"
if SBE_YEARS:
    encoded = SBE_YEARS.replace(" ", "+")
    url += f"?years={encoded}"

print(f"POST {url}")

headers = {"Content-Length": "0"}
if ADMIN_TOKEN:
    headers["Authorization"] = f"Bearer {ADMIN_TOKEN}"

req = urllib.request.Request(url, data=b"", headers=headers, method="POST")

try:
    with urllib.request.urlopen(req, timeout=540) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        print(f"Status: {resp.status}")
        print(f"ok={data.get('ok')}")
        for script, result in data.get("scripts", {}).items():
            ok  = result.get("ok", "?")
            out = (result.get("stdout") or "")[-600:]
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
