import sqlite3
import tempfile
import unittest
from pathlib import Path

import ingest_governor_actions as ingest


class GovernorActionsIngestTests(unittest.TestCase):
    def test_run_creates_table_and_veto_overrides_without_openstates(self):
        original_openstates = ingest.OPENSTATES_DB
        original_polls = ingest.POLLS_DB
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            polls_db = Path(tmp) / "polls.db"
            ingest.OPENSTATES_DB = str(Path(tmp) / "missing_openstates.db")
            ingest.POLLS_DB = str(polls_db)
            try:
                count = ingest.run(sessions=["2026"])
            finally:
                ingest.OPENSTATES_DB = original_openstates
                ingest.POLLS_DB = original_polls

            conn = sqlite3.connect(polls_db)
            try:
                table = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='governor_actions'"
                ).fetchone()
                veto_count = conn.execute(
                    "SELECT COUNT(*) FROM governor_actions WHERE action = 'vetoed'"
                ).fetchone()[0]
                hb1385_title = conn.execute(
                    """
                    SELECT title
                    FROM governor_actions
                    WHERE bill_number = 'HB1385' AND session = '2026'
                    """
                ).fetchone()[0]
            finally:
                conn.close()

        self.assertIsNotNone(table)
        self.assertGreaterEqual(count, 28)
        self.assertGreaterEqual(veto_count, 28)
        self.assertEqual(
            hb1385_title,
            "Higher educational institutions, public; membership of governing boards.",
        )


if __name__ == "__main__":
    unittest.main()
