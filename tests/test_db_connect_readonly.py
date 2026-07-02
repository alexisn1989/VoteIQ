"""Regression tests for the read-only context-builder connections.

The shared _connect factory opens databases with mode=ro so context builders
can never take a write lock (or corrupt the DB), and WAL keeps readers from
blocking the ingest writer. See _enable_wal_mode in main.py for the WAL half.
"""
import sqlite3

import pytest

import voteiq.services.context._db as db


@pytest.fixture()
def wal_db(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE x(a)")
    conn.execute("INSERT INTO x VALUES (1)")
    conn.commit()
    assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    conn.close()
    monkeypatch.setitem(db.DB_PATHS, "polls", path)
    return path


def test_connect_reads(wal_db):
    conn = db._connect("polls")
    assert conn.execute("SELECT a FROM x").fetchone()[0] == 1
    conn.close()


def test_connect_rejects_writes(wal_db):
    conn = db._connect("polls")
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("INSERT INTO x VALUES (2)")
    conn.close()


def test_reader_not_blocked_by_open_write_txn(wal_db):
    writer = sqlite3.connect(str(wal_db))
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO x VALUES (3)")
    try:
        reader = db._connect("polls")
        # WAL: reader sees the last committed snapshot, without blocking
        assert reader.execute("SELECT COUNT(*) FROM x").fetchone()[0] == 1
        reader.close()
    finally:
        writer.rollback()
        writer.close()
