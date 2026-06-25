"""PriorityBlockList — block-level budget allocator for context assembly."""
from __future__ import annotations


class PriorityBlockList:
    """Drop-in replacement for list[str] with per-block priority tracking.

    Builders call blocks.append(text) unchanged.  The orchestrator calls
    blocks.set_priority(n) before each builder group to tag its blocks.
    budget_fit() drops lowest-priority whole blocks first when over budget,
    so vote facts are never sliced mid-sentence by a byte-level truncation.
    """

    CRITICAL = 1  # never dropped: scope guards, vote waterfall, PAC correlation
    HIGH = 2      # core facts: votes, bills, named-legislator lookups
    MEDIUM = 3    # supplemental: profiles, lobbying, donor trends
    LOW = 4       # dropped first: rendering rules, keyword fallback

    def __init__(self) -> None:
        self._items: list[tuple[int, str]] = []
        self._current = self.MEDIUM

    def set_priority(self, priority: int) -> "PriorityBlockList":
        self._current = priority
        return self

    def append(self, text: str) -> None:
        self._items.append((self._current, text))

    def insert(self, index: int, text: str) -> None:
        # insert(0, ...) is used for critical out_of_scope guards — always CRITICAL
        self._items.insert(index, (self.CRITICAL, text))

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def __iter__(self):
        return (text for _, text in self._items)

    def __getitem__(self, key):
        if isinstance(key, slice):
            return [t for _, t in self._items[key]]
        return self._items[key][1]

    def budget_fit(self, max_chars: int, sep: str = "\n\n---\n\n") -> str:
        """Assemble blocks within budget, dropping lowest-priority whole blocks first."""
        items = self._items
        if not items:
            return ""

        sep_len = len(sep)
        n = len(items)
        total = sum(len(t) for _, t in items) + sep_len * max(0, n - 1)

        if total <= max_chars:
            return sep.join(t for _, t in items)

        dropped: set[int] = set()
        # Sort non-CRITICAL candidates: lowest priority (highest p) first,
        # then latest position first to preserve early context.
        candidates = sorted(
            (i for i, (p, _) in enumerate(items) if p > self.CRITICAL),
            key=lambda i: (-items[i][0], -i),
        )
        for idx in candidates:
            if total <= max_chars:
                break
            total -= len(items[idx][1]) + sep_len
            dropped.add(idx)

        texts = [t for i, (_, t) in enumerate(items) if i not in dropped]
        result = sep.join(texts)
        # Safety net: if CRITICAL blocks alone exceed max_chars, hard-cut with notice
        if len(result) > max_chars:
            result = result[:max_chars].rstrip() + "\n\n[Database Context truncated to fit model context.]"
        return result
