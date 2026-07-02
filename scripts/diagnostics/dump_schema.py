import sqlite3
conn = sqlite3.connect("polls.db")
tables = conn.execute("SELECT name FROM sqlite_master WHERE type=?  ORDER BY name", ("table",)).fetchall()
for (t,) in tables:
    cols = conn.execute("PRAGMA table_info([" + t + "])").fetchall()
    print(t + ":")
    for c in cols:
        print("  " + str(c[1]) + " " + str(c[2]))
conn.close()
