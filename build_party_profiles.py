#!/usr/bin/env python3
"""
Build party legislative agenda profile chunks from openstates_va.db.

One chunk per party per session covering:
  - Bills the party collectively sponsored (by policy area)
  - Unanimous YES / unanimous NO votes (party bloc)
  - Bills where the parties split (Dem vs Rep)
  - Average party alignment score across members
  - Key NO votes on bills that passed anyway

Output: va_party_profiles.jsonl  (ingest with: python ingest.py --file va_party_profiles.jsonl)

Usage:
    python build_party_profiles.py
    python build_party_profiles.py --session 2026
"""
import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).parent / "openstates_va.db"

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


def is_committee_motion(motion: str) -> bool:
    m = (motion or "").lower()
    return m.startswith("reported from") or m.startswith("subcommittee")


def build_party_profiles(conn: sqlite3.Connection, sessions: list[str]) -> list[dict]:
    chunks = []
    built_date = date.today().strftime("%B %d, %Y")

    for session in sessions:
        # All bills this session
        bill_rows = conn.execute(
            "SELECT bill_id, title, subjects, sponsors, result, openstates_url FROM bills WHERE session=?",
            (session,)
        ).fetchall()
        if not bill_rows:
            continue

        bill_info: dict[str, dict] = {}
        for bill_id, title, subjects, sponsors, result, url in bill_rows:
            topics = tag_topics((title or "") + " " + (subjects or ""))
            bill_info[bill_id] = {
                "title":    (title or "").strip(),
                "result":   (result or "").strip(),
                "topics":   topics,
                "url":      url or f"https://openstates.org/va/bills/{session}/{bill_id}/",
                "sponsors": [s.strip() for s in (sponsors or "").split(",") if s.strip()],
            }

        # All floor votes this session
        vote_rows = conn.execute(
            "SELECT voter_name, option, bill_id, party, motion FROM votes WHERE session=?",
            (session,)
        ).fetchall()

        # Per-bill, per-party vote counts  →  (bill_id, party): {yes: N, no: N}
        bill_party_votes: dict[tuple, dict] = defaultdict(lambda: {"yes": 0, "no": 0})
        # Per-member, per-bill vote record  →  member -> {bill_id: option}
        member_bill_votes: dict[str, dict] = defaultdict(dict)
        # Per-party sponsor sets
        party_sponsors: dict[str, Counter] = defaultdict(Counter)
        party_sponsored_bills: dict[str, list] = defaultdict(list)

        legislator_party: dict[str, str] = {}

        for voter_name, option, bill_id, party, motion in vote_rows:
            if not voter_name or not party or is_committee_motion(motion):
                continue
            opt = (option or "").lower()
            if opt not in ("yes", "no"):
                continue
            legislator_party[voter_name] = party
            bill_party_votes[(bill_id, party)][opt] += 1
            member_bill_votes[voter_name][bill_id] = opt

        # Map sponsors to party via legislator_party
        for bill_id, binfo in bill_info.items():
            for sponsor in binfo["sponsors"]:
                p = legislator_party.get(sponsor)
                if p:
                    for t in binfo["topics"]:
                        party_sponsors[p][t] += 1
                    party_sponsored_bills[p].append(bill_id)

        # Identify all parties present
        all_parties = sorted(set(legislator_party.values()))

        for party in all_parties:
            if party not in ("Democratic", "Republican"):
                continue  # skip independents/others for now

            members = [n for n, p in legislator_party.items() if p == party]
            member_count = len(members)
            if member_count < 3:
                continue

            # ── Caucus / faction detection ────────────────────────────────
            # Step 1: find bills where this party was internally split (20-80% yes)
            split_bill_ids = []
            for bid in bill_info:
                pv = bill_party_votes.get((bid, party))
                if not pv:
                    continue
                total_p = pv["yes"] + pv["no"]
                if total_p < 5:
                    continue
                yes_pct = pv["yes"] / total_p
                if 0.15 <= yes_pct <= 0.85:
                    split_bill_ids.append(bid)

            # Step 2: per-member alignment on split votes only
            member_alignment: dict[str, dict] = {}
            for member in members:
                if member not in member_bill_votes:
                    continue
                mvotes = member_bill_votes[member]
                match = dissent = 0
                dissent_topics: Counter = Counter()
                for bid in split_bill_ids:
                    opt = mvotes.get(bid)
                    if not opt:
                        continue
                    pv = bill_party_votes[(bid, party)]
                    party_maj = "yes" if pv["yes"] >= pv["no"] else "no"
                    if opt == party_maj:
                        match += 1
                    else:
                        dissent += 1
                        for t in bill_info[bid]["topics"]:
                            dissent_topics[t] += 1
                total_split = match + dissent
                if total_split < 5:
                    continue
                alignment_pct = round(match / total_split * 100, 1)
                member_alignment[member] = {
                    "alignment": alignment_pct,
                    "dissent_count": dissent,
                    "dissent_topics": dissent_topics,
                }

            # Step 3: bucket members into factions by split-vote alignment
            loyalists  = [(n, d) for n, d in member_alignment.items() if d["alignment"] >= 95]
            moderates  = [(n, d) for n, d in member_alignment.items() if 75 <= d["alignment"] < 95]
            mavericks  = [(n, d) for n, d in member_alignment.items() if d["alignment"] < 75]

            # Top dissent topics per faction
            def top_dissent_topics(faction):
                combined: Counter = Counter()
                for _, d in faction:
                    combined.update(d["dissent_topics"])
                return [t for t, _ in combined.most_common(3)]

            # Overall party floor vote totals
            party_yes = sum(bill_party_votes[(bid, party)]["yes"] for bid in bill_info)
            party_no  = sum(bill_party_votes[(bid, party)]["no"]  for bid in bill_info)
            party_total = party_yes + party_no

            # Unanimous bloc votes (all members voted same way, min 5 members)
            unanimous_yes_bills = []
            unanimous_no_bills  = []
            split_bills         = []  # significant split within party

            for bid, binfo in bill_info.items():
                key = (bid, party)
                pv  = bill_party_votes.get(key)
                if not pv or (pv["yes"] + pv["no"]) < 5:
                    continue
                total_p = pv["yes"] + pv["no"]
                yes_pct = pv["yes"] / total_p

                if yes_pct == 1.0:
                    unanimous_yes_bills.append(bid)
                elif yes_pct == 0.0:
                    unanimous_no_bills.append(bid)
                elif 0.2 <= yes_pct <= 0.8:
                    split_bills.append((bid, round(yes_pct * 100)))

            # Cross-party split: bills where Dem and Rep majority voted opposite
            other_party = "Republican" if party == "Democratic" else "Democratic"
            partisan_splits = []
            for bid in bill_info:
                p_pv = bill_party_votes.get((bid, party))
                o_pv = bill_party_votes.get((bid, other_party))
                if not p_pv or not o_pv:
                    continue
                if (p_pv["yes"] + p_pv["no"]) < 5 or (o_pv["yes"] + o_pv["no"]) < 5:
                    continue
                p_maj = "yes" if p_pv["yes"] >= p_pv["no"] else "no"
                o_maj = "yes" if o_pv["yes"] >= o_pv["no"] else "no"
                if p_maj != o_maj:
                    partisan_splits.append((bid, p_maj, o_maj))

            # NO votes on bills that passed (party opposed but lost)
            party_no_on_passed = [
                bid for bid in bill_info
                if bill_info[bid]["result"] == "pass"
                and bill_party_votes.get((bid, party), {}).get("no", 0) >
                   bill_party_votes.get((bid, party), {}).get("yes", 0)
            ]

            # Build narrative
            lines = [
                f"Party Legislative Agenda: {party} Party — Virginia General Assembly {session} Session",
                f"Source: [OpenStates](https://openstates.org/va/) | [LIS](https://lis.virginia.gov)",
                f"Last updated: {built_date} | Data coverage: {session} Regular Session",
                f"",
                f"[CONFIRMED — OpenStates voter records] {member_count} {party} legislators recorded votes in {session}.",
            ]

            # Sponsored bill policy areas
            sponsored = party_sponsored_bills.get(party, [])
            topic_counter = party_sponsors.get(party, Counter())
            if sponsored:
                lines.append(f"\n[CONFIRMED — OpenStates sponsorship records] Bills sponsored by {party} members: {len(sponsored)}")
                if topic_counter:
                    top = [f"{t} ({n})" for t, n in topic_counter.most_common(6)]
                    lines.append(f"  Top policy areas: {', '.join(top)}")

            # Overall floor vote totals
            if party_total:
                pct_yes = round(party_yes / party_total * 100, 1)
                lines.append(f"\n[CONFIRMED — OpenStates floor votes] {party} caucus cast {party_total} floor votes: {party_yes} YES ({pct_yes}%), {party_no} NO")

            # ── Caucus ranking ────────────────────────────────────────────
            if member_alignment and split_bill_ids:
                all_ranked = sorted(member_alignment.items(), key=lambda x: -x[1]["alignment"])
                lines.append(
                    f"\n[CONFIRMED — split-vote caucus ranking, {len(split_bill_ids)} contested votes] "
                    f"{party} caucus ranked by party loyalty ({len(all_ranked)} members with 5+ split votes):"
                )

                # Full ranked list: rank | name | alignment% | top dissent topic
                lines.append(f"  Rank | Name | Split-vote alignment | Top dissent area")
                for rank, (member, d) in enumerate(all_ranked, 1):
                    top_t = next(iter(d["dissent_topics"]), "") if d["dissent_topics"] else "—"
                    faction = "loyalist" if d["alignment"] >= 95 else ("moderate" if d["alignment"] >= 75 else "maverick")
                    lines.append(
                        f"  #{rank:>3}. {member} — {d['alignment']}% [{faction}]"
                        + (f" | dissents on: {top_t}" if top_t != "—" else "")
                    )

                # Summary by faction
                lines.append(f"\n  Faction summary:")
                if loyalists:
                    lines.append(f"  Party loyalists (≥95%, {len(loyalists)} members): "
                                 + ", ".join(n for n, _ in sorted(loyalists, key=lambda x: -x[1]["alignment"])[:6]))
                if moderates:
                    mod_topics = top_dissent_topics(moderates)
                    topic_str = f" — breaks on: {', '.join(mod_topics)}" if mod_topics else ""
                    lines.append(f"  Moderate/swing (75–94%, {len(moderates)} members){topic_str}: "
                                 + ", ".join(n for n, _ in sorted(moderates, key=lambda x: x[1]["alignment"])[:6]))
                if mavericks:
                    mav_topics = top_dissent_topics(mavericks)
                    topic_str = f" — dissents on: {', '.join(mav_topics)}" if mav_topics else ""
                    lines.append(f"  Mavericks (<75%, {len(mavericks)} members){topic_str}: "
                                 + ", ".join(f"{n} ({d['alignment']}%)"
                                             for n, d in sorted(mavericks, key=lambda x: x[1]["alignment"])[:6]))

                # Most divisive bills
                most_divisive = sorted(
                    [(bid, bill_party_votes[(bid, party)]) for bid in split_bill_ids],
                    key=lambda x: abs(x[1]["yes"] - x[1]["no"])
                )[:5]
                if most_divisive:
                    lines.append(f"\n  Most divisive bills within {party} caucus:")
                    for bid, pv in most_divisive:
                        binfo = bill_info[bid]
                        total_p = pv["yes"] + pv["no"]
                        yes_pct = round(pv["yes"] / total_p * 100)
                        topic_tag = f" [{binfo['topics'][0]}]" if binfo["topics"] else ""
                        lines.append(
                            f"    [{bid}]({binfo['url']}) — {binfo['title'][:80]}{topic_tag} "
                            f"({yes_pct}% of caucus YES, {pv['yes']}-{pv['no']} split)"
                        )

            # Unanimous bloc votes
            if unanimous_yes_bills:
                lines.append(f"\n[CONFIRMED] Unanimous {party} YES votes (entire caucus agreed, {len(unanimous_yes_bills)} bills):")
                for bid in unanimous_yes_bills[:8]:
                    binfo = bill_info[bid]
                    result_tag = "PASSED" if binfo["result"] == "pass" else "FAILED"
                    topic_tag  = f" [{binfo['topics'][0]}]" if binfo["topics"] else ""
                    lines.append(f"  [{bid}]({binfo['url']}) — {binfo['title'][:100]} [{result_tag}]{topic_tag}")

            if unanimous_no_bills:
                lines.append(f"\n[CONFIRMED] Unanimous {party} NO votes (entire caucus opposed, {len(unanimous_no_bills)} bills):")
                for bid in unanimous_no_bills[:8]:
                    binfo = bill_info[bid]
                    result_tag = "PASSED" if binfo["result"] == "pass" else "FAILED"
                    topic_tag  = f" [{binfo['topics'][0]}]" if binfo["topics"] else ""
                    lines.append(f"  [{bid}]({binfo['url']}) — {binfo['title'][:100]} [{result_tag}]{topic_tag}")

            # Party NO on bills that passed (opposed but lost)
            if party_no_on_passed:
                lines.append(f"\n[CONFIRMED] Bills {party} caucus majority opposed but passed anyway ({len(party_no_on_passed)} bills):")
                for bid in party_no_on_passed[:8]:
                    binfo = bill_info[bid]
                    pv = bill_party_votes.get((bid, party), {})
                    topic_tag = f" [{binfo['topics'][0]}]" if binfo["topics"] else ""
                    lines.append(f"  [{bid}]({binfo['url']}) — {binfo['title'][:100]}{topic_tag} ({pv.get('no',0)}-N vs {pv.get('yes',0)}-Y within caucus)")

            # Partisan splits (cross-party disagreement)
            if partisan_splits:
                lines.append(f"\n[CONFIRMED] Partisan split votes — {party} and {other_party} caucuses voted opposite ({len(partisan_splits)} bills):")
                for bid, p_maj, o_maj in partisan_splits[:10]:
                    binfo = bill_info[bid]
                    result_tag = "PASSED" if binfo["result"] == "pass" else "FAILED"
                    topic_tag  = f" [{binfo['topics'][0]}]" if binfo["topics"] else ""
                    lines.append(
                        f"  [{bid}]({binfo['url']}) — {binfo['title'][:90]}{topic_tag} "
                        f"[{result_tag}] ({party}: {p_maj.upper()}, {other_party}: {o_maj.upper()})"
                    )

            # Internal party splits
            if split_bills:
                lines.append(f"\n[CONFIRMED] Internal {party} splits — caucus divided ({len(split_bills)} bills):")
                for bid, yes_pct in split_bills[:6]:
                    binfo = bill_info[bid]
                    topic_tag = f" [{binfo['topics'][0]}]" if binfo["topics"] else ""
                    lines.append(f"  [{bid}]({binfo['url']}) — {binfo['title'][:90]}{topic_tag} ({yes_pct}% of caucus voted YES)")

            text = "\n".join(lines)
            slug = re.sub(r"[^a-z0-9]+", "_", party.lower()).strip("_")
            chunk_id = f"party_agenda_{slug}_{session}"

            chunks.append({
                "chunk_id":   chunk_id,
                "text":       text,
                "bill_id":    "",
                "session_id": session,
                "state":      "VA",
                "year":       session,
                "chunk_index": 0,
                "chunk_type": "party_agenda",
                "metadata": {
                    "source":       "openstates_party_profile",
                    "party":        party,
                    "session":      session,
                    "member_count": str(member_count),
                    "sponsored_bills": str(len(sponsored)),
                    "partisan_splits": str(len(partisan_splits)),
                },
            })

    return chunks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", help="Single session e.g. 2026 (default: all in DB)")
    parser.add_argument("--out", default="va_party_profiles.jsonl")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    if args.session:
        sessions = [args.session]
    else:
        rows = conn.execute("SELECT DISTINCT session FROM bills ORDER BY session").fetchall()
        sessions = [r[0] for r in rows]

    print(f"Building party profiles for sessions: {sessions}")
    chunks = build_party_profiles(conn, sessions)
    conn.close()

    out = Path(args.out)
    with out.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"\nWrote {len(chunks)} party profile chunks to {out}")
    if chunks:
        import sys
        sys.stdout.buffer.write((chunks[0]["text"][:800] + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
