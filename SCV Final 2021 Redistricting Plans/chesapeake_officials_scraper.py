import requests
from bs4 import BeautifulSoup
import json
import logging
from pathlib import Path

# Configure logging for VoteIQ scraping tasks
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

class ChesapeakeOfficialScraper:
    def __init__(self):
        self.base_url = "https://www.cityofchesapeake.net"
        self.headers = {
            "User-Agent": "VoteIQ-Data-Engine/1.0 (Integration Task - Chesapeake)"
        }
        # Mapping for Constitutional Offices and their specific landing pages
        self.constitutional_offices = {
            "Commonwealth's Attorney": {
                "url": f"{self.base_url}/608/Commonwealths-Attorney",
                "role": "Chief law enforcement officer responsible for prosecuting criminal cases."
            },
            "Commissioner of the Revenue": {
                "url": f"{self.base_url}/607/Commissioner-of-Revenue",
                "role": "Responsible for assessing taxes and administering tax relief programs."
            },
            "City Treasurer": {
                "url": f"{self.base_url}/610/Treasurer",
                "role": "The official collector of city funds and manager of city receivables."
            },
            "Clerk of the Circuit Court": {
                "url": f"{self.base_url}/606/Clerk-of-the-Circuit-Court",
                "role": "Handles land records, marriage licenses, and court administration."
            },
            "Sheriff": {
                "url": f"{self.base_url}/609/Sheriff",
                "role": "Responsible for court security, jail operations, and civil process."
            }
        }

    def scrape_constitutional_officers(self):
        """Scrapes names of constitutional officers from their respective department pages."""
        results = []
        for office, info in self.constitutional_offices.items():
            logging.info(f"Scraping {office} from {info['url']}...")
            try:
                response = requests.get(info['url'], headers=self.headers, timeout=15)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Prioritize 'contactName' class. If using h1, ensure it's not a generic nav item.
                name_tag = soup.find(class_='contactName')
                if not name_tag:
                    h1_tag = soup.find('h1')
                    # Ignore headers that are just the office name or known junk
                    if h1_tag and h1_tag.get_text(strip=True) not in [office, "Explore", "Chesapeake"]:
                        name_tag = h1_tag
                
                name = name_tag.get_text(strip=True) if name_tag else "Unknown"
                
                # Clean known fragments and common menu items caught by scrapers
                noise = [office, "Chesapeake", "City of", "Explore", "Board of Zoning Appeals"]
                for title_part in noise:
                    name = name.replace(title_part, "").strip()

                # Clean up title suffix if present in the text (e.g., "John Doe, Sheriff")
                if "," in name:
                    name = name.split(",")[0].strip()

                name = name.strip(", ") or "Unknown"

                results.append({
                    "office": office,
                    "locality": "Chesapeake",
                    "official_name": name,
                    "role_description": info['role'],
                    "source_url": info['url']
                })
            except Exception as e:
                logging.error(f"Failed to scrape {office}: {e}")
        return results

    def scrape_city_council(self):
        """Scrapes the City Council members page."""
        url = f"{self.base_url}/1893/City-Council-Members"
        logging.info(f"Scraping City Council from {url}...")
        results = []
        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Look for headers containing member names
            # Chesapeake typically lists these in h2 or h3 tags within the main content
            council_tags = soup.find_all(['h2', 'h3'], class_='subhead1') or soup.find_all(['h2', 'h3'])
            
            for tag in council_tags:
                text = tag.get_text(strip=True)
                if any(title in text for title in ["Mayor", "Council Member"]):
                    role = "Mayor (At-Large)" if "Mayor" in text else "City Council Member (At-Large)"
                    if "Vice Mayor" in text: role = "Vice Mayor (At-Large)"
                    
                    # Clean the title out of the name string
                    name = text.replace("Mayor", "").replace("Council Member", "").replace("Vice", "").strip()
                    
                    if "," in name:
                        name = name.split(",")[0].strip()
                    
                    results.append({
                        "office": role,
                        "locality": "Chesapeake",
                        "official_name": name,
                        "role_description": "Elected At-Large representative serving the entire City of Chesapeake.",
                        "source_url": url
                    })
        except Exception as e:
            logging.error(f"Failed to scrape City Council: {e}")
        return results

    def run(self):
        """Executes all scraping tasks and consolidates data."""
        all_data = []
        all_data.extend(self.scrape_constitutional_officers())
        all_data.extend(self.scrape_city_council())
        return all_data

if __name__ == "__main__":
    scraper = ChesapeakeOfficialScraper()
    data = scraper.run()
    
    # Print to console as JSON
    print(json.dumps(data, indent=4))
    
    # Save to JSON file
    output_path = Path(__file__).with_name("chesapeake_elected_officials.json")
    with output_path.open('w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)
