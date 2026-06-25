"""Shared pytest fixtures for VoteIQ tests.

The `db` fixture patches dc.DB_PATHS to point at isolated temp databases
and clears DATA_DIR env-var overrides.  Tests call db.polls(), db.openstates(),
etc. to create schema and seed data — no setUp/tearDown boilerplate required.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from voteiq.services import database_context as dc


@pytest.fixture()
def db(tmp_path):
    """Fixture-isolated database harness.

    Usage::

        def test_something(db):
            db.polls(
                "CREATE TABLE va_bills (bill_number TEXT, ...)",
                "INSERT INTO va_bills VALUES ('HB84', ...)",
            )
            context = dc.build_database_context("What is HB84?")
            assert "bill_number=HB84" in context
    """
    _paths = {
        "polls": tmp_path / "polls.db",
        "openstates": tmp_path / "openstates_va.db",
        "legislative_intelligence": tmp_path / "legislative_intelligence.db",
        "virginia_legislature": tmp_path / "virginia_legislature.db",
    }
    _orig_paths = {k: dc.DB_PATHS[k] for k in _paths}
    _orig_env = {var: os.environ.pop(var, None) for var in dc.DATA_DIR_ENV_VARS}

    for name, path in _paths.items():
        dc.DB_PATHS[name] = path

    class _DB:
        def _exec(self, db_name: str, *statements: str) -> None:
            conn = sqlite3.connect(_paths[db_name])
            try:
                for stmt in statements:
                    conn.execute(stmt)
                conn.commit()
            finally:
                conn.close()

        def polls(self, *statements: str) -> None:
            self._exec("polls", *statements)

        def openstates(self, *statements: str) -> None:
            self._exec("openstates", *statements)

        def legislative(self, *statements: str) -> None:
            self._exec("legislative_intelligence", *statements)

        def legislature(self, *statements: str) -> None:
            self._exec("virginia_legislature", *statements)

    yield _DB()

    dc.DB_PATHS.update(_orig_paths)
    for var, val in _orig_env.items():
        if val is not None:
            os.environ[var] = val
