"""Shared FastAPI dependencies."""
from __future__ import annotations

import os

from fastapi import Header, HTTPException, Query


def require_admin_token(
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    """Enforce ADMIN_TOKEN on admin/debug endpoints.
    Accepts the token as ?token= query param or Authorization: Bearer <token>.
    If no token env var is set the app is in dev mode and all requests pass.
    """
    expected = (
        os.getenv("ADMIN_TOKEN")
        or os.getenv("VOTEIQ_ADMIN_TOKEN")
        or os.getenv("UPLOAD_ADMIN_TOKEN")
        or os.getenv("POLL_INGEST_ADMIN_TOKEN")
        or ""
    )
    if not expected:
        return  # dev mode — no token configured
    bearer = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    provided = bearer or (token or "")
    if provided != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing admin token")
