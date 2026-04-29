import requests
from bs4 import BeautifulSoup
import json

def scrape_vb_council():
    url = "https://clerk.virginiabeach.gov/city-council/city-council-members"
    response = requests.get(url, timeout=15)

    if response.status_code != 200:
        print(f"Failed to reach the site. Status: {response.status_code}")
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    officials = []

    cards = soup.find_all('div', class_='shadow-news-card-simple')
    print(f"Found {len(cards)} council member cards")

    for card in cards:
        name_tag = card.find('p', class_='faux-h5')
        name = name_tag.get_text(strip=True) if name_tag else "Unknown"

        district_tag = card.find('p', class_='font-semibold')
        district = district_tag.get_text(strip=True) if district_tag else "Unknown"

        email_tag = card.find('a', href=lambda h: h and h.startswith('mailto:'))
        email = email_tag['href'].replace('mailto:', '') if email_tag else "No Email Found"

        officials.append({
            "name": name,
            "district": district,
            "email": email,
            "jurisdiction": "Virginia Beach"
        })
        print(f"  {name} | {district} | {email}")

    with open('voteiq_officials.json', 'w') as f:
        json.dump(officials, f, indent=4)

    print(f"\nSaved {len(officials)} officials to voteiq_officials.json")

if __name__ == "__main__":
    scrape_vb_council()
