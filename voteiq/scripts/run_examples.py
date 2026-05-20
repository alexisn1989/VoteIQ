import argparse
from voteiq.reports.civic_analyst import run_analyst


def main() -> None:
    parser = argparse.ArgumentParser(description="VoteIQ Civic Analyst")
    parser.add_argument("--name",       required=True,
                        help="Legislator display name")
    parser.add_argument("--id",         required=True,
                        help="FEC candidate_id (federal) or lis_id (state)")
    parser.add_argument("--level",      default="federal",
                        choices=["federal", "state"])
    parser.add_argument("--type",       default="triangle",
                        choices=["triangle", "donor_shift", "geography",
                                 "network", "effectiveness"])
    parser.add_argument("--cycle",      default=None,
                        help="Cycle year (e.g. 2024 for federal, 2023 for state)")
    parser.add_argument("--committees", nargs="*", default=[])
    args = parser.parse_args()

    cycle = int(args.cycle) if args.cycle and args.level == "federal" else args.cycle

    run_analyst(
        name=args.name,
        member_id=args.id,
        level=args.level,
        analyst_type=args.type,
        cycle=cycle,
        committees=args.committees,
        defections=[],
    )


if __name__ == "__main__":
    main()
