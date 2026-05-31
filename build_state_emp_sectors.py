"""
Build employer-industry sector breakdown for VA SBE donations and update
donor_map_cache.json with:
  - 'emp_sectors'     : named-employer industries (skips retired/self/NA)
  - 'retired_sectors' : occupation-based industries for retired/not-employed donors
"""
import sqlite3, json, re, sys
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')

DB_PATH   = "polls.db"
CACHE_PATH = "data/donor_map_cache.json"

# ── Status labels to SKIP (not industries) ────────────────────────────────────
_SKIP_PATTERNS = re.compile(
    r"^(retired|not employed|unemployed|homemaker|housewife|self[- ]?employed"
    r"|self$|n/?a$|none$|na$|not applicable|information requested|requested"
    r"|unknown|not provided|none provided|n/a|not available|student"
    r"|disabled|volunteer|minister|clergy|priest|pastor|rabbi|nun"
    r"|freelance|contractor$|consultant$|independent$)$",
    re.I
)

# ── Employer → industry keyword map ──────────────────────────────────────────
# Ordered: first match wins. Longer/more-specific patterns first.
KEYWORD_MAP = [
    # Defense / Military
    ("huntington ingalls",       "Defense"),
    ("booz allen",                "Defense"),
    ("lockheed",                  "Defense"),
    ("raytheon",                  "Defense"),
    ("general dynamics",          "Defense"),
    ("northrop grumman",          "Defense"),
    ("leidos",                    "Defense"),
    ("saic",                      "Defense"),
    ("l3harris",                  "Defense"),
    ("bae systems",               "Defense"),
    ("caci",                      "Defense"),
    ("mantech",                   "Defense"),
    ("ita international",         "Defense"),
    ("london bridge trading",     "Defense"),
    # Energy / Utilities
    ("dominion energy",           "Energy"),
    ("dominion",                  "Energy"),
    ("appalachian power",         "Energy"),
    ("virginia natural gas",      "Energy"),
    ("columbia gas",              "Energy"),
    ("solar",                     "Energy"),
    ("renewable",                 "Energy"),
    ("petroleum",                 "Energy"),
    ("oil and gas",               "Energy"),
    ("oil & gas",                 "Energy"),
    ("pipeline",                  "Energy"),
    ("offshore",                  "Energy"),
    ("electric",                  "Energy"),
    ("utility",                   "Energy"),
    ("utilities",                 "Energy"),
    ("energy",                    "Energy"),
    # Finance / Investment
    ("paloma partners",           "Finance"),
    ("pbm capital",               "Finance"),
    ("bluestream",                "Finance"),
    ("genworth",                  "Finance"),
    ("level one partners",        "Finance"),
    ("rlj companies",             "Finance"),
    ("bank of america",           "Finance"),
    ("wells fargo",               "Finance"),
    ("jpmorgan",                  "Finance"),
    ("goldman sachs",             "Finance"),
    ("morgan stanley",            "Finance"),
    ("capital one",               "Finance"),
    ("pnc bank",                  "Finance"),
    ("bank",                      "Finance"),
    ("financial",                 "Finance"),
    ("investment",                "Finance"),
    ("investor",                  "Finance"),
    ("capital",                   "Finance"),
    ("asset management",          "Finance"),
    ("asset mgmt",                "Finance"),
    ("wealth management",         "Finance"),
    ("wealth mgmt",               "Finance"),
    ("hedge fund",                "Finance"),
    ("private equity",            "Finance"),
    ("venture capital",           "Finance"),
    ("securities",                "Finance"),
    ("brokerage",                 "Finance"),
    ("insurance",                 "Finance"),
    ("mortgage",                  "Finance"),
    ("lending",                   "Finance"),
    ("credit union",              "Finance"),
    ("accounting",                "Finance"),
    ("cpa",                       "Finance"),
    # Real Estate
    ("holtzman",                  "Real Estate"),
    ("cumberland development",    "Real Estate"),
    ("suburban capital",          "Real Estate"),
    ("real estate",               "Real Estate"),
    ("realty",                    "Real Estate"),
    ("developer",                 "Real Estate"),
    ("development",               "Real Estate"),
    ("property",                  "Real Estate"),
    ("housing",                   "Real Estate"),
    ("apartment",                 "Real Estate"),
    ("commercial properties",     "Real Estate"),
    ("construction",              "Construction"),
    ("contractor",                "Construction"),
    ("homebuilder",               "Construction"),
    ("builder",                   "Construction"),
    # Legal
    ("buchanan ingersoll",        "Legal"),
    ("consumer litigation",       "Legal"),
    ("law office",                "Legal"),
    ("law firm",                  "Legal"),
    ("attorneys",                 "Legal"),
    ("attorney",                  "Legal"),
    ("solicitors",                "Legal"),
    ("law group",                 "Legal"),
    ("law pllc",                  "Legal"),
    ("law llc",                   "Legal"),
    (" law ",                     "Legal"),
    ("legal",                     "Legal"),
    # Healthcare
    ("hospital",                  "Healthcare"),
    ("health system",             "Healthcare"),
    ("health care",               "Healthcare"),
    ("healthcare",                "Healthcare"),
    ("medical",                   "Healthcare"),
    ("physician",                 "Healthcare"),
    ("doctor",                    "Healthcare"),
    ("clinic",                    "Healthcare"),
    ("pharma",                    "Healthcare"),
    ("dental",                    "Healthcare"),
    ("optometry",                 "Healthcare"),
    ("surgery",                   "Healthcare"),
    ("radiology",                 "Healthcare"),
    ("therapy",                   "Healthcare"),
    ("nursing",                   "Healthcare"),
    # Technology
    ("google",                    "Technology"),
    ("amazon",                    "Technology"),
    ("microsoft",                 "Technology"),
    ("apple inc",                 "Technology"),
    ("facebook",                  "Technology"),
    ("meta ",                     "Technology"),
    ("oracle",                    "Technology"),
    ("cisco",                     "Technology"),
    ("ibm",                       "Technology"),
    ("software",                  "Technology"),
    ("technology",                "Technology"),
    ("tech ",                     "Technology"),
    ("cyber",                     "Technology"),
    ("data ",                     "Technology"),
    ("digital",                   "Technology"),
    ("telecom",                   "Telecom"),
    ("verizon",                   "Telecom"),
    ("at&t",                      "Telecom"),
    ("comcast",                   "Telecom"),
    # Education
    ("university",                "Education"),
    ("college",                   "Education"),
    ("school",                    "Education"),
    ("education",                 "Education"),
    ("academy",                   "Education"),
    # Government / Public sector
    ("commonwealth of virginia",  "Government"),
    ("city of ",                  "Government"),
    ("county of ",                "Government"),
    ("department of ",            "Government"),
    ("dept of ",                  "Government"),
    ("state of ",                 "Government"),
    ("federal ",                  "Government"),
    ("u.s. ",                     "Government"),
    ("us government",             "Government"),
    ("government",                "Government"),
    ("public service",            "Government"),
    ("public works",              "Government"),
    # Nonprofit / Advocacy
    ("heising-simons",            "Nonprofit"),
    ("heising simons",            "Nonprofit"),
    ("foundation",                "Nonprofit"),
    ("nonprofit",                 "Nonprofit"),
    ("non-profit",                "Nonprofit"),
    ("charitable",                "Nonprofit"),
    ("charity",                   "Nonprofit"),
    ("association",               "Nonprofit"),
    ("advocacy",                  "Nonprofit"),
    # Hospitality / Food
    ("hotel",                     "Hospitality"),
    ("resort",                    "Hospitality"),
    ("restaurant",                "Hospitality"),
    ("hospitality",               "Hospitality"),
    ("food",                      "Hospitality"),
    # Automotive
    ("automobile",                "Automotive"),
    ("automotive",                "Automotive"),
    ("auto dealer",               "Automotive"),
    ("car dealer",                "Automotive"),
    # Agriculture
    ("farm",                      "Agriculture"),
    ("agriculture",               "Agriculture"),
    ("farming",                   "Agriculture"),
    # Retail / Consumer
    ("retail",                    "Retail"),
    ("wholesale",                 "Retail"),
    # Manufacturing
    ("newmarket",                 "Manufacturing"),
    ("manufacturing",             "Manufacturing"),
    ("industrial",                "Manufacturing"),
    ("plastics",                  "Manufacturing"),
    # Media / Entertainment
    ("media",                     "Media"),
    ("publishing",                "Media"),
    ("newspaper",                 "Media"),
    ("broadcasting",              "Media"),
    # Consulting
    ("consulting",                "Consulting"),
    ("advisors",                  "Consulting"),
    ("advisory",                  "Consulting"),
    ("management consulting",     "Consulting"),
    # Small Business / Entrepreneur
    ("business owner",            "Small Business"),
    ("entrepreneur",              "Small Business"),
    ("small business",            "Small Business"),
]

# ── Occupation → industry map (for retired/not-employed donors) ──────────────
# Used when employer is a skip-status. First match wins.
OCC_KEYWORD_MAP = [
    # Legal
    ("attorney",        "Legal"),
    ("lawyer",          "Legal"),
    ("solicitor",       "Legal"),
    ("paralegal",       "Legal"),
    ("judge",           "Legal"),
    ("counsel",         "Legal"),
    # Healthcare
    ("physician",       "Healthcare"),
    ("doctor",          "Healthcare"),
    ("dentist",         "Healthcare"),
    ("nurse",           "Healthcare"),
    ("pharmacist",      "Healthcare"),
    ("surgeon",         "Healthcare"),
    ("therapist",       "Healthcare"),
    ("psychiatrist",    "Healthcare"),
    ("radiologist",     "Healthcare"),
    ("optometrist",     "Healthcare"),
    ("medical",         "Healthcare"),
    ("healthcare",      "Healthcare"),
    # Finance
    ("investor",        "Finance"),
    ("banker",          "Finance"),
    ("accountant",      "Finance"),
    ("financial",       "Finance"),
    ("broker",          "Finance"),
    ("cpa",             "Finance"),
    ("economist",       "Finance"),
    ("actuary",         "Finance"),
    # Real Estate
    ("real estate",     "Real Estate"),
    ("realtor",         "Real Estate"),
    ("developer",       "Real Estate"),
    # Small Business / Entrepreneur
    ("business owner",  "Small Business"),
    ("entrepreneur",    "Small Business"),
    ("entrepr",         "Small Business"),   # catches "entrepreneurer" typo
    # Education
    ("teacher",         "Education"),
    ("professor",       "Education"),
    ("educator",        "Education"),
    ("principal",       "Education"),
    ("librarian",       "Education"),
    # Government / Military
    ("military",        "Government / Military"),
    ("veteran",         "Government / Military"),
    ("officer",         "Government / Military"),
    ("colonel",         "Government / Military"),
    ("general",         "Government / Military"),
    ("admiral",         "Government / Military"),
    ("sergeant",        "Government / Military"),
    ("captain",         "Government / Military"),
    ("diplomat",        "Government / Military"),
    ("ambassador",      "Government / Military"),
    ("government",      "Government / Military"),
    # Technology / Engineering
    ("engineer",        "Technology / Engineering"),
    ("software",        "Technology / Engineering"),
    ("technology",      "Technology / Engineering"),
    ("scientist",       "Technology / Engineering"),
    ("researcher",      "Technology / Engineering"),
    ("architect",       "Technology / Engineering"),
    # Nonprofit / Philanthropy
    ("philanthropist",  "Nonprofit / Philanthropy"),
    ("activist",        "Nonprofit / Philanthropy"),
    ("nonprofit",       "Nonprofit / Philanthropy"),
    ("community",       "Nonprofit / Philanthropy"),
    ("minister",        "Nonprofit / Philanthropy"),
    ("clergy",          "Nonprofit / Philanthropy"),
    ("pastor",          "Nonprofit / Philanthropy"),
    ("rabbi",           "Nonprofit / Philanthropy"),
    # Media / Creative
    ("writer",          "Media / Creative"),
    ("author",          "Media / Creative"),
    ("journalist",      "Media / Creative"),
    ("artist",          "Media / Creative"),
    ("designer",        "Media / Creative"),
    ("filmmaker",       "Media / Creative"),
    ("musician",        "Media / Creative"),
    ("photographer",    "Media / Creative"),
    # Agriculture
    ("farmer",          "Agriculture"),
    ("rancher",         "Agriculture"),
    ("agriculture",     "Agriculture"),
    # Consulting
    ("consultant",      "Consulting"),
    ("advisor",         "Consulting"),
    # Executive / Business
    ("executive",       "Executive / Management"),
    ("ceo",             "Executive / Management"),
    ("coo",             "Executive / Management"),
    ("cfo",             "Executive / Management"),
    ("president",       "Executive / Management"),
    ("manager",         "Executive / Management"),
    ("director",        "Executive / Management"),
]

# Occupation values that are still too vague to classify
_OCC_SKIP = re.compile(
    r"^(retired|not employed|unemployed|homemaker|housewife|self[- ]?employed"
    r"|self$|n/?a$|none$|na$|not applicable|information requested|requested"
    r"|unknown|not provided|none provided|n/a|not available|student"
    r"|disabled|volunteer|writer at home|artist at home"
    r"|not employed outside home|philanthropist at home)$",
    re.I
)


def classify_employer(emp: str) -> str | None:
    """Return industry sector or None if unclassifiable / status entry."""
    if not emp or not emp.strip():
        return None
    emp_clean = emp.strip().lower()
    if _SKIP_PATTERNS.match(emp_clean):
        return None
    for kw, sector in KEYWORD_MAP:
        if kw in emp_clean:
            return sector
    return None


def classify_occupation(occ: str) -> str | None:
    """Return industry sector from occupation string, or None."""
    if not occ or not occ.strip():
        return None
    occ_clean = occ.strip().lower()
    if _OCC_SKIP.match(occ_clean):
        return None
    for kw, sector in OCC_KEYWORD_MAP:
        if kw in occ_clean:
            return sector
    return None


def build_emp_sectors(conn):
    rows = conn.execute("""
        SELECT employer, ROUND(SUM(amount),0) AS total, COUNT(*) AS donors
        FROM va_cf_schedule_a
        WHERE is_individual=1 AND amount>0
          AND employer IS NOT NULL AND employer != ''
        GROUP BY lower(trim(employer))
    """).fetchall()

    sector_totals: dict[str, dict] = {}
    unclassified = 0
    for r in rows:
        sec = classify_employer(r["employer"])
        if sec is None:
            unclassified += r["donors"]
            continue
        if sec not in sector_totals:
            sector_totals[sec] = {"total": 0.0, "donors": 0}
        sector_totals[sec]["total"]  += r["total"]
        sector_totals[sec]["donors"] += r["donors"]

    result = sorted(
        [{"label": k, "total": round(v["total"], 0), "donors": v["donors"]}
         for k, v in sector_totals.items()],
        key=lambda x: -x["total"]
    )
    print(f"[emp_sectors] Classified into {len(result)} industries. Unclassified donors: {unclassified:,}")
    for r in result:
        print(f"  {r['label']:<25} ${r['total']:>12,.0f}  ({r['donors']:,} donors)")
    return result


def build_retired_sectors(conn):
    """
    For donors whose employer is a skip-status (Retired, Not Employed, Self, etc.),
    classify by their occupation field to show what industry they likely came from.
    """
    rows = conn.execute("""
        SELECT employer, occupation, ROUND(SUM(amount),0) AS total, COUNT(*) AS donors
        FROM va_cf_schedule_a
        WHERE is_individual=1 AND amount>0
          AND occupation IS NOT NULL AND occupation != ''
        GROUP BY lower(trim(employer)), lower(trim(occupation))
    """).fetchall()

    sector_totals: dict[str, dict] = {}
    unclassified = 0
    for r in rows:
        emp = (r["employer"] or "").strip()
        # Only process skip-employer rows
        if not _SKIP_PATTERNS.match(emp.lower()):
            continue
        sec = classify_occupation(r["occupation"])
        if sec is None:
            unclassified += r["donors"]
            continue
        if sec not in sector_totals:
            sector_totals[sec] = {"total": 0.0, "donors": 0}
        sector_totals[sec]["total"]  += r["total"]
        sector_totals[sec]["donors"] += r["donors"]

    result = sorted(
        [{"label": k, "total": round(v["total"], 0), "donors": v["donors"]}
         for k, v in sector_totals.items()],
        key=lambda x: -x["total"]
    )
    print(f"\n[retired_sectors] Classified into {len(result)} occupation sectors. Unclassified donors: {unclassified:,}")
    for r in result:
        print(f"  {r['label']:<30} ${r['total']:>12,.0f}  ({r['donors']:,} donors)")
    return result


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    emp_sectors     = build_emp_sectors(conn)
    retired_sectors = build_retired_sectors(conn)
    conn.close()

    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        cache = json.load(f)

    cache["state"]["emp_sectors"]     = emp_sectors
    cache["state"]["retired_sectors"] = retired_sectors

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, separators=(",", ":"))

    print(f"\nCache updated — {len(emp_sectors)} employer sectors, {len(retired_sectors)} retired occupation sectors written.")
