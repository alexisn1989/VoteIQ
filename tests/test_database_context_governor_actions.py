import sqlite3
import tempfile
import unittest
from pathlib import Path

from voteiq.services import database_context as dc


class GovernorActionContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp.name) / "polls.db"
        self.openstates_path = Path(self.tmp.name) / "openstates_va.db"
        self.original_polls_path = dc.DB_PATHS["polls"]
        self.original_openstates_path = dc.DB_PATHS["openstates"]
        dc.DB_PATHS["polls"] = self.db_path
        dc.DB_PATHS["openstates"] = self.openstates_path

    def tearDown(self):
        dc.DB_PATHS["polls"] = self.original_polls_path
        dc.DB_PATHS["openstates"] = self.original_openstates_path
        self.tmp.cleanup()

    def _exec(self, *statements):
        conn = sqlite3.connect(self.db_path)
        try:
            for statement in statements:
                conn.execute(statement)
            conn.commit()
        finally:
            conn.close()

    def _exec_openstates(self, *statements):
        conn = sqlite3.connect(self.openstates_path)
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

    def test_mixed_spanberger_veto_and_finance_query_returns_both_contexts(self):
        self._exec(
            """
            CREATE TABLE governor_actions (
                bill_number TEXT, session TEXT, title TEXT, action_label TEXT,
                action TEXT, action_date TEXT, governor TEXT, source_url TEXT
            )
            """,
            """
            INSERT INTO governor_actions VALUES (
                'HB1385', '2026',
                'Higher educational institutions, public; membership of governing boards.',
                'Vetoed', 'vetoed', '2026-05-19',
                'Abigail Spanberger', 'https://governor.example/hb1385'
            )
            """,
            """
            CREATE TABLE va_finance_people (
                person_name TEXT, office TEXT, party TEXT, committee_name TEXT,
                finance_url TEXT, source_url TEXT, data_confidence TEXT, fetched_at TEXT
            )
            """,
            """
            INSERT INTO va_finance_people VALUES (
                'Abigail Spanberger', 'Governor', 'Democratic',
                'Spanberger for Governor', 'https://www.vpap.org/committees/106366/',
                'https://www.elections.virginia.gov/', 'high',
                '2026-05-23T08:00:00Z'
            )
            """,
            """
            CREATE TABLE va_cf_schedule_a (
                candidate_name TEXT, election_cycle TEXT, first_name TEXT,
                last_or_company TEXT, employer TEXT, occupation TEXT,
                is_individual INTEGER, transaction_date TEXT, amount REAL
            )
            """,
            """
            INSERT INTO va_cf_schedule_a VALUES
                ('Abigail  Spanberger', '2025', '', 'DGA Action', '', 'Federal PAC', 0, '2025-09-01', 1000000),
                ('Abigail  Spanberger', '2025', 'Jane', 'Doe', 'Acme', 'Engineer', 1, '2025-10-01', 250)
            """
        )

        context = dc.build_database_context(
            "Research on Spanberger vetoes with financial record"
        )

        self.assertIn("polls.governor_actions lookup", context)
        self.assertIn("bill_number=HB1385", context)
        self.assertIn("polls.va_finance_people campaign finance profile", context)
        self.assertIn("polls.va_cf_schedule_a campaign finance totals", context)
        self.assertIn("total_amount=1000250.0", context)
        self.assertIn("donor_name=DGA Action", context)
        self.assertIn("Source: Virginia SBE Campaign Finance", context)

    def test_finicial_typo_still_triggers_campaign_finance_context(self):
        self._exec(
            """
            CREATE TABLE va_cf_schedule_a (
                candidate_name TEXT, election_cycle TEXT, first_name TEXT,
                last_or_company TEXT, employer TEXT, occupation TEXT,
                is_individual INTEGER, transaction_date TEXT, amount REAL
            )
            """,
            """
            INSERT INTO va_cf_schedule_a VALUES (
                'Abigail  Spanberger', '2025', '', 'Clean Virginia Fund',
                '', 'PAC', 0, '2025-08-01', 500000
            )
            """
        )

        context = dc.build_database_context("Spanberger finicial record")

        self.assertIn("polls.va_cf_schedule_a campaign finance totals", context)
        self.assertIn("total_amount=500000.0", context)

    def test_finance_debug_query_without_entity_returns_table_inventory(self):
        self._exec(
            """
            CREATE TABLE va_cf_schedule_a (
                candidate_name TEXT, election_cycle TEXT, amount REAL
            )
            """,
            """
            INSERT INTO va_cf_schedule_a VALUES (
                'Abigail  Spanberger', '2025', 500000
            )
            """
        )

        context = dc.build_database_context("why did financial records return no data")

        self.assertIn("lookup_status=needs_entity", context)
        self.assertIn("Campaign finance query needs a candidate", context)
        self.assertIn("- va_cf_schedule_a: rows=1", context)

    def test_finance_zero_match_returns_table_inventory_not_empty_context(self):
        self._exec(
            """
            CREATE TABLE va_cf_schedule_a (
                candidate_name TEXT, election_cycle TEXT, amount REAL
            )
            """,
            """
            INSERT INTO va_cf_schedule_a VALUES (
                'Abigail  Spanberger', '2025', 500000
            )
            """
        )

        context = dc.build_database_context("Does Jane Example have campaign finance records?")

        self.assertIn("lookup_status=zero_records", context)
        self.assertIn("No rows matched searched_terms=jane, example", context)
        self.assertIn("- va_cf_schedule_a: rows=1", context)

    def test_spanberger_governor_record_and_campaign_query_uses_state_records(self):
        self._exec(
            """
            CREATE TABLE governor_actions (
                bill_number TEXT, session TEXT, title TEXT, action_label TEXT,
                action TEXT, action_date TEXT, governor TEXT, source_url TEXT
            )
            """,
            """
            INSERT INTO governor_actions VALUES (
                'HB1385', '2026',
                'Higher educational institutions, public; membership of governing boards.',
                'Vetoed', 'vetoed', '2026-05-19',
                'Abigail Spanberger', 'https://governor.example/hb1385'
            )
            """,
            """
            CREATE TABLE va_cf_schedule_a (
                candidate_name TEXT, election_cycle TEXT, first_name TEXT,
                last_or_company TEXT, employer TEXT, occupation TEXT,
                is_individual INTEGER, transaction_date TEXT, amount REAL
            )
            """,
            """
            INSERT INTO va_cf_schedule_a VALUES (
                'Abigail  Spanberger', '2025', '', 'DGA Action',
                '', 'Federal PAC', 0, '2025-09-01', 1000000
            )
            """,
            """
            CREATE TABLE congress_votes (
                bioguide_id TEXT, congress INTEGER, session INTEGER,
                vote_number INTEGER, vote_date TEXT, bill TEXT, question TEXT,
                member_vote TEXT, result TEXT
            )
            """,
            """
            CREATE TABLE federal_votes (
                bioguide_id TEXT, congress INTEGER, vote TEXT, vote_date TEXT
            )
            """,
        )

        context = dc.build_database_context(
            "Abigail Spanberger state governor record and campaign data correlation"
        )

        self.assertIn("polls.governor_actions lookup", context)
        self.assertIn("lookup_status=records_found", context)
        self.assertIn("bill_number=HB1385", context)
        self.assertIn("polls.va_cf_schedule_a campaign finance totals", context)
        self.assertIn("total_amount=1000000.0", context)
        self.assertNotIn("polls federal vote lookup", context)
        self.assertNotIn("bioguide_id=S001209", context)

    def test_explicit_federal_vote_lookup_returns_spanberger_vote_rows_when_present(self):
        self._exec(
            """
            CREATE TABLE congress_votes (
                bioguide_id TEXT, congress INTEGER, session INTEGER,
                vote_number INTEGER, vote_date TEXT, bill TEXT, question TEXT,
                member_vote TEXT, result TEXT
            )
            """,
            """
            INSERT INTO congress_votes VALUES
                ('S001209', 118, 2, 1, '2024-01-10', 'H R 1', 'On Passage', 'Yea', 'Passed'),
                ('S001209', 118, 2, 2, '2024-01-11', 'H R 2', 'On Passage', 'Nay', 'Failed')
            """,
            """
            CREATE TABLE federal_votes (
                bioguide_id TEXT, congress INTEGER, vote TEXT, vote_date TEXT
            )
            """,
        )

        context = dc.build_database_context("Spanberger former congressional roll call voting record")

        self.assertIn("congress_votes summary", context)
        self.assertIn("total_votes=2", context)
        self.assertIn("yea_votes=1", context)
        self.assertIn("nay_votes=1", context)
        self.assertIn("congress_votes recent", context)

    def test_person_research_report_query_returns_state_legislator_votes(self):
        self._exec_openstates(
            """
            CREATE TABLE legislators (
                id TEXT, name TEXT, party TEXT, chamber TEXT, district TEXT,
                openstates_url TEXT
            )
            """,
            """
            INSERT INTO legislators VALUES (
                'ocd-person/rouse', 'Aaron R. Rouse', 'Democratic',
                'Senate', '22', 'https://openstates.org/person/rouse/'
            )
            """,
            """
            CREATE TABLE votes (
                bill_id TEXT, session TEXT, vote_date TEXT, chamber TEXT,
                motion TEXT, result TEXT, voter_name TEXT, option TEXT,
                party TEXT, district TEXT
            )
            """,
            """
            INSERT INTO votes VALUES (
                'SB764', '2026', '2026-03-14', 'Senate',
                'Adopt Conference Committee Report', 'pass',
                'Aaron R. Rouse', 'yes', 'Democratic', '22'
            )
            """,
            """
            CREATE TABLE bills (
                bill_id TEXT, session TEXT, title TEXT, sponsors TEXT,
                latest_action TEXT, latest_date TEXT, result TEXT,
                openstates_url TEXT
            )
            """,
        )

        context = dc.build_database_context("Aaron Rouse research report")

        self.assertIn("openstates.legislators person", context)
        self.assertIn("name=Aaron R. Rouse", context)
        self.assertIn("openstates.votes person", context)
        self.assertIn("bill_id=SB764", context)

    def test_rouse_voting_and_campaign_finance_ignores_voting_as_name_term(self):
        self._exec(
            """
            CREATE TABLE va_finance_people (
                person_name TEXT, office TEXT, district TEXT, party TEXT,
                role TEXT, committee_name TEXT, finance_url TEXT,
                source_url TEXT, data_confidence TEXT, fetched_at TEXT
            )
            """,
            """
            INSERT INTO va_finance_people VALUES (
                'Aaron Rouse', 'State Senate', '22', 'Democratic',
                'officeholder', NULL, NULL, NULL, 'high',
                '2026-05-17T19:20:27+00:00'
            )
            """,
            """
            CREATE TABLE va_cf_schedule_a (
                candidate_name TEXT, election_cycle TEXT, first_name TEXT,
                last_or_company TEXT, employer TEXT, occupation TEXT,
                is_individual INTEGER, transaction_date TEXT, amount REAL
            )
            """,
            """
            INSERT INTO va_cf_schedule_a VALUES (
                'Mr. Aaron Roosevelt Rouse', '2018', '',
                'Health Care Services of Hampton Roads, Inc.', '',
                'Healthcare', 0, '2018-04-05', 250
            )
            """,
        )
        self._exec_openstates(
            """
            CREATE TABLE votes (
                bill_id TEXT, session TEXT, vote_date TEXT, chamber TEXT,
                motion TEXT, result TEXT, voter_name TEXT, option TEXT,
                party TEXT, district TEXT
            )
            """,
            """
            INSERT INTO votes VALUES (
                'SB764', '2026', '2026-03-14', 'Senate',
                'Adopt Conference Committee Report', 'pass',
                'Aaron R. Rouse', 'yes', 'Democratic', '22'
            )
            """,
            """
            CREATE TABLE bills (
                bill_id TEXT, session TEXT, title TEXT, sponsors TEXT,
                latest_action TEXT, latest_date TEXT, result TEXT,
                openstates_url TEXT
            )
            """,
        )

        context = dc.build_database_context("Aaron Rouse voting record and campaign finance")

        self.assertIn("polls.va_finance_people campaign finance profile", context)
        self.assertIn("person_name=Aaron Rouse", context)
        self.assertIn("polls.va_cf_schedule_a campaign finance totals", context)
        self.assertIn("total_amount=250.0", context)
        self.assertIn("openstates.votes person", context)
        self.assertNotIn("searched_terms=aaron, rouse, voting", context)


if __name__ == "__main__":
    unittest.main()
