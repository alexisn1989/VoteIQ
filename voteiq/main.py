"""VoteIQ FastAPI application.

This is intentionally small: application setup belongs here, while routes live
under ``voteiq.api.routes`` and business logic moves into ``voteiq.services``.
"""
from __future__ import annotations

from pathlib import Path
import threading

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from voteiq.api.router import api_router

BASE_DIR = Path(__file__).resolve().parent.parent

app = FastAPI(title="VoteIQ")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(api_router)


@app.on_event("startup")
async def _startup_governor_actions() -> None:
    def _task() -> None:
        try:
            import ingest_governor_actions

            n = ingest_governor_actions.run(sessions=["2025", "2026"])
            print(f"Governor actions: ensured {n} records.")
        except Exception as exc:
            print(f"Governor actions ingest skipped: {exc}")

    threading.Thread(target=_task, daemon=True).start()
