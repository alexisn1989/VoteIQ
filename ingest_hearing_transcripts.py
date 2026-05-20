"""
Transcribe Virginia legislative hearing videos with OpenAI Whisper
and store segments + full text in polls.db.

Usage:
    python ingest_hearing_transcripts.py hearing_video.mp4
    python ingest_hearing_transcripts.py hearing_video.mp4 --title "HB 10 Finance Subcommittee" --bill HB10 --model medium
    python ingest_hearing_transcripts.py hearing_video.mp4 --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent
_DEFAULT_DB = os.path.join(os.getenv("DATA_DIR", str(_BASE_DIR)), "polls.db")

DDL = """
CREATE TABLE IF NOT EXISTS hearing_transcripts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript_id TEXT NOT NULL UNIQUE,
    source_file   TEXT,
    title         TEXT,
    bill_id       TEXT,
    hearing_date  TEXT,
    model         TEXT,
    language      TEXT,
    full_text     TEXT,
    segments_json TEXT,
    duration_s    REAL,
    ingested_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ht_bill   ON hearing_transcripts(bill_id);
CREATE INDEX IF NOT EXISTS idx_ht_date   ON hearing_transcripts(hearing_date);
"""


def _db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    conn.commit()
    return conn


def _transcript_id(source_file: str) -> str:
    return hashlib.sha1(Path(source_file).resolve().as_posix().encode()).hexdigest()[:16]


def transcribe(
    video_path: str,
    *,
    model_name: str = "base",
    language: str | None = None,
) -> dict:
    try:
        import whisper
    except ImportError:
        print("openai-whisper not installed. Run: pip install openai-whisper", file=sys.stderr)
        sys.exit(1)

    print(f"[whisper] Loading model '{model_name}'…", flush=True)
    model = whisper.load_model(model_name)

    opts: dict = {}
    if language:
        opts["language"] = language

    print(f"[whisper] Transcribing {video_path}…", flush=True)
    result = model.transcribe(video_path, **opts)
    return result


def ingest(
    video_path: str,
    *,
    db_path: str = _DEFAULT_DB,
    title: str | None = None,
    bill_id: str | None = None,
    hearing_date: str | None = None,
    model_name: str = "base",
    language: str | None = None,
    dry_run: bool = False,
) -> dict:
    if not os.path.exists(video_path):
        return {"ok": False, "error": f"File not found: {video_path}"}

    result = transcribe(video_path, model_name=model_name, language=language)

    segments = [
        {
            "start": round(seg["start"], 2),
            "end":   round(seg["end"], 2),
            "text":  seg["text"].strip(),
        }
        for seg in result.get("segments", [])
    ]

    duration = segments[-1]["end"] if segments else 0.0
    tid = _transcript_id(video_path)
    now = datetime.now(timezone.utc).isoformat()

    row = {
        "transcript_id": tid,
        "source_file":   str(Path(video_path).name),
        "title":         title or Path(video_path).stem,
        "bill_id":       bill_id,
        "hearing_date":  hearing_date or now[:10],
        "model":         model_name,
        "language":      result.get("language", "en"),
        "full_text":     result.get("text", "").strip(),
        "segments_json": json.dumps(segments, ensure_ascii=False),
        "duration_s":    duration,
        "ingested_at":   now,
    }

    print(f"[ingest] {len(segments)} segments — {duration:.0f}s — lang={row['language']}", flush=True)

    if dry_run:
        print("[dry-run] Would write:", json.dumps({k: v for k, v in row.items() if k != "segments_json"}, indent=2))
        return {"ok": True, "dry_run": True, "segments": len(segments)}

    conn = _db(db_path)
    conn.execute(
        """INSERT INTO hearing_transcripts
               (transcript_id, source_file, title, bill_id, hearing_date,
                model, language, full_text, segments_json, duration_s, ingested_at)
           VALUES (:transcript_id, :source_file, :title, :bill_id, :hearing_date,
                   :model, :language, :full_text, :segments_json, :duration_s, :ingested_at)
           ON CONFLICT(transcript_id) DO UPDATE SET
               title         = excluded.title,
               bill_id       = excluded.bill_id,
               hearing_date  = excluded.hearing_date,
               full_text     = excluded.full_text,
               segments_json = excluded.segments_json,
               duration_s    = excluded.duration_s,
               ingested_at   = excluded.ingested_at""",
        row,
    )
    conn.commit()
    conn.close()

    # Write sidecar files alongside the video for quick reference
    base = Path(video_path).with_suffix("")
    base.with_suffix(".json").write_text(
        json.dumps(segments, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    base.with_suffix(".txt").write_text(row["full_text"], encoding="utf-8")

    print(f"[done] transcript_id={tid} saved to {db_path}", flush=True)
    return {"ok": True, "transcript_id": tid, "segments": len(segments), "duration_s": duration}


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Transcribe hearing video → polls.db")
    p.add_argument("video", help="Path to video/audio file")
    p.add_argument("--db",       default=_DEFAULT_DB, help="polls.db path")
    p.add_argument("--title",    help="Human-readable hearing title")
    p.add_argument("--bill",     dest="bill_id", help="Associated bill ID (e.g. HB10)")
    p.add_argument("--date",     dest="hearing_date", help="Hearing date YYYY-MM-DD")
    p.add_argument("--model",    default="base",
                   choices=["tiny", "base", "small", "medium", "large"],
                   help="Whisper model size (default: base)")
    p.add_argument("--language", help="Force language code, e.g. 'en'")
    p.add_argument("--dry-run",  action="store_true", help="Parse but don't write to DB")
    args = p.parse_args(argv)

    result = ingest(
        args.video,
        db_path=args.db,
        title=args.title,
        bill_id=args.bill_id,
        hearing_date=args.hearing_date,
        model_name=args.model,
        language=args.language,
        dry_run=args.dry_run,
    )
    if not result["ok"]:
        print(f"[error] {result.get('error')}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
