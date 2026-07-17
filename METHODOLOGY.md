# VoteIQ Methodology

## What VoteIQ Is

VoteIQ is a nonpartisan civic intelligence platform that 
organizes and analyzes publicly available government data 
for Hampton Roads, Virginia.

VoteIQ does not editorialize, endorse, or oppose any 
candidate, party, or political position.

All findings reflect statistical patterns in public 
records only.

---

## Data Sources

### Federal Data

| Source | What We Use | Direct Link |
|--------|-------------|-------------|
| FEC Schedule A | Individual contribution records | fec.gov/data |
| FEC Committee Data | PAC and committee contributions | fec.gov/data |
| Congress.gov | Bills sponsored, voting record | congress.gov |
| U.S. Census TIGER | Congressional district boundaries | census.gov |

### Virginia State Data

| Source | What We Use | Direct Link |
|--------|-------------|-------------|
| Virginia SBE | Campaign finance filings 1999–present | vpap.org |
| Virginia LIS | Bills introduced, voting record | lis.virginia.gov |
| VGIN | State legislative boundaries | vgin.vdem.virginia.gov |

### Local Data

| Source | What We Use | Direct Link |
|--------|-------------|-------------|
| Hampton Roads FOIA | City campaign finance records | voteiq.io/foia |
| Legistar API | City council votes and legislation | legistar.com |
| Census TIGER | Local district boundaries | census.gov |

---

## How We Classify Donor Data

### Donor Size Labels

VoteIQ classifies individual contributions by average 
donation size per sector. Labels reflect donation 
patterns only — not donor intent, coordination, 
or political motivation.

| Label | Definition |
|-------|------------|
| **Small-dollar base** | Average donation under $200 |
| **Mixed donor base** | Average donation $200–$999 |
| **Industry-linked money** | Average donation $1,000+ |
| **Large-donor money** | Average donation $5,000+ |
| **Near-max donor money** | Federal donors near $3,300 limit |
| **Major institutional money** | Virginia state donors $50,000+ |

### Important Context: Federal vs Virginia State

**Federal contribution limits:**
Individual donors are legally capped at $3,300 per 
candidate per election cycle. A donor giving $3,300 
has reached the legal maximum.

**Virginia state contribution limits:**
Virginia has no contribution limits for state 
legislative races. Individuals, corporations, and 
party committees may give unlimited amounts. This 
is one of the least restrictive campaign finance 
environments in the United States.

VoteIQ always notes which level of government is 
being analyzed and applies context accordingly.

---

## How We Classify Industry Sectors

Donors are assigned to industry sectors based on:
- **Federal data:** Employer name from FEC Schedule A filings
- **Virginia state data:** OccupationOrTypeOfBusiness and 
  NameOfEmployer fields from Virginia SBE filings

| Sector | Classification Criteria |
|--------|------------------------|
| Defense | Employer matches known defense contractors |
| Finance | Banking, insurance, investment employers |
| Real Estate | Real estate, development, realty employers |
| Healthcare | Hospitals, medical practices, pharma employers |
| Legal | Law firms, attorneys |
| Energy | Utilities, oil, gas, renewable energy |
| Technology | Software, hardware, telecom employers |
| Education | Universities, schools, school boards |
| Agriculture | Farming, food production employers |
| Ideological | PACs with ideological designation |
| Party Committee | Official party caucus committees |
| Retired | Self-reported retired occupation |
| Self-Employed | Self-reported self-employment |
| Other/Unclassified | Employer not matched to known sector |

**Important limitation:** Sector classification depends 
on the accuracy of donor-reported employer information. 
VoteIQ does not independently verify employer data. 
Misclassifications may occur when employer names are 
abbreviated, misspelled, or unlisted.

---

## How We Calculate Funding Profiles

### Grassroots vs Large-Donor Split

VoteIQ calculates the percentage of total dollars raised 
from small-dollar vs large-donor sources. This reflects 
the composition of a funding base — not its legitimacy 
or legality.

All contributions reflected in VoteIQ data are legal 
under applicable federal and state law.

### Funding Tension Score

The Funding Tension Score reflects the degree to which 
a legislator's largest donor sectors align with or 
diverge from their committee assignments.

| Score | Meaning |
|-------|---------|
| Low | Donor sectors and committee assignments show limited overlap |
| Medium | Some overlap between donor sectors and committee jurisdiction |
| High | Significant overlap between top donor sectors and committee assignments |

**Critical note:** A High Funding Tension Score does not 
imply corruption, impropriety, or quid pro quo. It 
reflects a statistical pattern in public data only. 
Many factors influence both fundraising and committee 
assignments. Correlation does not imply causation.

---

## How We Analyze Voting Records

### Party Defection Classification

VoteIQ identifies votes where a legislator broke with 
their party majority and classifies each defection as:

| Classification | Meaning |
|---------------|---------|
| **Donor-aligned defection** | Broke with party on a bill related to a top donor sector |
| **Donor-contradicted defection** | Broke with party against the interest of a top donor sector |
| **Unrelated defection** | Break with party on a bill unrelated to donor sectors |

**Critical note:** Defection classifications reflect 
statistical relationships between voting patterns and 
donor data only. They do not imply that donations 
influenced votes in any way.

### Minority Party Context

In chambers where a legislator's party is in the 
minority, vote agreement rates will naturally appear 
lower. VoteIQ always flags minority party status when 
interpreting voting statistics.

---

## Known Data Limitations

| Limitation | Impact |
|------------|--------|
| FEC data covers itemized contributions only ($200+) | Small donors under $200 are not reflected in individual contribution analysis |
| Virginia SBE data quality varies by filing period | Older filings may have incomplete employer information |
| Sector classification depends on self-reported employer data | Misclassifications possible for abbreviated or unlisted employers |
| Congress.gov vote endpoint limitations | Some federal votes sourced from clerk.house.gov |
| Local city data | Currently limited to available FOIA responses |
| Dark money | 501(c)(4) expenditures not visible in FEC data |
| Super PAC money | Independent expenditures tracked separately from direct contributions |

---

## Correlation vs Causation

**VoteIQ does not claim that donations influence votes.**

All analysis reflects statistical patterns in public 
records. The presence of a relationship between donor 
sectors and voting patterns does not imply that any 
individual or organization acted improperly.

Campaign contributions are legal activities regulated 
by federal and state law. VoteIQ presents data for 
informational purposes only.

---

## Corrections Policy

VoteIQ is committed to data accuracy. If you identify 
an error in our data or analysis:

**Email:** [alexisnieuwenhuys89@gmail.com](mailto:alexisnieuwenhuys89@gmail.com)  
**Response time:** Within 48 hours  
**Correction process:** Verified errors are corrected 
and noted publicly

We welcome corrections from candidates, campaigns, 
journalists, and members of the public.

---

## Contact

**General inquiries:** [alexisnieuwenhuys89@gmail.com](mailto:alexisnieuwenhuys89@gmail.com)  
**Data corrections:** [alexisnieuwenhuys89@gmail.com](mailto:alexisnieuwenhuys89@gmail.com)  
**Press inquiries:** press@voteiq.io  
**Partnership inquiries:** partners@voteiq.io  

---

## About VoteIQ

VoteIQ is an independent civic technology platform 
built in Hampton Roads, Virginia.

VoteIQ is not affiliated with any political party, 
campaign, government agency, or advocacy organization.

VoteIQ receives no advertising revenue. 
Funding comes from subscription access and 
partnership agreements.

*Last updated: May 2026*  
*Data current through: May 2026*
