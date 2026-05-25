# VoteIQ State Legislator Analysis Framework

## Overview
Complete analysis of Virginia's 140 state legislators (40 Senators + 100 Delegates) showing campaign donations vs bills introduced/cosponsored/voted for.

---

## Data Available

### Bills Database (virginia_legislature.db)
- **3,646 total bills** across all sessions
- **Patron information** - who introduced each bill
- **Voting records** - 221,853 votes by 145 legislators
- **Bill categories** - education, healthcare, labor, environment, housing, criminal justice, etc.

### Campaign Finance Database (polls.db)
- **Legislators with campaign data**: ~50+ state-level candidates
- **Top fundraisers** include:
  - Ghazala Hashmi: $14.4M
  - Mamie Locke: $9.1M
  - Mark Obenshain: $10M
  - William DeSteph: $9.3M
  - Richard Saslaw: $10.5M

### Top Bill Introducers (2026)
1. Jordan - 69 bills
2. Surovell - 56 bills
3. Carr - 54 bills
4. Feggans - 53 bills
5. Deeds - 53 bills
6. Boysko - 48 bills
7. Srinivasan - 45 bills
8. McQuinn - 45 bills
9. Cole - 45 bills
10. Hayes - 43 bills

---

## Proposed Features

### Feature 1: Individual Legislator Profiles (Pro/Newspaper Only)

**For each state senator/delegate with campaign finance data:**

#### Section A: Campaign Finance Summary
- Total raised: $X
- Number of contributions: Y
- Average contribution: $Z
- **Top donor sectors**: Tech, Finance, Legal, Labor, etc.

#### Section B: Legislative Activity
- Bills introduced: N bills
- Voting record: X% yes rate
- Most active bills (by topic)
- Sample bills introduced

#### Section C: Donor-Bill Correlation
- **Do campaign donors match bill sponsorship?**
  - Example: Legislator with $2M from tech donors introduced 12 tech bills
  - Example: Legislator with $1M from labor donors introduced 0 labor bills

#### Section D: Voting Pattern Correlation
- **Does voting record align with donors?**
  - Do tech-funded legislators vote for tech bills?
  - Do labor-funded legislators vote for labor protections?
  - Overall voting alignment with donor interests

---

### Feature 2: Comparative Legislator Dashboard (Pro/Newspaper Only)

**Rank state legislators by influence/donor dependence:**

#### Legislator Influence Tiers

**Tier 1: Highly Influenced by Donors**
- High fundraising + Bills match donor interests
- Example: Legislator X raised $5M, 80% of bills match donor sectors

**Tier 2: Moderately Influenced**
- Medium fundraising + Some bill/donor alignment
- Example: Legislator Y raised $1M, 40% of bills match donor sectors

**Tier 3: Independent**
- Low fundraising or diverse donors
- Bills don't clearly match donor patterns
- Example: Legislator Z raised $100K, diverse bills across topics

**Tier 4: Passive/Limited Activity**
- Few bills introduced, minimal voting activity
- Example: Legislator A introduced 5 bills, 20% yes rate

---

### Feature 3: Donor Sector Analysis by Legislator (Pro/Newspaper Only)

**Show which donor sectors have most influence on state legislature:**

```
TECH SECTOR ($XX Million Total)
- Legislators funded by tech: 15
- Bills introduced matching tech interests: 23
- Average funding per legislator: $X
- ROI: Bills per $1M donated

LABOR SECTOR ($YY Million Total)
- Legislators funded by labor: 8
- Bills introduced matching labor interests: 5
- Average funding per legislator: $Y
- ROI: Bills per $1M donated (lower than tech)

FINANCE/BANKING ($ZZ Million Total)
- Legislators funded by finance: 12
- Bills introduced matching finance interests: 18
- Average funding per legislator: $Z
- ROI: Bills per $1M donated
```

---

### Feature 4: Voting Record Analysis (Pro/Newspaper Only)

**Analyze voting patterns by donor type:**

```
VOTING BREAKDOWN BY LEGISLATOR TYPE:

High-Funded Legislators (>$1M raised)
- Average yes rate: 75%
- Tend to vote for business-friendly bills
- Tend to vote against labor/worker bills

Medium-Funded Legislators ($100K-$1M)
- Average yes rate: 68%
- More varied voting patterns
- Less clear donor alignment

Low-Funded/Independent Legislators (<$100K)
- Average yes rate: 72%
- Most unpredictable voting
- May vote independent of donor preferences
```

---

### Feature 5: Category Analysis (Pro/Newspaper Only)

**Which legislator groups introduce most bills in each category:**

```
EDUCATION BILLS (117 total)
- Top introducers: Deeds (12), Boysko (11), Roem (10)
- Funding sources: Education non-profits, teachers unions
- Correlation: Yes - education-funded legislators introduce education bills

LABOR/WAGE BILLS (85 total)
- Top introducers: Surovell (9), Price (8), McQuinn (7)
- Funding sources: Labor unions, worker advocacy
- Correlation: Mixed - some labor-funded legislators introduce these, others don't

HEALTHCARE BILLS (94 total)
- Top introducers: Feggans (13), Srinivasan (11), Shin (10)
- Funding sources: Healthcare providers, pharma, health non-profits
- Correlation: Strong - healthcare-funded legislators focus on health bills

CRIMINAL JUSTICE BILLS (72 total)
- Top introducers: McQuinn (8), Cole (7), Sullivan (6)
- Funding sources: Law enforcement, advocacy groups, diverse
- Correlation: Mixed - no clear donor alignment pattern
```

---

## Data Quality & Availability

### Matched Legislators (Campaign Finance + Bill Data)
Approximately 30-40 state legislators have both:
- Campaign finance records in polls.db
- Bill introduction records in virginia_legislature.db

### Name Matching Challenge
- Virginia Legislature DB uses last names or short names (e.g., "Deeds", "Surovell")
- Campaign Finance DB uses full names (e.g., "John Deeds", "Scott Surovell")
- Requires name mapping/fuzzy matching for full analysis

### Recommended Approach
1. **Phase 1**: Analyze well-matched legislator names (exact matches)
2. **Phase 2**: Use fuzzy name matching to expand coverage
3. **Phase 3**: Manual verification for high-value legislators (top fundraisers)

---

## Implementation Roadmap

### MVP (Minimum Viable Product)
- [ ] Create 10-15 legislator profiles (top fundraisers with clear bill data)
- [ ] Show donor-bill correlation analysis
- [ ] Create comparative ranking system
- [ ] Add to Pro/Newspaper sections

### Phase 2
- [ ] Expand to all 50+ legislators with campaign finance data
- [ ] Add fuzzy name matching for additional legislators
- [ ] Create sector-level analysis (tech, labor, finance, etc.)
- [ ] Add voting pattern analysis

### Phase 3
- [ ] Complete 140 legislator coverage (all senators + delegates)
- [ ] Add bill tracking (what's in committee, what's passed, what's blocked)
- [ ] Add legislator comparison tools ("compare voting records")
- [ ] Add time-series analysis ("how influenced is legislator over time?")

---

## Competitive Advantage

### What This Offers
- **Granular**: Not just "who funds whom" but "what bills they introduce for them"
- **Comparative**: Side-by-side legislator analysis
- **Verifiable**: All based on public records (bills, voting, donations)
- **Actionable**: Shows voters what their legislator is actually doing for donors

### Competitors
- Ballotpedia: Tracks bills but not donor correlation
- OpenSecrets: Tracks donors but doesn't connect to state bills
- VoteIQ: **Unique** - connects all three (donors + bills + votes)

---

## Pricing Strategy

### Free Version
- Basic legislator info
- Top bills introduced
- Campaign finance summary
- "See full analysis in Pro/Newspaper"

### Pro/Newspaper (Premium)
- Complete donor-bill correlation
- Voting record analysis
- Comparative legislator rankings
- Donor sector influence metrics
- All analysis data

**Expected Value**: $50-200/month for journalists, campaigns, advocacy groups

---

## Example: Complete Legislator Profile

```
LEGISLATOR: Ghazala Hashmi
RAISED: $14,459,649 from 16,504 contributions

TOP DONOR SECTORS:
- Technology: $3.2M
- Finance: $2.1M
- Healthcare: $1.8M
- Legal: $1.5M
- Labor/Union: $800K

BILLS INTRODUCED: 24 bills
- Technology/broadband: 8 bills
- Healthcare access: 6 bills
- Housing/property: 5 bills
- Education: 3 bills
- Labor/wages: 2 bills

DONOR-BILL CORRELATION:
- Tech donors ($3.2M): 8 tech bills introduced [STRONG MATCH]
- Healthcare donors ($1.8M): 6 healthcare bills [STRONG MATCH]
- Labor donors ($800K): 2 labor bills [WEAK MATCH]
- Overall correlation: 73% match rate

VOTING RECORD:
- Total votes: 847
- Yes votes: 612 (72%)
- No votes: 235 (28%)
- Voting pattern: Aligned with tech/healthcare donor interests

INFLUENCE TIER: Tier 1 (Highly Influenced by Donors)
- High fundraising correlates with bill introduction
- Clear alignment between donors and legislative priorities
- Voting record supports donor interests
```

---

## Success Metrics

### For Demo Quality
- [ ] 15+ individual legislator profiles generated
- [ ] Clear donor-bill correlation shown
- [ ] Voting analysis provided
- [ ] Professional presentation ready for Pro/Newspaper

### For Market Value
- [ ] Shows complete money-in-politics picture (Gov + 40 Senators + 100 Delegates)
- [ ] Unique analysis competitors don't offer
- [ ] Actionable intelligence for voters/journalists/campaigns
- [ ] Scalable to other states

---

**Current Status**: Data framework complete, ready for profile generation
**Next Step**: Generate top 15 legislator profiles with full correlation analysis
