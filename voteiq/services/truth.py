from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent.parent
TRUTH_DB = Path(
    os.getenv(
        "TRUTH_LOG_DB",
        str(Path(os.getenv("DATA_DIR", str(BASE_DIR))) / "truth_logs.db"),
    )
)

TRUTH_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS truth_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT,
    sql_queries TEXT,
    sql_results TEXT,
    rag_docs TEXT,
    final_answer TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_BILL_RE = re.compile(r"\b(?:HB|SB|HJ|SJ|HR|SR|HJR|SJR)\s*-?\s*\d{1,5}\b", re.I)
_MONEY_RE = re.compile(r"\$[\d,]+(?:\.\d+)?")
_VOTE_COUNT_RE = re.compile(
    r"\b(?P<yes>\d{1,4})\s*(?:yes|yea|ayes|y)\b.{0,40}?\b(?P<no>\d{1,4})\s*(?:no|nay|n)\b",
    re.I,
)
_DASH_VOTE_COUNT_RE = re.compile(r"\b(?P<yes>\d{1,4})\s*[-–]\s*(?P<no>\d{1,4})\b")
_VOTED_RE = re.compile(
    r"\b(?P<entity>[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,4})\s+voted\s+(?P<vote>yes|no|yea|nay)\b",
    re.I,
)
_OUTCOME_RE = re.compile(
    r"\b(?:(?P<bill1>(?:HB|SB|HJ|SJ|HR|SR|HJR|SJR)\s*-?\s*\d{1,5}).{0,80}?\b(?P<outcome1>passed|failed)|"
    r"(?P<outcome2>passed|failed).{0,80}?\b(?P<bill2>(?:HB|SB|HJ|SJ|HR|SR|HJR|SJR)\s*-?\s*\d{1,5}))\b",
    re.I,
)


def init_truth_log_db(db_path: str | Path | None = None) -> Path:
    path = Path(db_path or TRUTH_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(TRUTH_LOG_SCHEMA)
        conn.commit()
    return path


def _json_dumps(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value if value is not None else [], default=str, ensure_ascii=False)


def log_truth_event(
    query: str,
    sql_queries: Any,
    sql_results: Any,
    rag_docs: Any,
    final_answer: str,
    db_path: str | Path | None = None,
) -> int:
    path = init_truth_log_db(db_path)
    with sqlite3.connect(path) as conn:
        cur = conn.execute(
            """
            INSERT INTO truth_logs (query, sql_queries, sql_results, rag_docs, final_answer)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                query or "",
                _json_dumps(sql_queries),
                _json_dumps(sql_results),
                _json_dumps(rag_docs),
                final_answer or "",
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text or "") if part.strip()]


def _normalize_bill(text: str) -> str:
    match = _BILL_RE.search(text or "")
    return re.sub(r"\s|-", "", match.group(0)).upper() if match else ""


def _nearby_bill(text: str) -> str:
    return _normalize_bill(text)


def extract_facts(text: str) -> list[dict]:
    """Extract only simple structured claims; intentionally regex-only."""
    claims: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add(claim_type: str, value: str, entity: str, claim_text: str) -> None:
        key = (claim_type, value.lower(), entity.lower(), claim_text.lower())
        if key in seen:
            return
        seen.add(key)
        claims.append(
            {
                "type": claim_type,
                "value": value,
                "entity": entity,
                "claim": claim_text.strip(),
            }
        )

    for sentence in _sentences(text):
        bill = _nearby_bill(sentence)
        for match in _VOTE_COUNT_RE.finditer(sentence):
            add("vote_count", f"{match.group('yes')}-{match.group('no')}", bill, match.group(0))
        if re.search(r"\b(?:vote|votes|passed|failed|yea|nay|yes|no)\b", sentence, re.I):
            for match in _DASH_VOTE_COUNT_RE.finditer(sentence):
                add("vote_count", f"{match.group('yes')}-{match.group('no')}", bill, match.group(0))
        for match in _MONEY_RE.finditer(sentence):
            entity = bill or _best_entity_before(sentence, match.start())
            add("money_amount", match.group(0), entity, match.group(0))
        for match in _VOTED_RE.finditer(sentence):
            vote = "YES" if match.group("vote").lower() in {"yes", "yea"} else "NO"
            add("vote_position", vote, match.group("entity").strip(), match.group(0))
        for match in _OUTCOME_RE.finditer(sentence):
            outcome = (match.group("outcome1") or match.group("outcome2") or "").lower()
            entity = _normalize_bill(match.group("bill1") or match.group("bill2") or sentence)
            add("bill_outcome", outcome, entity, match.group(0))

    return claims


def _best_entity_before(sentence: str, pos: int) -> str:
    prefix = sentence[:pos]
    names = re.findall(r"\b[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,4}\b", prefix)
    return names[-1].strip() if names else ""


def _flatten_sql_results(sql_results: Any) -> str:
    if sql_results is None:
        return ""
    if isinstance(sql_results, str):
        return sql_results
    return json.dumps(sql_results, default=str, ensure_ascii=False)


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _money_variants(value: str) -> set[str]:
    raw = value.replace("$", "").replace(",", "")
    variants = {value, raw}
    try:
        amount = float(raw)
        variants.add(f"{amount:,.0f}")
        variants.add(f"{amount:.0f}")
        variants.add(f"${amount:,.0f}")
        variants.add(f"{amount:,.2f}")
        variants.add(f"${amount:,.2f}")
    except ValueError:
        pass
    return variants


def verify_claims(claims, sql_results) -> list[dict]:
    """Verify extracted claims only against supplied SQL result text/data."""
    haystack = _flatten_sql_results(sql_results)
    hay_norm = _norm(haystack)
    results: list[dict] = []
    for claim in claims or []:
        claim_type = claim.get("type", "")
        value = str(claim.get("value", ""))
        entity = str(claim.get("entity", ""))
        verified = False
        reason = None

        if not haystack:
            reason = "no_sql_results"
        elif claim_type == "money_amount":
            verified = any(_norm(v) in hay_norm for v in _money_variants(value))
            if entity:
                verified = verified and _norm(entity) in hay_norm
            reason = None if verified else "money_amount_not_found_in_sql_results"
        elif claim_type == "vote_count":
            yes, _, no = value.partition("-")
            variants = {value, f"{yes} yes", f"{yes} yea", f"{no} no", f"{no} nay"}
            verified = (_norm(value) in hay_norm) or (
                any(_norm(v) in hay_norm for v in variants if yes in v)
                and any(_norm(v) in hay_norm for v in variants if no in v)
            ) or (
                any(_norm(v) in hay_norm for v in (f"yes={yes}", f"yea={yes}", f"ayes={yes}", f"y={yes}"))
                and any(_norm(v) in hay_norm for v in (f"no={no}", f"nay={no}", f"nays={no}", f"n={no}"))
            )
            if entity:
                verified = verified and _norm(entity) in hay_norm
            reason = None if verified else "vote_count_not_found_in_sql_results"
        elif claim_type in {"vote_position", "bill_outcome"}:
            verified = _norm(value) in hay_norm
            if entity:
                verified = verified and _norm(entity) in hay_norm
            reason = None if verified else f"{claim_type}_not_found_in_sql_results"
        else:
            reason = "unsupported_claim_type"

        results.append(
            {
                "claim": claim.get("claim") or f"{entity} {value}".strip(),
                "verified": bool(verified),
                "reason": reason,
            }
        )
    return results


def truth_score(verification_results: list[dict]) -> float:
    if not verification_results:
        return 0.0
    return 1.0 if all(item.get("verified") for item in verification_results) else -1.0


def get_truth_debug(log_id: int, db_path: str | Path | None = None) -> dict | None:
    path = init_truth_log_db(db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM truth_logs WHERE id=?", (log_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    data = dict(row)
    claims = extract_facts(data.get("final_answer") or "")
    verification = verify_claims(claims, data.get("sql_results") or "")
    data["extracted_claims"] = claims
    data["verification_results"] = verification
    data["truth_score"] = truth_score(verification)
    return data
