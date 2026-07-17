import requests
from bs4 import BeautifulSoup
import json
import logging
from pathlib import Path

# Configure logging for VoteIQ scraping tasks
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

class SuffolkOfficialScraper:
    def __init__(self):
        self.base_url = "https://www.suffolkva.us"
        self.headers = {
            "User-Agent": "VoteIQ-Data-Engine/1.0 (Integration Task - Suffolk)"
        }
        # Map of offices to their specific roles
        self.office_map = {
            "Commonwealth's Attorney": {
                "role": "Chief law enforcement officer responsible for prosecuting criminal cases."
            },
            "Commissioner of the Revenue": {
                "role": "Responsible for assessing taxes and administering tax relief programs."
            },
            "City Treasurer": {
                "role": "The official collector of city funds and manager of city receivables."
            },
            "Clerk of the Circuit Court": {
                "role": "Handles land records, marriage licenses, and court administration."
            },
            "Sheriff": {
                "role": "Responsible for court security, jail operations, and civil process."
            }
        }

    def scrape_constitutional_officers(self):
        """Scrapes names of constitutional officers from the central landing page."""
        url = f"{self.base_url}/918/Constitutional-Officers"
        logging.info(f"Scraping Constitutional Officers from {url}...")
        results = []
        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Focus only on the main content area to avoid picking up Mayor/Council links in the nav
            content_area = soup.find(id='divMainContent') or soup.find('main') or soup
            
            for office, info in self.office_map.items():
                name = "Unknown"
                # Find the header or strong tag containing the office title
                # We search specifically within the content area
                section = content_area.find(lambda tag: tag.name in ['h2', 'h3', 'strong', 'a'] and office in tag.get_text())
                
                if section:
                    # CivicPlus directory pages often put the name in a 'contactName' class 
                    # or the immediately following text block.
                    # We limit the search to the next few elements to avoid jumping to another office.
                    name_tag = section.find_next(class_='contactName')
                    if not name_tag:
                        # Fallback to the next paragraph if it doesn't look like another office title
                        next_p = section.find_next('p')
                        if next_p and not any(o in next_p.get_text() for o in self.office_map.keys()):
                            name_tag = next_p
                    
                    if name_tag:
                        raw_name = name_tag.get_text(strip=True)
                        # Clean known noise and prefixes
                        name = raw_name
                        noise = [office, "Suffolk", "City of", "Explore", "The Honorable", "Mayor", "Council"]
                        for title_part in noise:
                            name = name.replace(title_part, "").strip()
                        name = name.strip(", ") or "Unknown"

                results.append({
                    "office": office,
                    "locality": "Suffolk",
                    "official_name": name,
                    "role_description": info["role"],
                    "source_url": url
                })
        except Exception as e:
            logging.error(f"Failed to scrape Constitutional Officers: {e}")
        return results

    def scrape_city_council(self):
        """Scrapes the City Council members page."""
        url = f"{self.base_url}/881/City-Council-Mayor"
        logging.info(f"Scraping City Council from {url}...")
        results = []
        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Look for directory links (CivicPlus standard)
            council_links = soup.select('a[href*="/Directory.aspx?EID="]')
            seen_names = set()
            
            for link in council_links:
                raw_text = link.get_text(strip=True)
                if raw_text and raw_text != "View All":
                    role = "City Council Member"
                    if "Mayor" in raw_text and "Vice" not in raw_text:
                        role = "Mayor"
                    elif "Vice Mayor" in raw_text:
                        role = "Vice Mayor"
                    
                    # Clean the name: remove roles and prefixes
                    name = raw_text
                    noise = ["Mayor", "Vice", "The Honorable", "Council Member"]
                    for junk in noise:
                        name = name.replace(junk, "").strip()
                    
                    name = name.strip(", ")

                    if not name or name in seen_names:
                        continue

                    seen_names.add(name)
                    results.append({
                        "office": role,
                        "locality": "Suffolk",
                        "official_name": name,
                        "role_description": "Elected representative serving on the Suffolk City Council.",
                        "source_url": url
                    })
        except Exception as e:
            logging.error(f"Failed to scrape City Council: {e}")
        return results

    def scrape_school_board(self):
        """Scrapes the Suffolk School Board members page."""
        url = "https://www.spsk12.net/school-board/board-members"
        logging.info(f"Scraping School Board from {url}...")
        results = []
        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # School board members are typically listed in a directory or within content blocks
            # We target the main content containers to find member names and titles
            members = soup.find_all(['h2', 'h3', 'p', 'strong'])
            seen_names = set()

            for member in members:
                text = member.get_text(strip=True)
                # Looking for patterns like "Name - Role" or names followed by "Borough"
                if any(role in text for role in ["Chair", "Board Member", "Borough"]):
                    # Clean up common prefixes and suffixes
                    name = text
                    role = "School Board Member"
                    
                    if "Chair" in text and "Vice" not in text:
                        role = "School Board Chair"
                    elif "Vice Chair" in text:
                        role = "School Board Vice Chair"

                    # Extract name part (usually before a dash or comma)
                    if " - " in name:
                        name = name.split(" - ")[0]
                    elif "," in name:
                        name = name.split(",")[0]
                    
                    name = name.replace("The Honorable", "").strip()
                    
                    if name and name not in seen_names and len(name.split()) >= 2:
                        seen_names.add(name)
                        results.append({
                            "office": role,
                            "locality": "Suffolk",
                            "official_name": name,
                            "role_description": "Elected representative responsible for Suffolk Public Schools governance.",
                            "source_url": url
                        })
        except Exception as e:
            logging.error(f"Failed to scrape School Board: {e}")
        return results

    def run(self):
        """Executes all scraping tasks and consolidates data."""
        all_data = []
        all_data.extend(self.scrape_constitutional_officers())
        all_data.extend(self.scrape_city_council())
        all_data.extend(self.scrape_school_board())
        return all_data

if __name__ == "__main__":
    scraper = SuffolkOfficialScraper()
    data = scraper.run()
    print(json.dumps(data, indent=4))
    output_path = Path(__file__).with_name("suffolk_elected_officials.json")
    with output_path.open('w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)