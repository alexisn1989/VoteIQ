#!/usr/bin/env python3
"""
build_va_state_finance.py
Full Virginia SBE campaign finance pipeline.

Reads all finance_csv/<period>/ScheduleA.csv files,
classifies sectors, resolves contributor → legislator,
and writes:

  polls.db
    va_cf_schedule_a   — raw normalized contributions (all candidates)

  legislative_intelligence.db
    va_sbe_contributions — enriched, legislator-matched contributions
    donor_sector_totals  — aggregated by (legislator, sector, cycle)

Usage:
    python build_va_state_finance.py            # full rebuild
    python build_va_state_finance.py --since 2022  # only cycle >= 2022
    python build_va_state_finance.py --dry-run  # count rows, don't write
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    from rapidfuzz import process as rf_process, fuzz
    _RF = True
except ImportError:
    _RF = False

BASE_DIR    = Path(__file__).resolve().parent
DATA_DIR    = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
POLLS_DB    = DATA_DIR / "polls.db"
LEG_DB      = DATA_DIR / "legislative_intelligence.db"
FINANCE_DIR = DATA_DIR / "finance_csv"
DATA_DIR.mkdir(parents=True, exist_ok=True)

BATCH_SIZE  = 25_000

# ── Sector keyword classifier ─────────────────────────────────────────────────

# NOTE: The four healthcare sub-sectors below replace the former single
# "Healthcare" bucket. They are ordered Insurance -> Pharma -> Hospitals ->
# Professionals and MUST stay ahead of "Finance": health-specific insurer terms
# have to match before Finance's generic "insurance" keyword. Generic
# "health"/"medical"/"healthcare" with no clearer signal falls through to
# "Health Professionals" (the chosen residual bucket).
SECTOR_KEYWORDS: list[tuple[str, list[str]]] = [
    # ── Union early-exits ─────────────────────────────────────────────────────
    # Must come before the healthcare / education sector buckets so that union
    # *organisations* don't get classified by their industry keyword instead.
    ("Healthcare Worker Unions", ["nurses united", "nurses union", "nursing union",
                                  "nurses organizing", "healthcare workers union",
                                  "health care workers union", "seiu healthcare",
                                  "1199 seiu", "national nurses"]),
    ("Education Unions",         ["national education association", "nea fund",
                                  "american federation of teachers", "aft cope",
                                  "teachers union", "education union",
                                  "faculty union"]),
    # ── Wine & Spirits industry early-exit ───────────────────────────────────
    # Must come before Ideological so industry PACs (e.g. "Virginia Wine
    # Wholesalers PAC", "Virginia Beer Wholesalers Association PAC") are not
    # swept into Ideological by the generic "pac " keyword.
    # NOTE: generic "beverage assoc/wholesal/distribut" deliberately NOT used —
    # they conflate soft-drink bottlers and broadline food/vending distributors
    # (Pepsi, Coca-Cola, Virginia Beverage Association [a soft-drink PAC],
    # Atlantic Dominion Distributors) with the alcohol industry. Real alcohol
    # distributors are caught by wine/beer/spirits/alcohol terms or by the
    # explicit company names below.
    ("Wine & Spirits", ["wine wholesal", "wine distribut", "wine assoc",
                        "beer wholesal", "beer distribut", "beer assoc",
                        "spirits wholesal", "spirits distribut",
                        "malt beverage", "licensed beverage",
                        "alcohol wholesal", "alcohol distribut",
                        "alcohol beverage", "beverage alcohol", "alcoholic beverage",
                        "distilled spirits council", "beer institute",
                        "breakthru beverage", "republic national distrib",
                        "southern glazer", "reyes beverage"]),
    # ── Ideological early-exit ────────────────────────────────────────────────
    # Must come before Financial Services so "campaign fund", "pac", etc.
    # are not mis-classified by the generic "fund" keyword.
    ("Ideological",    ["campaign fund", "campaign committee", "campaign account",
                        "for governor", "for senate", "for delegate", "for house",
                        "for congress", "for president",
                        "political organization", "political action",
                        "political education", "political fund", "political committee",
                        "pac ", " pac", "p.a.c", "p.b.a pac",
                        "democrat", "republican", "libertarian", "green party",
                        "action fund", "victory fund", "caucus fund",
                        "leadership fund", "senate fund", "house fund",
                        "victory committee", "caucus committee",
                        "leadership committee", "inaugural committee",
                        "clean virginia", "democracy engine", "fundraising platform",
                        "association fund", "employee association",
                        "george soros", "soros fund",
                        "dga action", "dga ", " dga", "rga ", " rga",
                        "dccc", "dscc", "nrcc", "nrsc", "dlcc", "rlc ",
                        "governors association", "legislative campaign committee",
                        "common good virginia", "common good va",
                        "everytown", "gun safety", "gun violence",
                        "people for the american way", "planned parenthood action",
                        "naral", "nra political",
                        "advocacy", "civic action", "civic fund"]),
    # ── Healthcare sub-sectors ────────────────────────────────────────────────
    ("Health Insurance",   ["health insur", "health plan", "healthplan",
                            "managed care", " hmo", "medicare advantage",
                            "anthem", "aetna", "cigna", "humana", "unitedhealth",
                            "united health", "kaiser perman", "wellpoint",
                            "elevance", "centene", "molina health", "optum",
                            "carefirst"]),
    ("Pharma",             ["pharmaceutic", "pharma inc", "pharma llc", "pharma,",
                            "biotech", "biopharm", "drug manufactur", "drugmaker",
                            "therapeutics", "life science", "medical device",
                            "med device", "medical supply", "medtronic", "pfizer",
                            "merck", "amgen", "abbvie", "genentech", "novartis",
                            "astrazeneca", "gilead", "biogen"]),
    ("Hospitals",          ["hospital", "health system", "healthcare system", "livongo",
                            "health care system", "medical center", "med center",
                            "health center", "clinic", "nursing home",
                            "skilled nursing", "senior living", "assisted living",
                            "long-term care", "long term care", "home health",
                            "hospice", "sentara", "bon secours", "inova",
                            "vcu health", "carilion", "riverside health",
                            "valley health", "centra health", "ballad health",
                            "uva health", "hca healthcare"]),
    ("Health Professionals", ["physician", "doctor", "nurse", "nursing",
                            "dentist", "dental", "orthodont", "surgeon",
                            "surgery", "therapist", "therapy", "psychiatr",
                            "psycholog", "optom", "ophthalmolog", "veterinar",
                            "chiropract", "radiol", "oncol", "cardiolog",
                            "pediatric", "orthopedic", "obstetric", "gynecolog",
                            "podiatr", "dermatolog", "neurolog", "anesthesiol",
                            "patholog", "urolog", "pharmacist", "pharmacy",
                            "medical practice", "medical group", "medical associat",
                            "practitioner", "health", "medical", "healthcare",
                            "health care", "medicine", "clinical"]),
    # ── Legal sub-sectors ────────────────────────────────────────────────────
    ("Trial Lawyers",  ["trial lawyer", "trial attorney", "plaintiff attorney",
                        "plaintiffs attorney", "plaintiff lawyer", "personal injury",
                        "american association for justice", "aaj ",
                        "virginia trial lawyers", "vtla",
                        "allen & allen", "marks & harrison", "tronfeld",
                        "cochran firm", "mass tort", "class action",
                        "injury attorney", "accident attorney", "wrongful death"]),
    ("Corporate Law",  ["mcguirewoods", "mcguire woods", "hunton", "williams mullen",
                        "troutman", "reed smith", "leclairryan", "gentry locke",
                        "hogan lovells", "skadden", "jones day", "kirkland",
                        "gibson dunn", "latham ", "cooley llp", "wilmer",
                        "covington", "sidley", "holland & knight",
                        "corporate counsel", "general counsel",
                        "corporate law", "mergers and acquisitions",
                        "intellectual property attorney", "patent attorney",
                        "securities attorney", "tax attorney"]),
    ("Legal",          ["attorney", "lawyer", "law firm", "law office", "counsel",
                        "solicitor", "paralegal", "esquire", "barrister", "litigation"]),
    # ── Real Estate sub-sectors ───────────────────────────────────────────────
    ("Realtors",              ["realtor", "realty", "realtors assoc",
                               "century 21", "keller williams", "long & foster",
                               "coldwell banker", "re/max", "sotheby",
                               "real estate agent", "real estate broker"]),
    ("Homebuilders",          ["homebuil", "home builder", "home builders",
                               "residential builder", "virginia shelter",
                               "nvr corp", "ryan homes", "k. hovnanian",
                               "toll brothers", "lennar", "dr horton", "pulte"]),
    ("Commercial Real Estate", ["real estate", "propert", "developer",
                                "development", "construction", "contractor",
                                "builder", "architect", "land", "housing",
                                "appraiser", "apartment", "reit", "multifamily",
                                "commercial real", "asphalt", "paving",
                                "comstock", "rlj compan"]),
    # ── Energy early-exits ────────────────────────────────────────────────────
    # Must come before Financial Services so "solar trading", "energy capital",
    # etc. don't get mis-classified by generic finance keywords.
    ("Renewables",  ["solar", "wind energy", "wind farm", "wind turbine",
                     "renewable", "clean energy", "clean power", "green energy",
                     "photovoltaic", "geothermal", "biomass", "hydroelectric",
                     "hydrogen energy", "clean tech", "american clean power",
                     "sun tribe"]),
    ("Utilities",   ["dominion energy", "dominion virginia", "dominion resources",
                     "appalachian power", "columbia gas", "washington gas",
                     "virginia natural gas", "duke energy", "nrg energy",
                     "southern company", "firstenergy", "xcel energy",
                     "electric utility", "electric company", "electric & gas",
                     "power company", "power & light", "gas distribut",
                     "gas company", "gas & electric", "nuclear"]),
    # ── Finance sub-sectors (Insurance first so health-specific insurers are
    # already caught above; Banking/Investment split before generic catch-all) ──
    ("Insurance",           ["insur", "reinsur", "underwriting", "allstate",
                             "state farm", "nationwide", "liberty mutual",
                             "progressive ins", "travelers ins", "metlife",
                             "unum", "lincoln national", "lincoln financial",
                             "ameritas", "erie ins", "guardian life", "kinsale"]),
    ("Investment & Securities", ["hedge fund", "private equity", "asset management",
                             "asset mgmt", "investment management", "wealth management",
                             "venture capital", "investment bank", "investment advisor",
                             "investment firm", "investment group", "brokerage",
                             "fund management", "portfolio management", "securities",
                             "paloma partners", "bluestream", "founders fund",
                             "level one partners", "capfi partners",
                             "rdv corp", "yucaipa", "ivor and co"]),
    ("Banking",             ["bank", "credit union", "savings institution", "thrift",
                             "federal reserve", "fdic"]),
    ("Financial Services",  ["financial", "investment", "accountant", "cpa", "auditor",
                             "lender", "capital", "wealth", "broker",
                             "mortgage", "lending", "trading", "financi", "octus"]),
    # ── AI / Crypto early-exits (before Software & IT / Financial Services) ─────
    ("Artificial Intelligence", ["artificial intelligence", "openai", "anthropic inc",
                                  "deepmind", "palantir", "machine learning",
                                  "generative ai", "large language model", " llm ",
                                  "scale ai", "databricks", "c3.ai", "cohere",
                                  "hugging face", "inflection ai", "mistral ai",
                                  "nvidia corp", "nvidia inc", "ai research",
                                  "ai lab ", "ai company"]),
    ("Crypto/Blockchain",        ["crypto", "bitcoin", "coinbase", "blockchain.com",
                                  "ethereum", "digital asset", "defi labs", "web3 ",
                                  "binance", "kraken digital", "gemini trust",
                                  "ripple labs", "solana labs", "polygon labs",
                                  "chainlink", "cryptocurrency", "virtual currency",
                                  "ftx trading", "coin savage", "virginia blockchain",
                                  "digital currency"]),
    # ── Technology sub-sectors (Telecom first; Software & IT; generic catch-all) ─
    ("Telecom",             ["telecom", "telecommunications", "wireless carrier",
                             "broadband", "internet provider", "cable provider",
                             "cox communic", "at&t", "verizon", "comcast",
                             "t-mobile", "sprint", "charter comm", "spectrum comm",
                             "centurylink", "lumen tech", "frontier comm", "directv",
                             "dish network", "cincinnati bell"]),
    ("Software & IT",       ["software", "information technology",
                             "it services", "it consulting", "it solutions",
                             "it support", "it company", "it firm",
                             "computer", "digital", "cloud", "saas", "cybersecurity",
                             "programmer", "google", "microsoft", "amazon web",
                             "apple inc", "meta platform", "salesforce",
                             "oracle corp", "ibm ", "cisco sys", "bloomberg"]),
    ("IT & Engineering",    ["tech", "engineer", "developer", "network", "data"]),
    # ── Energy sub-sectors (Renewables, then Utilities, then Fossil Fuels
    # as catch-all — "dominion energy" catches before generic "energy") ─────────
    ("Renewables",          ["solar", "wind energy", "wind farm", "wind turbine",
                             "renewable", "clean energy", "clean power",
                             "green energy", "photovoltaic", "geothermal",
                             "biomass", "hydroelectric", "hydrogen energy",
                             "clean tech", "american clean power"]),
    ("Utilities",           ["utility", "utilities", "electric utility",
                             "electric company", " electric co", "electric & gas",
                             "power company", "power & light", "light company",
                             "gas distribut", "gas company", "gas & electric",
                             " power", "power monitors", "dominion energy",
                             "dominion virginia", "dominion resources",
                             "appalachian power", "columbia gas", "washington gas",
                             "virginia natural gas", "duke energy", "nrg energy",
                             "southern company", "firstenergy", "xcel energy",
                             "nuclear plant", "nuclear station", "nuclear power",
                             "nuclear energy", "nuclear"]),
    # NOTE: bare "electric" removed — it swept up electrical contractors and the
    # IBEW union into Fossil Fuels. Contractors now fall to "contractor"
    # (Commercial Real Estate); IBEW to "electrical workers" (Building unions).
    ("Fossil Fuels",        ["oil", "gas", "petroleum", "coal", "refin", "pipeline",
                             "drill", "frack", "coal mining", "coal mine", "coalfield",
                             "upstream", "crownquest", "crown quest", "holtzman corp",
                             "energy"]),
    # ── Agriculture sub-sectors ──────────────────────────────────────────────
    ("Tobacco",             ["tobacco", "altria", "philip morris", "pmi ",
                             "universal leaf", "cigar", "cigarette",
                             "us smokeless", "american snuff",
                             "reynolds american", "rai services", "lorillard",
                             "british american tobacco", "bat usa"]),
    ("Wine & Spirits",      ["wine", "winery", "vineyard", "distiller",
                             "craft brew", "brewery", "meadery", "cider",
                             "wine wholesaler", "wine assoc",
                             "spirits", "liquor", "whiskey", "bourbon",
                             "beer wholesal", "beer distribut", "malt beverage"]),
    ("Livestock & Poultry", ["livestock", "cattle", "poultry", "dairy",
                             "hog", "swine", "beef", "equine", "horse",
                             "mountaire", "perdue", "smithfield food",
                             "tyson food", "pilgrim"]),
    ("Agribusiness",        ["farm", "agricultur", "farmer", "crop",
                             "grain", "soy", "corn ", "wheat", "seed",
                             "fertilizer", "harvest", "cooperative",
                             "cotton", "sugar cane", "forestry", "timber",
                             "rancher", "agronomist", "horticultur",
                             "peanut", "nursery"]),
    ("Education",      ["teacher", "professor", "school", "universit", "college",
                        "educat", "principal", "librarian", "tutor", "academic",
                        "faculty", "superintendent"]),
    ("Transportation", ["trucking", "logistics", "transport", "airline", "rail",
                        "shipping", "driver", "pilot", "fleet", "freight",
                        "maritime"]),
    # ── Labor sub-sectors ────────────────────────────────────────────────────
    # Note: Healthcare Worker Unions + Education Unions are caught above
    ("Public Employee Unions",    ["afscme", "afge",
                                   "american federation of state",
                                   "american federation of government",
                                   "government employees", "state employees",
                                   "municipal employees", "public employees",
                                   "firefighters union", "fire fighters union",
                                   "police union", "postal workers",
                                   "letter carriers", "national postal"]),
    ("Building & Industrial Unions", ["uaw", "ibew", "iatse", "teamster",
                                   "sheet metal workers", "boilermakers",
                                   "ironworkers", "iron workers", "machinists",
                                   "liuna", "laborers international",
                                   "laborers' international", "plumbers union",
                                   "carpenters union", "operating engineers",
                                   "painters union", "bricklayers", "plasterers",
                                   "cement masons", "afl-cio", "building trades",
                                   "united auto workers", "united steelworkers",
                                   "electrical workers"]),
    ("Service & Retail Workers",  ["seiu", "ufcw", "unite here",
                                   "communications workers", "cwa cope", "cwa ",
                                   "seafarers", "transport workers",
                                   "amalgamated transit", "writers guild",
                                   "labor council", "service employees",
                                   "retail workers", "united food"]),
    # No catch-all — "Building & Industrial Unions" is the residual in apportion()
    ("Defense",        ["defense", "military", "veteran", "aerospace", "weapon",
                        "contractor dod", "army", "navy", "marine", "air force"]),
    # ── Gambling / Casinos early-exit ─────────────────────────────────────────
    ("Gambling/Casinos", ["casino", "gambling", "gaming", "lottery", "sportsbook",
                          "sports betting", "draftkings", "fanduel",
                          "caesars", "hard rock hotel", "hard rock casino",
                          "penn national", "betmgm", "harrah", "wynn resort",
                          "las vegas sands", "american gaming assoc",
                          "rush street gaming", "pamunkey",
                          "horse racing", "racetrack", "slot machine"]),
    ("Hospitality",    ["hotel", "restaurant", "hospitality", "bar ", "tavern",
                        "brewery", "winery", "catering", "tourism", "resort",
                        "motel"]),
    ("Manufacturing",  ["manufactur", "factory", "industrial", "plant",
                        "assembly", "production", "machinery", "fabricat",
                        "foundry", "mill", "newmarket corp", "westrock"]),
    ("Retail",         ["retail", "store", "shop ", "merchant", "dealer",
                        "wholesale"]),
    ("Individual/Other", []),  # catch-all — must be last
]


# Utility and energy PAC names that would otherwise be mis-classified as
# "Ideological" because the generic " pac" / "pac " keyword in SECTOR_KEYWORDS
# catches them before the Utilities entry is reached.
_UTILITY_PAC_OVERRIDES = re.compile(
    r'dominion\s+(energy|power|virginia|resources|enery|engery|political)'
    r'|appalachian\s+power'
    r'|columbia\s+gas'
    r'|washington\s+gas'
    r'|american\s+electric\s+power'
    r'|\baep\b(\s+pac|\s+political)?'
    r'|old\s+dominion\s+power',
    re.IGNORECASE,
)


# Short single-token keywords that must match on a WORD BOUNDARY, not as a raw
# substring, otherwise they collide with unrelated words and surnames:
#   "mill"  in "Miller"          → Manufacturing (wrong)
#   "land"  in "Cumberland"      → Commercial Real Estate (wrong; it's a coal co)
#   "coal"  in "coalition"       → Fossil Fuels (wrong; advocacy orgs)
#   "gas"   in "Biagas"/"Dragas" → Fossil Fuels (wrong; surnames)
#   "oil"   in "boilermakers"    → Fossil Fuels (wrong; union)
#   "rail"  in "trail"/"Railey"  → Transportation (wrong)
#   "corn " in "popcorn"/"acorn" → Agribusiness (wrong)
#   "plant" in "plantation"      → Manufacturing (wrong)
# The dict key is the exact keyword string as it appears in SECTOR_KEYWORDS; the
# value is a compiled pattern that allows common legitimate suffixes (mills,
# milling, railroad, coalfield, …) but requires a left word boundary.
_BOUNDARY_KEYWORDS = {
    "mill":  re.compile(r"\bmill(s|ing|work|stone|wright)?\b", re.I),
    "land":  re.compile(r"\bland\b", re.I),
    "coal":  re.compile(r"\bcoal(s|field|fields)?\b", re.I),
    "gas":   re.compile(r"\bgas(es|oline)?\b", re.I),
    "oil":   re.compile(r"\boil(s|field|fields)?\b", re.I),
    "rail":  re.compile(r"\brail(road|roads|way|ways|car|cars)?\b", re.I),
    "corn ": re.compile(r"\bcorn\b", re.I),
    "plant": re.compile(r"\bplant(s|ing)?\b", re.I),
    "horse": re.compile(r"\bhorse(s|back)?\b", re.I),
    "beef":  re.compile(r"\bbeef\b", re.I),
    "seed":  re.compile(r"\bseed(s|ling|lings)?\b", re.I),
    "wine":  re.compile(r"\bwine(s|ry|ries)?\b", re.I),
}


def _kw_match(keyword: str, text: str) -> bool:
    """True if keyword matches text. Boundary-listed keywords use a word-boundary
    regex (so 'mill' won't match 'Miller'); all others use substring matching to
    preserve intentional stems like 'insur', 'pharmaceutic', 'agricultur'."""
    pat = _BOUNDARY_KEYWORDS.get(keyword)
    if pat is not None:
        return pat.search(text) is not None
    return keyword in text


def classify_sector(occupation: str, employer: str, company: str = "") -> str:
    # Named utility/energy PAC override — must precede ALL keyword scans because
    # the Ideological " pac" / "pac " substring match otherwise captures
    # "Dominion Energy PAC", "AEP PAC", "Appalachian Power PAC", etc. before
    # the Utilities entry is reached.
    if company and _UTILITY_PAC_OVERRIDES.search(company.strip()):
        return "Utilities"

    occ_lower = occupation.strip().lower()
    # VPAP uses these occupation values for political/committee entities
    if occ_lower in ("political", "candidate committee", "political campaign", "campaign"):
        return "Ideological"
    # VPAP uses "Labor Union" / "Labor" as occupation for union-affiliated donors
    if occ_lower in ("labor union", "labor"):
        return "Service & Retail Workers"
    # "Gaming" as occupation = casino/gambling industry worker
    if occ_lower == "gaming":
        return "Gambling/Casinos"
    # "Investor" as occupation = financial services
    if occ_lower in ("investor", "investments"):
        return "Financial Services"
    # For corporate donors, match company name alone first.
    # Prevents a generic occupation (e.g. "PAC", "Financial Services")
    # from overriding a known corporate identity (e.g. "Altria" → Tobacco).
    if company:
        co = company.strip().lower()
        for sector, keywords in SECTOR_KEYWORDS[:-1]:
            if any(_kw_match(k, co) for k in keywords):
                return sector

    combined = f"{occupation} {employer} {company}".lower()
    for sector, keywords in SECTOR_KEYWORDS[:-1]:    # skip catch-all
        if any(_kw_match(k, combined) for k in keywords):
            return sector
    return "Individual/Other"


# ── Virginia donor tier thresholds ───────────────────────────────────────────

VA_THRESHOLDS = {
    "mega_donor":          10_000,
    "major_institutional":  1_000,
    "mixed":                  200,
    "grassroots":               0,
}


def donor_tier(amount: float) -> str:
    if amount >= VA_THRESHOLDS["mega_donor"]:
        return "Mega Donor"
    if amount >= VA_THRESHOLDS["major_institutional"]:
        return "Major Institutional"
    if amount >= VA_THRESHOLDS["mixed"]:
        return "Large Donor"
    return "Grassroots"


# ── Date normalization ────────────────────────────────────────────────────────

_DATE_FMTS = ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%Y/%m/%d")


def to_iso_date(raw: str) -> str:
    """Convert any common date string to YYYY-MM-DD. Return '' on failure."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw     # leave as-is if nothing matched


# ── Name helpers ──────────────────────────────────────────────────────────────

_PREFIXES = re.compile(
    r"^(mr\.?|mrs\.?|ms\.?|dr\.?|hon\.?|rev\.?|judge|senator|delegate|del\.?)\s+",
    re.I,
)

def clean_candidate_name(raw: str) -> str:
    name = _PREFIXES.sub("", raw.strip())
    return re.sub(r"\s+", " ", name).strip()


def build_name_map(leg_conn: sqlite3.Connection) -> dict[str, str]:
    """Return {normalised_name: lis_id} for all legislators."""
    rows = leg_conn.execute("SELECT member_id, member_name FROM members").fetchall()
    return {clean_candidate_name(r[1]).lower(): r[0] for r in rows}


def fuzzy_resolve(cleaned: str,
                  name_map: dict[str, str],
                  names_list: list[str],
                  threshold: int = 78) -> str | None:
    if not _RF or not cleaned:
        return None
    result = rf_process.extractOne(
        cleaned, names_list,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=threshold,
    )
    if result:
        return name_map.get(result[0])
    return None


# ── CSV reader ────────────────────────────────────────────────────────────────

def read_csv(path: Path):
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with path.open(encoding=enc, errors="replace", newline="") as f:
                reader = csv.DictReader(f)
                yield from reader
            return
        except UnicodeDecodeError:
            continue


# ── Schema setup ──────────────────────────────────────────────────────────────

def setup_polls_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        DROP TABLE IF EXISTS va_cf_schedule_a;
        CREATE TABLE va_cf_schedule_a (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            report_uid           TEXT,
            schedule_a_id        TEXT,
            committee_contact_id TEXT,
            first_name           TEXT,
            last_or_company      TEXT,
            employer             TEXT,
            occupation           TEXT,
            city                 TEXT,
            state_code           TEXT,
            zip_code             TEXT,
            is_individual        INTEGER,
            transaction_date     TEXT,
            amount               REAL,
            total_to_date        REAL,
            source_period        TEXT,
            candidate_name       TEXT,
            election_cycle       TEXT
        );
        CREATE INDEX idx_sca_report  ON va_cf_schedule_a(report_uid);
        CREATE INDEX idx_sca_cycle   ON va_cf_schedule_a(election_cycle);
        CREATE INDEX idx_sca_amount  ON va_cf_schedule_a(amount);
    """)
    conn.commit()


def setup_leg_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        DROP TABLE IF EXISTS va_sbe_contributions;
        CREATE TABLE va_sbe_contributions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            legislator_id    TEXT,
            candidate_name   TEXT,
            cycle            TEXT,
            contributor_name TEXT,
            employer         TEXT,
            occupation       TEXT,
            amount           REAL,
            transaction_date TEXT,
            city             TEXT,
            state            TEXT,
            is_individual    INTEGER,
            sector           TEXT,
            donor_tier       TEXT
        );
        CREATE INDEX idx_sbc_leg   ON va_sbe_contributions(legislator_id);
        CREATE INDEX idx_sbc_cycle ON va_sbe_contributions(cycle);
        CREATE INDEX idx_sbc_date  ON va_sbe_contributions(transaction_date);

        DROP TABLE IF EXISTS donor_sector_totals;
        CREATE TABLE donor_sector_totals (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            legislator_id TEXT,
            sector        TEXT,
            total_amount  REAL,
            donor_count   INTEGER,
            cycle         TEXT,
            UNIQUE(legislator_id, sector, cycle)
        );
        CREATE INDEX idx_dst_leg ON donor_sector_totals(legislator_id);
    """)
    conn.commit()


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run(since_cycle: int | None = None, dry_run: bool = False) -> None:
    polls  = sqlite3.connect(str(POLLS_DB),  timeout=30)
    leg    = sqlite3.connect(str(LEG_DB),    timeout=30)
    for c in (polls, leg):
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")

    if not dry_run:
        print("Setting up schemas …")
        setup_polls_db(polls)
        setup_leg_db(leg)

    # Build name resolution index
    name_map   = build_name_map(leg)
    names_list = list(name_map.keys())
    print(f"Loaded {len(name_map):,} legislator names for matching")

    # Candidate resolution cache (raw name → lis_id or None)
    cache: dict[str, str | None] = {}

    def resolve(raw: str) -> str | None:
        if raw in cache:
            return cache[raw]
        cleaned = clean_candidate_name(raw).lower()
        lis_id  = name_map.get(cleaned) or fuzzy_resolve(cleaned, name_map, names_list)
        cache[raw] = lis_id
        return lis_id

    periods = sorted(p.name for p in FINANCE_DIR.iterdir() if p.is_dir())
    print(f"Scanning {len(periods)} period folders …\n")

    raw_batch: list = []
    enr_batch: list = []
    total_raw = total_enr = 0
    skipped   = 0

    for period in periods:
        year_str = period[:4]
        try:
            year = int(year_str)
        except ValueError:
            continue
        if since_cycle and year < since_cycle:
            continue

        report_path = FINANCE_DIR / period / "Report.csv"
        sched_path  = FINANCE_DIR / period / "ScheduleA.csv"
        if not report_path.exists() or not sched_path.exists():
            continue

        # Build ReportUID → (lis_id, candidate_name, cycle) for this period
        report_map: dict[str, tuple[str | None, str, str]] = {}
        for row in read_csv(report_path):
            uid   = (row.get("ReportUID") or "").strip()
            cname = (row.get("CandidateName") or "").strip()
            ryear = (row.get("ReportYear") or year_str).strip() or year_str
            if uid and cname:
                report_map[uid] = (resolve(cname), cname, ryear)

        if not report_map:
            continue

        period_raw = period_enr = 0

        for row in read_csv(sched_path):
            uid = (row.get("ReportUID") or "").strip()
            if uid not in report_map:
                skipped += 1
                continue

            # Parse amount
            try:
                amount = float(
                    (row.get("Amount") or "0")
                    .replace("$", "").replace(",", "").strip()
                )
            except ValueError:
                continue
            if amount <= 0:
                continue

            lis_id, cand_name, cycle = report_map[uid]

            is_individual = (
                row.get("IsIndividual", "True").strip().lower() == "true"
            )
            employer      = (row.get("NameOfEmployer") or "").strip()
            occupation    = (row.get("OccupationOrTypeOfBusiness") or "").strip()
            first         = (row.get("FirstName") or "").strip()
            last_company  = (row.get("LastOrCompanyName") or "").strip()
            contrib_name  = (
                f"{first} {last_company}".strip() if is_individual
                else last_company
            )
            city          = (row.get("City") or "").strip()
            state_code    = (row.get("StateCode") or "").strip()
            zip_code      = (row.get("ZipCode") or "").strip()
            raw_date      = (row.get("TransactionDate") or "").strip()
            iso_date      = to_iso_date(raw_date)
            sched_id      = (row.get("ScheduleAId") or "").strip()
            cc_id         = (row.get("CommitteeContactId") or "").strip()
            total_to_date = float(
                (row.get("TotalToDate") or "0")
                .replace("$", "").replace(",", "").strip() or "0"
            )

            # Raw row → polls.db
            raw_batch.append((
                uid, sched_id, cc_id,
                first, last_company,
                employer, occupation,
                city, state_code, zip_code,
                1 if is_individual else 0,
                iso_date, amount, total_to_date,
                period, cand_name, cycle,
            ))
            period_raw += 1

            # Enriched row → legislative_intelligence.db (legislator-matched only)
            if lis_id:
                sector = classify_sector(
                    occupation, employer,
                    last_company if not is_individual else "",
                )
                tier   = donor_tier(amount)
                enr_batch.append((
                    lis_id, cand_name, cycle,
                    contrib_name, employer, occupation,
                    amount, iso_date,
                    city, state_code,
                    1 if is_individual else 0,
                    sector, tier,
                ))
                period_enr += 1

            # Flush batches
            if len(raw_batch) >= BATCH_SIZE:
                if not dry_run:
                    polls.executemany(
                        """INSERT INTO va_cf_schedule_a
                           (report_uid, schedule_a_id, committee_contact_id,
                            first_name, last_or_company, employer, occupation,
                            city, state_code, zip_code, is_individual,
                            transaction_date, amount, total_to_date,
                            source_period, candidate_name, election_cycle)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        raw_batch,
                    )
                    polls.commit()
                total_raw += len(raw_batch)
                raw_batch = []

            if len(enr_batch) >= BATCH_SIZE:
                if not dry_run:
                    leg.executemany(
                        """INSERT INTO va_sbe_contributions
                           (legislator_id, candidate_name, cycle,
                            contributor_name, employer, occupation,
                            amount, transaction_date, city, state,
                            is_individual, sector, donor_tier)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        enr_batch,
                    )
                    leg.commit()
                total_enr += len(enr_batch)
                enr_batch = []

        if period_raw:
            print(f"  {period}: {period_raw:>6,} raw  {period_enr:>6,} matched")

    # Flush remainders
    if raw_batch:
        if not dry_run:
            polls.executemany(
                """INSERT INTO va_cf_schedule_a
                   (report_uid, schedule_a_id, committee_contact_id,
                    first_name, last_or_company, employer, occupation,
                    city, state_code, zip_code, is_individual,
                    transaction_date, amount, total_to_date,
                    source_period, candidate_name, election_cycle)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                raw_batch,
            )
            polls.commit()
        total_raw += len(raw_batch)

    if enr_batch:
        if not dry_run:
            leg.executemany(
                """INSERT INTO va_sbe_contributions
                   (legislator_id, candidate_name, cycle,
                    contributor_name, employer, occupation,
                    amount, transaction_date, city, state,
                    is_individual, sector, donor_tier)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                enr_batch,
            )
            leg.commit()
        total_enr += len(enr_batch)

    print(f"\nva_cf_schedule_a:     {total_raw:>10,} rows  (polls.db)")
    print(f"va_sbe_contributions:  {total_enr:>10,} rows  (legislative_intelligence.db)")
    print(f"Skipped (no report):  {skipped:>10,} rows")

    # Aggregate donor_sector_totals
    if not dry_run:
        print("\nAggregating donor_sector_totals …")
        leg.execute("""
            INSERT INTO donor_sector_totals
                (legislator_id, sector, total_amount, donor_count, cycle)
            SELECT
                legislator_id,
                sector,
                ROUND(SUM(amount), 2),
                COUNT(*),
                cycle
            FROM va_sbe_contributions
            GROUP BY legislator_id, sector, cycle
            ON CONFLICT(legislator_id, sector, cycle)
            DO UPDATE SET
                total_amount = excluded.total_amount,
                donor_count  = excluded.donor_count
        """)
        leg.commit()
        dst_count = leg.execute("SELECT COUNT(*) FROM donor_sector_totals").fetchone()[0]
        print(f"donor_sector_totals:   {dst_count:>10,} rows  (legislative_intelligence.db)")

    polls.close()
    leg.close()
    print("\nDone.")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Virginia state finance pipeline")
    parser.add_argument(
        "--since", type=int, default=None,
        help="Only process contribution cycles >= this year (e.g. --since 2022)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Count rows without writing to any database",
    )
    args = parser.parse_args()
    run(since_cycle=args.since, dry_run=args.dry_run)

    if not args.dry_run:
        from ingest_health_check import HealthCheckRunner
        print("\nRunning post-ingest health check …")
        report = HealthCheckRunner().run()
        if not report["passed"]:
            print(
                json.dumps({
                    "event": "ingest_health_fail",
                    "hard_failures": report["hard_failures"],
                    "warnings": report["warnings"],
                }),
                file=sys.stderr,
            )
            sys.exit(1)
        if report["warnings"]:
            print(
                json.dumps({"event": "ingest_health_warnings", "warnings": report["warnings"]}),
                file=sys.stderr,
            )
        print("Health check passed.")
