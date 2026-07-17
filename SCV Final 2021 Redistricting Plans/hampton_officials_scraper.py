import requests
from bs4 import BeautifulSoup
import json
import logging
from pathlib import Path

# Configure logging for VoteIQ scraping tasks
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

class HamptonOfficialScraper:
    def __init__(self):
        self.base_url = "https://hampton.gov"
        self.headers = {
            "User-Agent": "VoteIQ-Data-Engine/1.0 (Integration Task - Hampton)"
        }
        # Mapping for Constitutional Offices and their specific landing pages
        self.constitutional_offices = {
            "Commonwealth's Attorney": {
                "url": f"{self.base_url}/194/Commonwealths-Attorney",
                "role": "Chief law enforcement officer responsible for prosecuting criminal cases."
            },
            "Commissioner of the Revenue": {
                "url": f"{self.base_url}/175/Commissioner-of-the-Revenue",
                "role": "Responsible for assessing taxes and administering tax relief programs."
            },
            "City Treasurer": {
                "url": f"{self.base_url}/344/Treasurer",
                "role": "The official collector of city funds and manager of city receivables."
            },
            "Clerk of the Circuit Court": {
                "url": f"{self.base_url}/226/Clerk-of-the-Circuit-Court",
                "role": "Handles land records, marriage licenses, and court administration."
            },
            "Sheriff": {
                "url": f"{self.base_url}/244/Sheriff",
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
                
                # Focus on the main content area to avoid sidebar/nav links
                content_area = soup.find(id='divMainContent') or soup.find('main') or soup

                # Prioritize 'contactName' class within the content area
                name_tag = content_area.find(class_='contactName')
                if not name_tag:
                    h1_tag = content_area.find('h1')
                    # Ignore headers that are just the office name or known junk
                    h1_text = h1_tag.get_text(strip=True) if h1_tag else ""
                    if h1_tag and h1_text not in [office, "Explore", "Hampton", "Board of Zoning Appeals"]:
                        name_tag = h1_tag
                
                name = name_tag.get_text(strip=True) if name_tag else "Unknown"
                
                # Clean known fragments and common menu items caught by scrapers
                noise = [office, "Hampton", "City of", "Explore", "Board of Zoning Appeals", "Division of Fire & Rescue", "The Honorable"]
                for title_part in noise:
                    name = name.replace(title_part, "").strip()
                
                name = name.strip(", ") or "Unknown"

                results.append({
                    "office": office,
                    "locality": "Hampton",
                    "official_name": name,
                    "role_description": info['role'],
                    "source_url": info['url']
                })
            except Exception as e:
                logging.error(f"Failed to scrape {office}: {e}")
        return results

    def scrape_city_council(self):
        """Scrapes the City Council members page."""
        url = f"{self.base_url}/86/City-Council"
        logging.info(f"Scraping City Council from {url}...")
        results = []
        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Hampton lists council members within a directory or content block
            # We look for links or headers that identify the members
            council_links = soup.select('a[href*="/Directory.aspx?EID="]')
            seen_names = set()
            
            for link in council_links:
                name = link.get_text(strip=True)
                if name and name != "View All" and name not in seen_names:
                    role = "City Council Member (At-Large)"
                    if "Mayor" in name:
                        role = "Mayor (At-Large)"
                        name = name.replace("Mayor", "").strip()
                    elif "Vice Mayor" in name:
                        role = "Vice Mayor (At-Large)"
                        name = name.replace("Vice Mayor", "").strip()
                    
                    seen_names.add(name)
                    results.append({
                        "office": role,
                        "locality": "Hampton",
                        "official_name": name,
                        "role_description": "Elected At-Large representative serving the entire City of Hampton.",
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
    scraper = HamptonOfficialScraper()
    data = scraper.run()
    print(json.dumps(data, indent=4))
    output_path = Path(__file__).with_name("hampton_elected_officials.json")
    with output_path.open('w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)
