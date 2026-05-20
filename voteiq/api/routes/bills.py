"""Bill search and description routes."""
from __future__ import annotations

from fastapi import APIRouter

from voteiq.api.legacy import include_matching_routes

router = APIRouter(tags=["bills"])

include_matching_routes(
    router,
    lambda path: path.startswith("/api/bill-descriptions/"),
)

