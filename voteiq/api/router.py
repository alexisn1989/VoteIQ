"""Top-level API router composition."""
from __future__ import annotations

from fastapi import APIRouter

from voteiq.api.routes import admin, analyst, bills, chat, district, elections, floor, governor, local_council, members, news, polls, prediction, tasks

api_router = APIRouter()

for route_module in (admin, chat, analyst, district, elections, floor, governor, local_council, members, bills, polls, news, prediction, tasks):
    api_router.include_router(route_module.router)
