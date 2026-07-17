"""
va_bill_number.py
-----------------
Canonical form for Virginia General Assembly bill/resolution numbers.

Normalizations applied:
  * Uppercase + strip whitespace
  * SJR<n> / HJR<n>  ->  SJ<n> / HJ<n>   (LIS uses both; canonical drops the R)
  * Leading zeros stripped from the numeric suffix (e.g. HB0042 -> HB42)

Recognized prefixes: HB SB HJ SJ HR SR (plus their JR variants).
Returns None for anything that doesn't match a known pattern.
"""

from __future__ import annotations
import re

# Matches HB, SB, HJ, SJ, HJR, SJR  +  optional leading zeros  +  number
_JB_RE = re.compile(r'^([SH])([JB])(R?)0*([1-9]\d*)$', re.IGNORECASE)

# Matches HR, SR  +  optional leading zeros  +  number
_SR_RE = re.compile(r'^([SH])(R)0*([1-9]\d*)$', re.IGNORECASE)


def normalize_bill_number(raw: str) -> str | None:
    """Return the canonical bill number string, or None if unrecognized."""
    if not raw:
        return None
    s = raw.strip().upper()

    m = _JB_RE.match(s)
    if m:
        chamber, kind, _r, num = m.groups()
        return f"{chamber}{kind}{num}"   # HJR2 -> HJ2, HB42 -> HB42

    m = _SR_RE.match(s)
    if m:
        chamber, _, num = m.groups()
        return f"{chamber}R{num}"        # SR5 -> SR5, HR10 -> HR10

    return None
