"""Pure query-parsing helpers — no DB calls, no I/O."""
from __future__ import annotations

import re

BILL_RE = re.compile(r"\b(HB|SB|HJ|SJ|HR|SR|HJR|SJR)\s*-?\s*(\d{1,5})\b", re.I)
YEAR_RE = re.compile(r"\b20\d{2}\b")

_KEYWORD_STOP = {
    "what", "when", "where", "which", "about", "with", "from", "that",
    "this", "have", "does", "did", "were", "they", "them", "your",
    "show", "tell", "give", "list", "vote", "votes", "voted", "bill",
    "bills", "governor",
}


def _norm_bill(value: str) -> str:
    match = BILL_RE.search(value or "")
    return f"{match.group(1).upper()}{match.group(2)}" if match else ""


def _bill_numbers(query: str) -> list[str]:
    seen: set[str] = set()
    bills: list[str] = []
    for match in BILL_RE.finditer(query or ""):
        bill = f"{match.group(1).upper()}{match.group(2)}"
        if bill not in seen:
            seen.add(bill)
            bills.append(bill)
    return bills


def _session_year(query: str) -> str:
    years = YEAR_RE.findall(query or "")
    return years[-1] if years else "2026"


def _keywords(query: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", query or "")
    result: list[str] = []
    seen: set[str] = set()
    for word in words:
        low = word.lower().strip("'")
        if len(low) < 4 or low in _KEYWORD_STOP or low in seen:
            continue
        seen.add(low)
        result.append(low)
        if len(result) >= 8:
            break
    return result
