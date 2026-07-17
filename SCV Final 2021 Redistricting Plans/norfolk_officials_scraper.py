import requests
from bs4 import BeautifulSoup
import json
import logging
from pathlib import Path

# Configure logging for VoteIQ scraping tasks
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

class NorfolkOfficialScraper:
    def __init__(self):
        self.base_url = "https://www.norfolk.gov/227/Constitutional-Officers" # Main page listing all constitutional officers
        self.headers = {
            "User-Agent": "VoteIQ-Data-Engine/1.0 (Integration Task - Norfolk)"
        }
        # Map of offices to their specific metadata
        # For Norfolk, we'll scrape from a single page that lists them all.
        self.office_map = {
            "Commonwealth’s Attorney": {
                "role": "Chief law enforcement official, prosecuting felonies and misdemeanors."
            },
            "Commissioner of the Revenue": {
                "role": "Chief tax assessor, responsible for determining property, business, and income taxes."
            },
            "City Treasurer": {
                "role": "Official collector of city receivables and manager of collected funds."
            },
            "Clerk of the Circuit Court": {
                "role": "Court administrator, records deeds, handles probates, and issues marriage licenses."

            },
            "Sheriff": {
                "role": "Responsible for court security, jail operations, and service of civil process."
            }
        }

    def scrape_all_offices(self):
        """Scrapes the main Constitutional Officers page for all officials' names and details."""
        url = self.base_url
        logging.info(f"Scraping all Constitutional Officers from {url}...")
        
        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            results = []
            
            # Norfolk lists constitutional officers under <h2> headers
            # We iterate through our map to find the corresponding sections
            for office_name, info in self.office_map.items():
                # Use a flexible match for the header (handling curly/straight quotes)
                clean_office_key = office_name.replace("’", "'")
                section = soup.find('h2', string=lambda t: t and clean_office_key in t.replace("’", "'"))
                
                if section and section.find_next_sibling('p'):
                    raw_name = section.find_next_sibling('p').get_text(strip=True)
                    
                    # Noise filtering logic: Remove the office title, locality, and prefixes
                    name = raw_name
                    noise = [office_name, clean_office_key, "Norfolk", "City of", "The Honorable"]
                    for title_part in noise:
                        name = name.replace(title_part, "").strip()
                    
                    name = name.strip(", ") or "Unknown"

                    results.append({
                        "office": office_name,
                        "locality": "Norfolk",
                        "official_name": name,
                        "role_description": info["role"],
                        "source_url": url
                    })
            
            return results

        except Exception as e:
            logging.error(f"Failed to scrape Norfolk Constitutional Officers: {e}")
            return []

    def run(self):
        return self.scrape_all_offices()

if __name__ == "__main__":
    scraper = NorfolkOfficialScraper()
    official_data = scraper.run()
    
    # Output the structured data for VoteIQ ingestion
    print(json.dumps(official_data, indent=4))
    
    # Optionally save to the project directory
    output_path = Path(__file__).with_name("norfolk_constitutional_officers.json")
    with output_path.open('w', encoding='utf-8') as f:
        json.dump(official_data, f, indent=4)
