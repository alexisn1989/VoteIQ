from ingest import ingest, test_query
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VoteIQ bill ingest")
    parser.add_argument("--file",  required=True, help="Path to bill JSON file")
    parser.add_argument("--reset", action="store_true", help="Wipe collection before ingest")
    parser.add_argument("--test",  action="store_true", help="Run test queries after ingest")
    args = parser.parse_args()

    ingest(args.file, reset=args.reset)

    if args.test:
        test_query("what is the minimum wage schedule")
        test_query("how did Senator Obenshain vote")
        test_query("fiscal impact on Medicaid")
        test_query("who sponsored HB1")
