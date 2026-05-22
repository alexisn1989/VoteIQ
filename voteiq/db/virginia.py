import sqlite3
from voteiq.config.finance_rules import POLLS_DB


def query(sql: str, params: tuple = ()) -> list:
    conn = sqlite3.connect(POLLS_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        return [tuple(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
