#!/usr/bin/env python3
"""Build Chroma-ready JSONL chunks from va_news rows in polls.db."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

from voteiq.utils import clean_json_text


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "polls.db"


def _bill_ids(*texts: str) -> list[str]:
    haystack = " ".join(t or "" for t in texts)
    return sorted(set(
        re.findall(r"\b(?:HB|SB|HJ|SJ|HR|H\.R\.|S\.)\s*\.?\s*\d+\b", haystack, flags=re.I)
    ))


def _as_scalar_list(values) -> list[str]:
    if not values:
        return []
    if isinstance(values, list):
        return [str(v) for v in values if str(v).strip()]
    return [str(values)]


def build_rows(db_path: Path = DB_PATH) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT article_id, source, url, title, published_at, gemini_json, fetched_at
        FROM va_news
        WHERE gemini_json IS NOT NULL
        ORDER BY COALESCE(published_at, fetched_at) DESC
    """).fetchall()
    conn.close()

    chunks: list[dict] = []
    for row in rows:
        try:
            data = json.loads(clean_json_text(row["gemini_json"] or "{}"))
        except Exception:
            data = {}

        headline = data.get("headline") or row["title"] or "Article"
        summary = data.get("summary") or ""
        outlet = data.get("outlet") or row["source"] or ""
        published_date = (data.get("published") or row["published_at"] or "")[:10]
        topics = _as_scalar_list(data.get("topics"))
        politicians = data.get("politicians") or []
        politician_names = [
            p.get("name", "") for p in politicians
            if isinstance(p, dict) and p.get("name")
        ]
        bills = _as_scalar_list(data.get("bills") or data.get("bills_mentioned"))
        if not bills:
            bills = _bill_ids(headline, summary)

        text_lines = [
            f"News Article: {headline}",
            f"Outlet: {outlet}",
            f"Published: {published_date}",
            f"URL: {row['url'] or ''}",
        ]
        if topics:
            text_lines.append(f"Topics: {', '.join(topics)}")
        if politician_names:
            text_lines.append(f"Politicians mentioned: {', '.join(politician_names)}")
        if bills:
            text_lines.append(f"Bills mentioned: {', '.join(bills)}")
        if summary:
            text_lines.append(f"Summary: {summary}")

        metadata = {
            "source": "news",
            "content_type": "article",
            "outlet": outlet,
            "published_date": published_date,
            "url": row["url"] or "",
            "topics": topics,
            "politicians": politician_names,
            "bills": bills,
            "article_id": row["article_id"],
        }
        chunks.append({
            "chunk_id": f"{row['article_id']}_news",
            "text": "\n".join(text_lines),
            "source": "news",
            "content_type": "article",
            "outlet": outlet,
            "published_date": published_date,
            "url": row["url"] or "",
            "topics": topics,
            "politicians": politician_names,
            "bills": bills,
            "bill_id": "",
            "session_id": "",
            "state": "VA",
            "year": published_date[:4],
            "chunk_index": 0,
            "chunk_type": "news_article",
            "metadata": metadata,
        })
    return chunks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--out", default="va_news_rag.jsonl")
    args = parser.parse_args()

    chunks = build_rows(Path(args.db))
    out = Path(args.out)
    with out.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    print(f"Wrote {len(chunks)} news chunks to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
