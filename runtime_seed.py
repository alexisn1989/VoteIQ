"""Runtime polls.db seeding -- must be imported before anything opens polls.db.

Replaces polls.db with a gzipped seed downloaded from POLLS_DB_SEED_URL.
This must happen at runtime rather than in build.sh because Render does not
mount the persistent disk during the build phase -- anything build.sh writes
to DATA_DIR is discarded. A version marker on the disk prevents
re-downloading on every restart; bump POLLS_DB_SEED_VERSION in the Render
dashboard to force a refresh.
"""

import gzip
import os
import shutil
import sqlite3
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def seed_polls_db_from_url() -> None:
    url = os.getenv("POLLS_DB_SEED_URL")
    if not url:
        return

    data_dir_raw = os.getenv("DATA_DIR", BASE_DIR)
    data_dir = data_dir_raw if os.path.isdir(data_dir_raw) else BASE_DIR
    polls_db = os.path.join(data_dir, "polls.db")

    version = os.getenv("POLLS_DB_SEED_VERSION", "1")
    marker = os.path.join(data_dir, ".polls_db_seed_version")
    try:
        with open(marker, "r", encoding="utf-8") as f:
            if f.read().strip() == version:
                print(f"[seed] polls.db seed version {version} already applied -- skipping")
                return
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"[seed] marker read failed ({exc}) -- proceeding with download")

    new_db = polls_db + ".new"
    try:
        print(f"[seed] Downloading polls.db seed version {version}...")
        req = urllib.request.Request(url, headers={"User-Agent": "VoteIQ-seed/1.0"})
        # Stream-decompress straight from the HTTP response so the 500+ MB
        # .gz never needs to be stored on disk.
        with urllib.request.urlopen(req, timeout=120) as resp:
            with gzip.GzipFile(fileobj=resp) as gz, open(new_db, "wb") as out:
                shutil.copyfileobj(gz, out, length=1024 * 1024)

        conn = sqlite3.connect(new_db)
        n_tables = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
        conn.close()
        if n_tables < 20:
            raise RuntimeError(f"seed sanity check failed: only {n_tables} tables")

        os.replace(new_db, polls_db)
        with open(marker, "w", encoding="utf-8") as f:
            f.write(version)
        size_mb = os.path.getsize(polls_db) / 1_048_576
        print(f"[seed] OK polls.db seeded: {n_tables} tables, {size_mb:,.0f} MB")
    except Exception as exc:
        print(f"[seed] WARN Seed failed ({exc}) -- keeping existing polls.db")
        try:
            os.remove(new_db)
        except OSError:
            pass


seed_polls_db_from_url()
