#!/usr/bin/env python3
"""
Build Virginia finance resource + people RAG chunks from polls.db for ingest into ChromaDB.

Output: va_finance_rag.jsonl  (ingest with: python ingest.py --file va_finance_rag.jsonl)
"""
import argparse
import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from build_va_state_finance import classify_sector

DB_PATH = Path(__file__).parent / "polls.db"
LEG_DB_PATH = Path(__file__).parent / "legislative_intelligence.db"


def _money(value: float | int | None) -> str:
    return f"${float(value or 0):,.0f}"


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")
    return cleaned or "unknown"


def _id_hash(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def _office_label(chamber: str, district: str) -> str:
    chamber = chamber or "State Legislator"
    district = district or ""
    if district:
        return f"{chamber} District {district}"
    return chamber


def build_finance_info_chunks(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, title, url, description, category, fetched_at FROM va_finance_info ORDER BY id"
    ).fetchall()
    chunks = []
    for id_, title, url, description, category, fetched_at in rows:
        text_lines = [f"Finance Resource: {title}"]
        if description:
            text_lines.append(description)
        if url:
            text_lines.append(f"URL: {url}")
        text = "\n".join(text_lines)

        chunks.append({
            "chunk_id": f"va_finance_info_{id_}",
            "text": text,
            "source_type": "finance_resource",
            "category": category or "",
            "url": url or "",
            "title": title or "",
            "fetched_at": fetched_at or "",
            "metadata": {
                "source_type": "finance_resource",
                "category": category or "",
                "url": url or "",
                "title": title or "",
                "fetched_at": fetched_at or "",
            },
        })
    return chunks


def build_finance_people_chunks(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, person_name, office, district, party, role, incumbent, committee_name, finance_url, source_url, data_confidence, fetched_at FROM va_finance_people ORDER BY office, district, person_name"
    ).fetchall()
    chunks = []
    for (
        id_, person_name, office, district, party, role, incumbent,
        committee_name, finance_url, source_url, data_confidence, fetched_at
    ) in rows:
        lines = [f"Person: {person_name}"]
        if office:
            lines.append(f"Office: {office}")
        if district:
            lines.append(f"District: {district}")
        if party:
            lines.append(f"Party: {party}")
        if role:
            lines.append(f"Role: {role}")
        lines.append("Incumbent: " + ("Yes" if incumbent else "No"))
        if committee_name:
            lines.append(f"Committee: {committee_name}")
        if finance_url:
            lines.append(f"Finance URL: {finance_url}")
        if source_url:
            lines.append(f"Source URL: {source_url}")
        if data_confidence:
            lines.append(f"Data confidence: {data_confidence}")
        text = "\n".join(lines)

        chunks.append({
            "chunk_id": f"va_finance_person_{id_}",
            "text": text,
            "source_type": "finance_person",
            "person_name": person_name or "",
            "office": office or "",
            "district": district or "",
            "party": party or "",
            "role": role or "",
            "incumbent": bool(incumbent),
            "committee_name": committee_name or "",
            "finance_url": finance_url or "",
            "source_url": source_url or "",
            "data_confidence": data_confidence or "",
            "fetched_at": fetched_at or "",
            "metadata": {
                "source_type": "finance_person",
                "person_name": person_name or "",
                "office": office or "",
                "district": district or "",
                "party": party or "",
                "role": role or "",
                "incumbent": bool(incumbent),
                "committee_name": committee_name or "",
                "finance_url": finance_url or "",
                "source_url": source_url or "",
                "data_confidence": data_confidence or "",
                "fetched_at": fetched_at or "",
            },
        })
    return chunks


def _load_top_donors_by_sector(
    conn: sqlite3.Connection,
    top_n: int = 5,
    since_cycle: int | None = None,
) -> dict[tuple[str, str, str], list[dict]]:
    cycle_filter = "AND CAST(cycle AS INTEGER) >= ?" if since_cycle else ""
    params: list = []
    if since_cycle:
        params.append(since_cycle)
    params.append(top_n)
    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT
                legislator_id,
                cycle,
                sector,
                contributor_name,
                employer,
                occupation,
                amount,
                transaction_date,
                city,
                state,
                donor_tier,
                ROW_NUMBER() OVER (
                    PARTITION BY legislator_id, cycle, sector
                    ORDER BY amount DESC, transaction_date DESC
                ) AS rn
            FROM va_sbe_contributions
            WHERE legislator_id IS NOT NULL
              AND sector IS NOT NULL
              AND amount > 0
              {cycle_filter}
        )
        SELECT legislator_id, cycle, sector, contributor_name, employer, occupation,
               amount, transaction_date, city, state, donor_tier
        FROM ranked
        WHERE rn <= ?
        ORDER BY legislator_id, cycle, sector, amount DESC
        """,
        params,
    ).fetchall()
    out: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        key = (row["legislator_id"], str(row["cycle"] or ""), row["sector"] or "")
        out[key].append({
            "name": row["contributor_name"] or "Unknown donor",
            "amount": float(row["amount"] or 0),
            "date": row["transaction_date"] or "",
            "employer": row["employer"] or "",
            "occupation": row["occupation"] or "",
            "city": row["city"] or "",
            "state": row["state"] or "",
            "tier": row["donor_tier"] or "",
        })
    return out


def _load_mega_donors(
    conn: sqlite3.Connection,
    threshold: int = 10_000,
    top_n: int = 20,
    since_cycle: int | None = None,
) -> dict[tuple[str, str], list[dict]]:
    cycle_filter = "AND CAST(cycle AS INTEGER) >= ?" if since_cycle else ""
    params: list = [threshold]
    if since_cycle:
        params.append(since_cycle)
    params.append(top_n)
    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT
                legislator_id,
                cycle,
                contributor_name,
                sector,
                employer,
                occupation,
                amount,
                transaction_date,
                city,
                state,
                donor_tier,
                ROW_NUMBER() OVER (
                    PARTITION BY legislator_id, cycle
                    ORDER BY amount DESC, transaction_date DESC
                ) AS rn
            FROM va_sbe_contributions
            WHERE legislator_id IS NOT NULL
              AND amount >= ?
              {cycle_filter}
        )
        SELECT legislator_id, cycle, contributor_name, sector, employer, occupation,
               amount, transaction_date, city, state, donor_tier
        FROM ranked
        WHERE rn <= ?
        ORDER BY legislator_id, cycle, amount DESC
        """,
        params,
    ).fetchall()
    out: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        out[(row["legislator_id"], str(row["cycle"] or ""))].append({
            "name": row["contributor_name"] or "Unknown donor",
            "sector": row["sector"] or "Individual/Other",
            "amount": float(row["amount"] or 0),
            "date": row["transaction_date"] or "",
            "employer": row["employer"] or "",
            "occupation": row["occupation"] or "",
            "city": row["city"] or "",
            "state": row["state"] or "",
            "tier": row["donor_tier"] or "",
        })
    return out


def build_sbe_state_finance_chunks(conn: sqlite3.Connection, since_cycle: int | None = None) -> list[dict]:
    """Build searchable Virginia SBE campaign-finance chunks from enriched state records."""
    conn.row_factory = sqlite3.Row
    top_donors = _load_top_donors_by_sector(conn, since_cycle=since_cycle)
    mega_donors = _load_mega_donors(conn, since_cycle=since_cycle)
    cycle_filter = "AND CAST(dst.cycle AS INTEGER) >= ?" if since_cycle else ""
    params = [since_cycle] if since_cycle else []

    rows = conn.execute(
        f"""
        SELECT
            dst.legislator_id,
            l.name,
            l.party,
            l.chamber,
            l.district,
            dst.cycle,
            dst.sector,
            dst.total_amount,
            dst.donor_count
        FROM donor_sector_totals dst
        LEFT JOIN legislators l ON l.lis_id = dst.legislator_id
        WHERE dst.legislator_id IS NOT NULL
          AND dst.cycle IS NOT NULL
          {cycle_filter}
        ORDER BY dst.legislator_id, dst.cycle DESC, dst.total_amount DESC
        """,
        params,
    ).fetchall()

    grouped: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[(row["legislator_id"], str(row["cycle"] or ""))].append(row)

    chunks: list[dict] = []
    for (lis_id, cycle), sectors in grouped.items():
        first = sectors[0]
        name = first["name"] or first["legislator_id"]
        party = first["party"] or ""
        chamber = first["chamber"] or ""
        district = first["district"] or ""
        office = _office_label(chamber, district)
        total = sum(float(r["total_amount"] or 0) for r in sectors)
        donor_count = sum(int(r["donor_count"] or 0) for r in sectors)
        top = sectors[:8]

        lines = [
            f"Virginia SBE campaign finance summary for {name}",
            f"Candidate: {name}",
            f"LIS ID: {lis_id}",
            f"Office: {office}",
            f"Party: {party}",
            f"Cycle: {cycle}",
            f"Total itemized contributions by classified sector: {_money(total)} from {donor_count:,} contribution records.",
            "Data source: Virginia SBE campaign finance filings. Virginia has no state contribution limit.",
            "Top donor sectors:",
        ]
        for row in top:
            pct = (float(row["total_amount"] or 0) / total * 100) if total else 0
            lines.append(
                f"- {row['sector']}: {_money(row['total_amount'])} "
                f"from {int(row['donor_count'] or 0):,} records ({pct:.1f}% of classified total)"
            )

        md = mega_donors.get((lis_id, cycle), [])
        if md:
            lines.append("Largest individual/institutional contribution records in this cycle:")
            for donor in md[:10]:
                donor_bits = [donor["name"], donor["sector"], _money(donor["amount"])]
                if donor["date"]:
                    donor_bits.append(donor["date"])
                if donor["employer"]:
                    donor_bits.append(f"employer: {donor['employer']}")
                lines.append("- " + " | ".join(donor_bits))

        metadata = {
            "source_type": "state_campaign_finance",
            "source": "virginia_sbe",
            "source_label": "Virginia SBE campaign finance filings",
            "chunk_type": "state_finance_summary",
            "person_name": name,
            "lis_id": lis_id,
            "cycle": cycle,
            "office": office,
            "party": party,
            "chamber": chamber,
            "district": district,
            "total_amount": round(total, 2),
            "donor_count": donor_count,
        }
        chunks.append({
            "chunk_id": f"va_sbe_summary_{_safe_id(lis_id)}_{cycle}",
            "text": "\n".join(lines),
            **metadata,
            "metadata": metadata,
        })

        for row in sectors:
            sector = row["sector"] or "Individual/Other"
            sector_total = float(row["total_amount"] or 0)
            sector_count = int(row["donor_count"] or 0)
            sector_lines = [
                f"Virginia SBE sector finance for {name}",
                f"Candidate: {name}",
                f"LIS ID: {lis_id}",
                f"Office: {office}",
                f"Party: {party}",
                f"Cycle: {cycle}",
                f"Sector: {sector}",
                f"Sector total: {_money(sector_total)} from {sector_count:,} contribution records.",
                "Data source: Virginia SBE campaign finance filings. Virginia has no state contribution limit.",
            ]
            donors = top_donors.get((lis_id, cycle, sector), [])
            if donors:
                sector_lines.append("Largest contribution records in this sector:")
                for donor in donors:
                    donor_bits = [donor["name"], _money(donor["amount"])]
                    if donor["date"]:
                        donor_bits.append(donor["date"])
                    if donor["employer"]:
                        donor_bits.append(f"employer: {donor['employer']}")
                    if donor["occupation"]:
                        donor_bits.append(f"occupation: {donor['occupation']}")
                    sector_lines.append("- " + " | ".join(donor_bits))

            sector_meta = {
                **metadata,
                "chunk_type": "state_finance_sector",
                "sector": sector,
                "sector_total": round(sector_total, 2),
                "sector_donor_count": sector_count,
            }
            chunks.append({
                "chunk_id": f"va_sbe_sector_{_safe_id(lis_id)}_{cycle}_{_safe_id(sector)}",
                "text": "\n".join(sector_lines),
                **sector_meta,
                "metadata": sector_meta,
            })

    return chunks


def _push_top(bucket: dict, key: tuple, donor: dict, limit: int = 5) -> None:
    items = bucket.setdefault(key, [])
    items.append(donor)
    items.sort(key=lambda d: d["amount"], reverse=True)
    if len(items) > limit:
        del items[limit:]


def build_raw_sbe_candidate_chunks(conn: sqlite3.Connection, since_cycle: int | None = None) -> list[dict]:
    """Fallback chunks from raw SBE Schedule A rows, independent of LIS matching."""
    conn.row_factory = sqlite3.Row
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='va_cf_schedule_a'"
    ).fetchone()
    if not table:
        return []

    candidate_totals: dict[tuple[str, str], dict] = {}
    sector_totals: dict[tuple[str, str, str], dict] = {}
    top_sector_donors: dict[tuple[str, str, str], list[dict]] = {}
    top_candidate_donors: dict[tuple[str, str], list[dict]] = {}

    cycle_filter = "AND CAST(election_cycle AS INTEGER) >= ?" if since_cycle else ""
    params = [since_cycle] if since_cycle else []
    rows = conn.execute(
        f"""
        SELECT candidate_name, election_cycle, first_name, last_or_company,
               employer, occupation, is_individual, transaction_date, amount,
               city, state_code
        FROM va_cf_schedule_a
        WHERE candidate_name IS NOT NULL
          AND candidate_name != ''
          AND amount > 0
          {cycle_filter}
        """,
        params,
    )
    for row in rows:
        candidate = " ".join((row["candidate_name"] or "").split())
        cycle = str(row["election_cycle"] or "")
        if not candidate or not cycle:
            continue
        amount = float(row["amount"] or 0)
        sector = classify_sector(
            row["occupation"] or "",
            row["employer"] or "",
            row["last_or_company"] or "" if not row["is_individual"] else "",
        )
        ckey = (candidate, cycle)
        skey = (candidate, cycle, sector)
        candidate_totals.setdefault(ckey, {"total": 0.0, "count": 0})
        candidate_totals[ckey]["total"] += amount
        candidate_totals[ckey]["count"] += 1

        sector_totals.setdefault(skey, {"total": 0.0, "count": 0})
        sector_totals[skey]["total"] += amount
        sector_totals[skey]["count"] += 1

        first = (row["first_name"] or "").strip()
        last = (row["last_or_company"] or "").strip()
        donor_name = f"{first} {last}".strip() if row["is_individual"] else last
        donor = {
            "name": donor_name or "Unknown donor",
            "amount": amount,
            "date": row["transaction_date"] or "",
            "employer": row["employer"] or "",
            "occupation": row["occupation"] or "",
            "city": row["city"] or "",
            "state": row["state_code"] or "",
            "sector": sector,
        }
        _push_top(top_sector_donors, skey, donor)
        _push_top(top_candidate_donors, ckey, donor, limit=10)

    by_candidate: dict[tuple[str, str], list[tuple[str, dict]]] = defaultdict(list)
    for (candidate, cycle, sector), stats in sector_totals.items():
        by_candidate[(candidate, cycle)].append((sector, stats))

    chunks: list[dict] = []
    for (candidate, cycle), stats in sorted(candidate_totals.items()):
        sectors = sorted(
            by_candidate.get((candidate, cycle), []),
            key=lambda item: item[1]["total"],
            reverse=True,
        )
        total = stats["total"]
        count = stats["count"]
        lines = [
            f"Virginia SBE raw campaign finance summary for {candidate}",
            f"Candidate: {candidate}",
            f"Cycle: {cycle}",
            f"Total itemized Schedule A contributions: {_money(total)} from {count:,} contribution records.",
            "Data source: Virginia SBE Schedule A campaign finance filings.",
            "This chunk is keyed by SBE candidate name and does not require LIS legislator matching.",
            "Virginia has no state contribution limit.",
            "Top donor sectors:",
        ]
        for sector, sector_stats in sectors[:8]:
            pct = (sector_stats["total"] / total * 100) if total else 0
            lines.append(
                f"- {sector}: {_money(sector_stats['total'])} "
                f"from {sector_stats['count']:,} records ({pct:.1f}% of total)"
            )
        donors = top_candidate_donors.get((candidate, cycle), [])
        if donors:
            lines.append("Largest contribution records:")
            for donor in donors:
                bits = [donor["name"], donor["sector"], _money(donor["amount"])]
                if donor["date"]:
                    bits.append(donor["date"])
                if donor["employer"]:
                    bits.append(f"employer: {donor['employer']}")
                lines.append("- " + " | ".join(bits))

        meta = {
            "source_type": "state_campaign_finance",
            "source": "virginia_sbe",
            "source_label": "Virginia SBE Schedule A campaign finance filings",
            "chunk_type": "state_finance_candidate_raw",
            "person_name": candidate,
            "candidate_name": candidate,
            "cycle": cycle,
            "total_amount": round(total, 2),
            "donor_count": count,
        }
        chunks.append({
            "chunk_id": f"va_sbe_raw_summary_{_safe_id(candidate)}_{cycle}_{_id_hash(candidate, cycle)}",
            "text": "\n".join(lines),
            **meta,
            "metadata": meta,
        })

        for sector, sector_stats in sectors:
            sector_total = sector_stats["total"]
            sector_count = sector_stats["count"]
            sector_lines = [
                f"Virginia SBE raw sector finance for {candidate}",
                f"Candidate: {candidate}",
                f"Cycle: {cycle}",
                f"Sector: {sector}",
                f"Sector total: {_money(sector_total)} from {sector_count:,} contribution records.",
                "Data source: Virginia SBE Schedule A campaign finance filings.",
                "This chunk is keyed by SBE candidate name and does not require LIS legislator matching.",
            ]
            for donor in top_sector_donors.get((candidate, cycle, sector), []):
                bits = [donor["name"], _money(donor["amount"])]
                if donor["date"]:
                    bits.append(donor["date"])
                if donor["employer"]:
                    bits.append(f"employer: {donor['employer']}")
                if donor["occupation"]:
                    bits.append(f"occupation: {donor['occupation']}")
                sector_lines.append("- " + " | ".join(bits))

            sector_meta = {
                **meta,
                "chunk_type": "state_finance_sector_raw",
                "sector": sector,
                "sector_total": round(sector_total, 2),
                "sector_donor_count": sector_count,
            }
            chunks.append({
                "chunk_id": f"va_sbe_raw_sector_{_safe_id(candidate)}_{cycle}_{_safe_id(sector)}_{_id_hash(candidate, cycle, sector)}",
                "text": "\n".join(sector_lines),
                **sector_meta,
                "metadata": sector_meta,
            })

    return chunks


def main() -> None:
    parser = argparse.ArgumentParser(description="Build RAG chunks from Virginia finance data in polls.db")
    parser.add_argument("--out", default="va_finance_rag.jsonl", help="Output JSONL file")
    parser.add_argument(
        "--since",
        type=int,
        default=2022,
        help="Only include SBE contribution cycles >= this year (default: 2022; use 2000 for full history)",
    )
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found. Run ingest_va_finance.py first.")
        return

    conn = sqlite3.connect(DB_PATH)
    finance_info_chunks = build_finance_info_chunks(conn)
    finance_people_chunks = build_finance_people_chunks(conn)
    raw_sbe_chunks = build_raw_sbe_candidate_chunks(conn, since_cycle=args.since)
    conn.close()

    sbe_chunks = []
    if LEG_DB_PATH.exists():
        leg_conn = sqlite3.connect(LEG_DB_PATH)
        leg_conn.row_factory = sqlite3.Row
        sbe_chunks = build_sbe_state_finance_chunks(leg_conn, since_cycle=args.since)
        leg_conn.close()
    else:
        print(f"WARNING: {LEG_DB_PATH} not found; skipping SBE contribution chunks.")

    chunks = finance_info_chunks + finance_people_chunks + sbe_chunks + raw_sbe_chunks
    out_path = Path(args.out)
    with out_path.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    print(f"Wrote {len(chunks)} finance chunks to {out_path}")
    if chunks:
        print("Sample chunk:")
        print(chunks[0]["text"][:500])


if __name__ == "__main__":
    main()
