"""Top-level API router composition."""
from __future__ import annotations

from fastapi import APIRouter

from voteiq.api.routes import admin, analyst, bills, chat, district, elections, members, news, polls

api_router = APIRouter()

for route_module in (admin, chat, analyst, district, elections, members, bills, polls, news):
    api_router.include_router(route_module.router)

