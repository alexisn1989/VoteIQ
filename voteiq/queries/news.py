import json
import sqlite3

from voteiq.config.db_routing import query_polls


def get_legislator_news(legislator_name: str, limit: int = 5) -> list[dict]:
    """
    Return recent va_news articles that mention legislator_name.

    Searches both the article title and the gemini_json politician list
    (which is more reliable than title-only for shorter name variants).
    Results are deduplicated and sorted newest-first.
    """
    limit = max(1, min(int(limit), 50))
    pattern = f"%{legislator_name}%"

    sql = """
        SELECT title, source, url, published_at,
               gemini_json
        FROM va_news
        WHERE (title LIKE ?
           OR  gemini_json LIKE ?)
          AND gemini_json NOT LIKE '%_error%'
        ORDER BY COALESCE(published_at, fetched_at) DESC
        LIMIT ?
    """
    rows = query_polls(sql, (pattern, pattern, limit))

    results = []
    for row in rows:
        item = dict(row) if hasattr(row, "keys") else {
            "title": row[0],
            "source": row[1],
            "url": row[2],
            "published_at": row[3],
            "gemini_json": row[4],
        }
        gj_raw = item.pop("gemini_json", None)
        try:
            gj = json.loads(gj_raw) if gj_raw else {}
        except (json.JSONDecodeError, TypeError):
            gj = {}
        item["author"] = (gj.get("author") or "").strip()
        item["summary"] = gj.get("summary", "")
        item["politicians"] = [p.get("name", "") for p in (gj.get("politicians") or [])]
        item["topics"] = gj.get("topics", [])
        results.append(item)

    return results
