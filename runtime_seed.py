"""Runtime database seeding -- must be imported before anything opens the dbs.

Replaces polls.db (and optional sibling databases) with gzipped seeds
downloaded from GitHub release assets. This must happen at runtime rather
than in build.sh because Render does not mount the persistent disk during
the build phase -- anything build.sh writes to DATA_DIR is discarded.

POLLS_DB_SEED_URL points at the polls.db seed asset. Sibling database
seeds are derived from the same release URL by filename (e.g.
.../polls_seed.db.gz -> .../openstates_va.db.gz), so adding assets to the
release is enough -- no extra env vars. Missing sibling assets are skipped
with a warning. A version marker per database prevents re-downloading on
every restart; bump POLLS_DB_SEED_VERSION in the Render dashboard to force
a refresh of everything.

The app runs its dbs in WAL mode, so a stale .db-wal / .db-shm pair from
the previous instance can survive on the persistent disk. If those survive
a swap, SQLite replays the old WAL into the new file and corrupts it
("malformed database schema"). So we always remove sidecar files before
swapping, and we re-seed even on a matching marker if the db fails a
schema probe.
"""

import gzip
import os
import shutil
import sqlite3
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")

# Sibling databases shipped on the same release as the polls.db seed.
# Asset name on the release must be "<filename>.gz".
_EXTRA_DBS = (
    "openstates_va.db",
    "legislative_intelligence.db",
    "virginia_legislature.db",
)


def _table_count(db_path: str) -> int:
    """Return the number of tables, or -1 if the db can't be opened/validated.

    Runs PRAGMA quick_check so incompatible schema objects (e.g. indexes or
    triggers compiled against a newer SQLite) are caught before the swap, not
    after. Querying sqlite_master alone is not sufficient because it bypasses
    schema compilation.
    """
    try:
        conn = sqlite3.connect(db_path)
        try:
            result = conn.execute("PRAGMA quick_check").fetchone()
            if not result or result[0] != "ok":
                return -1
            return conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return -1


def _remove_sidecars(db_path: str) -> None:
    for suffix in _SIDECAR_SUFFIXES:
        try:
            os.remove(db_path + suffix)
            print(f"[seed] removed stale {os.path.basename(db_path + suffix)}")
        except OSError:
            pass


def _seed_one(url: str, db_path: str, marker: str, version: str,
              min_tables: int, optional: bool) -> None:
    name = os.path.basename(db_path)
    try:
        with open(marker, "r", encoding="utf-8") as f:
            marker_matches = f.read().strip() == version
    except Exception:
        marker_matches = False

    if marker_matches:
        if _table_count(db_path) >= min_tables:
            print(f"[seed] {name} seed version {version} already applied -- skipping")
            return
        print(f"[seed] marker matches but {name} fails schema probe -- re-seeding")

    new_db = db_path + ".new"
    try:
        print(f"[seed] Downloading {name} seed version {version}...")
        req = urllib.request.Request(url, headers={"User-Agent": "VoteIQ-seed/1.0"})
        # Stream-decompress straight from the HTTP response so the .gz
        # never needs to be stored on disk.
        with urllib.request.urlopen(req, timeout=120) as resp:
            with gzip.GzipFile(fileobj=resp) as gz, open(new_db, "wb") as out:
                shutil.copyfileobj(gz, out, length=1024 * 1024)

        n_tables = _table_count(new_db)
        if n_tables < min_tables:
            raise RuntimeError(f"seed sanity check failed: only {n_tables} tables")

        # Stale WAL/journal files from the previous instance would be replayed
        # into the new db on first open and corrupt it -- remove them first.
        _remove_sidecars(db_path)
        os.replace(new_db, db_path)

        n_after = _table_count(db_path)
        if n_after < min_tables:
            raise RuntimeError(f"post-swap probe failed: {n_after} tables readable")

        with open(marker, "w", encoding="utf-8") as f:
            f.write(version)
        size_mb = os.path.getsize(db_path) / 1_048_576
        print(f"[seed] OK {name} seeded: {n_after} tables, {size_mb:,.0f} MB")
    except urllib.error.HTTPError as exc:
        if optional and exc.code == 404:
            print(f"[seed] {name} seed asset not on release (404) -- skipping")
        else:
            print(f"[seed] WARN {name} seed failed ({exc}) -- keeping existing db")
        _cleanup(new_db)
    except Exception as exc:
        print(f"[seed] WARN {name} seed failed ({exc}) -- keeping existing db")
        _cleanup(new_db)


def _cleanup(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def seed_dbs_from_url() -> None:
    url = os.getenv("POLLS_DB_SEED_URL")
    if not url:
        return

    data_dir_raw = os.getenv("DATA_DIR", BASE_DIR)
    data_dir = data_dir_raw if os.path.isdir(data_dir_raw) else BASE_DIR
    version = os.getenv("POLLS_DB_SEED_VERSION", "1")

    _seed_one(
        url,
        os.path.join(data_dir, "polls.db"),
        os.path.join(data_dir, ".polls_db_seed_version"),
        version,
        min_tables=20,
        optional=False,
    )

    release_base = url.rsplit("/", 1)[0]
    for filename in _EXTRA_DBS:
        _seed_one(
            f"{release_base}/{filename}.gz",
            os.path.join(data_dir, filename),
            os.path.join(data_dir, f".{filename}.seed_version"),
            version,
            min_tables=1,
            optional=True,
        )


seed_dbs_from_url()
