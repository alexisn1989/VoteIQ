"""Scrape current Virginia Beach city officials and school board members,
then merge them into voteiq_officials.json.

Run once (or whenever officials change):
    python3 scrape_vb_officials.py

Adds entries for:
  - Sheriff
  - Commonwealth's Attorney
  - Commissioner of the Revenue
  - City Treasurer
  - Clerk of the Circuit Court
  - School Board District 1-7
  - School Board At-Large (both seats)

Existing council-member entries (Mayor, District 1-10) are preserved.
"""
import json
import re
import sys
import os
import time

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing deps — run: pip install requests beautifulsoup4")
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OFFICIALS_JSON = os.path.join(BASE_DIR, "voteiq_officials.json")

HEADERS = {"User-Agent": "VoteIQ-data-bot/1.0 (public records research)"}
TIMEOUT = 20


def _get(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print(f"  WARNING: could not fetch {url}: {e}")
        return None


def _entry(name, district, email="", url="", party=""):
    return {
        "name": name,
        "district": district,
        "email": email,
        "jurisdiction": "Virginia Beach",
        "url": url,
        "party": party,
    }


def scrape_constitutional_officers():
    """Pull Sheriff, CA, Treasurer, Commissioner, Clerk from vbgov.com."""
    results = []

    pages = [
        ("Sheriff",                    "https://www.vbso.net/",
         "https://www.vbso.net/",      "sheriff@vbgov.com"),
        ("Commonwealth's Attorney",    "https://www.vbgov.com/government/departments/commonwealths-attorney/Pages/default.aspx",
         "https://www.vbgov.com/government/departments/commonwealths-attorney/Pages/default.aspx",
         "commonwealthsattorney@vbgov.com"),
        ("Commissioner of the Revenue","https://www.vbgov.com/government/departments/commissioner-of-the-revenue/Pages/default.aspx",
         "https://www.vbgov.com/government/departments/commissioner-of-the-revenue/Pages/default.aspx",
         "commissioner@vbgov.com"),
        ("City Treasurer",             "https://www.vbgov.com/government/departments/city-treasurer/Pages/default.aspx",
         "https://www.vbgov.com/government/departments/city-treasurer/Pages/default.aspx",
         "treasurer@vbgov.com"),
        ("Clerk of the Circuit Court", "https://www.vbgov.com/government/departments/circuit-court-clerk/Pages/default.aspx",
         "https://www.vbgov.com/government/departments/circuit-court-clerk/Pages/default.aspx",
         "clerk@vbgov.com"),
    ]

    for district, page_url, source_url, fallback_email in pages:
        print(f"  Fetching {district}...")
        soup = _get(page_url)
        name = None
        if soup:
            # Try common patterns for department head names
            for pattern in [
                r"(?:Sheriff|Commissioner|Treasurer|Clerk|Attorney)\s+([A-Z][a-z]+ (?:[A-Z]\. )?[A-Z][a-z]+)",
                r"([A-Z][a-z]+ (?:[A-Z]\. )?[A-Z][a-z]+),?\s+(?:Sheriff|Commissioner|Treasurer|Clerk|Attorney)",
            ]:
                m = re.search(pattern, soup.get_text())
                if m:
                    name = m.group(1).strip()
                    break
        if name:
            print(f"    Found: {name}")
            results.append(_entry(name, district, fallback_email, source_url))
        else:
            print(f"    Could not auto-detect name — enter manually below.")
            name = input(f"  Enter current {district} name (or press Enter to skip): ").strip()
            if name:
                results.append(_entry(name, district, fallback_email, source_url))
        time.sleep(0.5)

    return results


def scrape_school_board():
    """Pull VB School Board members from vbschools.com."""
    results = []
    print("  Fetching VB School Board...")
    soup = _get("https://www.vbschools.com/domain/4")
    if not soup:
        soup = _get("https://www.vbschools.com/Page/4")

    members_found = []
    if soup:
        # Look for member names in the page
        text = soup.get_text("\n")
        # Pattern: "District N - Name" or "At-Large - Name"
        for m in re.finditer(
            r"District\s+(\d+)\s*[-–]\s*([A-Z][a-z]+ (?:[A-Z]\. )?[A-Z][a-z]+)",
            text,
        ):
            members_found.append(("district", int(m.group(1)), m.group(2).strip()))
        for m in re.finditer(
            r"At[- ]Large\s*[-–]\s*([A-Z][a-z]+ (?:[A-Z]\. )?[A-Z][a-z]+)",
            text,
        ):
            members_found.append(("at_large", None, m.group(1).strip()))

    if members_found:
        for kind, num, name in members_found:
            print(f"    Found: {kind} {num or ''} → {name}")
            if kind == "at_large":
                results.append(_entry(name, "School Board At-Large",
                                      url="https://www.vbschools.com/domain/4"))
            else:
                results.append(_entry(name, f"School Board District {num}",
                                      url="https://www.vbschools.com/domain/4"))
    else:
        print("  Could not auto-detect school board members.")
        print("  Enter manually (Districts 1-7 + At-Large).")
        print("  Find them at: https://www.vbschools.com/domain/4")
        for i in range(1, 8):
            name = input(f"  School Board District {i} (Enter to skip): ").strip()
            if name:
                results.append(_entry(name, f"School Board District {i}",
                                      url="https://www.vbschools.com/domain/4"))
        for label in ("At-Large Seat 1", "At-Large Seat 2"):
            name = input(f"  School Board {label} (Enter to skip): ").strip()
            if name:
                results.append(_entry(name, "School Board At-Large",
                                      url="https://www.vbschools.com/domain/4"))

    return results


def main():
    print("Loading voteiq_officials.json...")
    with open(OFFICIALS_JSON, encoding="utf-8") as f:
        existing = json.load(f)

    # Keep only council members (Mayor + District 1-10)
    council_districts = {"Mayor"} | {f"District {i}" for i in range(1, 11)}
    kept = [e for e in existing if e.get("district") in council_districts]
    print(f"  Keeping {len(kept)} council members, replacing everything else.\n")

    new_entries = []

    print("=== Constitutional Officers ===")
    new_entries.extend(scrape_constitutional_officers())

    print("\n=== School Board ===")
    new_entries.extend(scrape_school_board())

    merged = kept + new_entries
    with open(OFFICIALS_JSON, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    print(f"\n✓ voteiq_officials.json updated: {len(merged)} total entries")
    print(f"  Council: {len(kept)}, New: {len(new_entries)}")
    added = [e["district"] for e in new_entries]
    for d in sorted(added):
        name = next(e["name"] for e in new_entries if e["district"] == d)
        print(f"  + {d}: {name}")


if __name__ == "__main__":
    main()
