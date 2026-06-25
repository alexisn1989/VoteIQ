"""Tests for voteiq.services.context._budget.PriorityBlockList."""
import pytest
from voteiq.services.context._budget import PriorityBlockList

SEP = "\n\n---\n\n"


def _pbl(*pairs):
    """Build a PriorityBlockList from (priority, text) pairs."""
    b = PriorityBlockList()
    for priority, text in pairs:
        b.set_priority(priority)
        b.append(text)
    return b


class TestBudgetFit:
    def test_within_budget_joins_all(self):
        b = _pbl((PriorityBlockList.HIGH, "A"), (PriorityBlockList.LOW, "B"))
        assert b.budget_fit(99999) == f"A{SEP}B"

    def test_empty_returns_empty(self):
        assert PriorityBlockList().budget_fit(99999) == ""

    def test_low_dropped_before_high(self):
        high_text = "H" * 100
        low_text = "L" * 100
        b = _pbl((PriorityBlockList.HIGH, high_text), (PriorityBlockList.LOW, low_text))
        result = b.budget_fit(len(high_text) + 10)
        assert high_text in result
        assert low_text not in result

    def test_critical_never_dropped(self):
        crit = "C" * 200
        low = "L" * 200
        b = _pbl((PriorityBlockList.CRITICAL, crit), (PriorityBlockList.LOW, low))
        result = b.budget_fit(len(crit) + 10)
        assert crit in result
        assert low not in result

    def test_drops_lowest_priority_first(self):
        b = _pbl(
            (PriorityBlockList.HIGH, "H" * 50),
            (PriorityBlockList.MEDIUM, "M" * 50),
            (PriorityBlockList.LOW, "L" * 50),
        )
        # Budget forces dropping: LOW goes first
        budget = len("H" * 50) + len("M" * 50) + len(SEP) + 5
        result = b.budget_fit(budget)
        assert "H" * 50 in result
        assert "M" * 50 in result
        assert "L" * 50 not in result

    def test_insert_is_always_critical(self):
        b = PriorityBlockList()
        b.set_priority(PriorityBlockList.LOW)
        b.append("low block " * 20)
        b.insert(0, "GUARD")
        result = b.budget_fit(len("GUARD") + 10)
        assert "GUARD" in result

    def test_safety_net_hard_cut(self):
        crit = "C" * 500
        b = _pbl((PriorityBlockList.CRITICAL, crit))
        result = b.budget_fit(100)
        assert len(result) <= 100 + len("\n\n[Database Context truncated to fit model context.]")
        assert "truncated" in result


class TestListInterface:
    def test_len(self):
        b = _pbl((PriorityBlockList.HIGH, "a"), (PriorityBlockList.LOW, "b"))
        assert len(b) == 2

    def test_bool_empty(self):
        assert not PriorityBlockList()

    def test_bool_nonempty(self):
        b = _pbl((PriorityBlockList.HIGH, "x"))
        assert b

    def test_iter(self):
        b = _pbl((PriorityBlockList.HIGH, "x"), (PriorityBlockList.LOW, "y"))
        assert list(b) == ["x", "y"]

    def test_getitem_index(self):
        b = _pbl((PriorityBlockList.HIGH, "first"), (PriorityBlockList.LOW, "second"))
        assert b[0] == "first"
        assert b[1] == "second"

    def test_getitem_slice(self):
        b = _pbl(
            (PriorityBlockList.HIGH, "a"),
            (PriorityBlockList.HIGH, "b"),
            (PriorityBlockList.HIGH, "c"),
        )
        assert b[1:] == ["b", "c"]
        assert b[:2] == ["a", "b"]

    def test_set_priority_returns_self(self):
        b = PriorityBlockList()
        assert b.set_priority(PriorityBlockList.HIGH) is b
