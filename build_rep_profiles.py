#!/usr/bin/env python3
"""
Build representative profile RAG chunks from openstates_va.db.

Each chunk is a narrative profile of one legislator per session:
  - Bills they sponsored (passed vs. failed) with subjects
  - Their voting record (yes rate, notable no votes)
  - Pattern of priorities across sessions

Output: va_rep_profiles.jsonl  (ready for ingest into ChromaDB)

Usage:
    python build_rep_profiles.py
    python build_rep_profiles.py --session 2026
    python build_rep_profiles.py --out custom.jsonl
"""
import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

DB_PATH = Path(__file__).parent / "openstates_va.db"

# Keyword buckets for subject tagging from bill titles
TOPICS = {
    "criminal justice": ["criminal", "crime", "felony", "misdemeanor", "sentencing",
                         "prison", "jail", "parole", "probation", "police", "law enforcement",
                         "firearms", "gun", "weapon", "assault", "homicide", "trafficking"],
    "education":        ["education", "school", "teacher", "student", "curriculum",
                         "tuition", "university", "college", "literacy", "library",
                         "charter", "kindergarten", "higher education"],
    "healthcare":       ["health", "medical", "hospital", "medicaid", "medicare",
                         "mental health", "substance", "addiction", "opioid", "vaccine",
                         "insurance", "prescription", "nursing"],
    "housing":          ["housing", "rent", "landlord", "tenant", "affordable",
                         "zoning", "eviction", "mortgage", "homelessness", "homeless"],
    "environment":      ["environment", "climate", "energy", "solar", "carbon",
                         "pollution", "water", "air quality", "conservation", "recycling"],
    "transportation":   ["transportation", "highway", "transit", "road", "bridge",
                         "vehicle", "traffic", "rail", "bus", "toll"],
    "taxes & budget":   ["tax", "revenue", "budget", "appropriation", "fiscal",
                         "exemption", "deduction", "income tax", "property tax", "fee"],
    "elections":        ["election", "voting", "voter", "ballot", "redistrict",
                         "campaign finance", "lobbyist", "ethics", "campaign"],
    "social services":  ["welfare", "snap", "medicaid", "child care", "family",
                         "disability", "senior", "poverty", "assistance", "benefit"],
    "economic development": ["business", "economic", "commerce", "trade", "workforce",
                              "minimum wage", "labor", "employer", "corporation", "startup"],
}


def tag_topics(text: str) -> list[str]:
    text_lower = text.lower()
    return [topic for topic, keywords in TOPICS.items()
            if any(kw in text_lower for kw in keywords)]


def build_profiles(conn: sqlite3.Connection, sessions: list[str]) -> list[dict]:
    chunks = []

    for session in sessions:
        # All bills this session
        bill_rows = conn.execute(
            "SELECT bill_id, title, subjects, sponsors, result, openstates_url FROM bills WHERE session=?",
            (session,)
        ).fetchall()

        if not bill_rows:
            continue

        # Map: sponsor_name -> list of bills
        sponsor_bills: dict[str, list[dict]] = defaultdict(list)
        for bill_id, title, subjects, sponsors_str, result, url in bill_rows:
            topics = tag_topics((title or "") + " " + (subjects or ""))
            bill = {
                "bill_id": bill_id,
                "title":   (title or "").strip(),
                "result":  (result or "").strip(),
                "topics":  topics,
                "url":     url or "",
            }
            for name in (sponsors_str or "").split(","):
                name = name.strip()
                if name:
                    sponsor_bills[name].append(bill)

        # All votes this session: voter_name -> list of {bill_id, option, bill_result}
        vote_rows = conn.execute(
            "SELECT voter_name, option, bill_id, result, party, district, motion FROM votes WHERE session=?",
            (session,)
        ).fetchall()

        # Committee motions start with "Reported from" or "Subcommittee"
        def is_committee(motion: str) -> bool:
            m = (motion or "").lower()
            return m.startswith("reported from") or m.startswith("subcommittee")

        voter_data: dict[str, dict] = defaultdict(lambda: {
            "votes": [], "committee_votes": [], "party": "", "district": ""
        })
        for voter_name, option, bill_id, vote_result, party, district, motion in vote_rows:
            if not voter_name:
                continue
            d = voter_data[voter_name]
            entry = {"bill_id": bill_id, "option": option, "bill_result": vote_result, "motion": motion or ""}
            if is_committee(motion):
                d["committee_votes"].append(entry)
            else:
                d["votes"].append(entry)
            if party:
                d["party"] = party
            if district:
                d["district"] = district

        # All known names = sponsors + voters
        all_names = set(sponsor_bills.keys()) | set(voter_data.keys())

        for name in sorted(all_names):
            bills      = sponsor_bills.get(name, [])
            vdata           = voter_data.get(name, {"votes": [], "committee_votes": [], "party": "", "district": ""})
            party           = vdata["party"]
            district        = vdata["district"]
            floor_votes     = vdata["votes"]
            committee_votes = vdata["committee_votes"]

            passed  = [b for b in bills if b["result"] == "pass"]
            failed  = [b for b in bills if b["result"] == "fail"]
            other   = [b for b in bills if b["result"] not in ("pass", "fail")]

            # Floor vote stats
            floor_yes = [v for v in floor_votes if v["option"] == "yes"]
            floor_no  = [v for v in floor_votes if v["option"] == "no"]
            total_v   = len(floor_yes) + len(floor_no)
            yes_rate  = round(len(floor_yes) / total_v * 100, 1) if total_v else None

            # Committee vote stats
            comm_yes = [v for v in committee_votes if v["option"] == "yes"]
            comm_no  = [v for v in committee_votes if v["option"] == "no"]

            # Topic breakdown of sponsored bills
            topic_counter: Counter = Counter()
            for b in bills:
                for t in b["topics"]:
                    topic_counter[t] += 1

            # Floor: voted NO on bills that ultimately passed (went against majority)
            notable_no = [v for v in floor_no if v["bill_result"] == "pass"][:5]

            # Committee: voted NO (killed or blocked in committee)
            comm_killed = [v for v in comm_no if v["bill_result"] == "fail"][:5]

            # Hypocrisy signal: voted NO in committee but YES on floor (or vice versa)
            floor_yes_ids = {v["bill_id"] for v in floor_yes}
            comm_no_ids   = {v["bill_id"] for v in comm_no}
            flip_bills    = floor_yes_ids & comm_no_ids  # blocked in committee, yes on floor

            # Fetch titles for bills we want to display
            lookup_ids = list({v["bill_id"] for v in notable_no + comm_killed} | flip_bills)
            bill_title_map = {}
            if lookup_ids:
                rows = conn.execute(
                    f"SELECT bill_id, title FROM bills WHERE session=? AND bill_id IN ({','.join('?'*len(lookup_ids))})",
                    [session] + lookup_ids
                ).fetchall()
                bill_title_map = {r[0]: r[1] for r in rows}

            # Build narrative
            chamber_str = "Senate" if any(
                b["bill_id"].startswith("S") for b in bills
            ) or "senator" in name.lower() else "House of Delegates"
            party_str = f" ({party})" if party else ""
            dist_str  = f", District {district}" if district else ""

            lines = [
                f"Representative Profile: {name}{party_str} — Virginia {chamber_str}{dist_str} ({session} session)",
            ]

            if party:
                lines.append(f"Party: {party}")

            # Sponsored bills summary
            if bills:
                lines.append(f"\nSponsored {len(bills)} bill(s) in {session}:")
                lines.append(f"  Passed: {len(passed)}  |  Failed: {len(failed)}  |  Other/pending: {len(other)}")
                if topic_counter:
                    top_topics = [f"{t} ({n})" for t, n in topic_counter.most_common(4)]
                    lines.append(f"  Top issue areas: {', '.join(top_topics)}")

                if failed:
                    lines.append(f"\nFailed bills (what {name.split()[0]} fought for but couldn't pass):")
                    for b in failed[:8]:
                        topic_tag = f" [{', '.join(b['topics'][:2])}]" if b["topics"] else ""
                        lines.append(f"  {b['bill_id']}: {b['title'][:120]}{topic_tag}")

                if passed:
                    lines.append(f"\nPassed bills:")
                    for b in passed[:5]:
                        lines.append(f"  {b['bill_id']}: {b['title'][:120]}")

            # Floor voting record
            if total_v:
                lines.append(f"\nFloor voting record ({session}):")
                lines.append(f"  {total_v} floor votes — {len(floor_yes)} YES ({yes_rate}%), {len(floor_no)} NO")

            if notable_no:
                lines.append(f"  Voted NO on floor but bill still passed:")
                for v in notable_no:
                    title = bill_title_map.get(v["bill_id"], "")
                    lines.append(f"    {v['bill_id']}: {title[:100]}" if title else f"    {v['bill_id']}")

            # Committee voting record
            if committee_votes:
                comm_total = len(comm_yes) + len(comm_no)
                lines.append(f"\nCommittee voting record ({session}):")
                lines.append(f"  {comm_total} committee votes — {len(comm_yes)} YES, {len(comm_no)} NO")

            if comm_killed:
                lines.append(f"  Voted to kill in committee (bill later failed):")
                for v in comm_killed:
                    title = bill_title_map.get(v["bill_id"], "")
                    motion = v["motion"].replace("Reported from ", "").split(" with")[0]
                    lines.append(f"    {v['bill_id']} [{motion}]: {title[:90]}" if title else f"    {v['bill_id']} [{motion}]")

            if flip_bills:
                lines.append(f"  Blocked in committee but voted YES on floor ({len(flip_bills)} bills) — possible position shift or party pressure")
                for bid in list(flip_bills)[:3]:
                    title = bill_title_map.get(bid, "")
                    lines.append(f"    {bid}: {title[:90]}" if title else f"    {bid}")

            text = "\n".join(lines)

            # Slug for chunk ID
            slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            chunk_id = f"rep_profile_{slug}_{session}"

            chunks.append({
                "chunk_id":    chunk_id,
                "text":        text,
                "bill_id":     "",
                "session_id":  session,
                "state":       "VA",
                "year":        session,
                "chunk_index": 0,
                "chunk_type":  "rep_profile",
                "metadata": {
                    "source":        "openstates_profile",
                    "name":          name,
                    "party":         party,
                    "district":      district,
                    "session":       session,
                    "bills_sponsored": str(len(bills)),
                    "bills_passed":  str(len(passed)),
                    "bills_failed":  str(len(failed)),
                    "yes_rate":      str(yes_rate or ""),
                },
            })

    return chunks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", help="Single session e.g. 2026 (default: all in DB)")
    parser.add_argument("--out", default="va_rep_profiles.jsonl")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found. Run build_openstates_db.py first.")
        return

    conn = sqlite3.connect(DB_PATH)

    if args.session:
        sessions = [args.session]
    else:
        rows = conn.execute("SELECT DISTINCT session FROM bills ORDER BY session").fetchall()
        sessions = [r[0] for r in rows]

    print(f"Building profiles for sessions: {sessions}")
    chunks = build_profiles(conn, sessions)
    conn.close()

    out = Path(args.out)
    with out.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"\nWrote {len(chunks)} profile chunks to {out}")

    # Stats
    with_failed = sum(1 for c in chunks if int(c["metadata"]["bills_failed"]) > 0)
    print(f"  Profiles with failed bills: {with_failed}")
    print(f"  Sample:")
    if chunks:
        print(chunks[0]["text"][:600])


if __name__ == "__main__":
    main()
