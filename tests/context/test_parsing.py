"""Tests for voteiq.services.context._parsing."""
import pytest
from voteiq.services.context._parsing import (
    _norm_bill, _bill_numbers, _session_year, _keywords,
)


class TestNormBill:
    def test_standard(self):
        assert _norm_bill("HB84") == "HB84"

    def test_with_space(self):
        assert _norm_bill("HB 84") == "HB84"

    def test_with_dash(self):
        assert _norm_bill("HB-84") == "HB84"

    def test_lowercase(self):
        assert _norm_bill("hb84") == "HB84"

    def test_senate_bill(self):
        assert _norm_bill("SB1234") == "SB1234"

    def test_no_match(self):
        assert _norm_bill("nothing here") == ""

    def test_empty(self):
        assert _norm_bill("") == ""

    def test_none_safe(self):
        assert _norm_bill(None) == ""


class TestBillNumbers:
    def test_single(self):
        assert _bill_numbers("HB84") == ["HB84"]

    def test_multiple(self):
        assert _bill_numbers("HB84 and SB1000") == ["HB84", "SB1000"]

    def test_deduplicates(self):
        assert _bill_numbers("HB84 and HB84") == ["HB84"]

    def test_preserves_order(self):
        assert _bill_numbers("SB200 then HB84") == ["SB200", "HB84"]

    def test_empty_query(self):
        assert _bill_numbers("") == []

    def test_no_bills(self):
        assert _bill_numbers("what did the governor do") == []


class TestSessionYear:
    def test_explicit_year(self):
        assert _session_year("HB84 in 2025") == "2025"

    def test_latest_year_wins(self):
        assert _session_year("from 2024 to 2025") == "2025"

    def test_default_when_absent(self):
        assert _session_year("how did they vote") == "2026"

    def test_empty(self):
        assert _session_year("") == "2026"


class TestKeywords:
    def test_basic_extraction(self):
        kws = _keywords("abortion funding bill")
        assert "abortion" in kws
        assert "funding" in kws

    def test_stops_filtered(self):
        kws = _keywords("what bills did they vote on")
        assert "what" not in kws
        assert "vote" not in kws
        assert "bills" not in kws

    def test_short_words_excluded(self):
        kws = _keywords("the big dog ran")
        assert "the" not in kws
        assert "big" not in kws  # len 3, excluded

    def test_max_eight(self):
        kws = _keywords("environment climate water energy transport education healthcare economy budget")
        assert len(kws) <= 8

    def test_empty(self):
        assert _keywords("") == []
