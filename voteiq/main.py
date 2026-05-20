"""VoteIQ FastAPI application.

This is intentionally small: application setup belongs here, while routes live
under ``voteiq.api.routes`` and business logic moves into ``voteiq.services``.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from voteiq.api.router import api_router

BASE_DIR = Path(__file__).resolve().parent.parent

app = FastAPI(title="VoteIQ")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(api_router)

