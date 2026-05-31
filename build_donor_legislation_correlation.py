"""
Build donor-legislation alignment data for VA state legislators.

For each legislator:
  1. Get their donor industry breakdown from va_cf_schedule_a (employer keyword classification)
  2. Get their vote records from va_legislator_recent_votes
  3. Classify each bill title by industry (keyword map)
  4. Compute alignment score: % of YES votes on bills in their top donor industry
  5. Track sponsored bills per industry from legiscan_va_bill_sponsors

Output written to cache["state"]["donor_legislation"] in donor_map_cache.json
"""
import sqlite3, json, re, sys
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')

DB_PATH    = "polls.db"
CACHE_PATH = "data/donor_map_cache.json"

# ── Skip patterns (same as emp_sectors) ──────────────────────────────────────
_SKIP_EMP = re.compile(
    r"^(retired|not employed|unemployed|homemaker|housewife|self[- ]?employed"
    r"|self$|n/?a$|none$|na$|not applicable|information requested|requested"
    r"|unknown|not provided|none provided|n/a|not available|student"
    r"|disabled|volunteer|minister|clergy|priest|pastor|rabbi|nun"
    r"|freelance|contractor$|consultant$|independent$)$",
    re.I
)

# ── Employer → donor industry (first match wins) ──────────────────────────────
EMP_MAP = [
    ("huntington ingalls","Defense"),("booz allen","Defense"),("lockheed","Defense"),
    ("raytheon","Defense"),("general dynamics","Defense"),("northrop grumman","Defense"),
    ("leidos","Defense"),("saic","Defense"),("l3harris","Defense"),("bae systems","Defense"),
    ("caci","Defense"),("mantech","Defense"),
    ("dominion energy","Energy"),("dominion","Energy"),("appalachian power","Energy"),
    ("virginia natural gas","Energy"),("solar","Energy"),("renewable","Energy"),
    ("petroleum","Energy"),("oil and gas","Energy"),("pipeline","Energy"),
    ("electric","Energy"),("utility","Energy"),("utilities","Energy"),("energy","Energy"),
    ("paloma partners","Finance"),("pbm capital","Finance"),("bluestream","Finance"),
    ("genworth","Finance"),("level one partners","Finance"),("rlj companies","Finance"),
    ("bank","Finance"),("financial","Finance"),("investment","Finance"),("investor","Finance"),
    ("capital","Finance"),("asset management","Finance"),("wealth management","Finance"),
    ("hedge fund","Finance"),("private equity","Finance"),("venture capital","Finance"),
    ("securities","Finance"),("insurance","Finance"),("mortgage","Finance"),
    ("lending","Finance"),("credit union","Finance"),("accounting","Finance"),("cpa","Finance"),
    ("holtzman","Real Estate"),("cumberland development","Real Estate"),
    ("suburban capital","Real Estate"),("real estate","Real Estate"),("realty","Real Estate"),
    ("developer","Real Estate"),("development","Real Estate"),("property","Real Estate"),
    ("housing","Real Estate"),("apartment","Real Estate"),
    ("construction","Construction"),("homebuilder","Construction"),("builder","Construction"),
    ("buchanan ingersoll","Legal"),("consumer litigation","Legal"),("law office","Legal"),
    ("law firm","Legal"),("attorneys","Legal"),("attorney","Legal"),
    ("law group","Legal"),(" law ","Legal"),("legal","Legal"),
    ("hospital","Healthcare"),("health system","Healthcare"),("health care","Healthcare"),
    ("healthcare","Healthcare"),("medical","Healthcare"),("physician","Healthcare"),
    ("doctor","Healthcare"),("clinic","Healthcare"),("pharma","Healthcare"),
    ("dental","Healthcare"),("surgery","Healthcare"),("therapy","Healthcare"),
    ("nursing","Healthcare"),
    ("google","Technology"),("amazon","Technology"),("microsoft","Technology"),
    ("oracle","Technology"),("cisco","Technology"),("ibm","Technology"),
    ("software","Technology"),("technology","Technology"),("tech ","Technology"),
    ("cyber","Technology"),("data ","Technology"),("digital","Technology"),
    ("telecom","Telecom"),("verizon","Telecom"),("at&t","Telecom"),("comcast","Telecom"),
    ("university","Education"),("college","Education"),("school","Education"),
    ("education","Education"),("academy","Education"),
    ("commonwealth of virginia","Government"),("city of ","Government"),
    ("county of ","Government"),("department of ","Government"),
    ("state of ","Government"),("federal ","Government"),("government","Government"),
    ("heising-simons","Nonprofit"),("heising simons","Nonprofit"),
    ("foundation","Nonprofit"),("nonprofit","Nonprofit"),("non-profit","Nonprofit"),
    ("charitable","Nonprofit"),("charity","Nonprofit"),("association","Nonprofit"),
    ("advocacy","Nonprofit"),
    ("hotel","Hospitality"),("resort","Hospitality"),("restaurant","Hospitality"),
    ("hospitality","Hospitality"),("food","Hospitality"),
    ("farm","Agriculture"),("agriculture","Agriculture"),("farming","Agriculture"),
    ("retail","Retail"),("wholesale","Retail"),
    ("newmarket","Manufacturing"),("manufacturing","Manufacturing"),
    ("industrial","Manufacturing"),
    ("media","Media"),("publishing","Media"),("newspaper","Media"),
    ("consulting","Consulting"),("advisors","Consulting"),("advisory","Consulting"),
    ("business owner","Small Business"),("entrepreneur","Small Business"),
]

# ── Bill title → policy industry (first match wins) ──────────────────────────
BILL_MAP = [
    # Energy / Utilities
    ("electric utilit",   "Energy"),("electric co",   "Energy"),
    ("dominion",          "Energy"),("appalachian power","Energy"),
    ("natural gas",       "Energy"),("petroleum",     "Energy"),
    ("solar energy",      "Energy"),("renewable energy","Energy"),
    ("offshore wind",     "Energy"),("pipeline",      "Energy"),
    ("utility",           "Energy"),("utilities",     "Energy"),
    ("net metering",      "Energy"),("energy storage","Energy"),
    ("rate case",         "Energy"),("rate increase", "Energy"),
    # Healthcare / Pharma
    ("medicaid",          "Healthcare"),("medicare",   "Healthcare"),
    ("health insurance",  "Healthcare"),("health benefit","Healthcare"),
    ("hospital",          "Healthcare"),("pharmacy",   "Healthcare"),
    ("pharmaceutical",    "Healthcare"),("prescription","Healthcare"),
    ("vaccine",           "Healthcare"),("mental health","Healthcare"),
    ("optometr",          "Healthcare"),("dentist",    "Healthcare"),
    ("physician",         "Healthcare"),("telehealth", "Healthcare"),
    ("birth-related",     "Healthcare"),("neurological","Healthcare"),
    ("controlled substance","Healthcare"),("opioid",   "Healthcare"),
    ("health care",       "Healthcare"),("healthcare", "Healthcare"),
    ("medical",           "Healthcare"),
    # Finance / Banking
    ("bank",              "Finance"),("banking",      "Finance"),
    ("financial institution","Finance"),("credit union","Finance"),
    ("insurance",         "Finance"),("mortgage",     "Finance"),
    ("predatory lending", "Finance"),("payday loan",  "Finance"),
    ("investment",        "Finance"),("securities",   "Finance"),
    ("retirement",        "Finance"),("pension",      "Finance"),
    # Real Estate / Housing
    ("landlord",          "Real Estate"),("tenant",   "Real Estate"),
    ("rental",            "Real Estate"),("eviction",  "Real Estate"),
    ("real estate",       "Real Estate"),("zoning",   "Real Estate"),
    ("affordable housing","Real Estate"),("homeowner", "Real Estate"),
    ("condominium",       "Real Estate"),("HOA",       "Real Estate"),
    ("housing",           "Real Estate"),("deed",      "Real Estate"),
    # Education
    ("school",            "Education"),("education",  "Education"),
    ("teacher",           "Education"),("student",    "Education"),
    ("university",        "Education"),("college",    "Education"),
    ("tuition",           "Education"),("curriculum", "Education"),
    ("charter school",    "Education"),("k-12",       "Education"),
    # Labor / Employment
    ("minimum wage",      "Labor"),("wage",          "Labor"),
    ("worker",            "Labor"),("employment",    "Labor"),
    ("labor",             "Labor"),("workforce",     "Labor"),
    ("paid leave",        "Labor"),("overtime",      "Labor"),
    ("workplace",         "Labor"),("unemployment",  "Labor"),
    # Environment
    ("environment",       "Environment"),("climate",  "Environment"),
    ("pollution",         "Environment"),("carbon",   "Environment"),
    ("emissions",         "Environment"),("conservation","Environment"),
    ("clean water",       "Environment"),("clean air","Environment"),
    ("recycling",         "Environment"),("composting","Environment"),
    ("pesticide",         "Environment"),("watershed","Environment"),
    # Firearms
    ("firearm",           "Firearms"),("handgun",     "Firearms"),
    ("concealed",         "Firearms"),("gun",         "Firearms"),
    ("weapon",            "Firearms"),("ammunition",  "Firearms"),
    # Agriculture
    ("farm",              "Agriculture"),("agriculture","Agriculture"),
    ("livestock",         "Agriculture"),("crop",     "Agriculture"),
    ("pesticide",         "Agriculture"),("fertilizer","Agriculture"),
    # Technology / Data
    ("broadband",         "Technology"),("internet",  "Technology"),
    ("cybersecurity",     "Technology"),("data privacy","Technology"),
    ("artificial intelligence","Technology"),("technology","Technology"),
    # Taxation
    ("income tax",        "Taxation"),("sales tax",  "Taxation"),
    ("property tax",      "Taxation"),("tax credit", "Taxation"),
    ("tax relief",        "Taxation"),("tax exemption","Taxation"),
    ("tax rate",          "Taxation"),("revenue",    "Taxation"),
    # Defense / Veterans
    ("veteran",           "Defense / Veterans"),("military","Defense / Veterans"),
    ("national guard",    "Defense / Veterans"),
    # Criminal Justice / Legal
    ("criminal",          "Criminal Justice"),("sentencing","Criminal Justice"),
    ("incarcerat",        "Criminal Justice"),("parole",  "Criminal Justice"),
    ("probation",         "Criminal Justice"),("police",  "Criminal Justice"),
    ("law enforcement",   "Criminal Justice"),("felony",  "Criminal Justice"),
    ("misdemeanor",       "Criminal Justice"),("juvenile","Criminal Justice"),
    # Elections / Government
    ("election",          "Elections"),("voting",     "Elections"),
    ("ballot",            "Elections"),("campaign finance","Elections"),
    ("redistricting",     "Elections"),("voter",      "Elections"),
]


def classify_employer(emp: str) -> str | None:
    if not emp or not emp.strip():
        return None
    ec = emp.strip().lower()
    if _SKIP_EMP.match(ec):
        return None
    for kw, sec in EMP_MAP:
        if kw in ec:
            return sec
    return None


def classify_bill(title: str) -> str | None:
    if not title:
        return None
    tc = title.lower()
    for kw, ind in BILL_MAP:
        if kw.lower() in tc:
            return ind
    return None


def normalize_name(name: str):
    """Return (last_lower, first_initial_lower) for fuzzy matching."""
    name = re.sub(r'^(mr\.?|mrs\.?|ms\.?|dr\.?|hon\.?|gov\.?|lt\.?\s*gov\.?'
                  r'|sen\.?|rep\.?|atty\.?|gen\.?)\s+', '', name, flags=re.I)
    parts = name.strip().split()
    if len(parts) >= 2:
        return parts[-1].lower().rstrip(','), parts[0].lower()[:1]
    return parts[0].lower() if parts else '', ''


def build_correlation(conn):
    # 1. Build per-legislator donor industry totals from va_cf_schedule_a
    emp_rows = conn.execute("""
        SELECT candidate_name, employer, ROUND(SUM(amount),0) AS total, COUNT(*) AS donors
        FROM va_cf_schedule_a
        WHERE is_individual=1 AND amount>0
          AND employer IS NOT NULL AND employer != ''
        GROUP BY candidate_name, lower(trim(employer))
    """).fetchall()

    # cand_sectors[(last,fi)] = {industry: {total, donors}}
    cand_sectors = defaultdict(lambda: defaultdict(lambda: {"total": 0.0, "donors": 0}))
    cand_name_map = {}  # (last,fi) -> canonical voter_name (filled later)
    cand_display  = {}  # (last,fi) -> candidate_name for display
    for r in emp_rows:
        sec = classify_employer(r["employer"])
        if sec is None:
            continue
        key = normalize_name(r["candidate_name"])
        if key[0]:
            cand_sectors[key][sec]["total"]  += r["total"]
            cand_sectors[key][sec]["donors"] += r["donors"]
            if key not in cand_display:
                cand_display[key] = r["candidate_name"]

    # 2. Get per-legislator vote records
    vote_rows = conn.execute("""
        SELECT voter_name, option, title, bill_id, chamber
        FROM va_legislator_recent_votes
        WHERE option IN ('yes','no')
    """).fetchall()

    # leg_votes[(last,fi)] = [(option, industry_or_None)]
    leg_votes   = defaultdict(list)
    leg_meta    = {}  # (last,fi) -> {voter_name, party, chamber}
    for r in vote_rows:
        key = normalize_name(r["voter_name"])
        ind = classify_bill(r["title"])
        leg_votes[key].append({"option": r["option"], "industry": ind,
                                "title": r["title"], "bill_id": r["bill_id"]})
        if key not in leg_meta:
            leg_meta[key] = {"voter_name": r["voter_name"], "chamber": r["chamber"]}

    # 3. Get party from va_legislator_vote_summary
    party_rows = conn.execute(
        "SELECT voter_name, party, chamber FROM va_legislator_vote_summary"
    ).fetchall()
    party_map = {}
    for r in party_rows:
        key = normalize_name(r["voter_name"])
        party_map[key] = {"party": r["party"], "chamber": r["chamber"]}

    # 4. Get sponsored bills
    sponsor_rows = conn.execute("""
        SELECT s.legislator_name, b.bill_id, b.bill_number, b.title, b.status_label
        FROM legiscan_va_bill_sponsors s
        JOIN legiscan_va_bills b ON b.bill_id = s.bill_id
        WHERE s.sponsor_order = 1
    """).fetchall()
    sponsored = defaultdict(list)
    for r in sponsor_rows:
        key = normalize_name(r["legislator_name"])
        ind = classify_bill(r["title"])
        sponsored[key].append({
            "bill_id": r["bill_id"], "bill_number": r["bill_number"],
            "title": (r["title"] or "")[:80], "status": r["status_label"],
            "industry": ind
        })

    # 5. Build output — only legislators with both vote and donor data
    results = []
    for key, votes in leg_votes.items():
        if key not in cand_sectors or not cand_sectors[key]:
            continue

        meta     = leg_meta.get(key, {})
        pm       = party_map.get(key, {})
        voter_nm = meta.get("voter_name", "")
        party    = pm.get("party", "Unknown")
        chamber  = pm.get("chamber", meta.get("chamber", ""))

        # Donor industry summary
        donor_inds = sorted(
            [{"label": k, "total": round(v["total"], 0), "donors": v["donors"]}
             for k, v in cand_sectors[key].items()],
            key=lambda x: -x["total"]
        )
        top_donor_ind    = donor_inds[0]["label"] if donor_inds else None
        top_donor_amount = donor_inds[0]["total"]  if donor_inds else 0

        # Vote breakdown by industry
        ind_votes = defaultdict(lambda: {"yes": 0, "no": 0, "bills": []})
        for v in votes:
            ind = v["industry"]
            if ind:
                ind_votes[ind][v["option"]] += 1
                if len(ind_votes[ind]["bills"]) < 5:
                    ind_votes[ind]["bills"].append(
                        {"title": v["title"][:70], "bill_id": v["bill_id"],
                         "option": v["option"]}
                    )

        industry_votes = {}
        for ind, d in ind_votes.items():
            total_votes = d["yes"] + d["no"]
            if total_votes == 0:
                continue
            industry_votes[ind] = {
                "yes": d["yes"], "no": d["no"],
                "alignment": round(d["yes"] / total_votes * 100, 1),
                "bills": d["bills"]
            }

        # Alignment on top donor industry
        top_alignment = None
        if top_donor_ind and top_donor_ind in industry_votes:
            top_alignment = industry_votes[top_donor_ind]["alignment"]

        # Sponsored bills classified
        leg_sponsored = [b for b in sponsored.get(key, []) if b["industry"]]

        results.append({
            "name":              voter_nm,
            "party":             party,
            "chamber":           chamber,
            "donor_industries":  donor_inds[:5],
            "top_donor_industry": top_donor_ind,
            "top_donor_amount":  top_donor_amount,
            "industry_votes":    industry_votes,
            "top_alignment":     top_alignment,
            "sponsored_bills":   leg_sponsored[:10],
        })

    # Sort by top_alignment descending (nulls last), then by donor amount
    results.sort(key=lambda x: (-(x["top_alignment"] or -1), -x["top_donor_amount"]))

    print(f"\nBuilt correlation for {len(results)} legislators")
    print(f"{'Legislator':<30} {'Party':<5} {'Top Donor Industry':<20} {'$':>10}  {'Alignment':>10}")
    print("-" * 80)
    for r in results[:25]:
        aln = f"{r['top_alignment']:.0f}%" if r['top_alignment'] is not None else "  n/a"
        print(f"  {r['name']:<28} {r['party'][:1]:<5} {(r['top_donor_industry'] or ''):<20} "
              f"${r['top_donor_amount']:>9,.0f}  {aln:>8}")
    return results


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    correlation = build_correlation(conn)
    conn.close()

    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        cache = json.load(f)

    cache["state"]["donor_legislation"] = correlation

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, separators=(",", ":"))

    print(f"\nCache updated — donor_legislation written ({len(correlation)} legislators).")
