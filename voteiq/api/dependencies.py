"""Shared FastAPI dependencies."""
from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException


def require_admin_token(
    authorization: str | None = Header(default=None),
) -> None:
    """Enforce ADMIN_TOKEN on admin/debug endpoints.

    Accepts the token via ``Authorization: Bearer <token>`` only. Query-param
    tokens (the old ``?token=`` form) are no longer accepted — they leak into
    access logs, browser history, and Referer headers.

    If no token env var is set: requests pass in local dev, but fail closed in
    production (FLASK_ENV=production or RENDER set) so a misconfigured deploy
    can't silently expose admin endpoints.
    """
    expected = (
        os.getenv("ADMIN_TOKEN")
        or os.getenv("VOTEIQ_ADMIN_TOKEN")
        or os.getenv("UPLOAD_ADMIN_TOKEN")
        or os.getenv("POLL_INGEST_ADMIN_TOKEN")
        or ""
    )
    if not expected:
        if os.getenv("FLASK_ENV") == "production" or os.getenv("RENDER"):
            raise HTTPException(status_code=503, detail="ADMIN_TOKEN not configured")
        return  # local dev — no token configured
    bearer = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    if not hmac.compare_digest(bearer.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Invalid or missing admin token")
