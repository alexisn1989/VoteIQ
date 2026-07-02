"""Static Virginia district and member reference data.

Extracted verbatim from root main.py (decomposition phase 2, 2026-07-01).
Canonical home for congressional/HOD/SD district contexts, prior-cycle party
baselines used by the flip maps, and the VA federal delegation FEC id map
(VA_MEMBERS, previously duplicated in ingest_fec_schedule_e.py and imported
from main by chat.py where it never existed — a latent ImportError).

Data-only module: no imports, no I/O, safe to import from anywhere.
"""

# VA federal delegation: bioguide_id -> (display name, FEC candidate id)
VA_MEMBERS = {
    "W000804": ("Rob Wittman",        "H8VA01147"),
    "K000399": ("Jennifer Kiggans",   "H2VA02064"),
    "S000185": ("Bobby Scott",        "H6VA01117"),
    "M001227": ("Jennifer McClellan", "H4VA04066"),
    "M001239": ("John McGuire",       "H0VA07133"),
    "C001118": ("Ben Cline",          "H8VA06104"),
    "V000138": ("Eugene Vindman",     "H4VA07234"),
    "B001292": ("Don Beyer",          "H4VA08224"),
    "G000568": ("Morgan Griffith",    "H0VA09055"),
    "S001230": ("Suhas Subramanyam",  "H4VA10279"),
    "W000831": ("James Walkinshaw",   "H6VA11066"),
    "W000805": ("Mark Warner",        "S6VA00093"),
    "K000384": ("Tim Kaine",          "S2VA00142"),
}

DISTRICT_CONTEXT = {
    "VA-00": {"rep": None, "party": None, "region": "Statewide — Virginia", "url": None},
    "VA-01": {"rep": "Rob Wittman",       "party": "Republican", "region": "Western Chesapeake Bay / suburban Richmond",                          "url": "https://wittman.house.gov/contact"},
    "VA-02": {"rep": "Jen Kiggans",       "party": "Republican", "region": "Hampton Roads (Virginia Beach, Chesapeake, Suffolk)",                  "url": "https://kiggans.house.gov/contact"},
    "VA-03": {"rep": "Bobby Scott",       "party": "Democrat",   "region": "Inner Hampton Roads (Newport News, Hampton, Norfolk)",                 "url": "https://bobbyscott.house.gov/contact"},
    "VA-04": {"rep": "Jennifer McClellan","party": "Democrat",   "region": "Richmond city and Southside Virginia",                                "url": "https://mcclellan.house.gov/contact"},
    "VA-05": {"rep": "John McGuire",      "party": "Republican", "region": "Central and Southside Virginia",                                       "url": "https://mcguire.house.gov/contact"},
    "VA-06": {"rep": "Ben Cline",         "party": "Republican", "region": "Western Virginia / Shenandoah Valley",                                 "url": "https://cline.house.gov/contact"},
    "VA-07": {"rep": "Eugene Vindman",    "party": "Democrat",   "region": "Northern Virginia suburbs / central Virginia",                         "url": "https://vindman.house.gov/contact"},
    "VA-08": {"rep": "Don Beyer",         "party": "Democrat",   "region": "Northern Virginia inner suburbs (Arlington, Alexandria)",               "url": "https://beyer.house.gov/contact"},
    "VA-09": {"rep": "Morgan Griffith",   "party": "Republican", "region": "Southwest Virginia",                                                   "url": "https://morgangriffith.house.gov/contact"},
    "VA-10": {"rep": "Suhas Subramanyam", "party": "Democrat",   "region": "Northern Virginia outer suburbs (Loudoun, Prince William)",             "url": "https://subramanyam.house.gov/contact"},
    "VA-11": {"rep": "James Walkinshaw",  "party": "Democrat",   "region": "Northern Virginia outer suburbs (Fairfax County)",                     "url": "https://walkinshaw.house.gov/contact"},
}

# Virginia House of Delegates — 100 districts (2021 redistricting, 2026 session members)
HOD_CONTEXT = {
    1:   {"delegate": "Patrick A. Hope",                    "party": "Democrat",    "locality": "Arlington"},
    2:   {"delegate": "Adele Y. McClure",                   "party": "Democrat",    "locality": "Arlington"},
    3:   {"delegate": "Alfonso H. Lopez",                   "party": "Democrat",    "locality": "Arlington/Alexandria"},
    4:   {"delegate": "Charniele L. Herring",               "party": "Democrat",    "locality": "Fairfax/Alexandria"},
    5:   {"delegate": "Elizabeth B. Bennett-Parker",        "party": "Democrat",    "locality": "Alexandria"},
    6:   {"delegate": "Richard C. Sullivan, Jr.",           "party": "Democrat",    "locality": "Fairfax"},
    7:   {"delegate": "Karen A. Keys-Gamarra",              "party": "Democrat",    "locality": "Fairfax"},
    8:   {"delegate": "Irene Shin",                         "party": "Democrat",    "locality": "Fairfax/Herndon"},
    9:   {"delegate": "Karrie K. Delaney",                  "party": "Democrat",    "locality": "Fairfax"},
    10:  {"delegate": "Dan I. Helmer",                      "party": "Democrat",    "locality": "Fairfax"},
    11:  {"delegate": "David L. Bulova",                    "party": "Democrat",    "locality": "Fairfax/Fairfax City"},
    12:  {"delegate": "Holly M. Seibold",                   "party": "Democrat",    "locality": "Fairfax"},
    13:  {"delegate": "Marcus B. Simon",                    "party": "Democrat",    "locality": "Fairfax/Falls Church"},
    14:  {"delegate": "Vivian E. Watts",                    "party": "Democrat",    "locality": "Fairfax"},
    15:  {"delegate": "Laura Jane H. Cohen",                "party": "Democrat",    "locality": "Fairfax"},
    16:  {"delegate": "Paul E. Krizek",                     "party": "Democrat",    "locality": "Fairfax"},
    17:  {"delegate": "Mark D. Sickles",                    "party": "Democrat",    "locality": "Fairfax"},
    18:  {"delegate": "Kathy K. L. Tran",                   "party": "Democrat",    "locality": "Fairfax"},
    19:  {"delegate": "Rozia A. Henson, Jr.",               "party": "Democrat",    "locality": "Fairfax/Prince William"},
    20:  {"delegate": "Michelle-Ann E. Lopes Maldonado",    "party": "Democrat",    "locality": "Prince William"},
    21:  {"delegate": "Joshua E. Thomas",                   "party": "Democrat",    "locality": "Prince William"},
    22:  {"delegate": "Elizabeth R. Guzman",                "party": "Democrat",    "locality": "Prince William"},
    23:  {"delegate": "Candi Patrice Mundon King",          "party": "Democrat",    "locality": "Prince William/Stafford"},
    24:  {"delegate": "Luke E. Torian",                     "party": "Democrat",    "locality": "Prince William"},
    25:  {"delegate": "Briana D. Sewell",                   "party": "Democrat",    "locality": "Prince William"},
    26:  {"delegate": "JJ Singh",                           "party": "Democrat",    "locality": "Loudoun"},
    27:  {"delegate": "Atoosa R. Reaser",                   "party": "Democrat",    "locality": "Loudoun"},
    28:  {"delegate": "David A. Reid",                      "party": "Democrat",    "locality": "Loudoun"},
    29:  {"delegate": "Fernando J. Martinez",               "party": "Democrat",    "locality": "Loudoun"},
    30:  {"delegate": "John Chilton McAuliff",              "party": "Democrat",    "locality": "Fauquier/Loudoun"},
    31:  {"delegate": "Delores R. Oates",                   "party": "Republican",  "locality": "Clarke/Frederick/Warren"},
    32:  {"delegate": "William D. Wiley",                   "party": "Republican",  "locality": "Frederick/Winchester"},
    33:  {"delegate": "Justin L. Pence",                    "party": "Republican",  "locality": "Page/Rockingham/Shenandoah/Warren"},
    34:  {"delegate": "Tony O. Wilt",                       "party": "Republican",  "locality": "Rockingham/Harrisonburg"},
    35:  {"delegate": "Chris S. Runion",                    "party": "Republican",  "locality": "Augusta/Bath/Highland/Rockingham"},
    36:  {"delegate": "Ellen H. Campbell",                  "party": "Republican",  "locality": "Augusta/Rockbridge/Staunton/Waynesboro"},
    37:  {"delegate": "Terry L. Austin",                    "party": "Republican",  "locality": "Alleghany/Botetourt/Craig/Rockbridge"},
    38:  {"delegate": "S. Sam Rasoul",                      "party": "Democrat",    "locality": "Roanoke"},
    39:  {"delegate": "Will P. Davis",                      "party": "Republican",  "locality": "Franklin/Roanoke"},
    40:  {"delegate": "Joseph P. McNamara",                 "party": "Republican",  "locality": "Roanoke/Salem"},
    41:  {"delegate": "Lily V. Franklin",                   "party": "Democrat",    "locality": "Montgomery/Roanoke"},
    42:  {"delegate": "Jason S. Ballard",                   "party": "Republican",  "locality": "Giles/Montgomery/Pulaski/Radford"},
    43:  {"delegate": "James W. Morefield",                 "party": "Republican",  "locality": "Bland/Buchanan/Dickenson/Russell/Tazewell"},
    44:  {"delegate": "Israel D. O'Quinn",                  "party": "Republican",  "locality": "Russell/Washington/Bristol"},
    45:  {"delegate": "Terry G. Kilgore",                   "party": "Republican",  "locality": "Lee/Scott/Wise/Norton"},
    46:  {"delegate": "Mitchell D. Cornett",                "party": "Republican",  "locality": "Grayson/Pulaski/Smyth/Wythe"},
    47:  {"delegate": "Wren M. Williams",                   "party": "Republican",  "locality": "Carroll/Floyd/Henry/Patrick/Galax"},
    48:  {"delegate": "Eric J. Phillips",                   "party": "Republican",  "locality": "Henry/Pittsylvania/Martinsville"},
    49:  {"delegate": "Madison John R. Whittle",            "party": "Republican",  "locality": "Halifax/Pittsylvania/Danville"},
    50:  {"delegate": "Thomas C. Wright, Jr.",              "party": "Republican",  "locality": "Charlotte/Halifax/Lunenburg/Mecklenburg/Prince Edward"},
    51:  {"delegate": "Eric R. Zehr",                       "party": "Republican",  "locality": "Bedford/Campbell/Pittsylvania"},
    52:  {"delegate": "Wendell S. Walker",                  "party": "Republican",  "locality": "Campbell/Lynchburg"},
    53:  {"delegate": "Timothy P. Griffin",                 "party": "Republican",  "locality": "Amherst/Bedford/Nelson"},
    54:  {"delegate": "Katrina E. Callsen",                 "party": "Democrat",    "locality": "Albemarle/Charlottesville"},
    55:  {"delegate": "Amy J. Laufer",                      "party": "Democrat",    "locality": "Albemarle/Fluvanna/Louisa/Nelson"},
    56:  {"delegate": "Thomas A. Garrett, Jr.",             "party": "Republican",  "locality": "Appomattox/Buckingham/Cumberland/Fluvanna/Goochland/Louisa/Prince Edward"},
    57:  {"delegate": "May Nivar",                          "party": "Democrat",    "locality": "Goochland/Henrico"},
    58:  {"delegate": "Rodney T. Willett",                  "party": "Democrat",    "locality": "Henrico"},
    59:  {"delegate": "H. F. Fowler Jr.",                   "party": "Republican",  "locality": "Hanover/Henrico/Louisa"},
    60:  {"delegate": "Scott A. Wyatt",                     "party": "Republican",  "locality": "Hanover/New Kent"},
    61:  {"delegate": "Michael J. Webert",                  "party": "Republican",  "locality": "Culpeper/Fauquier/Rappahannock"},
    62:  {"delegate": "Karen F. Hamilton",                  "party": "Republican",  "locality": "Culpeper/Greene/Madison/Orange"},
    63:  {"delegate": "Phillip A. Scott",                   "party": "Republican",  "locality": "Orange/Spotsylvania"},
    64:  {"delegate": "Stacey A. Carroll",                  "party": "Democrat",    "locality": "Stafford"},
    65:  {"delegate": "Joshua G. Cole",                     "party": "Democrat",    "locality": "Spotsylvania/Stafford/Fredericksburg"},
    66:  {"delegate": "Nicole Tarlton Cole",                "party": "Democrat",    "locality": "Caroline/Spotsylvania"},
    67:  {"delegate": "Hillary Pugh Kent",                  "party": "Republican",  "locality": "Caroline/King George/Lancaster/Northumberland/Richmond/Westmoreland"},
    68:  {"delegate": "M. Keith Hodges",                    "party": "Republican",  "locality": "Essex/Gloucester/King and Queen/King William/Mathews/Middlesex"},
    69:  {"delegate": "Mark C. Downey",                     "party": "Democrat",    "locality": "Gloucester/James City/York/Newport News"},
    70:  {"delegate": "Shelly A. Simonds",                  "party": "Democrat",    "locality": "Newport News"},
    71:  {"delegate": "Jessica L. Anderson",                "party": "Democrat",    "locality": "James City/New Kent/Williamsburg"},
    72:  {"delegate": "R. Lee Ware Jr.",                    "party": "Republican",  "locality": "Amelia/Chesterfield/Nottoway/Powhatan"},
    73:  {"delegate": "Leslie C. Mehta",                    "party": "Democrat",    "locality": "Chesterfield"},
    74:  {"delegate": "Mike A. Cherry",                     "party": "Republican",  "locality": "Chesterfield/Colonial Heights"},
    75:  {"delegate": "Lindsey M. Dougherty",               "party": "Democrat",    "locality": "Chesterfield/Prince George/Hopewell"},
    76:  {"delegate": "Debra D. Gardner",                   "party": "Democrat",    "locality": "Chesterfield"},
    77:  {"delegate": "Michael J. Jones",                   "party": "Democrat",    "locality": "Chesterfield/Richmond"},
    78:  {"delegate": "Betsy B. Carr",                      "party": "Democrat",    "locality": "Richmond"},
    79:  {"delegate": "Rae C. Cousins",                     "party": "Democrat",    "locality": "Richmond"},
    80:  {"delegate": "Destiny L. LeVere Bolling",          "party": "Democrat",    "locality": "Henrico"},
    81:  {"delegate": "Delores L. McQuinn",                 "party": "Democrat",    "locality": "Charles City/Chesterfield/Henrico"},
    82:  {"delegate": "Kimberly Pope Adams",                "party": "Democrat",    "locality": "Dinwiddie/Prince George/Surry/Petersburg"},
    83:  {"delegate": "H. Otto Wachsmann, Jr.",             "party": "Republican",  "locality": "Brunswick/Dinwiddie/Greensville/Isle of Wight/Southampton/Sussex/Emporia"},
    84:  {"delegate": "Nadarius E. Clark",                  "party": "Democrat",    "locality": "Chesapeake/Isle of Wight/Franklin/Suffolk"},
    85:  {"delegate": "Marcia S. Price",                    "party": "Democrat",    "locality": "Newport News"},
    86:  {"delegate": "Virgil G. Thornton, Sr.",            "party": "Democrat",    "locality": "York/Hampton/Poquoson"},
    87:  {"delegate": "Jeion A. Ward",                      "party": "Democrat",    "locality": "Hampton"},
    88:  {"delegate": "Don L. Scott Jr.",                   "party": "Democrat",    "locality": "Portsmouth"},
    89:  {"delegate": "Karen Robins Carnegie",              "party": "Democrat",    "locality": "Chesapeake/Suffolk"},
    90:  {"delegate": "James A. Leftwich, Jr.",             "party": "Republican",  "locality": "Chesapeake"},
    91:  {"delegate": "C. E. Hayes Jr.",                    "party": "Democrat",    "locality": "Chesapeake/Portsmouth"},
    92:  {"delegate": "Bonita Grace Anthony",               "party": "Democrat",    "locality": "Chesapeake/Norfolk"},
    93:  {"delegate": "Jackie Hope Glass",                  "party": "Democrat",    "locality": "Norfolk"},
    94:  {"delegate": "Phil M. Hernandez",                  "party": "Democrat",    "locality": "Norfolk"},
    95:  {"delegate": "Alex Q. Askew",                      "party": "Democrat",    "locality": "Norfolk/Virginia Beach"},
    96:  {"delegate": "Kelly K. Convirs-Fowler",            "party": "Democrat",    "locality": "Virginia Beach"},
    97:  {"delegate": "Michael B. Feggans",                 "party": "Democrat",    "locality": "Virginia Beach"},
    98:  {"delegate": "Barry D. Knight",                    "party": "Republican",  "locality": "Virginia Beach"},
    99:  {"delegate": "Anne Ferrell Tata",                  "party": "Republican",  "locality": "Virginia Beach"},
    100: {"delegate": "Robert S. Bloxom Jr.",               "party": "Republican",  "locality": "Accomack/Northampton/Virginia Beach"},
}

# 2023-2025 session composition — 51 R, 49 D (used for flip map comparison)
HOD_2023_PARTY = {d: "Republican" for d in [
    21, 22, 30, 31, 32, 33, 34, 35, 36, 37, 39, 40, 41,
    42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53,
    56, 57, 59, 60, 61, 62, 63, 64, 66, 67, 68, 69,
    71, 72, 73, 74, 75, 82, 83, 86, 89, 90, 97, 98, 99, 100,
]}

# Prior-cycle numbered district baselines. These were elected under pre-2021
# redistricting boundaries, so 2023 flip views label them as numbered-district
# comparisons rather than same-boundary seat flips.
# HOD_2021_PARTY: old pre-redistricting district numbers (2011 maps, used only for pre-2023 HOD flip reference)
HOD_2021_PARTY = {d: "Republican" for d in [
    1, 3, 4, 5, 6, 7, 8, 9, 12, 14, 15, 16, 17, 18, 19, 20,
    22, 23, 24, 25, 26, 27, 28, 29, 30, 33, 54, 55, 56, 58,
    59, 60, 61, 62, 63, 64, 65, 66, 75, 78, 81, 82, 83, 84,
    85, 88, 91, 96, 97, 98, 99, 100,
]}
HOD_2021_PARTY.update({
    d: "Democrat" for d in range(1, 101) if d not in HOD_2021_PARTY
})

SD_2019_PARTY = {d: "Republican" for d in [
    3, 4, 7, 8, 11, 12, 14, 15, 17, 19, 20, 22, 23, 24, 26,
    27, 28, 38, 40,
]}
SD_2019_PARTY.update({
    d: "Democrat" for d in range(1, 41) if d not in SD_2019_PARTY
})

# HOD_PRE2021_PARTY: pre-election party for each NEW 2021-redistricted HOD district (used for 2021 flip map)
# Derived by matching 2019 HOD winners (by name) to 2021 candidates; unmatched open seats default to 2021 winner.
# Pre-election composition: 46 R, 54 D → result 50 R, 50 D (net +4 R)
HOD_PRE2021_PARTY = {d: "Republican" for d in [
    1, 3, 4, 5, 6, 7, 8, 9, 14, 15, 16, 17, 18, 19, 20,
    22, 23, 24, 25, 26, 27, 29, 30, 33, 51, 54, 55, 56, 58,
    59, 60, 61, 62, 64, 65, 66, 78, 81, 82, 84, 88, 96, 97, 98, 99, 100,
]}
HOD_PRE2021_PARTY.update({
    d: "Democrat" for d in range(1, 101) if d not in HOD_PRE2021_PARTY
})

# HOD_PRE2023_PARTY: pre-election party for each NEW 2021-redistricted HOD district (used for 2023 flip map)
# Derived from 2021 incumbent name matching + winner defaults for open seats.
# Pre-election composition: 50 R, 50 D → result 49 R, 51 D (net +1 D)
HOD_PRE2023_PARTY = {d: "Republican" for d in [
    22, 30, 31, 32, 33, 34, 35, 36, 37, 39, 40, 41, 42, 43, 44, 45, 46, 47,
    48, 49, 50, 51, 52, 53, 56, 57, 59, 60, 61, 62, 63, 64, 66, 67, 68, 69,
    71, 72, 73, 74, 75, 82, 83, 86, 89, 90, 97, 98, 99, 100,
]}
HOD_PRE2023_PARTY.update({
    d: "Democrat" for d in range(1, 101) if d not in HOD_PRE2023_PARTY
})

# SD_PRE2023_PARTY: pre-election party for each NEW 2021-redistricted Senate district (used for 2023 flip map)
# Pre-election composition: 19 R, 21 D → result 18 R, 22 D (net +1 D, SD16 flipped D)
# SD16: Siobhan Dunnavant (R incumbent) ran and lost to VanValkenburg (D) — confirmed flip
SD_PRE2023_PARTY = {d: "Republican" for d in [
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 16, 17, 19, 20, 25, 26, 27, 28,
]}
SD_PRE2023_PARTY.update({
    d: "Democrat" for d in range(1, 41) if d not in SD_PRE2023_PARTY
})

# Virginia State Senate — 40 districts (2021 redistricting, 2026 session members)
_HOD_BASE = "https://house.vga.virginia.gov/members/{}"
_HOD_MEMBER_URL = {
    1:"H0219",2:"H0375",3:"H0239",4:"H0208",5:"H0406",6:"H0269",7:"H0370",8:"H0344",9:"H0294",10:"H0317",
    11:"H0403",12:"H0351",13:"H0264",14:"H0108",15:"H0355",16:"H0281",17:"H0405",18:"H0305",19:"H0365",20:"H0340",
    21:"H0382",22:"H0297",23:"H0404",24:"H0227",25:"H0343",26:"H0385",27:"H0380",28:"H0301",29:"H0374",30:"H0395",
    31:"H0377",32:"H0329",33:"H0398",34:"H0231",35:"H0321",36:"H0350",37:"H0253",38:"H0266",39:"H0357",40:"H0308",
    41:"H0393",42:"H0333",43:"H0224",44:"H0242",45:"H0056",46:"H0390",47:"H0348",48:"H0384",49:"H0401",50:"H0136",
    51:"H0383",52:"H0325",53:"H0364",54:"H0354",55:"H0371",56:"H0362",57:"H0397",58:"H0327",59:"H0259",60:"H0328",
    61:"H0247",62:"H0394",63:"H0342",64:"H0388",65:"H0314",66:"H0389",67:"H0369",68:"H0238",69:"H0392",70:"H0323",
    71:"H0386",72:"H0124",73:"H0396",74:"H0335",75:"H0391",76:"H0361",77:"H0402",78:"H0212",79:"H0356",80:"H0372",
    81:"H0207",82:"H0399",83:"H0347",84:"H0336",85:"H0284",86:"H0400",87:"H0173",88:"H0322",89:"H0387",90:"H0262",
    91:"H0285",92:"H0353",93:"H0349",94:"H0366",95:"H0311",96:"H0295",97:"H0360",98:"H0407",99:"H0345",100:"H0267",
}
_HOD_MEMBER_URL = {d: _HOD_BASE.format(mid) for d, mid in _HOD_MEMBER_URL.items()}

_SD_URL = "https://apps.senate.virginia.gov/Senator/memberpage.php?id={}"
SD_CONTEXT = {
    1:  {"senator": "Timmy French",             "party": "Republican", "region": "Clarke, Frederick, Shenandoah, Warren; Winchester",                                                                           "url": _SD_URL.format("S121")},
    2:  {"senator": "Mark Obenshain",           "party": "Republican", "region": "Augusta, Bath, Highland, Page, Rockingham; Harrisonburg",                                                                    "url": _SD_URL.format("S68")},
    3:  {"senator": "Chris Head",               "party": "Republican", "region": "Alleghany, Augusta, Bedford, Botetourt, Craig, Roanoke, Rockbridge",                                                         "url": _SD_URL.format("S122")},
    4:  {"senator": "Dave Suetterlein",         "party": "Republican", "region": "Montgomery, Roanoke; Roanoke, Salem",                                                                                        "url": _SD_URL.format("S101")},
    5:  {"senator": "Travis Hackworth",         "party": "Republican", "region": "Bland, Giles, Montgomery, Pulaski, Smyth, Tazewell, Wythe; Radford",                                                        "url": _SD_URL.format("S112")},
    6:  {"senator": "Todd Pillion",             "party": "Republican", "region": "Buchanan, Dickenson, Lee, Russell, Scott, Washington, Wise; Bristol, Norton",                                               "url": _SD_URL.format("S111")},
    7:  {"senator": "Bill Stanley",             "party": "Republican", "region": "Carroll, Floyd, Franklin, Grayson, Henry, Patrick, Wythe; Martinsville, Galax",                                             "url": _SD_URL.format("S82")},
    8:  {"senator": "Mark Peake",               "party": "Republican", "region": "Bedford, Campbell; Lynchburg",                                                                                               "url": _SD_URL.format("S105")},
    9:  {"senator": "Tammy Brankley Mulchi",    "party": "Republican", "region": "Charlotte, Halifax, Lunenburg, Mecklenburg, Nottoway, Pittsylvania, Prince Edward; Danville",                               "url": _SD_URL.format("S131")},
    10: {"senator": "Luther Cifers",            "party": "Republican", "region": "Amelia, Appomattox, Buckingham, Cumberland, Fluvanna, Goochland, Hanover, Henrico, Louisa, Powhatan, Prince Edward",        "url": _SD_URL.format("S132")},
    11: {"senator": "Creigh Deeds",             "party": "Democrat",   "region": "Albemarle, Amherst, Louisa, Nelson; Charlottesville",                                                                       "url": _SD_URL.format("S62")},
    12: {"senator": "Glen Sturtevant",          "party": "Republican", "region": "Chesterfield; Colonial Heights",                                                                                             "url": _SD_URL.format("S99")},
    13: {"senator": "Lashrecse Aird",           "party": "Democrat",   "region": "Charles City, Dinwiddie, Henrico, Prince George, Surry, Sussex; Hopewell, Petersburg",                                      "url": _SD_URL.format("S115")},
    14: {"senator": "Lamont Bagby",             "party": "Democrat",   "region": "Henrico; Richmond",                                                                                                          "url": _SD_URL.format("S114")},
    15: {"senator": "Michael Jones",            "party": "Democrat",   "region": "Chesterfield; Richmond",                                                                                                     "url": _SD_URL.format("S134")},
    16: {"senator": "Schuyler VanValkenburg",   "party": "Democrat",   "region": "Henrico",                                                                                                                    "url": _SD_URL.format("S129")},
    17: {"senator": "Emily Jordan",             "party": "Republican", "region": "Brunswick, Dinwiddie, Greensville, Isle of Wight, Southampton; Chesapeake, Emporia, Franklin, Portsmouth, Suffolk",         "url": _SD_URL.format("S116")},
    18: {"senator": "L. Louise Lucas",          "party": "Democrat",   "region": "Chesapeake, Portsmouth",                                                                                                     "url": _SD_URL.format("S19")},
    19: {"senator": "Christie New Craig",       "party": "Republican", "region": "Chesapeake, Virginia Beach",                                                                                                 "url": _SD_URL.format("S118")},
    20: {"senator": "Bill DeSteph",             "party": "Republican", "region": "Accomack, Northampton; Norfolk, Virginia Beach",                                                                             "url": _SD_URL.format("S96")},
    21: {"senator": "Angelia Williams Graves",  "party": "Democrat",   "region": "Norfolk",                                                                                                                    "url": _SD_URL.format("S130")},
    22: {"senator": "Aaron Rouse",              "party": "Democrat",   "region": "Virginia Beach",                                                                                                             "url": _SD_URL.format("S113")},
    23: {"senator": "Mamie Locke",              "party": "Democrat",   "region": "Hampton; Newport News",                                                                                                      "url": _SD_URL.format("S67")},
    24: {"senator": "Danny Diggs",              "party": "Republican", "region": "James City, York; Newport News, Poquoson, Williamsburg",                                                                     "url": _SD_URL.format("S119")},
    25: {"senator": "Richard Stuart",           "party": "Republican", "region": "Caroline, Essex, King George, King William, Lancaster, Middlesex, Northumberland, Richmond, Spotsylvania, Westmoreland",    "url": _SD_URL.format("S78")},
    26: {"senator": "Ryan McDougle",            "party": "Republican", "region": "Gloucester, Hanover, James City, Mathews, New Kent",                                                                        "url": _SD_URL.format("S69")},
    27: {"senator": "Tara Durant",              "party": "Republican", "region": "Spotsylvania, Stafford; Fredericksburg",                                                                                     "url": _SD_URL.format("S120")},
    28: {"senator": "Bryce Reeves",             "party": "Republican", "region": "Culpeper, Fauquier, Greene, Madison, Orange, Rappahannock, Spotsylvania",                                                   "url": _SD_URL.format("S88")},
    29: {"senator": "Jeremy McPike",            "party": "Democrat",   "region": "Prince William, Stafford",                                                                                                   "url": _SD_URL.format("S98")},
    30: {"senator": "Danica Roem",              "party": "Democrat",   "region": "Prince William; Manassas, Manassas Park",                                                                                    "url": _SD_URL.format("S126")},
    31: {"senator": "Russet Perry",             "party": "Democrat",   "region": "Fauquier, Loudoun",                                                                                                          "url": _SD_URL.format("S125")},
    32: {"senator": "Kannan Srinivasan",        "party": "Democrat",   "region": "Loudoun",                                                                                                                    "url": _SD_URL.format("S133")},
    33: {"senator": "Jennifer Carroll Foy",     "party": "Democrat",   "region": "Fairfax, Prince William",                                                                                                    "url": _SD_URL.format("S117")},
    34: {"senator": "Scott Surovell",           "party": "Democrat",   "region": "Fairfax",                                                                                                                    "url": _SD_URL.format("S100")},
    35: {"senator": "Dave Marsden",             "party": "Democrat",   "region": "Fairfax",                                                                                                                    "url": _SD_URL.format("S80")},
    36: {"senator": "Stella Pekarsky",          "party": "Democrat",   "region": "Fairfax",                                                                                                                    "url": _SD_URL.format("S124")},
    37: {"senator": "Saddam Azlan Salim",       "party": "Democrat",   "region": "Fairfax; Fairfax City, Falls Church",                                                                                       "url": _SD_URL.format("S127")},
    38: {"senator": "Jennifer Boysko",          "party": "Democrat",   "region": "Fairfax",                                                                                                                    "url": _SD_URL.format("S106")},
    39: {"senator": "Elizabeth Bennett-Parker", "party": "Democrat",   "region": "Arlington, Fairfax; Alexandria",                                                                                             "url": _SD_URL.format("S135")},
    40: {"senator": "Barbara Favola",           "party": "Democrat",   "region": "Arlington",                                                                                                                  "url": _SD_URL.format("S86")},
}

