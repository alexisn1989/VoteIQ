"""Run every demo beat in-process through the real app (no server needed).

Dev-side verification: uses the same BEATS as demo_warmup.py, so passing
here means the production warmup will exercise identical payloads.
Makes real model calls - costs a few dollars in Opus tokens.

Usage: python demo/verify_local.py
"""
import os
import sys

os.environ["BASE_URL"] = ""  # demo_warmup builds URLs as f"{BASE_URL}/chat"
sys.path.insert(0, ".")

from fastapi.testclient import TestClient  # noqa: E402

from app import app  # noqa: E402
import demo.demo_warmup as warmup  # noqa: E402

client = TestClient(app)


def post(url, json=None, timeout=None):
    return client.post(url or "/chat", json=json)


sys.exit(warmup.main(post=post))
