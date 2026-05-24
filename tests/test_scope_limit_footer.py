import re
import unittest
from pathlib import Path


CHAT_SOURCE = Path("voteiq/api/routes/chat.py").read_text(encoding="utf-8")


def _function_source(name: str) -> str:
    match = re.search(
        rf"def {name}\([^)]*\).*?(?=\n\ndef |\nclass |\n@router|\Z)",
        CHAT_SOURCE,
        flags=re.S,
    )
    if not match:
        raise AssertionError(f"Function {name} not found")
    return match.group(0)


class ScopeLimitFooterTests(unittest.TestCase):
    def setUp(self):
        self.source_line = _function_source("_source_line")
        self.polling_reply = _function_source("_scope_limit_polling_reply")
        self.polling_detector = _function_source("_is_public_opinion_polling_scope_limit_query")

    def test_polling_scope_limit_answer_type_is_defined(self):
        self.assertIn('SCOPE_LIMIT_ANSWER_TYPE = "Scope / dataset limitation"', CHAT_SOURCE)
        self.assertIn('POLLING_DATA_LIMIT = "Polling data is not currently included', CHAT_SOURCE)

    def test_polling_scope_limit_footer_says_sources_used_none(self):
        self.assertIn("Sources used: none", self.source_line)
        self.assertIn("SCOPE_LIMIT_ANSWER_TYPE", self.source_line)
        self.assertIn("POLLING_DATA_LIMIT", self.polling_reply)

    def test_polling_scope_limit_footer_avoids_generic_record_sources(self):
        scope_branch = self.source_line.split("if sources_used:", 1)[0]
        for source_name in (
            "Virginia LIS",
            "FEC",
            "Congress.gov",
            "VPAP",
            "Governor of Virginia",
            "Document/RAG context",
        ):
            self.assertNotIn(source_name, scope_branch)

    def test_external_polling_sites_are_suggestions_not_sources(self):
        self.assertIn("External places to check", self.polling_reply)
        self.assertIn("VCU Wilder School", self.polling_reply)
        self.assertIn("CNU Wason Center", self.polling_reply)
        self.assertIn("RealClearPolitics", self.polling_reply)
        self.assertIn("FiveThirtyEight", self.polling_reply)
        self.assertIn("external suggestions, not VoteIQ sources", self.polling_reply)

    def test_data_limits_mentions_polling_not_in_voteiq_dataset(self):
        self.assertIn(
            "Polling data is not currently included in VoteIQ's public-record dataset",
            CHAT_SOURCE,
        )

    def test_helpful_public_record_redirect_is_kept(self):
        self.assertIn(
            "Is there something specific you're trying to understand about your representatives or a bill?",
            self.polling_reply,
        )
        self.assertIn(
            "I may be able to help with the public-record side of that question.",
            self.polling_reply,
        )

    def test_polling_place_questions_are_not_scope_limited(self):
        self.assertIn("polling place", self.polling_detector)
        self.assertIn("polling location", self.polling_detector)
        self.assertIn("where do i vote", self.polling_detector)


if __name__ == "__main__":
    unittest.main()
