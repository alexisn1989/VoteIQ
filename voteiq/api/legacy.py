"""Compatibility helpers for the current root-level FastAPI app.

This lets the new package layout mount categorized routers while the large
legacy ``main.py`` is gradually split into services and route modules.
"""
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter
from fastapi.routing import APIRoute

import main as legacy_main


def include_matching_routes(router: APIRouter, predicate: Callable[[str], bool]) -> None:
    """Copy matching legacy routes into a new router."""
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for route in legacy_main.app.routes:
        path = getattr(route, "path", "")
        methods = tuple(sorted(getattr(route, "methods", []) or []))
        key = (path, methods)
        if key in seen or not isinstance(route, APIRoute) or not predicate(path):
            continue
        router.routes.append(route)
        seen.add(key)

