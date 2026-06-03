"""
scrape_va_2026.py
=================

Scrape every bill from the 2026 Virginia General Assembly Regular Session
and emit RAG-ready JSONL chunks, roughly 250 tokens each.

Data sources, in priority order:
  1. LegiScan API getDataset      -> all bill metadata in 2 calls
  2. LegiScan API getBillText     -> bill body (base64 PDF/HTML)
  3. LIS state_link PDF           -> direct download from lis.virginia.gov
                                     when LegiScan has no text yet
  4. Header-only chunk            -> last resort when no full text is available

Usage:
    set LEGISCAN_API_KEY=your-key-here
    pip install requests pypdf

    python scrape_va_2026.py --limit 5 --raw-dir ./raw --out test.jsonl
    python scrape_va_2026.py --out va_2026_chunks.jsonl
    python scrape_va_2026.py --out va_2026_chunks.jsonl --resume
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Iterable

import requests
from dotenv import load_dotenv
load_dotenv()

try:
    from pypdf import PdfReader

    HAVE_PYPDF = True
except ImportError:
    HAVE_PYPDF = False


LEGISCAN_BASE = "https://api.legiscan.com/"
USER_AGENT = "VoteIQ/0.1 (+https://voteiq.io) python-requests"

STATUS_NAMES = {
    0: "Pre-filed",
    1: "Introduced",
    2: "Engrossed",
    3: "Enrolled",
    4: "Passed",
    5: "Vetoed",
    6: "Failed",
}

TEXT_TYPE_NAMES = {
    1: "Introduced",
    2: "Committee Substitute",
    3: "Amended",
    4: "Engrossed",
    5: "Enrolled",
    6: "Chaptered",
    7: "Fiscal Note",
    8: "Analysis",
    9: "Draft",
    10: "Conference Substitute",
    11: "Prefiled",
    12: "Veto Message",
    13: "Veto Response",
    14: "Substitute",
}

TEXT_TYPE_PRIORITY = {
    6: 0,
    5: 1,
    4: 2,
    10: 3,
    14: 4,
    2: 5,
    3: 6,
    1: 7,
    11: 8,
}

TARGET_TOKENS = 250
TOKEN_CHAR_RATIO = 4
CHUNK_TARGET_CHARS = TARGET_TOKENS * TOKEN_CHAR_RATIO
CHUNK_OVERLAP_CHARS = 200
CHUNK_MIN_CHARS = 200

REQUEST_DELAY_S = 0.25
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 4

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("va-2026")


class LegiScanError(RuntimeError):
    """LegiScan API returned status=ERROR or HTTP failure."""


class LegiScan:
    """Minimal LegiScan Pull API client."""

    def __init__(self, api_key: str):
        self.key = api_key
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self.queries_used = 0

    def _get(self, op: str, **params) -> dict:
        params = {
            "key": self.key,
            "op": op,
            **{k: v for k, v in params.items() if v is not None},
        }
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = self.s.get(LEGISCAN_BASE, params=params, timeout=60)
            except requests.RequestException as e:
                if attempt == MAX_RETRIES:
                    raise
                log.warning("network error %s (attempt %d/%d)", e, attempt, MAX_RETRIES)
                time.sleep(2**attempt)
                continue

            if r.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
                wait = 2**attempt
                log.warning("HTTP %d on %s - retry in %ds", r.status_code, op, wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            time.sleep(REQUEST_DELAY_S)
            self.queries_used += 1
            data = r.json()
            if data.get("status") != "OK":
                msg = data.get("alert", {}).get("message", "unknown")
                raise LegiScanError(f"{op}: {msg}")
            return data
        raise RuntimeError("unreachable")

    def get_dataset_list(self, state: str | None = None, year: int | None = None) -> list[dict]:
        return self._get("getDatasetList", state=state, year=year)["datasetlist"]

    def get_dataset(self, session_id: int, access_key: str) -> dict:
        return self._get("getDataset", id=session_id, access_key=access_key)["dataset"]

    def get_bill_text(self, doc_id: int) -> dict:
        return self._get("getBillText", id=doc_id)["text"]


def extract_dataset_zip(dataset: dict, out_dir: Path) -> Path:
    """Decode the base64 ZIP and extract it. Returns extraction directory."""
    zip_bytes = base64.b64decode(dataset["zip"])
    target = out_dir / f"session_{dataset['session_id']}"
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        z.extractall(target)
    n = sum(1 for _ in target.rglob("*.json"))
    log.info("dataset extracted: %d JSON files in %s", n, target)
    return target


def iter_bill_records(dataset_dir: Path) -> Iterable[dict]:
    """Yield each bill record from the extracted LegiScan dataset."""
    bill_dirs = list(dataset_dir.rglob("bill"))
    if bill_dirs:
        for bd in bill_dirs:
            for p in sorted(bd.glob("*.json")):
                yield from _read_bill_file(p)
    else:
        for p in dataset_dir.rglob("*.json"):
            yield from _read_bill_file(p)


def _read_bill_file(p: Path) -> Iterable[dict]:
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("could not read %s: %s", p, e)
        return
    if isinstance(payload, dict) and isinstance(payload.get("bill"), dict):
        yield payload["bill"]


def pick_latest_text(bill: dict) -> dict | None:
    """Pick the most authoritative text version from bill['texts']."""
    texts = bill.get("texts") or []
    if not texts:
        return None
    return min(
        texts,
        key=lambda t: (
            TEXT_TYPE_PRIORITY.get(t.get("type_id"), 99),
            -_iso_to_int(t.get("date") or ""),
        ),
    )


def _iso_to_int(s: str) -> int:
    try:
        return int(s.replace("-", ""))
    except (ValueError, AttributeError):
        return 0


def fetch_bill_text(
    text_record: dict,
    legiscan: LegiScan | None,
    http: requests.Session,
) -> tuple[str, str]:
    """Try LegiScan first, then direct LIS PDF. Returns (text, source)."""
    doc_id = text_record.get("doc_id")
    state_link = text_record.get("state_link") or ""

    if doc_id and legiscan:
        try:
            payload = legiscan.get_bill_text(int(doc_id))
            text = decode_text_payload(payload)
            if text:
                return text, "legiscan"
        except (LegiScanError, requests.RequestException, ValueError) as e:
            log.debug("legiscan getBillText doc_id=%s failed: %s", doc_id, e)

    if state_link:
        try:
            text = fetch_state_link_text(state_link, http)
            if text:
                return text, "lis_state_link"
        except requests.RequestException as e:
            log.debug("state_link fetch failed for %s: %s", state_link, e)

    return "", "unavailable"


def decode_text_payload(payload: dict) -> str:
    """Decode getBillText response."""
    raw_b64 = payload.get("doc")
    if not raw_b64:
        return ""
    try:
        raw = base64.b64decode(raw_b64)
    except (ValueError, TypeError):
        return ""
    mime_id = payload.get("mime_id")
    if mime_id == 2:
        return _pdf_to_text(raw)
    if mime_id == 1:
        return _html_to_text(raw.decode("utf-8", errors="replace"))
    try:
        return clean_text(raw.decode("utf-8", errors="replace"))
    except Exception:
        return ""


def fetch_state_link_text(url: str, http: requests.Session) -> str:
    """Download a LIS-hosted document and extract text."""
    r = http.get(url, timeout=60, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    ctype = (r.headers.get("content-type") or "").lower()
    body = r.content
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        return _pdf_to_text(body)
    if "html" in ctype or url.lower().endswith((".html", ".htm")):
        return _html_to_text(body.decode("utf-8", errors="replace"))
    txt = _pdf_to_text(body)
    if txt:
        return txt
    return _html_to_text(body.decode("utf-8", errors="replace"))


def _pdf_to_text(raw: bytes) -> str:
    if not HAVE_PYPDF:
        log.warning("pypdf not installed; cannot extract PDF text")
        return ""
    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception as e:
        log.debug("PDF parse failed: %s", e)
        return ""
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            continue
    return clean_text("\n\n".join(pages))


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_NL_RE = re.compile(r"\n{3,}")


def _html_to_text(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    return clean_text(text)


def clean_text(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\r", "")
    text = _WS_RE.sub(" ", text)
    text = _NL_RE.sub("\n\n", text)
    return text.strip()


_SECTION_RE = re.compile(
    r"(?m)^(?:\s*§\s*[\d\.]|\s*SECTION\s+\d|\s*Section\s+\d|\s*\d+\.\s+That)"
)


def split_on_sections(text: str) -> list[str]:
    matches = list(_SECTION_RE.finditer(text))
    if len(matches) < 2:
        return [text]
    sections = []
    preamble = text[: matches[0].start()].strip()
    if preamble:
        sections.append(preamble)
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[m.start() : end].strip()
        if chunk:
            sections.append(chunk)
    return sections


def split_by_size(text: str, target: int, overlap: int) -> list[str]:
    """Sentence-aware size split with overlap."""
    if len(text) <= target:
        return [text] if text else []
    chunks = []
    i = 0
    while i < len(text):
        end = min(i + target, len(text))
        if end < len(text):
            window_start = max(end - 150, i + target // 2)
            boundary = -1
            for sep in (". ", "; ", "! ", "? ", "\n"):
                idx = text.rfind(sep, window_start, end)
                if idx > boundary:
                    boundary = idx + len(sep)
            if boundary > 0:
                end = boundary
        piece = text[i:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        i = max(end - overlap, i + 1)
    return chunks


def normalize_bill(bill: dict) -> dict:
    """Pull a small, clean view out of the LegiScan bill record."""
    sponsors = bill.get("sponsors") or []
    primary = next(
        (s for s in sponsors if s.get("sponsor_type_id") == 1),
        sponsors[0] if sponsors else None,
    )
    chief = primary.get("name") if primary else ""
    all_patrons = [s.get("name") for s in sponsors if s.get("name")]

    subjects = [
        s.get("subject_name")
        for s in (bill.get("subjects") or [])
        if s.get("subject_name")
    ]
    history = bill.get("history") or []
    last = history[-1] if history else {}

    return {
        "bill_id": bill.get("bill_number") or "UNKNOWN",
        "legiscan_bill_id": bill.get("bill_id"),
        "session_id": bill.get("session_id"),
        "session_name": (bill.get("session") or {}).get("session_name") or "",
        "session_title": (bill.get("session") or {}).get("session_title") or "",
        "special": bool((bill.get("session") or {}).get("special")),
        "title": bill.get("title") or "",
        "description": bill.get("description") or "",
        "status": STATUS_NAMES.get(bill.get("status"), str(bill.get("status") or "")),
        "status_date": bill.get("status_date") or "",
        "last_action": last.get("action") or "",
        "last_action_date": last.get("date") or "",
        "chief_patron": chief,
        "all_patrons": all_patrons,
        "subjects": subjects,
        "legiscan_url": bill.get("url") or "",
        "state_url": bill.get("state_link") or "",
    }


def build_chunks(bill_view: dict, body_text: str, text_meta: dict, text_source: str, year: int) -> list[dict]:
    """One header chunk plus N body chunks. Always emits at least one chunk."""
    bid = bill_view["bill_id"]
    sid = bill_view["session_id"]
    session_label = bill_view["session_name"] or bill_view["session_title"] or f"{year} VA Regular Session"

    parts = [f"{bid} ({session_label})"]
    if bill_view["title"]:
        parts.append(f"Title: {bill_view['title']}")
    if bill_view["chief_patron"]:
        parts.append(f"Chief patron: {bill_view['chief_patron']}")
    if bill_view["description"]:
        parts.append(f"Summary: {bill_view['description']}")
    if bill_view["status"]:
        parts.append(f"Status: {bill_view['status']}")
    if bill_view["last_action"]:
        date = f" ({bill_view['last_action_date']})" if bill_view["last_action_date"] else ""
        parts.append(f"Last action{date}: {bill_view['last_action']}")
    if bill_view["subjects"]:
        parts.append(f"Subjects: {', '.join(bill_view['subjects'])}")
    text_chunks = ["\n".join(parts)]

    if body_text:
        for section in split_on_sections(body_text):
            text_chunks.extend(split_by_size(section, CHUNK_TARGET_CHARS, CHUNK_OVERLAP_CHARS))

    text_chunks = [
        c for c in text_chunks if len(c) >= CHUNK_MIN_CHARS or len(text_chunks) == 1
    ]

    metadata = {
        "title": bill_view["title"],
        "session_name": bill_view["session_name"],
        "session_title": bill_view["session_title"],
        "special": bill_view["special"],
        "description": bill_view["description"],
        "status": bill_view["status"],
        "status_date": bill_view["status_date"],
        "last_action": bill_view["last_action"],
        "last_action_date": bill_view["last_action_date"],
        "chief_patron": bill_view["chief_patron"],
        "all_patrons": bill_view["all_patrons"],
        "subjects": bill_view["subjects"],
        "text_type": TEXT_TYPE_NAMES.get(text_meta.get("type_id"), ""),
        "text_date": text_meta.get("date") or "",
        "text_source": text_source,
        "legiscan_url": bill_view["legiscan_url"],
        "state_url": bill_view["state_url"],
    }

    out = []
    for idx, text in enumerate(text_chunks):
        out.append(
            {
                "chunk_id": f"{bid}-{year}-{idx:04d}",
                "bill_id": bid,
                "legiscan_bill_id": bill_view["legiscan_bill_id"],
                "session_id": sid,
                "state": "VA",
                "year": year,
                "chunk_index": idx,
                "chunk_type": "header" if idx == 0 else "body",
                "text": text,
                "char_count": len(text),
                "approx_tokens": len(text) // TOKEN_CHAR_RATIO,
                "metadata": metadata,
            }
        )
    return out


def find_va_dataset(legiscan: LegiScan, year: int) -> dict:
    """Locate the VA Regular Session dataset entry for the given year."""
    items = legiscan.get_dataset_list(state="VA", year=year)
    if not items:
        raise SystemExit(
            f"No VA {year} datasets returned by LegiScan. "
            "Check https://legiscan.com/VA/datasets"
        )
    regular = [d for d in items if not d.get("special")]
    pool = regular or items
    pool.sort(key=lambda d: d.get("dataset_date", ""), reverse=True)
    chosen = pool[0]
    log.info(
        "dataset selected: session_id=%s '%s' (%s, %d bytes)",
        chosen["session_id"],
        chosen.get("session_name") or "",
        chosen.get("dataset_date") or "",
        chosen.get("dataset_size") or 0,
    )
    return chosen


def existing_bill_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    seen = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                seen.add(json.loads(line)["bill_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    return seen


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--out", default=None, help="Output JSONL (default: va_{year}_chunks.jsonl)")
    p.add_argument("--limit", type=int, default=None, help="Cap bills processed for testing")
    p.add_argument("--resume", action="store_true", help="Skip bills already in --out")
    p.add_argument("--raw-dir", default="./raw", help="Where to extract the dataset ZIP")
    p.add_argument(
        "--dataset-dir",
        default=None,
        help="Use an already-extracted LegiScan dataset directory instead of downloading one",
    )
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--no-text", action="store_true", help="Skip per-bill text fetch")
    args = p.parse_args()

    if args.out is None:
        args.out = f"va_{args.year}_chunks.jsonl"

    api_key = os.environ.get("LEGISCAN_API_KEY")
    using_local_dataset = bool(args.dataset_dir)
    if not api_key and not using_local_dataset:
        print(
            "ERROR: set LEGISCAN_API_KEY (register at https://legiscan.com/legiscan)",
            file=sys.stderr,
        )
        return 2

    if not HAVE_PYPDF and not args.no_text:
        log.warning("pypdf not installed; bill PDFs will not be extracted. Run: pip install pypdf")

    legiscan = LegiScan(api_key) if api_key else None
    http = requests.Session()
    http.headers["User-Agent"] = USER_AGENT

    out_path = Path(args.out)
    raw_dir = Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    if args.dataset_dir:
        dataset_dir = Path(args.dataset_dir)
        if not dataset_dir.exists():
            print(f"ERROR: --dataset-dir does not exist: {dataset_dir}", file=sys.stderr)
            return 2
        log.info("using local dataset directory: %s", dataset_dir)
    else:
        assert legiscan is not None
        dataset_meta = find_va_dataset(legiscan, args.year)
        dataset = legiscan.get_dataset(dataset_meta["session_id"], dataset_meta["access_key"])
        dataset_dir = extract_dataset_zip(dataset, raw_dir)

    skip = existing_bill_ids(out_path) if args.resume else set()
    if skip:
        log.info("resume: skipping %d already-scraped bills", len(skip))

    mode = "a" if args.resume and out_path.exists() else "w"
    written = bills_done = bills_unavail = bills_failed = 0
    sources = {"legiscan": 0, "lis_state_link": 0, "unavailable": 0}

    with out_path.open(mode, encoding="utf-8") as out_f:
        bills = list(iter_bill_records(dataset_dir))
        if args.limit:
            bills = bills[: args.limit]
        log.info("processing %d bills", len(bills))

        for i, bill in enumerate(bills, 1):
            view = normalize_bill(bill)
            if view["bill_id"] in skip:
                continue

            text_record = pick_latest_text(bill) or {}
            body_text = ""
            text_source = "unavailable"

            if not args.no_text and text_record:
                try:
                    body_text, text_source = fetch_bill_text(text_record, legiscan, http)
                except Exception as e:
                    log.error("text fetch crashed for %s: %s", view["bill_id"], e)

            sources[text_source] = sources.get(text_source, 0) + 1
            if text_source == "unavailable":
                bills_unavail += 1

            try:
                rows = build_chunks(view, body_text, text_record, text_source, args.year)
            except Exception as e:
                log.error("chunking failed for %s: %s", view["bill_id"], e)
                bills_failed += 1
                continue

            for row in rows:
                out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1
            bills_done += 1

            if i % 50 == 0:
                log.info(
                    "progress: %d/%d bills, %d chunks, %d API queries",
                    i,
                    len(bills),
                    written,
                    legiscan.queries_used if legiscan else 0,
                )

    log.info(
        "done. bills=%d (no-text=%d, failed=%d) chunks=%d queries=%d",
        bills_done,
        bills_unavail,
        bills_failed,
        written,
        legiscan.queries_used if legiscan else 0,
    )
    log.info(
        "text sources: legiscan=%d lis_state_link=%d unavailable=%d",
        sources["legiscan"],
        sources["lis_state_link"],
        sources["unavailable"],
    )
    log.info("output: %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
