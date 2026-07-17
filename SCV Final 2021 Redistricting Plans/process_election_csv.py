import csv
import json
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def process_election_data(year):
    # Directory containing the downloaded CSV files
    csv_dir = Path(r"c:\Users\Alexis\OneDrive\Desktop\Vriginia_api_election")
    # Output path aligned with main.py expectations
    output_file = Path(__file__).parent / f"election_results_{year}.json"

    results = {
        "election_year": year,
        "state": "Virginia",
        "locality_results": {}
    }

    # Expected CSV filenames based on year
    if year == 2024:
        race_mappings = {
            "President": "2024_President_General.csv",
            "US Senate": "2024_Senate_General.csv"
        }
    elif year == 2023:
        race_mappings = {
            "State Senate": "2023_Senate_General.csv",
            "House of Delegates": "2023_House_General.csv"
        }
    elif year == 2021:
        race_mappings = {
            "Governor": "2021_Governor_General.csv",
            "Lieutenant Governor": "2021_Lieutenant_Governor_General.csv",
            "Attorney General": "2021_Attorney_General_General.csv",
            "House of Delegates": "2021_House_General.csv"
        }
    else:
        race_mappings = {}

    for race_name, filename in race_mappings.items():
        csv_path = csv_dir / filename
        if not csv_path.exists():
            logging.warning(f"Skipping {race_name}: {filename} not found in {csv_dir}")
            continue

        logging.info(f"Processing {race_name} from {filename}...")
        locality_data = {}
        state_totals = {}

        with open(csv_path, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Standardize headers (ELECT CSVs can vary slightly)
                loc = row.get('Locality Name') or row.get('LocalityName') or row.get('Locality')
                cand = row.get('Candidate Name') or row.get('Candidate') or row.get('Name')
                votes_str = row.get('Total Votes') or row.get('Votes') or row.get('Count')

                if not all([loc, cand, votes_str]): continue
                try:
                    votes = int(votes_str.replace(',', ''))
                except ValueError: continue

                if loc not in locality_data: locality_data[loc] = {}
                locality_data[loc][cand] = locality_data[loc].get(cand, 0) + votes
                state_totals[cand] = state_totals.get(cand, 0) + votes

        def calculate_pct(data_dict):
            total = sum(data_dict.values())
            sorted_cands = sorted(data_dict.items(), key=lambda x: x[1], reverse=True)
            
            return {
                "total": total,
                "candidates": [
                    {"name": k, "votes": v, "pct": round((v / total) * 100, 1)} 
                    for k, v in sorted_cands
                ],
                "winner": {
                    "name": sorted_cands[0][0],
                    "pct": round((sorted_cands[0][1] / total) * 100, 1)
                } if sorted_cands else {}
            }

        # Aligning with the "locality_results[office][locality]" structure
        results["locality_results"][race_name] = {
            loc: calculate_pct(cands) for loc, cands in locality_data.items()
        }
        # Store statewide summary separately if needed
        results.setdefault("statewide_summary", {})[race_name] = calculate_pct(state_totals)

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=4)
    logging.info(f"Successfully updated {output_file}")

if __name__ == "__main__":
    # Process both 2024 and 2023 data
    # Process 2024, 2023, and 2021 data
    process_election_data(2024)
    process_election_data(2023)
    process_election_data(2021)