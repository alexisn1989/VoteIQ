_SECTOR_KEYWORDS: dict[str, list[str]] = {
    "Finance":         ["bank", "financ", "invest", "capital", "asset", "fund", "credit", "insurance", "securities", "wealth", "brokerage", "mortgage", "equity", "hedge"],
    "Healthcare":      ["health", "medical", "hospital", "pharma", "clinic", "physician", "doctor", "nurse", "dental", "therapy", "biotech", "surgeon", "medicine"],
    "Legal":           ["attorney", "lawyer", "law firm", "legal", "counsel", "litigation", "esquire", "llp", "pllc"],
    "Energy":          ["energy", "oil", "gas", "petroleum", "electric", "utility", "power", "solar", "wind", "coal", "mining", "nuclear", "pipeline"],
    "Real Estate":     ["real estate", "realty", "property", "developer", "construction", "builder", "land", "housing", "architect", "contractor"],
    "Technology":      ["tech", "software", "data", "cyber", "cloud", "ai", "computer", "digital", "telecom", "internet", "network", "saas"],
    "Ideological":     ["pac", "committee", "political", "action", "democrat", "republican", "conservative", "liberal", "progress", "freedom", "patriot", "america"],
    "Agriculture":     ["farm", "agri", "crop", "livestock", "dairy", "ranch", "grain", "food production", "timber", "forest"],
    "Manufacturing":   ["manufactur", "industri", "factory", "product", "assembly", "fabricat", "equipment", "machin"],
    "Transportation":  ["transport", "truck", "logistics", "freight", "airline", "aviation", "ship", "rail", "auto dealer", "fleet"],
    "Retail":          ["retail", "store", "shop", "wholesale", "distribut", "commerce", "merchant"],
    "Hospitality":     ["hotel", "restaurant", "hospitality", "casino", "tourism", "resort", "food service", "beverage"],
    "Education":       ["school", "university", "college", "education", "academ", "teach", "professor", "research"],
    "Defense":         ["defense", "military", "aerospace", "contractor", "government", "federal", "lockheed", "boeing", "raytheon", "northrop"],
    "Labor":           ["union", "labor", "workers", "teamster", "afl", "seiu", "trade association"],
}


def classify_employer(employer: str, occupation: str = "") -> str:
    """
    Classify a single employer+occupation string into a sector.
    Returns 'Individual/Other' if no keyword matches.
    """
    text = f"{employer} {occupation}".lower()
    for sector, keywords in _SECTOR_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return sector
    return "Individual/Other"


_LARGE_DONOR_LABELS = frozenset({
    "Near-max donor money", "Industry-linked money",
    "Large-donor money", "Major institutional money",
})


def classify_sectors(sector_totals: list, config: dict) -> list:
    classified = []
    for row in sector_totals:
        sector, total, count, avg = row[0], row[1], row[2], row[3]
        if not count:
            continue
        avg = avg or 0.0

        if config.get("contribution_limit"):
            # Federal — thresholds per VoteIQ methodology
            if avg >= config["contribution_limit"] * 0.8:
                concentration, label = "Near-max donor money", "🔴"
            elif avg >= 1000:
                concentration, label = "Industry-linked money", "🔴"
            elif avg >= config["grassroots_threshold"]:
                concentration, label = "Mixed donor base", "🟡"
            else:
                concentration, label = "Small-dollar base", "🟢"
        else:
            # State — no limits; thresholds per VoteIQ methodology
            if avg >= config.get("major_donor", 50_000):
                concentration, label = "Major institutional money", "⚫"
            elif avg >= 5000:
                concentration, label = "Large-donor money", "🔴"
            elif avg >= 1000:
                concentration, label = "Industry-linked money", "🔴"
            elif avg >= config["grassroots_threshold"]:
                concentration, label = "Mixed donor base", "🟡"
            else:
                concentration, label = "Small-dollar base", "🟢"

        classified.append({
            "sector":        sector,
            "total":         total or 0.0,
            "donor_count":   count,
            "avg_donation":  avg,
            "concentration": concentration,
            "label":         label,
        })
    return classified
