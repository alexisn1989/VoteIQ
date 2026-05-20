"""Civic analyst endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from voteiq.api.legacy import include_matching_routes

router = APIRouter(tags=["analyst"])

include_matching_routes(
    router,
    lambda path: path.startswith("/api/analyst/") or path.startswith("/api/timing/"),
)

