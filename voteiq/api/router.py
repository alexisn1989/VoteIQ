"""Top-level API router composition."""
from __future__ import annotations

from fastapi import APIRouter

from voteiq.api.routes import admin, analyst, bills, chat, district, elections, floor, governor, members, news, polls, tasks

api_router = APIRouter()

for route_module in (admin, chat, analyst, district, elections, floor, governor, members, bills, polls, news, tasks):
    api_router.include_router(route_module.router)
