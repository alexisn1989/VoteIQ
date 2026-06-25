import unittest
from pathlib import Path


CHAT_SOURCE = Path("voteiq/api/routes/chat.py").read_text(encoding="utf-8")


class VotingRecordPromptSafetyTests(unittest.TestCase):
    def setUp(self):
        start = CHAT_SOURCE.index("RESPONSE FORMAT — use this exact structure for legislator questions")
        end = CHAT_SOURCE.index("CALL TO ACTION", start)
        self.prompt = CHAT_SOURCE[start:end].replace('\\"', '"')

    def test_removes_interpretive_labels(self):
        lowered = self.prompt.lower()
        self.assertNotIn("moderate voice", lowered)
        self.assertNotIn("caucus read", lowered)
        self.assertNotIn("dissenting votes", lowered)

    def test_uses_record_based_vote_distribution_label(self):
        self.assertIn("Recorded floor vote distribution", self.prompt)
        self.assertNotIn("Overall vote rate", self.prompt)

    def test_uses_recorded_no_votes_heading(self):
        self.assertIn("Recorded NO votes where the bill passed", self.prompt)
        self.assertNotIn("voted NO but bill passed", self.prompt)

    def test_includes_no_inference_note(self):
        self.assertIn("VoteIQ does not infer why [Name] voted NO", self.prompt)
        self.assertIn("vote outcomes, party-majority comparison, bill category, and whether the bill passed", self.prompt)

    def test_committee_vote_caveat_appears(self):
        self.assertIn("Committee vote records in the current dataset show", self.prompt)
        self.assertIn("verify against committee roll-call records", self.prompt)

    def test_metrics_omitted_if_denominators_unavailable(self):
        self.assertIn("If the denominator is not defined, omit this metric", self.prompt)
        self.assertIn("Use only metrics whose denominators are clearly defined", self.prompt)

    def test_issue_break_claim_requires_counts(self):
        self.assertIn("only say \"Issue categories with the most recorded party-majority breaks\" if the excerpt provides counts", self.prompt)
        self.assertIn("If counts are not available, omit the claim", self.prompt)

    def test_footer_only_lists_sources_actually_used(self):
        source_helpers = CHAT_SOURCE

        self.assertIn("sources_used: set[str] | None", source_helpers)
        self.assertIn('"OpenStates"', source_helpers)
        self.assertIn('"virginia lis" in context_lower', source_helpers)
        self.assertNotIn('if "[database context" in context_lower:\n        sources.add("Virginia LIS")', source_helpers)

    def test_response_sanitizer_covers_legacy_phrases(self):
        self.assertIn("def _sanitize_voting_record_language", CHAT_SOURCE)
        self.assertIn("Overall vote rate:", CHAT_SOURCE)
        self.assertIn("Recorded floor vote distribution:", CHAT_SOURCE)
        self.assertIn("Dissenting Votes", CHAT_SOURCE)
        self.assertIn("Recorded NO votes where the bill passed", CHAT_SOURCE)
        self.assertIn("Committee vote records in the current dataset show", CHAT_SOURCE)


if __name__ == "__main__":
    unittest.main()
