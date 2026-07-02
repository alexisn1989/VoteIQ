import sqlite3, tempfile, os
from pathlib import Path
from voteiq.services import database_context as dc
from voteiq.services.context._parsing import _keywords

query = "What bills did Aaron Rouse sponsor?"

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    orig = {k: dc.DB_PATHS[k] for k in dc.DB_PATHS}
    for k in dc.DATA_DIR_ENV_VARS:
        os.environ.pop(k, None)
    for key in dc.DB_PATHS:
        dc.DB_PATHS[key] = tmp_path / dc.DB_PATHS[key].name

    conn = sqlite3.connect(tmp_path / "polls.db")
    conn.execute(
        "CREATE TABLE va_legislator_sponsored_bills "
        "(legislator_name TEXT, session TEXT, bill_number TEXT, status_label TEXT)"
    )
    conn.execute("INSERT INTO va_legislator_sponsored_bills VALUES ('Aaron Rouse','2026','SB10','Passed')")
    conn.execute("INSERT INTO va_legislator_sponsored_bills VALUES ('Aaron Rouse','2026','SB11','Introduced')")
    conn.commit()
    conn.close()

    print("resolved polls path:", dc._resolve_db_path("polls"))
    print("temp path:", tmp_path / "polls.db")
    print("file exists:", (tmp_path / "polls.db").exists())

    terms = _keywords(query)
    print("terms:", terms)
    people = dc._person_terms(terms)
    print("people:", people)

    # simulate what the builder does
    conn2 = dc._connect("polls")
    print("conn2:", conn2)
    if conn2:
        matched = set()
        for p in people:
            p = (p or "").strip()
            if len(p) < 3:
                continue
            rows = conn2.execute(
                "SELECT DISTINCT legislator_name FROM va_legislator_sponsored_bills "
                "WHERE lower(legislator_name) LIKE ?",
                (f"%{p.lower()}%",),
            ).fetchall()
            print(f"  LIKE '%{p.lower()}%' -> {rows}")
            for r in rows:
                matched.add(r[0])
        print("matched:", matched)
    conn2.close()

    dc.DB_PATHS.update(orig)
