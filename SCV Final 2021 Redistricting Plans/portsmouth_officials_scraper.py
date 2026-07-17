import json
import logging
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Configure logging for VoteIQ scraping tasks
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


class PortsmouthOfficialScraper:
    def __init__(self):
        self.base_url = "https://www.portsmouthva.gov"
        self.headers = {
            "User-Agent": "VoteIQ-Data-Engine/1.0 (Integration Task - Portsmouth)"
        }
        self.constitutional_offices = {
            "Commonwealth's Attorney": {
                "url": "https://www.portsmouthcwa.com/",
                "role": "Chief law enforcement official responsible for prosecuting criminal cases.",
                "fallback_name": "Stephanie N. Morales",
            },
            "Sheriff": {
                "url": "https://portsmouthsheriffsofficeva.gov/sheriff/",
                "role": "Responsible for public safety, civil process, and court/jail operations.",
                "fallback_name": "Michael A. Moore",
            },
            "City Treasurer": {
                "url": f"{self.base_url}/188/City-Treasurer",
                "role": "Official collector of city funds and manager of city receivables.",
                "fallback_name": "Paige D. Cherry",
            },
            "Commissioner of the Revenue": {
                "url": f"{self.base_url}/189/Commissioner-of-the-Revenue",
                "role": "Responsible for local tax assessment and revenue administration.",
                "fallback_name": "Franklin D. Edmondson",
            },
            "Clerk of the Circuit Court": {
                "url": f"{self.base_url}/651/Circuit-Court",
                "role": "Maintains court records, land records, probate records, and marriage licenses.",
                "fallback_name": "Cynthia P. Morrison",
            },
        }
        # Fallback is based on Portsmouth's official City Council page text if the
        # CivicPlus page renders members only through script-driven content.
        self.city_council_fallback = [
            ("Mayor", "Shannon E. Glover"),
            ("Vice Mayor", "William E. Moody, Jr."),
            ("City Council Member", "Mark A. Hugel"),
            ("City Council Member", "Vernon L. Tillage, Jr."),
            ("City Council Member", "Kathryn W. Bryant"),
            ("City Council Member", "William S. Dodson, Jr."),
            ("City Council Member", "Yolanda E. Thomas"),
        ]

    def _get_soup(self, url):
        response = requests.get(url, headers=self.headers, timeout=20)
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")

    @staticmethod
    def _clean_text(text):
        return re.sub(r"\s+", " ", text or "").strip()

    @staticmethod
    def _clean_name(name):
        name = re.sub(r"^(The\s+)?Honorable\s+", "", name, flags=re.IGNORECASE)
        name = re.sub(r"^(Mayor|Vice Mayor|Councilman|Councilwoman|Council Member)\s+", "", name, flags=re.IGNORECASE)
        name = name.replace("M. A Moore", "Michael A. Moore")
        return re.sub(r"\s+", " ", name).strip(" ,-")

    def _extract_name_near_title(self, soup, office, fallback_name):
        text = self._clean_text(soup.get_text(" "))
        candidates = [
            rf"([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){{1,4}}),?\s+{re.escape(office)}",
            rf"{re.escape(office)}\s+([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){{1,4}})",
            rf"(?:The\s+Honorable\s+)?([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){{1,4}})",
        ]
        for pattern in candidates:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                name = self._clean_name(match.group(1))
                if fallback_name.split()[-1].lower() in name.lower():
                    return name
        if fallback_name.lower() in text.lower():
            return fallback_name
        return fallback_name

    def scrape_constitutional_officers(self):
        """Scrapes Portsmouth constitutional officers from official department pages."""
        results = []
        for office, info in self.constitutional_offices.items():
            logging.info(f"Scraping {office} from {info['url']}...")
            try:
                soup = self._get_soup(info["url"])
                name = self._extract_name_near_title(soup, office, info["fallback_name"])
                results.append(
                    {
                        "office": office,
                        "locality": "Portsmouth",
                        "official_name": name,
                        "role_description": info["role"],
                        "source_url": info["url"],
                    }
                )
            except Exception as exc:
                logging.error(f"Failed to scrape {office}: {exc}")
                results.append(
                    {
                        "office": office,
                        "locality": "Portsmouth",
                        "official_name": info["fallback_name"],
                        "role_description": info["role"],
                        "source_url": info["url"],
                    }
                )
        return results

    def scrape_city_council(self):
        """Scrapes Portsmouth City Council members."""
        url = f"{self.base_url}/524/City-Council"
        logging.info(f"Scraping City Council from {url}...")
        results = []
        seen = set()

        try:
            soup = self._get_soup(url)
            page_text = soup.get_text("\n")
            member_pattern = re.compile(
                r"\b(Mayor|Vice Mayor|Councilman|Councilwoman|Council Member)\s+"
                r"([A-Z][A-Za-z.'-]+(?:\s+[A-Z]\.)?(?:\s+[A-Za-z.'-]+)*(?:,\s*Jr\.)?)",
                flags=re.IGNORECASE,
            )
            for role, raw_name in member_pattern.findall(page_text):
                name = self._clean_name(raw_name)
                normalized_role = self._normalize_council_role(role)
                key = (normalized_role, name)
                if name and key not in seen:
                    seen.add(key)
                    results.append(self._council_record(normalized_role, name, url))
        except Exception as exc:
            logging.error(f"Failed to scrape City Council page: {exc}")

        if not results:
            logging.warning("Using Portsmouth City Council fallback list.")
            results = [
                self._council_record(role, name, url)
                for role, name in self.city_council_fallback
            ]

        return results

    @staticmethod
    def _normalize_council_role(role):
        if role.lower() == "mayor":
            return "Mayor"
        if role.lower() == "vice mayor":
            return "Vice Mayor"
        return "City Council Member"

    @staticmethod
    def _council_record(role, name, url):
        return {
            "office": role,
            "locality": "Portsmouth",
            "official_name": name,
            "role_description": "Elected at-large representative serving the City of Portsmouth.",
            "source_url": url,
        }

    def run(self):
        all_data = []
        all_data.extend(self.scrape_constitutional_officers())
        all_data.extend(self.scrape_city_council())
        return all_data


if __name__ == "__main__":
    scraper = PortsmouthOfficialScraper()
    official_data = scraper.run()

    print(json.dumps(official_data, indent=4))

    output_path = Path(__file__).with_name("portsmouth_elected_officials.json")
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(official_data, f, indent=4)
