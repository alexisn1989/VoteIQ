import sqlite3
import tempfile
import unittest
from pathlib import Path

from voteiq.services import database_context as dc


class GovernorActionContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp.name) / "polls.db"
        self.original_polls_path = dc.DB_PATHS["polls"]
        dc.DB_PATHS["polls"] = self.db_path

    def tearDown(self):
        dc.DB_PATHS["polls"] = self.original_polls_path
        self.tmp.cleanup()

    def _exec(self, *statements):
        conn = sqlite3.connect(self.db_path)
        try:
            for statement in statements:
                conn.execute(statement)
            conn.commit()
        finally:
            conn.close()

    def test_expected_local_schema_records_found(self):
        self._exec(
            """
            CREATE TABLE governor_actions (
                bill_number TEXT, session TEXT, title TEXT, action_label TEXT,
                raw_status TEXT, action_date TEXT, governor TEXT,
                sponsor_name TEXT, source_url TEXT
            )
            """,
            """
            INSERT INTO governor_actions VALUES (
                'HB1288', '2026', 'Enforcement of vehicle liens', 'VETOED',
                'vetoed', '2026-04-13', 'Abigail Spanberger',
                'Delegate Example', 'https://example.test/hb1288'
            )
            """,
        )

        context = dc.build_database_context("Spanberger vetoes 2026")

        self.assertIn("lookup_status=records_found", context)
        self.assertIn("bill_number=HB1288", context)
        self.assertIn("Footer: SQL governor action lookup", context)

    def test_missing_session_column_does_not_block_lookup(self):
        self._exec(
            """
            CREATE TABLE governor_actions (
                bill_number TEXT, title TEXT, status TEXT, date TEXT, governor TEXT
            )
            """,
            """
            INSERT INTO governor_actions VALUES (
                'HB86', 'Mattress Stewardship Program', 'VETOED',
                '2026-04-13', 'Abigail Spanberger'
            )
            """,
        )

        context = dc.build_database_context("Spanberger vetoes 2026")

        self.assertIn("lookup_status=records_found", context)
        self.assertIn("bill_number=HB86", context)

    def test_action_type_named_status_instead(self):
        self._exec(
            """
            CREATE TABLE governor_actions (
                bill_number TEXT, session TEXT, status TEXT, date TEXT, governor TEXT
            )
            """,
            """
            INSERT INTO governor_actions VALUES (
                'SB17', '2026', 'VETOED', '2026-04-13', 'Abigail Spanberger'
            )
            """,
        )

        context = dc.build_database_context("Spanberger vetoes 2026")

        self.assertIn("lookup_status=records_found", context)
        self.assertIn("status=VETOED", context)

    def test_title_column_named_bill_title_or_description(self):
        self._exec(
            """
            CREATE TABLE governor_actions (
                bill_id TEXT, bill_title TEXT, description TEXT, status TEXT, date TEXT
            )
            """,
            """
            INSERT INTO governor_actions VALUES (
                'SB404', 'Comprehensive plan',
                'Environmental justice strategy bill', 'VETOED', '2026-04-13'
            )
            """,
        )

        context = dc.build_database_context("environmental justice vetoes 2026")

        self.assertIn("lookup_status=records_found", context)
        self.assertIn("bill_id=SB404", context)
        self.assertIn("description=Environmental justice strategy bill", context)

    def test_table_missing_returns_diagnostic(self):
        self._exec("CREATE TABLE unrelated (id INTEGER)")

        context = dc.build_database_context("Spanberger vetoes 2026")

        self.assertIn("lookup_status=table_missing", context)
        self.assertNotIn("Footer: SQL governor action lookup", context)

    def test_zero_records_returns_diagnostic(self):
        self._exec(
            """
            CREATE TABLE governor_actions (
                bill_number TEXT, session TEXT, status TEXT, date TEXT, governor TEXT
            )
            """,
            """
            INSERT INTO governor_actions VALUES (
                'HB1', '2026', 'SIGNED', '2026-04-13', 'Other Governor'
            )
            """,
        )

        context = dc.build_database_context("Spanberger vetoes 2026")

        self.assertIn("lookup_status=zero_records", context)
        self.assertNotIn("Footer: SQL governor action lookup", context)

    def test_schema_mismatch_returns_diagnostic(self):
        self._exec("CREATE TABLE governor_actions (id INTEGER)")

        context = dc.build_database_context("Spanberger vetoes 2026")

        self.assertIn("lookup_status=schema_mismatch", context)
        self.assertIn("available_columns=id", context)
        self.assertNotIn("Footer: SQL governor action lookup", context)

    def test_lookup_error_returns_diagnostic(self):
        self._exec(
            """
            CREATE TABLE governor_actions (
                bill_number TEXT, session TEXT, status TEXT, date TEXT, governor TEXT
            )
            """
        )
        original = dc._like_any_text_clause
        dc._like_any_text_clause = lambda columns, terms: ("not valid sql", [])
        try:
            context = dc.build_database_context("Spanberger vetoes 2026")
        finally:
            dc._like_any_text_clause = original

        self.assertIn("lookup_status=lookup_error", context)
        self.assertNotIn("Footer: SQL governor action lookup", context)


if __name__ == "__main__":
    unittest.main()
