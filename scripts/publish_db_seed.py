#!/usr/bin/env python3
"""
Build and publish the production database seed.

Packages polls.db (and the optional sibling DBs openstates_va.db,
legislative_intelligence.db, virginia_legislature.db) as gzipped GitHub
release assets, matching exactly what runtime_seed.py expects to download
at runtime on Render.

This script cannot touch Render itself -- POLLS_DB_SEED_URL and
POLLS_DB_SEED_VERSION are dashboard-only env vars, not declared in
render.yaml. It ends by printing the one manual step left: the version
string to paste into the Render dashboard.

Usage:
    python scripts/publish_db_seed.py --dry-run   # validate + gzip only
    python scripts/publish_db_seed.py              # real publish
    python scripts/publish_db_seed.py --only polls  # republish just polls.db
    python scripts/publish_db_seed.py --keep-gz     # skip re-gzip on retry
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# name -> (asset filename, required, min_tables)
# Mirrors runtime_seed.py's polls.db handling (min_tables=20, required) and
# _EXTRA_DBS (min_tables=1, optional). Keep in sync with runtime_seed.py if
# that file's targets ever change.
_TARGETS: dict[str, tuple[str, bool, int]] = {
    "polls.db": ("polls_seed.db.gz", True, 20),
    "openstates_va.db": ("openstates_va.db.gz", False, 1),
    "legislative_intelligence.db": ("legislative_intelligence.db.gz", False, 1),
    "virginia_legislature.db": ("virginia_legislature.db.gz", False, 1),
}

_MAX_ASSET_BYTES = int(1.8 * 1024**3)  # safety margin under GitHub's 2GB/asset limit


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


# ── Validation (duplicated from runtime_seed.py's _table_count(), not
# imported -- importing that module runs seed_dbs_from_url() as a bare
# module-level side effect gated only on POLLS_DB_SEED_URL being set in the
# environment, which could silently trigger a real download/swap. See
# runtime_seed.py lines 50-70 for the original; keep this in sync with it. ──

def _table_count(db_path: Path) -> int:
    """Return the number of tables, or -1 if the db can't be opened/validated."""
    try:
        conn = sqlite3.connect(str(db_path))
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


def _preflight_gh() -> None:
    version = _run(["gh", "--version"])
    if version.returncode != 0:
        print("ERROR: gh CLI not found. Install from https://cli.github.com/", file=sys.stderr)
        sys.exit(1)
    auth = _run(["gh", "auth", "status"])
    if auth.returncode != 0:
        print("ERROR: gh is not authenticated. Run `gh auth login` first.", file=sys.stderr)
        print(auth.stderr, file=sys.stderr)
        sys.exit(1)


def _resolve_release(explicit_tag: str | None, new_release: bool) -> str:
    if explicit_tag:
        return explicit_tag

    result = _run(["gh", "api", "repos/{owner}/{repo}/releases"])
    if result.returncode != 0:
        print("ERROR: could not list releases via gh api.", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    releases = json.loads(result.stdout)
    drafts = [r for r in releases if r.get("draft")]
    non_drafts = [r for r in releases if not r.get("draft")]

    for d in drafts:
        print(f"[publish] note: found a draft release '{d.get('tag_name')}' "
              f"(id {d.get('id')}) -- leftover manual-process debris, ignoring it. "
              f"Delete by hand with `gh release delete {d.get('id')}` if you want it gone.")

    if new_release:
        version = date.today().isoformat()
        tag = f"db-seed-{version}"
        print(f"[publish] --new-release requested -- will create tag '{tag}'")
        return tag

    if len(non_drafts) == 1:
        tag = non_drafts[0]["tag_name"]
        print(f"[publish] auto-detected live release: '{tag}'")
        return tag

    if len(non_drafts) == 0:
        print("ERROR: no published (non-draft) release found. "
              "Pass --release-tag to target one explicitly, or --new-release to create one.",
              file=sys.stderr)
        sys.exit(1)

    print(f"ERROR: found {len(non_drafts)} published releases -- ambiguous. "
          "Pass --release-tag explicitly:", file=sys.stderr)
    for r in non_drafts:
        print(f"  {r.get('tag_name')}  (id {r.get('id')}, published {r.get('published_at')})",
              file=sys.stderr)
    sys.exit(1)


def _ensure_release_exists(tag: str, version: str) -> None:
    check = _run(["gh", "release", "view", tag])
    if check.returncode == 0:
        return
    print(f"[publish] release '{tag}' does not exist yet -- creating it")
    create = _run([
        "gh", "release", "create", tag,
        "--title", f"Database seed {version}",
        "--notes", f"Published by scripts/publish_db_seed.py on {version}.",
    ])
    if create.returncode != 0:
        print(f"ERROR: failed to create release '{tag}'.", file=sys.stderr)
        print(create.stderr, file=sys.stderr)
        sys.exit(1)


def _gzip_safely(src: Path, compresslevel: int) -> Path:
    """Gzip src to a .gz.tmp, verify it round-trips, then atomically rename."""
    tmp = src.with_suffix(src.suffix + ".gz.tmp")
    final = src.with_suffix(src.suffix + ".gz")

    with open(src, "rb") as fin, gzip.open(tmp, "wb", compresslevel=compresslevel) as fout:
        shutil.copyfileobj(fin, fout, length=1024 * 1024)

    # Integrity check: decompress the tmp file back and compare byte counts
    # against the original before it's ever renamed into place or uploaded.
    original_size = src.stat().st_size
    decompressed = 0
    with gzip.open(tmp, "rb") as f:
        while chunk := f.read(1024 * 1024):
            decompressed += len(chunk)
    if decompressed != original_size:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"gzip integrity check failed for {src.name}: "
            f"decompressed {decompressed} bytes, expected {original_size}"
        )

    os.replace(tmp, final)
    return final


def _upload_asset(tag: str, gz_path: Path, asset_name: str, retries: int = 3) -> bool:
    spec = f"{gz_path}#{asset_name}"
    for attempt in range(1, retries + 1):
        result = _run(["gh", "release", "upload", tag, spec, "--clobber"])
        if result.returncode == 0:
            return True
        print(f"[publish] upload attempt {attempt}/{retries} failed for {asset_name}: "
              f"{result.stderr.strip()}")
        if attempt < retries:
            time.sleep(5 * attempt)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and publish the polls.db seed release.")
    parser.add_argument("--db-dir", default=str(BASE_DIR),
                         help="Directory containing polls.db and sibling DBs (default: repo root)")
    parser.add_argument("--only", default=None,
                         help="Comma-separated subset of targets to publish "
                              "(e.g. 'polls' or 'polls,openstates_va'). Default: all present.")
    parser.add_argument("--release-tag", default=None,
                         help="Explicit release tag. Default: auto-detect the sole "
                              "non-draft release, or error if ambiguous.")
    parser.add_argument("--new-release", action="store_true",
                         help="Create a fresh release tag (db-seed-<today>) instead of "
                              "publishing to the existing one.")
    parser.add_argument("--version", default=None,
                         help="Version string for POLLS_DB_SEED_VERSION. Default: today's date.")
    parser.add_argument("--compresslevel", type=int, default=6)
    parser.add_argument("--keep-gz", action="store_true",
                         help="Don't delete local .gz files after upload.")
    parser.add_argument("--skip-validation", action="store_true",
                         help="DANGEROUS: skip PRAGMA quick_check / table-count validation.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Validate and gzip only -- make no gh release calls.")
    args = parser.parse_args(argv)

    db_dir = Path(args.db_dir)
    version = args.version or date.today().isoformat()
    only = set(t.strip() for t in args.only.split(",")) if args.only else None

    if not args.dry_run:
        _preflight_gh()

    # ── Step 1: locate + validate ──────────────────────────────────────────
    to_publish: list[tuple[str, Path, str, int]] = []  # (db_name, db_path, asset_name, min_tables)

    for db_name, (asset_name, required, min_tables) in _TARGETS.items():
        if only and db_name not in only and db_name.replace(".db", "") not in only:
            continue

        db_path = db_dir / db_name
        if not db_path.is_file():
            if required:
                print(f"ERROR: required file missing: {db_path}", file=sys.stderr)
                return 1
            print(f"[publish] {db_name} not found -- skipping (optional)")
            continue

        if not args.skip_validation:
            n_tables = _table_count(db_path)
            if n_tables < 0:
                print(f"ERROR: {db_name} failed PRAGMA quick_check -- refusing to publish "
                      f"a corrupt db. Use --only to exclude it if this is expected.",
                      file=sys.stderr)
                if required:
                    return 1
                print(f"[publish] skipping broken optional file {db_name}")
                continue
            if n_tables < min_tables:
                print(f"ERROR: {db_name} has only {n_tables} tables (need >= {min_tables}).",
                      file=sys.stderr)
                if required:
                    return 1
                print(f"[publish] skipping under-populated optional file {db_name}")
                continue
            print(f"[publish] {db_name}: OK ({n_tables} tables, "
                  f"{db_path.stat().st_size / 1_048_576:,.0f} MB)")

        to_publish.append((db_name, db_path, asset_name, min_tables))

    if not to_publish:
        print("ERROR: nothing to publish.", file=sys.stderr)
        return 1

    # ── Step 2: gzip safely ────────────────────────────────────────────────
    gz_files: list[tuple[Path, str]] = []  # (gz_path, asset_name)
    for db_name, db_path, asset_name, _ in to_publish:
        gz_path = db_dir / f"{db_name}.gz"
        if args.keep_gz and gz_path.is_file():
            print(f"[publish] reusing existing {gz_path.name} (--keep-gz)")
        else:
            print(f"[publish] gzipping {db_name}...")
            gz_path = _gzip_safely(db_path, args.compresslevel)

        size = gz_path.stat().st_size
        if size > _MAX_ASSET_BYTES:
            print(f"ERROR: {gz_path.name} is {size / 1_073_741_824:.2f} GB compressed, "
                  f"over the {_MAX_ASSET_BYTES / 1_073_741_824:.1f} GB safety margin "
                  f"(GitHub's per-asset limit is 2GB).", file=sys.stderr)
            return 1
        print(f"[publish] {gz_path.name}: {size / 1_048_576:,.0f} MB compressed")
        gz_files.append((gz_path, asset_name))

    if args.dry_run:
        print("\n[publish] --dry-run: validated and gzipped everything above; "
              "no gh release calls made.")
        return 0

    # ── Step 3: resolve release, ensure it exists ──────────────────────────
    tag = _resolve_release(args.release_tag, args.new_release)
    _ensure_release_exists(tag, version)

    # ── Step 4: upload, siblings first, polls.db last (fail-safe ordering) ─
    gz_files.sort(key=lambda pair: pair[1] == "polls_seed.db.gz")

    uploaded, failed = [], []
    for gz_path, asset_name in gz_files:
        print(f"[publish] uploading {asset_name}...")
        if _upload_asset(tag, gz_path, asset_name):
            uploaded.append(asset_name)
        else:
            failed.append(asset_name)
            print(f"[publish] FAILED to upload {asset_name} after retries.")

    if not args.keep_gz:
        for gz_path, _ in gz_files:
            gz_path.unlink(missing_ok=True)

    if failed:
        print(f"\n[publish] FAILED. Uploaded: {uploaded or 'none'}. Failed: {failed}.",
              file=sys.stderr)
        print("[publish] Release is left in a mixed state, but that's safe -- "
              "nothing has been announced yet. Re-run this script; it's idempotent.",
              file=sys.stderr)
        return 1

    asset_url = f"https://github.com/alexisn1989/VoteIQ/releases/download/{tag}/polls_seed.db.gz"
    print(f"""
Seed published to release '{tag}'.

In the Render dashboard, set:
  POLLS_DB_SEED_VERSION = {version}

POLLS_DB_SEED_URL does NOT need to change (still points at):
  {asset_url}

Reminder: render.yaml disk-size changes are not auto-applied by Render
either -- unrelated to this seed, but check it while you're in the dashboard.

After redeploy, verify via GET /health -> .seed / .disk.
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
