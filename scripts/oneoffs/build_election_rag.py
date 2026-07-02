#!/usr/bin/env python3
"""
Build RAG chunks from Virginia election results + legislator data.

Sources (all already in repo):
  va_members_2026.json         — 142 current legislators from OpenStates
  election_results_2017/2019/2021/2023/2025.json — HOD/Senate race results
  election_results_summary.json — 12 statewide race summaries

Output:
  va_election_rag.jsonl        — ready for ingest.py

Usage:
    python build_election_rag.py
    python build_election_rag.py --out custom.jsonl
"""
import argparse
import json
from pathlib import Path

BASE = Path(__file__).parent


def load_json(name: str) -> dict | list:
    p = BASE / name
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


# ── Legislator profile chunks ─────────────────────────────────────────────────

def member_chunks(members: list[dict]) -> list[dict]:
    chunks = []
    for m in members:
        name    = m["name"]
        party   = m["party"]
        chamber_key = (m.get("chamber") or "").lower()
        chamber = (
            "Executive Branch" if "executive" in chamber_key
            else "House of Delegates" if "lower" in chamber_key
            else "Senate"
        )
        dist    = m["district"]
        title   = m["title"]
        url     = m.get("url", "")

        heading = (
            f"{name} — Virginia {title}"
            if "executive" in chamber_key
            else f"{name} — Virginia {chamber}, District {dist}"
        )
        lines = [
            heading,
            f"Party: {party}",
            f"Title: {title}",
        ]
        if "executive" not in chamber_key:
            lines.append(f"District: {dist}")
        if url:
            lines.append(f"Profile: {url}")

        chunk_id = f"member_{m['id'].split('/')[-1]}_2026"
        chunks.append({
            "chunk_id":   chunk_id,
            "text":       "\n".join(lines),
            "bill_id":    "",
            "session_id": "2026",
            "state":      "VA",
            "year":       "2026",
            "chunk_index": 0,
            "chunk_type": "legislator_profile",
            "metadata": {
                "source":   "openstates_people",
                "name":     name,
                "party":    party,
                "chamber":  chamber,
                "district": dist,
            },
        })
    return chunks


# ── HOD/Senate district race chunks ──────────────────────────────────────────

def district_chunks(year: int, chamber: str, races: dict) -> list[dict]:
    chunks = []
    for district, info in races.items():
        candidates = info.get("candidates", [])
        total = info.get("total", 0) or sum(c.get("votes", 0) for c in candidates)
        if not candidates:
            continue

        winner = max(candidates, key=lambda c: c.get("votes", 0))
        losers = [c for c in candidates if c != winner]

        lines = [
            f"{year} Virginia {chamber} District {district} Election",
            f"Winner: {winner['name']} ({winner.get('party', '?')})"
            + (f" — {winner['votes']:,} votes ({winner.get('pct', 0):.1f}%)" if winner.get("votes") else ""),
        ]
        if total:
            lines.append(f"Total votes cast: {total:,}")
        if losers:
            for l in losers:
                lines.append(
                    f"  {l['name']} ({l.get('party', '?')}): {l.get('votes', 0):,} votes ({l.get('pct', 0):.1f}%)"
                )
        margin = None
        if len(candidates) >= 2 and total:
            sorted_c = sorted(candidates, key=lambda c: c.get("votes", 0), reverse=True)
            v1, v2 = sorted_c[0].get("votes", 0), sorted_c[1].get("votes", 0)
            margin = round((v1 - v2) / total * 100, 1)
            lines.append(f"Margin: {margin}%")
        if len(candidates) == 1:
            lines.append("Unopposed race.")

        chamber_short = "HOD" if "Delegate" in chamber or "House" in chamber else "SD"
        chunk_id = f"election_{year}_{chamber_short}_dist{district}"
        chunks.append({
            "chunk_id":    chunk_id,
            "text":        "\n".join(lines),
            "bill_id":     "",
            "session_id":  str(year),
            "state":       "VA",
            "year":        str(year),
            "chunk_index": 0,
            "chunk_type":  "election_race",
            "metadata": {
                "source":       "election_results_json",
                "year":         str(year),
                "chamber":      chamber_short,
                "district":     str(district),
                "winner":       winner["name"],
                "winner_party": winner.get("party", ""),
                "margin_pct":   str(margin or ""),
            },
        })
    return chunks


# ── Statewide race summary chunks ─────────────────────────────────────────────

def statewide_chunks(summary_data: dict) -> list[dict]:
    chunks = []
    for item in (summary_data.get("rag_chunks") or []):
        cid = f"statewide_{item.get('chunk_id', item.get('section', '').replace(' ', '_'))}"
        content = item.get("content", "")
        if not content:
            continue
        chunks.append({
            "chunk_id":    cid,
            "text":        content,
            "bill_id":     "",
            "session_id":  "",
            "state":       "VA",
            "year":        "",
            "chunk_index": 0,
            "chunk_type":  "statewide_race",
            "metadata": {
                "source":  "election_results_summary",
                "section": item.get("section", ""),
            },
        })
    return chunks


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="va_election_rag.jsonl")
    args = parser.parse_args()

    all_chunks: list[dict] = []

    # 1. Legislator profiles
    members = load_json("va_members_2026.json")
    if isinstance(members, list):
        mc = member_chunks(members)
        all_chunks.extend(mc)
        print(f"[members] {len(mc)} legislator profile chunks")

    # 2. HOD races (odd years: 2017, 2019, 2021, 2023, 2025)
    for year in [2017, 2019, 2021, 2023, 2025]:
        data = load_json(f"election_results_{year}.json")
        if not data:
            continue
        hod = data.get("hod", {})
        if hod:
            hc = district_chunks(year, "House of Delegates", hod)
            all_chunks.extend(hc)
            print(f"[{year} HOD] {len(hc)} district chunks")

    # 3. Senate races (2019, 2023)
    for year in [2019, 2023]:
        data = load_json(f"election_results_{year}.json")
        if not data:
            continue
        senate = data.get("senate", {})
        if senate:
            sc = district_chunks(year, "State Senate", senate)
            all_chunks.extend(sc)
            print(f"[{year} Senate] {len(sc)} district chunks")

    # 4. Statewide race summaries
    summary = load_json("election_results_summary.json")
    if summary:
        sw = statewide_chunks(summary)
        all_chunks.extend(sw)
        print(f"[statewide] {len(sw)} summary chunks")

    # Write JSONL
    out = Path(args.out)
    with out.open("w", encoding="utf-8") as f:
        for chunk in all_chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    print(f"\nTotal: {len(all_chunks)} chunks written to {out}")
    types = {}
    for c in all_chunks:
        t = c["chunk_type"]
        types[t] = types.get(t, 0) + 1
    for t, n in sorted(types.items()):
        print(f"  {t}: {n}")


if __name__ == "__main__":
    main()
