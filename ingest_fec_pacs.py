#!/usr/bin/env python3
"""
Ingest FEC PAC/industry contributions for Virginia's federal delegation into polls.db.

For each VA member:
  1. Look up their FEC candidate record via bioguide_id
  2. Get their principal campaign committee
  3. Fetch schedule_a contributions (paginate up to max_pages)
  4. Skip internal joint-fundraising committee transfers
  5. Categorize contributor_name by industry keyword
  6. Aggregate totals per industry
  7. Store in fec_industry_totals table

Usage:
    python ingest_fec_pacs.py
    python ingest_fec_pacs.py --cycle 2024
    python ingest_fec_pacs.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH  = BASE_DIR / "polls.db"

# Load .env so FEC_API_KEY is available when run directly
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

FEC_BASE = "https://api.open.fec.gov/v1"
HEADERS  = {"User-Agent": "VoteIQ/1.0 (Virginia civic data; alexisnieuwenhuys89@gmail.com)"}

# VA federal delegation: bioguide_id -> (display_name, office, fec_candidate_id)
# FEC candidate IDs looked up via /candidates/search/ — hardcoded to avoid per-run API calls
VA_MEMBERS = {
    "W000804": ("Rob Wittman",           "H", "H8VA01147"),
    "K000399": ("Jennifer Kiggans",      "H", "H2VA02064"),
    "S000185": ("Bobby Scott",           "H", "H6VA01117"),
    "M001227": ("Jennifer McClellan",    "H", "H4VA04066"),
    "M001239": ("John McGuire",          "H", "H0VA07133"),
    "C001118": ("Ben Cline",             "H", "H8VA06104"),
    "V000138": ("Eugene Vindman",        "H", "H4VA07234"),
    "B001292": ("Don Beyer",             "H", "H4VA08224"),
    "G000568": ("Morgan Griffith",       "H", "H0VA09055"),
    "S001230": ("Suhas Subramanyam",     "H", "H4VA10279"),
    "W000831": ("James Walkinshaw",      "H", "H6VA11066"),
    "W000805": ("Mark Warner",           "S", "S6VA00093"),
    "K000384": ("Tim Kaine",             "S", "S2VA00142"),
}

# contributor_name substring -> industry (checked lowercase, first match wins)
INDUSTRY_MAP: list[tuple[str, str]] = [
    # ── Aerospace & Weapons (prime platform / hardware contractors) ───────────
    ("raytheon",                    "Aerospace & Weapons"),
    ("lockheed",                    "Aerospace & Weapons"),
    ("boeing",                      "Aerospace & Weapons"),
    ("northrop",                    "Aerospace & Weapons"),
    ("general dynamics",            "Aerospace & Weapons"),
    ("l3harris",                    "Aerospace & Weapons"),
    ("l3 tech",                     "Aerospace & Weapons"),
    ("bae systems",                 "Aerospace & Weapons"),
    ("huntington ingalls",          "Aerospace & Weapons"),
    ("textron",                     "Aerospace & Weapons"),
    ("general atomics",             "Aerospace & Weapons"),
    ("orbital sciences",            "Aerospace & Weapons"),
    ("titan aerospace",             "Aerospace & Weapons"),
    ("space exploration",           "Aerospace & Weapons"),
    ("spacex",                      "Aerospace & Weapons"),
    ("am general",                  "Aerospace & Weapons"),
    ("ball corporation",            "Aerospace & Weapons"),   # aerospace structures
    # ── Defense IT & Services (IT, intel, logistics, professional services) ────
    ("saic ",                       "Defense IT & Services"),
    ("leidos",                      "Defense IT & Services"),
    ("booz allen",                  "Defense IT & Services"),
    ("mantech",                     "Defense IT & Services"),
    ("caci ",                       "Defense IT & Services"),
    ("dxc tech",                    "Defense IT & Services"),
    ("dyncorp",                     "Defense IT & Services"),
    ("kratos defense",              "Defense IT & Services"),
    ("leonardo drs",                "Defense IT & Services"),
    ("amentum",                     "Defense IT & Services"),
    ("perspecta",                   "Defense IT & Services"),
    ("palantir",                    "Defense IT & Services"),
    ("harris corporation",          "Defense IT & Services"),
    ("serco ",                      "Defense IT & Services"),
    ("burns and mcdonnell",         "Defense IT & Services"),
    # ── Defense (catch-all) ───────────────────────────────────────────────────
    ("vigor pac",                   "Defense"),
    ("engineering and software systems", "Defense"),
    ("advanced concepts and technologies", "Defense"),
    # ── Health Insurance (payers / managed care) ──────────────────────────────
    ("aetna",                       "Health Insurance"),
    ("cigna",                       "Health Insurance"),
    ("humana",                      "Health Insurance"),
    ("unitedhealth",                "Health Insurance"),
    ("united health",               "Health Insurance"),
    ("cvs health",                  "Health Insurance"),   # Aetna parent / PBM
    # ── Pharma / Biotech / Medical Devices & Supply ───────────────────────────
    ("pfizer",                      "Pharma"),
    ("merck",                       "Pharma"),
    ("amgen",                       "Pharma"),
    ("abbott lab",                  "Pharma"),
    ("johnson & johnson",           "Pharma"),
    ("medtronic",                   "Pharma"),
    ("thermo fisher",               "Pharma"),
    ("owens & minor",               "Pharma"),   # medical supply distribution
    ("pharma",                      "Pharma"),
    ("biotech",                     "Pharma"),
    # ── Hospitals / Health Systems / Senior & Long-Term Care ──────────────────
    ("select medical",              "Hospitals"),   # hospital/rehab chain
    ("american healthcare",         "Hospitals"),   # healthcare facilities/REIT
    ("argentum",                    "Hospitals"),   # senior living industry
    ("hospital",                    "Hospitals"),
    # ── Health Professionals (clinician & provider associations) ──────────────
    ("ama pac",                     "Health Professionals"),
    ("american medical",            "Health Professionals"),
    ("american dental",             "Health Professionals"),
    ("american academy",            "Health Professionals"),
    ("american college of physician","Health Professionals"),
    ("american society of anesthesiol","Health Professionals"),
    ("ophthalmology",               "Health Professionals"),
    ("ophthpac",                    "Health Professionals"),
    ("neurology",                   "Health Professionals"),
    ("brainpac",                    "Health Professionals"),
    ("dermatology",                 "Health Professionals"),
    ("skinpac",                     "Health Professionals"),
    ("us oncology",                 "Health Professionals"),
    ("oncology",                    "Health Professionals"),
    ("acp services",                "Health Professionals"),
    ("medical association",         "Health Professionals"),
    ("nutrition and dietetics",     "Health Professionals"),
    ("orthodontist",                "Health Professionals"),
    ("orthodontics",                "Health Professionals"),
    ("urology",                     "Health Professionals"),
    ("psychiatric association",     "Health Professionals"),
    ("american psychiatric",        "Health Professionals"),
    ("health pac",                  "Health Professionals"),   # generic residual
    ("constellation brands",        "Alcohol/Tobacco"),  # Corona, Modelo, wine
    ("corning incorporated",        "Technology"),
    ("american staffing",           "Small Business"),
    ("council of engineering",      "Small Business"),
    ("acec",                        "Small Business"),
    ("celanese",                    "Manufacturing"),
    ("eastman chemical",            "Manufacturing"),
    ("eastmanpac",                  "Manufacturing"),
    # ── Fossil Fuels (extraction, refining, pipelines) ────────────────────────
    ("koch",                        "Fossil Fuels"),
    ("kochpac",                     "Fossil Fuels"),
    ("exxon",                       "Fossil Fuels"),
    ("chevron",                     "Fossil Fuels"),
    ("conocophillips",              "Fossil Fuels"),
    ("halliburton",                 "Fossil Fuels"),
    ("schlumberger",                "Fossil Fuels"),
    ("marathon oil",                "Fossil Fuels"),
    ("devon energy",                "Fossil Fuels"),
    ("pioneer natural",             "Fossil Fuels"),
    ("american petroleum",          "Fossil Fuels"),
    ("valero",                      "Fossil Fuels"),
    ("phillips 66",                 "Fossil Fuels"),
    ("bp america",                  "Fossil Fuels"),
    ("shell oil",                   "Fossil Fuels"),
    ("coal",                        "Fossil Fuels"),
    ("pipeline",                    "Fossil Fuels"),
    ("oil & gas",                   "Fossil Fuels"),
    ("oil and gas",                 "Fossil Fuels"),
    ("williams companies",          "Fossil Fuels"),
    ("williams pac",                "Fossil Fuels"),
    # ── Utilities (regulated electric, gas distribution, nuclear) ─────────────
    ("dominion energy",             "Utilities"),
    ("dominion resources",          "Utilities"),
    ("duke energy",                 "Utilities"),
    ("southern company",            "Utilities"),
    ("nuclear energy",              "Utilities"),
    ("american gas",                "Utilities"),
    ("natural gas",                 "Utilities"),
    ("electric utilities",          "Utilities"),
    ("firstenergy",                 "Utilities"),
    ("rural electric cooperative",  "Utilities"),
    ("national rural electric",     "Utilities"),
    # ── Renewables ────────────────────────────────────────────────────────────
    ("solar energy",                "Renewables"),
    ("wind energy",                 "Renewables"),
    ("clean energy",                "Renewables"),
    ("renewable energy",            "Renewables"),
    ("american clean power",        "Renewables"),
    # ── Investment & Securities ───────────────────────────────────────────────
    ("goldman sachs",               "Investment & Securities"),
    ("jpmorgan",                    "Investment & Securities"),
    ("jp morgan",                   "Investment & Securities"),
    ("morgan stanley",              "Investment & Securities"),
    ("blackrock",                   "Investment & Securities"),
    ("fidelity",                    "Investment & Securities"),
    ("charles schwab",              "Investment & Securities"),
    ("investment bankers",          "Investment & Securities"),
    ("securities",                  "Investment & Securities"),
    # ── Banking ───────────────────────────────────────────────────────────────
    ("wells fargo",                 "Banking"),
    ("bank of america",             "Banking"),
    ("citigroup",                   "Banking"),
    ("american express",            "Banking"),
    ("visa inc",                    "Banking"),
    ("mastercard",                  "Banking"),
    ("credit union",                "Banking"),
    ("culac",                       "Banking"),       # Credit Union Legislative Action
    ("amalgamated bank",            "Banking"),
    ("american bankers",            "Banking"),
    ("american financial services", "Banking"),
    ("farm credit",                 "Banking"),
    # ── Insurance ─────────────────────────────────────────────────────────────
    ("prudential",                  "Insurance"),
    ("insurance",                   "Insurance"),
    # ── Financial Services (accounting / advisory catch-all) ─────────────────
    ("deloitte",                    "Financial Services"),
    ("kpmg",                        "Financial Services"),
    ("ernst & young",               "Financial Services"),
    ("price waterhouse",            "Financial Services"),
    ("financial services",          "Financial Services"),
    ("s&p global",                  "Financial Services"),
    # ── Artificial Intelligence ───────────────────────────────────────────────
    ("palantir",                    "Artificial Intelligence"),
    ("openai",                      "Artificial Intelligence"),
    ("anthropic",                   "Artificial Intelligence"),
    ("nvidia",                      "Artificial Intelligence"),
    ("databricks",                  "Artificial Intelligence"),
    ("scale ai",                    "Artificial Intelligence"),
    ("cohere",                      "Artificial Intelligence"),
    ("c3.ai",                       "Artificial Intelligence"),
    # ── Crypto / Blockchain ───────────────────────────────────────────────────
    ("coinbase",                    "Crypto/Blockchain"),
    ("ripple",                      "Crypto/Blockchain"),
    ("blockchain association",      "Crypto/Blockchain"),
    ("crypto council",              "Crypto/Blockchain"),
    ("digital currency group",      "Crypto/Blockchain"),
    ("bitcoin",                     "Crypto/Blockchain"),
    ("crypto",                      "Crypto/Blockchain"),
    # ── Software & IT ─────────────────────────────────────────────────────────
    ("google",                      "Software & IT"),
    ("alphabet",                    "Software & IT"),
    ("amazon",                      "Software & IT"),
    ("microsoft",                   "Software & IT"),
    ("apple inc",                   "Software & IT"),
    ("meta platform",               "Software & IT"),
    ("facebook",                    "Software & IT"),
    ("intel corp",                  "Software & IT"),
    ("ibm corp",                    "Software & IT"),
    ("oracle",                      "Software & IT"),
    ("salesforce",                  "Software & IT"),
    ("semiconductor",               "Software & IT"),
    ("tech pac",                    "Software & IT"),
    ("innovation political action", "Software & IT"),
    ("cyber",                       "Software & IT"),
    # ── Guns / NRA ────────────────────────────────────────────────────────────
    ("nra",                         "Guns/NRA"),
    ("national rifle",              "Guns/NRA"),
    ("gun owners",                  "Guns/NRA"),
    ("safari club",                 "Guns/NRA"),
    ("sci-pac",                     "Guns/NRA"),
    # ── Healthcare Worker Unions ──────────────────────────────────────────────
    ("nurses",                      "Healthcare Worker Unions"),
    ("national nurses",             "Healthcare Worker Unions"),
    ("1199 seiu",                   "Healthcare Worker Unions"),
    # ── Education Unions ─────────────────────────────────────────────────────
    ("national education association","Education Unions"),
    ("nea fund",                    "Education Unions"),
    ("teachers",                    "Education Unions"),
    ("american federation of teachers","Education Unions"),
    ("aft cope",                    "Education Unions"),
    # ── Public Employee Unions ────────────────────────────────────────────────
    ("afscme",                      "Public Employee Unions"),
    ("national active and retired federal","Public Employee Unions"),
    ("narfe",                       "Public Employee Unions"),
    ("state county & municipal",    "Public Employee Unions"),
    ("municipal employees",         "Public Employee Unions"),
    ("firefighter",                 "Public Employee Unions"),
    ("fire fighters",               "Public Employee Unions"),
    ("postal worker",               "Public Employee Unions"),
    ("postal supervisors",          "Public Employee Unions"),
    ("letter carrier",              "Public Employee Unions"),
    # ── Building & Industrial Unions ──────────────────────────────────────────
    ("afl-cio",                     "Building & Industrial Unions"),
    ("teamster",                    "Building & Industrial Unions"),
    ("united auto",                 "Building & Industrial Unions"),
    ("united steel",                "Building & Industrial Unions"),
    ("ibew",                        "Building & Industrial Unions"),
    ("electrical workers",          "Building & Industrial Unions"),
    ("machinists",                  "Building & Industrial Unions"),
    ("machinists non-partisan",     "Building & Industrial Unions"),
    ("liuna",                       "Building & Industrial Unions"),
    ("laborers' international",     "Building & Industrial Unions"),
    ("boilermakers",                "Building & Industrial Unions"),
    ("sheet metal",                 "Building & Industrial Unions"),
    ("operating engineers",         "Building & Industrial Unions"),
    ("plumbers",                    "Building & Industrial Unions"),
    ("united association",          "Building & Industrial Unions"),   # plumbers & pipefitters
    ("carpenters",                  "Building & Industrial Unions"),
    ("painters union",              "Building & Industrial Unions"),
    ("bricklayers",                 "Building & Industrial Unions"),
    ("plasterers",                  "Building & Industrial Unions"),
    ("cement masons",               "Building & Industrial Unions"),
    ("iron workers",                "Building & Industrial Unions"),
    ("national electrical contractors","Building & Industrial Unions"),
    ("necapac",                     "Building & Industrial Unions"),
    ("stand up for workers",        "Building & Industrial Unions"),
    # ── Service & Retail Workers ──────────────────────────────────────────────
    ("seiu",                        "Service & Retail Workers"),
    ("ufcw",                        "Service & Retail Workers"),
    ("united food",                 "Service & Retail Workers"),
    ("service employees",           "Service & Retail Workers"),
    ("communications workers",      "Service & Retail Workers"),
    ("cwa cope",                    "Service & Retail Workers"),
    ("transport workers",           "Service & Retail Workers"),
    ("amalgamated transit",         "Service & Retail Workers"),
    ("unite here",                  "Service & Retail Workers"),
    ("writers guild",               "Service & Retail Workers"),
    # ── Agriculture sub-sectors ───────────────────────────────────────────────
    ("tobacco",                     "Tobacco"),
    ("altria",                      "Tobacco"),
    ("philip morris",               "Tobacco"),
    ("cattle",                      "Livestock & Poultry"),
    ("poultry",                     "Livestock & Poultry"),
    ("pork producers",              "Livestock & Poultry"),
    ("national cattlemen",          "Livestock & Poultry"),
    ("beef association",            "Livestock & Poultry"),
    ("dairy",                       "Livestock & Poultry"),
    ("farm bureau",                 "Agribusiness"),
    ("agri",                        "Agribusiness"),
    ("grain ",                      "Agribusiness"),
    ("cotton",                      "Agribusiness"),
    ("american crystal sugar",      "Agribusiness"),
    ("sugar",                       "Agribusiness"),
    ("soybean",                     "Agribusiness"),
    ("corn growers",                "Agribusiness"),
    ("national farmers",            "Agribusiness"),
    ("food solutions",              "Agribusiness"),
    ("deere & company",             "Agribusiness"),
    ("john deere",                  "Agribusiness"),
    ("forest & paper",              "Manufacturing"),
    ("american forest",             "Manufacturing"),
    # ── Real Estate sub-sectors ───────────────────────────────────────────────
    ("national association of realtors", "Realtors"),
    ("realtors pac",                "Realtors"),
    ("realtors political",          "Realtors"),
    ("homebuilders",                "Homebuilders"),
    ("home builders",               "Homebuilders"),
    ("buildpac",                    "Homebuilders"),
    ("nahb",                        "Homebuilders"),
    ("associated builders",         "Homebuilders"),
    ("abc pac",                     "Homebuilders"),
    ("multifamily housing",         "Commercial Real Estate"),
    ("national multifamily",        "Commercial Real Estate"),
    ("real estate roundtable",      "Commercial Real Estate"),
    ("realpac",                     "Commercial Real Estate"),
    ("real estate investment trust","Commercial Real Estate"),
    ("nareit",                      "Commercial Real Estate"),
    ("home depot",                  "Commercial Real Estate"),
    ("real estate",                 "Commercial Real Estate"),
    # ── Transportation / Logistics ────────────────────────────────────────────
    ("airline",                     "Transportation"),
    ("delta air",                   "Transportation"),
    ("american airlines",           "Transportation"),
    ("united airlines",             "Transportation"),
    ("southwest air",               "Transportation"),
    ("fedex",                       "Transportation"),
    ("ups ",                        "Transportation"),
    ("railroad",                    "Transportation"),
    ("trucking",                    "Transportation"),
    ("matson",                      "Transportation"),
    ("shipping",                    "Transportation"),
    ("maritime",                    "Transportation"),
    ("air transport",               "Transportation"),
    ("smart pac",                   "Transportation"),  # Sheet Metal, Air, Rail, Transportation
    ("norfolk southern",            "Transportation"),
    ("united parcel service",       "Transportation"),
    ("ups political",               "Transportation"),
    # ── Telecom / Cable / Media ───────────────────────────────────────────────
    ("comcast",                     "Telecom"),
    ("at&t",                        "Telecom"),
    ("verizon",                     "Telecom"),
    ("t-mobile",                    "Telecom"),
    ("charter comm",                "Telecom"),
    ("national cable",              "Telecom"),
    ("ncta",                        "Telecom"),
    ("cox enterprises",             "Telecom"),
    ("coxpac",                      "Telecom"),
    ("directv",                     "Telecom"),
    ("dish network",                "Telecom"),
    ("telecom",                     "Telecom"),
    # ── Alcohol / Tobacco ─────────────────────────────────────────────────────
    ("beer",                        "Alcohol/Tobacco"),
    ("wine",                        "Alcohol/Tobacco"),
    ("spirits",                     "Alcohol/Tobacco"),
    ("altria",                      "Alcohol/Tobacco"),
    ("philip morris",               "Alcohol/Tobacco"),
    ("reynolds american",           "Alcohol/Tobacco"),
    ("rai pac",                     "Alcohol/Tobacco"),
    ("distilled spirits",           "Alcohol/Tobacco"),
    ("national beer",               "Alcohol/Tobacco"),
    ("anheuser",                    "Alcohol/Tobacco"),
    # ── Automotive ────────────────────────────────────────────────────────────
    ("automobile dealers",          "Automotive"),
    ("auto dealers",                "Automotive"),
    ("nada ",                       "Automotive"),
    ("auto care",                   "Automotive"),
    ("ford motor",                  "Automotive"),
    ("general motors",              "Automotive"),
    ("toyota",                      "Automotive"),
    ("honda",                       "Automotive"),
    # ── Trial Lawyers / Plaintiffs Bar ───────────────────────────────────────
    ("trial lawyers",               "Trial Lawyers"),
    ("american association for justice","Trial Lawyers"),
    ("aaj pac",                     "Trial Lawyers"),
    ("virginia trial lawyers",      "Trial Lawyers"),
    ("plaintiff",                   "Trial Lawyers"),
    # ── Corporate Law ─────────────────────────────────────────────────────────
    ("holland & knight",            "Corporate Law"),
    ("covington",                   "Corporate Law"),
    ("kirkland",                    "Corporate Law"),
    ("sidley",                      "Corporate Law"),
    ("mcguirewoods",                "Corporate Law"),
    ("hunton",                      "Corporate Law"),
    ("williams mullen",             "Corporate Law"),
    ("troutman",                    "Corporate Law"),
    ("jones day",                   "Corporate Law"),
    ("skadden",                     "Corporate Law"),
    ("hogan lovells",               "Corporate Law"),
    ("latham",                      "Corporate Law"),
    # ── Legal (general) ───────────────────────────────────────────────────────
    ("law firm",                    "Legal"),
    ("attorneys",                   "Legal"),
    # ── Small Business / Chamber ──────────────────────────────────────────────
    ("chamber of commerce",         "Small Business"),
    ("small business",              "Small Business"),
    ("nfib",                        "Small Business"),
    # ── Gambling / Casinos ───────────────────────────────────────────────────
    ("american gaming association",  "Gambling/Casinos"),
    ("casino",                       "Gambling/Casinos"),
    ("caesars",                      "Gambling/Casinos"),
    ("hard rock hotel",              "Gambling/Casinos"),
    ("hard rock casino",             "Gambling/Casinos"),
    ("draftkings",                   "Gambling/Casinos"),
    ("fanduel",                      "Gambling/Casinos"),
    ("penn national gaming",         "Gambling/Casinos"),
    ("mgm resorts",                  "Gambling/Casinos"),
    ("wynn resorts",                 "Gambling/Casinos"),
    ("las vegas sands",              "Gambling/Casinos"),
    ("betmgm",                       "Gambling/Casinos"),
    ("harrah",                       "Gambling/Casinos"),
    ("horse racing",                 "Gambling/Casinos"),
    ("thoroughbred",                 "Gambling/Casinos"),
    ("gaming association",           "Gambling/Casinos"),
    # ── Hospitality / Retail ─────────────────────────────────────────────────
    ("hotel",                       "Hospitality"),
    ("restaurant",                  "Hospitality"),
    ("aahoa",                       "Hospitality"),
    ("asian american hotel",        "Hospitality"),
    ("national restaurant",         "Hospitality"),
    ("walmart",                     "Retail"),
    ("target corp",                 "Retail"),
    ("convenience stores",          "Retail"),
    ("retail",                      "Retail"),
    # ── Grassroots / Small-Donor Platforms ───────────────────────────────────
    ("actblue",                     "Grassroots"),
    ("winred",                      "Grassroots"),
    ("democracy engine",            "Grassroots"),
    ("anedot",                      "Grassroots"),
]

# Patterns that indicate internal/party transfers — skip entirely
_INTERNAL_PATTERNS = [
    "victory committee",
    "victory fund",
    "grow the majority",
    "joint fundraising",
    "majority committee",
    "majority pac",
    "reclaim america",
    "fund for america",
    "nrcc",
    "dccc",
    "rnc ",
    "dnc ",
    "republican national",
    "democratic national",
    "leadership pac",
    "leadership fund",
    "senate leadership",
    "house leadership",
    "house freedom fund",
    "freedom fund",
    "forward together",
    "pac to the future",
    "common ground pac",
    "electing majority",
    "elect democratic",
    "elect republican",
    "american revival pac",
    "congressional black caucus",
    "friends of ",
    "one virginia fund",
    "democratic party of",
    "republican party of",
    "state party",
    "waging peace",
    "impact pac",
    # Party mobilization / leadership PACs
    "protect the house",
    "take back the house",
    "senate impact",
    "save america",
    "huck pac",
    "eye of the tiger",
    "no labels",
    "association of fundraising",
    "humane society legislative",  # advocacy, not industry
    "american israel public affairs",  # foreign policy lobby
    "aipac",
    # Candidate self-transfers
    "for va-",
    "for congress",
    "for senate",
    "for house",
    # Payment processors / political vendors
    "election cfo",
    "bluewest media",
    "sage media",
    "aisle 518",
    "global exchange connection",
    "minuteman press",
    "omni homestead",
    # Individual name patterns (shouldn't appear with is_individual=false, but just in case)
    "buchanan, katherine",
    "good, john p.",
    "subramanyam, suhas",
    "one virginia pac",
    "warner action fund",
    "public opinion strategies",
    "cecchi, giuseppe",
    "automotive free international trade",  # trade policy PAC, not automotive industry
    # Joint fundraising committees (party money bundles, not industry PACs)
    "va victory",
    "virginia victory",
    "senate victory",
    "senate impact",
    "justice 2024",
    "blue senate",
    "dem senate",
    "house majority",
    "majority forward",
    "blue america",
    "unitemized",
    "bennet warner",
    "gop winning women",
    "freedom fall blue wave",
    "freedom fall",
    "blue wave fund",
    "winning women",
    # Leadership PACs
    "ameripac",
    "commonwealth pac",
    "fair shot pac",
    "blue hen",
    "jeff pac",
    "jobs, education",
    "fight the right",
    "democracy defense",
    "common good usa",
    # Vendors / digital fundraising firms
    "rising tide interactive",
    "targeted victory",
    "bully pulpit",
    "new blue interactive",
    # Community/ideological PACs (not classifiable by industry)
    "hindu american",
    "hindus of",
    "indian american impact",
    "americans4hindus",
    "democratic asian",
]


def _fec_get(path: str, params: dict, api_key: str, timeout: int = 20) -> dict | None:
    params = dict(params)
    params["api_key"] = api_key
    url = f"{FEC_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(3):
        try:
            data = urllib.request.urlopen(req, timeout=timeout).read()
            return json.loads(data)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = 15 * (attempt + 1)
                print(f"  Rate limited — waiting {wait}s (attempt {attempt+1}/3)")
                time.sleep(wait)
            else:
                print(f"  FEC request failed: {exc}")
                return None
        except Exception as exc:
            print(f"  FEC request failed: {exc}")
            return None
    return None


def get_fec_candidate_id(bioguide_id: str, office: str, api_key: str) -> str | None:
    # FEC candidate IDs are hardcoded in VA_MEMBERS; this is a fallback name search
    name = VA_MEMBERS.get(bioguide_id, ("", office, None))[0]
    if not name:
        return None
    last = name.split()[-1]
    data = _fec_get("/candidates/search/", {"q": last, "office": office, "state": "VA", "per_page": 5}, api_key)
    if not data or not data.get("results"):
        return None
    return data["results"][0].get("candidate_id")


def get_principal_committee(candidate_id: str, api_key: str, cycle: int) -> str | None:
    data = _fec_get(
        f"/candidate/{candidate_id}/committees/",
        {"designation": "P", "cycle": cycle},
        api_key,
    )
    if not data or not data.get("results"):
        # Fall back without cycle filter
        data = _fec_get(
            f"/candidate/{candidate_id}/committees/",
            {"designation": "P"},
            api_key,
        )
    if not data or not data.get("results"):
        return None
    results = sorted(data["results"], key=lambda r: r.get("cycles", [0])[-1] if r.get("cycles") else 0, reverse=True)
    return results[0].get("committee_id")


_PAC_LOOKUP: dict[str, str] = {}  # populated by load_pac_lookup()


def load_pac_lookup(db_path: str | Path) -> None:
    """Load pac_name_lookup table into memory for use during classification."""
    global _PAC_LOOKUP
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT pac_name, industry FROM pac_name_lookup").fetchall()
        conn.close()
        _PAC_LOOKUP = {r[0].lower(): r[1] for r in rows}
        if _PAC_LOOKUP:
            print(f"[lookup] Loaded {len(_PAC_LOOKUP)} PAC name classifications from DB")
    except Exception:
        _PAC_LOOKUP = {}


def classify_contributor(name: str) -> str:
    low = name.lower()
    # 1. keyword map (fast, curated)
    for substr, industry in INDUSTRY_MAP:
        if substr in low:
            return industry
    # 2. Gemini-enriched lookup table (exact name match)
    if low in _PAC_LOOKUP:
        return _PAC_LOOKUP[low]
    return "Other"


def is_internal_transfer(name: str) -> bool:
    low = name.lower()
    return any(p in low for p in _INTERNAL_PATTERNS)


def fetch_contributions(committee_id: str, api_key: str, cycle: int, max_pages: int = 15) -> list[dict]:
    """Fetch non-individual (PAC/org) schedule_a contributions, paginate up to max_pages.

    Uses is_individual=false to get PAC, corporate, and organizational contributions,
    excluding individual donors who don't indicate industry affiliation.
    """
    all_results: list[dict] = []
    params: dict = {
        "committee_id": committee_id,
        "two_year_transaction_period": cycle,
        "per_page": 100,
        "sort": "-contribution_receipt_amount",
        "is_individual": "false",
    }
    page = 0
    last_indexes: dict = {}

    while page < max_pages:
        p = dict(params)
        if last_indexes:
            p.update(last_indexes)
        data = _fec_get("/schedules/schedule_a/", p, api_key)
        if not data:
            break
        results = data.get("results", [])
        if not results:
            break
        all_results.extend(results)
        page += 1

        pagination = data.get("pagination", {})
        li = pagination.get("last_indexes")
        if not li:
            break
        last_indexes = {f"last_{k}": v for k, v in li.items() if v is not None}
        time.sleep(0.3)

    return all_results


def aggregate_by_industry(contributions: list[dict]) -> dict[str, dict]:
    """Sum contributions by industry, skip internal transfers. Deduplicates same-PAC multi-transaction entries."""
    totals: dict[str, dict] = {}
    for c in contributions:
        name = c.get("contributor_name") or ""
        amount = float(c.get("contribution_receipt_amount") or 0)
        if amount <= 0:
            continue
        if is_internal_transfer(name):
            continue
        industry = classify_contributor(name)
        if industry not in totals:
            totals[industry] = {"total": 0.0, "count": 0, "donor_map": {}}
        totals[industry]["total"] += amount
        totals[industry]["count"] += 1
        # Aggregate multiple transactions from the same PAC (keyed case-insensitively)
        key = name.upper()
        if key not in totals[industry]["donor_map"]:
            totals[industry]["donor_map"][key] = {"name": name, "amount": 0.0}
        totals[industry]["donor_map"][key]["amount"] += amount

    # Convert donor_map → sorted top-5 list
    result: dict[str, dict] = {}
    for industry, data in totals.items():
        top5 = sorted(data["donor_map"].values(), key=lambda d: d["amount"], reverse=True)[:5]
        result[industry] = {
            "total": data["total"],
            "count": data["count"],
            "donors": list(top5),
        }
    return result


def setup_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS fec_industry_totals (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            bioguide_id       TEXT NOT NULL,
            member_name       TEXT,
            cycle             INTEGER NOT NULL,
            industry          TEXT NOT NULL,
            total_amount      REAL NOT NULL,
            contributor_count INTEGER DEFAULT 0,
            top_donors        TEXT,
            fetched_at        TEXT NOT NULL,
            UNIQUE(bioguide_id, cycle, industry)
        );
        CREATE INDEX IF NOT EXISTS idx_fit_bioguide ON fec_industry_totals(bioguide_id);
    """)
    conn.commit()


def store_industry_totals(
    conn: sqlite3.Connection,
    bioguide_id: str,
    member_name: str,
    cycle: int,
    industry_totals: dict[str, dict],
    dry_run: bool = False,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for industry, data in industry_totals.items():
        if data["total"] < 500:
            continue  # skip tiny amounts
        top_donors_json = json.dumps(data["donors"])
        print(
            f"    {industry:20s}: ${data['total']:>10,.0f}  ({data['count']} contributions)"
        )
        if not dry_run:
            conn.execute(
                """INSERT INTO fec_industry_totals
                       (bioguide_id, member_name, cycle, industry, total_amount,
                        contributor_count, top_donors, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(bioguide_id, cycle, industry) DO UPDATE SET
                       total_amount=excluded.total_amount,
                       contributor_count=excluded.contributor_count,
                       top_donors=excluded.top_donors,
                       fetched_at=excluded.fetched_at""",
                (bioguide_id, member_name, cycle, industry,
                 data["total"], data["count"], top_donors_json, now),
            )
            count += 1
    if not dry_run:
        conn.commit()
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest FEC PAC/industry contributions for VA delegation")
    parser.add_argument("--cycle",    type=int, default=None,
                        help="Single FEC two-year election cycle (e.g. 2024). Omit to use --cycles.")
    parser.add_argument("--cycles",   type=int, nargs="+", default=[2020, 2022, 2024],
                        help="One or more cycles to ingest (default: 2020 2022 2024)")
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--member",   help="Limit to a single bioguide_id")
    parser.add_argument("--max-pages",type=int, default=15, help="Schedule_a pages per member per cycle")
    args = parser.parse_args(argv)

    cycles = [args.cycle] if args.cycle else args.cycles

    api_key = os.getenv("FEC_API_KEY", "DEMO_KEY")
    if api_key == "DEMO_KEY":
        print("Warning: using FEC DEMO_KEY (rate-limited to 40 req/hr). Set FEC_API_KEY env var.")

    conn = sqlite3.connect(DB_PATH)
    setup_db(conn)
    load_pac_lookup(DB_PATH)

    # Competitive races where WinRed/ActBlue dominate the top pages, pushing PAC money below
    # the default pagination limit. Double the page budget to capture industry contributions.
    _COMPETITIVE = {"K000399", "V000138", "S001230", "M001239"}  # Kiggans, Vindman, Subramanyam, McGuire

    members = {k: v for k, v in VA_MEMBERS.items() if not args.member or k == args.member}
    total_industries = 0

    for cycle in cycles:
        print(f"\n{'#'*60}")
        print(f"CYCLE {cycle}")
        print(f"{'#'*60}")

        # Cache committee IDs within a cycle to avoid redundant lookups
        committee_cache: dict[str, str] = {}

        for bioguide_id, (name, office, fec_cand_id) in members.items():
            print(f"\n{'='*60}")
            print(f"{name} ({bioguide_id}, {office}), cycle={cycle}")

            cand_id = fec_cand_id or get_fec_candidate_id(bioguide_id, office, api_key)
            if not cand_id:
                print(f"  No FEC candidate record found — skipping")
                continue
            print(f"  Candidate ID: {cand_id}")
            time.sleep(0.3)

            if cand_id in committee_cache:
                cmte_id = committee_cache[cand_id]
            else:
                cmte_id = get_principal_committee(cand_id, api_key, cycle)
                if cmte_id:
                    committee_cache[cand_id] = cmte_id
            if not cmte_id:
                print(f"  No principal committee found — skipping")
                continue
            print(f"  Committee ID: {cmte_id}")
            time.sleep(0.5)

            member_max_pages = args.max_pages * 2 if bioguide_id in _COMPETITIVE else args.max_pages
            if member_max_pages != args.max_pages:
                print(f"  Competitive race — using {member_max_pages} pages")
            contributions = fetch_contributions(cmte_id, api_key, cycle, max_pages=member_max_pages)
            print(f"  Fetched {len(contributions)} contribution records")

            industry_totals = aggregate_by_industry(contributions)
            print(f"  Industry breakdown ({len(industry_totals)} categories):")
            stored = store_industry_totals(conn, bioguide_id, name, cycle, industry_totals, dry_run=args.dry_run)
            total_industries += stored
            time.sleep(1.0)

    conn.close()
    verb = "would store" if args.dry_run else "stored/updated"
    print(f"\nDone. {verb} {total_industries} industry rows across {len(members)} members × {len(cycles)} cycles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
