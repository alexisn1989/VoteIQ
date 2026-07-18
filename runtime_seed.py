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

# Populated by seed_dbs_from_url() -- read by GET /health so seed state is
# visible in one request instead of grepping boot logs for a print
# statement that only appears once, at import time.
SEED_STATUS: dict[str, dict] = {}
DISK_STATUS: dict = {}


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


def _read_marker(marker: str) -> str | None:
    try:
        with open(marker, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return None


def _check_disk_space(db_path: str, data_dir: str) -> tuple[bool, str]:
    """Estimate whether there's enough free space for a safe old+new swap.

    The swap needs the existing db AND a full new copy on disk at the same
    time (os.replace() below only happens after the download completes) --
    a disk that's merely big enough to hold one copy isn't big enough for
    the swap. This is a preflight estimate, not a guarantee: it compares
    against the *existing* file's size as a proxy for the incoming one.
    """
    try:
        free = shutil.disk_usage(data_dir).free
    except Exception:
        return True, "disk_usage check unavailable -- proceeding"

    existing_size = os.path.getsize(db_path) if os.path.isfile(db_path) else 0
    # No existing file (first-ever boot on a fresh disk) -- we can't know
    # the new size in advance, so assume a modest floor.
    estimated_new_size = existing_size if existing_size > 0 else 500 * 1024 * 1024
    margin = 200 * 1024 * 1024  # sidecar files, other DBs downloading concurrently
    needed = existing_size + estimated_new_size + margin

    msg = (f"free={free / 1_048_576:,.0f}MB needed=~{needed / 1_048_576:,.0f}MB "
           f"(existing={existing_size / 1_048_576:,.0f}MB + "
           f"new~={estimated_new_size / 1_048_576:,.0f}MB + margin)")
    return free >= needed, msg


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
            SEED_STATUS[name] = {
                "status": "up_to_date",
                "version_wanted": version,
                "version_applied": version,
            }
            return
        print(f"[seed] marker matches but {name} fails schema probe -- re-seeding")

    space_ok, space_msg = _check_disk_space(db_path, os.path.dirname(db_path))
    if not space_ok:
        print(f"[seed] WARN {name} seed skipped -- insufficient disk space ({space_msg})")
        SEED_STATUS[name] = {
            "status": "failed_preflight_space",
            "version_wanted": version,
            "version_applied": _read_marker(marker),
            "error": space_msg,
        }
        return

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
        SEED_STATUS[name] = {
            "status": "downloaded",
            "version_wanted": version,
            "version_applied": version,
            "tables": n_after,
            "size_mb": round(size_mb, 1),
        }
    except urllib.error.HTTPError as exc:
        if optional and exc.code == 404:
            print(f"[seed] {name} seed asset not on release (404) -- skipping")
            SEED_STATUS[name] = {
                "status": "not_on_release",
                "version_wanted": version,
                "version_applied": _read_marker(marker),
            }
        else:
            print(f"[seed] WARN {name} seed failed ({exc}) -- keeping existing db")
            SEED_STATUS[name] = {
                "status": "failed",
                "version_wanted": version,
                "version_applied": _read_marker(marker),
                "error": str(exc),
            }
        _cleanup(new_db)
    except Exception as exc:
        print(f"[seed] WARN {name} seed failed ({exc}) -- keeping existing db")
        SEED_STATUS[name] = {
            "status": "failed",
            "version_wanted": version,
            "version_applied": _read_marker(marker),
            "error": str(exc),
        }
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

    try:
        usage = shutil.disk_usage(data_dir)
        DISK_STATUS["free_gb"] = round(usage.free / 1_073_741_824, 2)
        DISK_STATUS["total_gb"] = round(usage.total / 1_073_741_824, 2)
    except Exception as exc:
        DISK_STATUS["error"] = str(exc)

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
