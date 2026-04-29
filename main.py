from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
import uvicorn
import folium
import geopandas as gpd
import os
import json
import requests
import anthropic
from address_lookup import find_district

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

# ── Election maps — all four modes built once at startup ──────────────────────
import copy
import math
from shapely.geometry import shape

_election_maps: dict[str, str] = {}

_MODE_LABELS = {
    "total":   "All Votes",
    "early":   "Early Voting",
    "eday":    "Election Day",
    "mail":    "Mail-In (Absentee)",
    "density": "Vote Density",
}

# Parsed election data — loaded once on first map request, reused across modes
_election_data_cache: dict = {}

def _load_election_data():
    if _election_data_cache:
        return _election_data_cache
    with open(os.path.join(BASE_DIR, "data_2026_special.json"), encoding="utf-8") as f:
        data = json.load(f)
    county_data, city_data = {}, {}
    for j in data.get("localResults", []):
        raw = j["name"].strip().upper()
        yes_v = no_v = yes_ev = no_ev = yes_ed = no_ed = yes_ml = no_ml = 0
        for item in j.get("ballotItems", []):
            for opt in item.get("ballotOptions", []):
                grps = {g["groupName"]: g["voteCount"] for g in opt.get("groupResults", [])}
                if opt["name"].upper() == "YES":
                    yes_v  += opt["voteCount"]
                    yes_ev += grps.get("Early Voting", 0)
                    yes_ed += grps.get("Election Day", 0)
                    yes_ml += grps.get("Mailed Absentee", 0)
                elif opt["name"].upper() == "NO":
                    no_v   += opt["voteCount"]
                    no_ev  += grps.get("Early Voting", 0)
                    no_ed  += grps.get("Election Day", 0)
                    no_ml  += grps.get("Mailed Absentee", 0)
        info = {
            "yes": yes_v, "no": no_v,
            "early_yes": yes_ev, "early_no": no_ev,
            "eday_yes":  yes_ed, "eday_no":  no_ed,
            "mail_yes":  yes_ml, "mail_no":  no_ml,
            "display_name": j["name"].strip(),
        }
        key = raw[:-7].strip() if raw.endswith(" COUNTY") else \
              raw[:-5].strip() if raw.endswith(" CITY") else \
              raw.replace("&", "AND").replace("  ", " ").strip()
        (city_data if raw.endswith(" CITY") else county_data)[key] = info
    with open(os.path.join(BASE_DIR, "va_counties.json"), encoding="utf-8") as gf:
        base_features = json.load(gf)["features"]
    _election_data_cache["county"] = county_data
    _election_data_cache["city"]   = city_data
    _election_data_cache["features"] = base_features
    return _election_data_cache


def _build_election_map(mode: str) -> str:
    """Build a single election results map for the given mode."""
    ed = _load_election_data()
    county_data  = ed["county"]
    city_data    = ed["city"]
    base_features = ed["features"]

    features = copy.deepcopy(base_features)
    for feat in features:
        fips3   = int(feat["properties"]["GEO_ID"][-3:])
        name_up = feat["properties"]["NAME"].strip().upper()
        r = (city_data if fips3 > 199 else county_data).get(name_up)
        if r:
            y = r[f"{mode}_yes"] if mode not in ("total", "density") else r["yes"]
            n = r[f"{mode}_no"]  if mode not in ("total", "density") else r["no"]
            t = y + n
            feat["properties"].update({
                "display_name": r["display_name"],
                "yes": y, "no": n, "total": t,
                "pct_yes": round(y / t * 100, 1) if t else None,
                "winner": ("Yes" if y >= n else "No") if t else "No data",
            })
        else:
            feat["properties"].update({
                "display_name": feat["properties"]["NAME"],
                "pct_yes": None, "winner": "No data",
                "yes": 0, "no": 0, "total": 0,
            })

    m = folium.Map(location=[37.5, -79.0], zoom_start=7, tiles="CartoDB positron", min_zoom=6)
    map_var = m.get_name()

    if mode == "density":
        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=lambda f: {"fillColor": "#f5f5f5", "color": "#bbb", "weight": 0.7, "fillOpacity": 0.5},
        ).add_to(m)
        max_total = max((f["properties"]["total"] for f in features), default=1) or 1
        for feat in features:
            props = feat["properties"]
            total = props["total"]
            if total == 0:
                continue
            try:
                centroid = shape(feat["geometry"]).centroid
            except Exception:
                continue
            radius = 4 + math.sqrt(total / max_total) * 34
            fill_color = "#1a52c8" if props["winner"] == "Yes" else "#ff4444"
            pct = props["pct_yes"] or 50
            folium.CircleMarker(
                location=[centroid.y, centroid.x], radius=radius,
                color="white", weight=1, fill=True,
                fill_color=fill_color, fill_opacity=0.78,
                tooltip=(
                    f"<b style='font-family:Arial'>{props['display_name']}</b><br>"
                    f"Total votes: <b>{total:,}</b><br>"
                    f"Yes: {props['yes']:,} ({pct}%)<br>"
                    f"No: {props['no']:,} ({100-pct}%)<br>"
                    f"Winner: <b>{props['winner']}</b>"
                ),
            ).add_to(m)
        legend_html = """
        <b style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;color:#555">Vote Density</b><br>
        <span style="background:#1a52c8;color:white;padding:2px 10px;border-radius:3px">Yes won</span>
        &nbsp;
        <span style="background:#ff4444;color:white;padding:2px 10px;border-radius:3px">No won</span>
        <br><small style="color:#666">Bigger circle = more votes cast</small>"""
    else:
        def style_fn(feat):
            pct = feat["properties"].get("pct_yes")
            if pct is None:
                return {"fillColor": "#cccccc", "color": "#888", "weight": 0.8, "fillOpacity": 0.5}
            if pct >= 50:
                intensity = int(80 + (pct - 50) / 50 * 175)
                color = f"#{255-intensity:02x}{255-intensity:02x}ff"
            else:
                intensity = int(80 + (50 - pct) / 50 * 175)
                color = f"#ff{255-intensity:02x}{255-intensity:02x}"
            return {"fillColor": color, "color": "#444", "weight": 0.5, "fillOpacity": 0.85}
        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=style_fn,
            tooltip=folium.GeoJsonTooltip(
                fields=["display_name", "yes", "no", "pct_yes", "winner"],
                aliases=["Jurisdiction:", "Yes votes:", "No votes:", "Yes %:", "Winner:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        legend_html = (
            f"<b style='font-size:11px;letter-spacing:0.06em;text-transform:uppercase;color:#555'>"
            f"{_MODE_LABELS[mode]}</b><br>"
            f"<span style='background:#1a52c8;color:white;padding:2px 10px;border-radius:3px'>Yes</span>"
            f"&nbsp;"
            f"<span style='background:#ff4444;color:white;padding:2px 10px;border-radius:3px'>No</span>"
            f"<br><small style='color:#666'>Deeper color = larger margin</small>"
        )

    m.get_root().html.add_child(folium.Element(f"""
    <div style="position:fixed;bottom:40px;left:40px;z-index:1000;
         background:white;padding:12px 16px;border-radius:8px;
         box-shadow:2px 2px 8px rgba(0,0,0,.3);font-family:Arial;font-size:13px;line-height:1.8">
      {legend_html}
    </div>
    """))
    rendered = m.get_root().render()
    bounds_js = (
        f"<script>"
        f"{map_var}.setMaxBounds([[35.9,-84.8],[39.7,-74.9]]);"
        f"{map_var}.options.maxBoundsViscosity=1.0;"
        f"</script>"
    )
    return rendered.replace("</html>", bounds_js + "</html>")




# ── Election results cache (for chat context) ─────────────────────────────────
def _load_election_results():
    with open(os.path.join(BASE_DIR, "data_2026_special.json"), encoding="utf-8") as f:
        data = json.load(f)

    def groups(opts, name):
        o = next((x for x in opts if x["name"].upper() == name), {})
        g = {x["groupName"]: x["voteCount"] for x in o.get("groupResults", [])}
        return o.get("voteCount", 0), g.get("Early Voting", 0), g.get("Election Day", 0), g.get("Mailed Absentee", 0)

    sw = data.get("results", {}).get("ballotItems", [{}])[0].get("ballotOptions", [])
    yes_tot, yes_ev, yes_ed, yes_ml = groups(sw, "YES")
    no_tot,  no_ev,  no_ed,  no_ml  = groups(sw, "NO")
    grand = yes_tot + no_tot

    local = {}
    for j in data.get("localResults", []):
        raw = j["name"].strip().upper()
        y = n = 0
        for item in j.get("ballotItems", []):
            for opt in item.get("ballotOptions", []):
                if opt["name"].upper() == "YES": y += opt["voteCount"]
                elif opt["name"].upper() == "NO": n += opt["voteCount"]
        t = y + n
        key = raw[:-7].strip() if raw.endswith(" COUNTY") else raw[:-5].strip() if raw.endswith(" CITY") else raw
        local[key] = {"yes": y, "no": n, "total": t,
                      "pct_yes": round(y/t*100,1) if t else 50,
                      "winner": "Yes" if y >= n else "No",
                      "display": j["name"].strip()}

    return {
        "yes": yes_tot, "no": no_tot, "total": grand,
        "yes_pct": round(yes_tot/grand*100,1) if grand else 50,
        "no_pct":  round(no_tot /grand*100,1) if grand else 50,
        "winner": "YES (Approve)" if yes_tot >= no_tot else "NO (Reject)",
        "early": {"yes": yes_ev, "no": no_ev},
        "election_day": {"yes": yes_ed, "no": no_ed},
        "mail": {"yes": yes_ml, "no": no_ml},
        "local": local,
    }

try:
    _results = _load_election_results()
    print("Election results cache ready.")
except Exception as e:
    _results = None
    print(f"Warning: could not load election results: {e}")

# All district GDFs are lazy — borrowed from address_lookup on first use
_va_cd_gdf  = None
_va_hod_gdf = None
_va_sd_gdf  = None

def _get_va_cd():
    global _va_cd_gdf
    if _va_cd_gdf is None:
        import address_lookup as _al
        if _al.va_cd is not None:
            _va_cd_gdf = _al.va_cd
        else:
            _va_cd_gdf = gpd.read_file(os.path.join(BASE_DIR, "tl_2023_51_cd118.shp"))
            _va_cd_gdf = _va_cd_gdf.to_crs(epsg=4326)
            _va_cd_gdf = _va_cd_gdf[['NAMELSAD', 'CD118FP', 'geometry']]
            _al.va_cd = _va_cd_gdf
    return _va_cd_gdf

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

DISTRICT_CONTEXT = {
    "VA-00": {"rep": None, "party": None, "region": "Statewide — Virginia"},
    "VA-01": {"rep": "Rob Wittman",       "party": "Republican", "region": "Western Chesapeake Bay / suburban Richmond"},
    "VA-02": {"rep": "Jen Kiggans",        "party": "Republican", "region": "Hampton Roads (Virginia Beach, Chesapeake, Suffolk)"},
    "VA-03": {"rep": "Bobby Scott",        "party": "Democrat",   "region": "Inner Hampton Roads (Newport News, Hampton, Norfolk)"},
    "VA-04": {"rep": "Jennifer McClellan", "party": "Democrat",   "region": "Richmond city and Southside Virginia"},
    "VA-05": {"rep": "John McGuire",       "party": "Republican", "region": "Central and Southside Virginia"},
    "VA-06": {"rep": "Ben Cline",          "party": "Republican", "region": "Western Virginia / Shenandoah Valley"},
    "VA-07": {"rep": "Eugene Vindman",     "party": "Democrat",   "region": "Northern Virginia suburbs / central Virginia"},
    "VA-08": {"rep": "Don Beyer",          "party": "Democrat",   "region": "Northern Virginia inner suburbs (Arlington, Alexandria)"},
    "VA-09": {"rep": "Morgan Griffith",    "party": "Republican", "region": "Southwest Virginia"},
    "VA-10": {"rep": "Suhas Subramanyam",  "party": "Democrat",   "region": "Northern Virginia outer suburbs (Loudoun, Prince William)"},
    "VA-11": {"rep": "James Walkinshaw",   "party": "Democrat",   "region": "Northern Virginia outer suburbs (Fairfax County)"},
}

# Virginia House of Delegates — 100 districts (2021 redistricting, 2026 session members)
HOD_CONTEXT = {
    1:   {"delegate": "Patrick A. Hope",              "party": "Democrat",    "locality": "Arlington"},
    2:   {"delegate": "Adele Y. McClure",             "party": "Democrat",    "locality": "Arlington"},
    3:   {"delegate": "Alfonso H. Lopez",             "party": "Democrat",    "locality": "Arlington/Alexandria"},
    4:   {"delegate": "Charniele L. Herring",         "party": "Democrat",    "locality": "Fairfax/Alexandria"},
    5:   {"delegate": "R. Kirk McPike",               "party": "Democrat",    "locality": "Alexandria"},
    6:   {"delegate": "Richard C. Sullivan, Jr.",     "party": "Democrat",    "locality": "Fairfax"},
    7:   {"delegate": "Karen Keys-Gamarra",           "party": "Democrat",    "locality": "Fairfax"},
    8:   {"delegate": "Irene Shin",                   "party": "Democrat",    "locality": "Fairfax/Herndon"},
    9:   {"delegate": "Karrie K. Delaney",            "party": "Democrat",    "locality": "Fairfax"},
    10:  {"delegate": "Dan Helmer",                   "party": "Democrat",    "locality": "Fairfax"},
    11:  {"delegate": "Gretchen M. Bulova",           "party": "Democrat",    "locality": "Fairfax/Fairfax City"},
    12:  {"delegate": "Holly M. Seibold",             "party": "Democrat",    "locality": "Fairfax"},
    13:  {"delegate": "Marcus B. Simon",              "party": "Democrat",    "locality": "Fairfax/Falls Church"},
    14:  {"delegate": "Vivian E. Watts",              "party": "Democrat",    "locality": "Fairfax"},
    15:  {"delegate": "Laura Jane Cohen",             "party": "Democrat",    "locality": "Fairfax"},
    16:  {"delegate": "Paul E. Krizek",               "party": "Democrat",    "locality": "Fairfax"},
    17:  {"delegate": "Garrett McGuire",              "party": "Democrat",    "locality": "Fairfax"},
    18:  {"delegate": "Kathy KL Tran",                "party": "Democrat",    "locality": "Fairfax"},
    19:  {"delegate": "Rozia A. Henson, Jr.",         "party": "Democrat",    "locality": "Fairfax/Prince William"},
    20:  {"delegate": "Michelle Lopes Maldonado",     "party": "Democrat",    "locality": "Prince William"},
    21:  {"delegate": "Josh Thomas",                  "party": "Democrat",    "locality": "Prince William"},
    22:  {"delegate": "Elizabeth R. Guzman",          "party": "Democrat",    "locality": "Prince William"},
    23:  {"delegate": "Margaret Angela Franklin",     "party": "Democrat",    "locality": "Prince William/Stafford"},
    24:  {"delegate": "Luke E. Torian",               "party": "Democrat",    "locality": "Prince William"},
    25:  {"delegate": "Briana D. Sewell",             "party": "Democrat",    "locality": "Prince William"},
    26:  {"delegate": "JJ Singh",                     "party": "Democrat",    "locality": "Loudoun"},
    27:  {"delegate": "Atoosa R. Reaser",             "party": "Democrat",    "locality": "Loudoun"},
    28:  {"delegate": "David A. Reid",                "party": "Democrat",    "locality": "Loudoun"},
    29:  {"delegate": "Fernando J. Martinez",         "party": "Democrat",    "locality": "Loudoun"},
    30:  {"delegate": "John C. McAuliff",             "party": "Democrat",    "locality": "Fauquier/Loudoun"},
    31:  {"delegate": "Delores Oates",                "party": "Republican",  "locality": "Clarke/Frederick/Warren"},
    32:  {"delegate": "William D. Wiley",             "party": "Republican",  "locality": "Frederick/Winchester"},
    33:  {"delegate": "Justin L. Pence",              "party": "Republican",  "locality": "Page/Rockingham/Shenandoah/Warren"},
    34:  {"delegate": "Tony O. Wilt",                 "party": "Republican",  "locality": "Rockingham/Harrisonburg"},
    35:  {"delegate": "Chris Runion",                 "party": "Republican",  "locality": "Augusta/Bath/Highland/Rockingham"},
    36:  {"delegate": "Ellen H. McLaughlin",          "party": "Republican",  "locality": "Augusta/Rockbridge/Staunton/Waynesboro"},
    37:  {"delegate": "Terry L. Austin",              "party": "Republican",  "locality": "Alleghany/Botetourt/Craig/Rockbridge"},
    38:  {"delegate": "Sam Rasoul",                   "party": "Democrat",    "locality": "Roanoke"},
    39:  {"delegate": "Will P. Davis",                "party": "Republican",  "locality": "Franklin/Roanoke"},
    40:  {"delegate": "Joseph P. McNamara",           "party": "Republican",  "locality": "Roanoke/Salem"},
    41:  {"delegate": "Lily V. Franklin",             "party": "Democrat",    "locality": "Montgomery/Roanoke"},
    42:  {"delegate": "Jason S. Ballard",             "party": "Republican",  "locality": "Giles/Montgomery/Pulaski/Radford"},
    43:  {"delegate": "James W. Morefield",           "party": "Republican",  "locality": "Bland/Buchanan/Dickenson/Russell/Tazewell"},
    44:  {"delegate": "Israel D. O'Quinn",            "party": "Republican",  "locality": "Russell/Washington/Bristol"},
    45:  {"delegate": "Terry G. Kilgore",             "party": "Republican",  "locality": "Lee/Scott/Wise/Norton"},
    46:  {"delegate": "Mitchell Cornett",             "party": "Republican",  "locality": "Grayson/Pulaski/Smyth/Wythe"},
    47:  {"delegate": "Wren M. Williams",             "party": "Republican",  "locality": "Carroll/Floyd/Henry/Patrick/Galax"},
    48:  {"delegate": "Eric J. Phillips",             "party": "Republican",  "locality": "Henry/Pittsylvania/Martinsville"},
    49:  {"delegate": "Madison Whittle",              "party": "Republican",  "locality": "Halifax/Pittsylvania/Danville"},
    50:  {"delegate": "Thomas C. Wright, Jr.",        "party": "Republican",  "locality": "Charlotte/Halifax/Lunenburg/Mecklenburg/Prince Edward"},
    51:  {"delegate": "Eric Zehr",                    "party": "Republican",  "locality": "Bedford/Campbell/Pittsylvania"},
    52:  {"delegate": "Wendell S. Walker",            "party": "Republican",  "locality": "Campbell/Lynchburg"},
    53:  {"delegate": "Timothy P. Griffin",           "party": "Republican",  "locality": "Amherst/Bedford/Nelson"},
    54:  {"delegate": "Katrina E. Callsen",           "party": "Democrat",    "locality": "Albemarle/Charlottesville"},
    55:  {"delegate": "Amy J. Laufer",                "party": "Democrat",    "locality": "Albemarle/Fluvanna/Louisa/Nelson"},
    56:  {"delegate": "Thomas A. Garrett, Jr.",       "party": "Republican",  "locality": "Appomattox/Buckingham/Cumberland/Fluvanna/Goochland/Louisa/Prince Edward"},
    57:  {"delegate": "May Nivar",                    "party": "Democrat",    "locality": "Goochland/Henrico"},
    58:  {"delegate": "Rodney T. Willett",            "party": "Democrat",    "locality": "Henrico"},
    59:  {"delegate": "Hyland F. Fowler, Jr.",        "party": "Republican",  "locality": "Hanover/Henrico/Louisa"},
    60:  {"delegate": "Scott A. Wyatt",               "party": "Republican",  "locality": "Hanover/New Kent"},
    61:  {"delegate": "Michael J. Webert",            "party": "Republican",  "locality": "Culpeper/Fauquier/Rappahannock"},
    62:  {"delegate": "Karen Fleming Hamilton",       "party": "Republican",  "locality": "Culpeper/Greene/Madison/Orange"},
    63:  {"delegate": "Phillip A. Scott",             "party": "Republican",  "locality": "Orange/Spotsylvania"},
    64:  {"delegate": "Stacey A. Carroll",            "party": "Democrat",    "locality": "Stafford"},
    65:  {"delegate": "Joshua G. Cole",               "party": "Democrat",    "locality": "Spotsylvania/Stafford/Fredericksburg"},
    66:  {"delegate": "Nicole Cole",                  "party": "Democrat",    "locality": "Caroline/Spotsylvania"},
    67:  {"delegate": "Hillary Pugh Kent",            "party": "Republican",  "locality": "Caroline/King George/Lancaster/Northumberland/Richmond/Westmoreland"},
    68:  {"delegate": "M. Keith Hodges",              "party": "Republican",  "locality": "Essex/Gloucester/King and Queen/King William/Mathews/Middlesex"},
    69:  {"delegate": "Mark C. Downey",               "party": "Democrat",    "locality": "Gloucester/James City/York/Newport News"},
    70:  {"delegate": "Shelly A. Simonds",            "party": "Democrat",    "locality": "Newport News"},
    71:  {"delegate": "Jessica L. Anderson",          "party": "Democrat",    "locality": "James City/New Kent/Williamsburg"},
    72:  {"delegate": "R. Lee Ware",                  "party": "Republican",  "locality": "Amelia/Chesterfield/Nottoway/Powhatan"},
    73:  {"delegate": "Leslie Chambers Mehta",        "party": "Democrat",    "locality": "Chesterfield"},
    74:  {"delegate": "Mike A. Cherry",               "party": "Republican",  "locality": "Chesterfield/Colonial Heights"},
    75:  {"delegate": "Lindsey Dougherty",            "party": "Democrat",    "locality": "Chesterfield/Prince George/Hopewell"},
    76:  {"delegate": "Debra D. Gardner",             "party": "Democrat",    "locality": "Chesterfield"},
    77:  {"delegate": "Charles H. Schmidt, Jr.",      "party": "Democrat",    "locality": "Chesterfield/Richmond"},
    78:  {"delegate": "Betsy B. Carr",                "party": "Democrat",    "locality": "Richmond"},
    79:  {"delegate": "Rae C. Cousins",               "party": "Democrat",    "locality": "Richmond"},
    80:  {"delegate": "Destiny L. LeVere Bolling",    "party": "Democrat",    "locality": "Henrico"},
    81:  {"delegate": "Delores L. McQuinn",           "party": "Democrat",    "locality": "Charles City/Chesterfield/Henrico"},
    82:  {"delegate": "Kimberly Pope Adams",          "party": "Democrat",    "locality": "Dinwiddie/Prince George/Surry/Petersburg"},
    83:  {"delegate": "Howard Otto Wachsmann, Jr.",   "party": "Republican",  "locality": "Brunswick/Dinwiddie/Greensville/Isle of Wight/Southampton/Sussex/Emporia"},
    84:  {"delegate": "Nadarius E. Clark",            "party": "Democrat",    "locality": "Chesapeake/Isle of Wight/Franklin/Suffolk"},
    85:  {"delegate": "Marcia S. Price",              "party": "Democrat",    "locality": "Newport News"},
    86:  {"delegate": "Virgil Gene Thornton, Sr.",    "party": "Democrat",    "locality": "York/Hampton/Poquoson"},
    87:  {"delegate": "Jeion A. Ward",                "party": "Democrat",    "locality": "Hampton"},
    88:  {"delegate": "Don Scott",                    "party": "Democrat",    "locality": "Portsmouth"},
    89:  {"delegate": "Karen Robins Carnegie",        "party": "Democrat",    "locality": "Chesapeake/Suffolk"},
    90:  {"delegate": "James A. Leftwich, Jr.",       "party": "Republican",  "locality": "Chesapeake"},
    91:  {"delegate": "C. E. Hayes, Jr.",             "party": "Democrat",    "locality": "Chesapeake/Portsmouth"},
    92:  {"delegate": "Bonita G. Anthony",            "party": "Democrat",    "locality": "Chesapeake/Norfolk"},
    93:  {"delegate": "Jackie Hope Glass",            "party": "Democrat",    "locality": "Norfolk"},
    94:  {"delegate": "Phil M. Hernandez",            "party": "Democrat",    "locality": "Norfolk"},
    95:  {"delegate": "Alex Q. Askew",                "party": "Democrat",    "locality": "Norfolk/Virginia Beach"},
    96:  {"delegate": "Kelly K. Convirs-Fowler",      "party": "Democrat",    "locality": "Virginia Beach"},
    97:  {"delegate": "Michael Feggans",              "party": "Democrat",    "locality": "Virginia Beach"},
    98:  {"delegate": "Andrew Rice",                  "party": "Republican",  "locality": "Virginia Beach"},
    99:  {"delegate": "Anne Ferrell H. Tata",         "party": "Republican",  "locality": "Virginia Beach"},
    100: {"delegate": "Robert S. Bloxom, Jr.",        "party": "Republican",  "locality": "Accomack/Northampton/Virginia Beach"},
}

# Virginia State Senate — 40 districts (2021 redistricting, 2026 session members)
SD_CONTEXT = {
    1:  {"senator": "Timmy French",             "party": "Republican", "region": "Clarke, Frederick, Shenandoah, Warren; Winchester"},
    2:  {"senator": "Mark Obenshain",           "party": "Republican", "region": "Augusta, Bath, Highland, Page, Rockingham; Harrisonburg"},
    3:  {"senator": "Chris Head",               "party": "Republican", "region": "Alleghany, Augusta, Bedford, Botetourt, Craig, Roanoke, Rockbridge"},
    4:  {"senator": "Dave Suetterlein",         "party": "Republican", "region": "Montgomery, Roanoke; Roanoke, Salem"},
    5:  {"senator": "Travis Hackworth",         "party": "Republican", "region": "Bland, Giles, Montgomery, Pulaski, Smyth, Tazewell, Wythe; Radford"},
    6:  {"senator": "Todd Pillion",             "party": "Republican", "region": "Buchanan, Dickenson, Lee, Russell, Scott, Washington, Wise; Bristol, Norton"},
    7:  {"senator": "Bill Stanley",             "party": "Republican", "region": "Carroll, Floyd, Franklin, Grayson, Henry, Patrick, Wythe; Martinsville, Galax"},
    8:  {"senator": "Mark Peake",               "party": "Republican", "region": "Bedford, Campbell; Lynchburg"},
    9:  {"senator": "Tammy Brankley Mulchi",    "party": "Republican", "region": "Charlotte, Halifax, Lunenburg, Mecklenburg, Nottoway, Pittsylvania, Prince Edward; Danville"},
    10: {"senator": "Luther Cifers",            "party": "Republican", "region": "Amelia, Appomattox, Buckingham, Cumberland, Fluvanna, Goochland, Hanover, Henrico, Louisa, Powhatan, Prince Edward"},
    11: {"senator": "Creigh Deeds",             "party": "Democrat",   "region": "Albemarle, Amherst, Louisa, Nelson; Charlottesville"},
    12: {"senator": "Glen Sturtevant",          "party": "Republican", "region": "Chesterfield; Colonial Heights"},
    13: {"senator": "Lashrecse Aird",           "party": "Democrat",   "region": "Charles City, Dinwiddie, Henrico, Prince George, Surry, Sussex; Hopewell, Petersburg"},
    14: {"senator": "Lamont Bagby",             "party": "Democrat",   "region": "Henrico; Richmond"},
    15: {"senator": "Michael Jones",            "party": "Democrat",   "region": "Chesterfield; Richmond"},
    16: {"senator": "Schuyler VanValkenburg",   "party": "Democrat",   "region": "Henrico"},
    17: {"senator": "Emily Jordan",             "party": "Republican", "region": "Brunswick, Dinwiddie, Greensville, Isle of Wight, Southampton; Chesapeake, Emporia, Franklin, Portsmouth, Suffolk"},
    18: {"senator": "L. Louise Lucas",          "party": "Democrat",   "region": "Chesapeake, Portsmouth"},
    19: {"senator": "Christie New Craig",       "party": "Republican", "region": "Chesapeake, Virginia Beach"},
    20: {"senator": "Bill DeSteph",             "party": "Republican", "region": "Accomack, Northampton; Norfolk, Virginia Beach"},
    21: {"senator": "Angelia Williams Graves",  "party": "Democrat",   "region": "Norfolk"},
    22: {"senator": "Aaron Rouse",              "party": "Democrat",   "region": "Virginia Beach"},
    23: {"senator": "Mamie Locke",              "party": "Democrat",   "region": "Hampton; Newport News"},
    24: {"senator": "Danny Diggs",              "party": "Republican", "region": "James City, York; Newport News, Poquoson, Williamsburg"},
    25: {"senator": "Richard Stuart",           "party": "Republican", "region": "Caroline, Essex, King George, King William, Lancaster, Middlesex, Northumberland, Richmond, Spotsylvania, Westmoreland"},
    26: {"senator": "Ryan McDougle",            "party": "Republican", "region": "Gloucester, Hanover, James City, Mathews, New Kent"},
    27: {"senator": "Tara Durant",              "party": "Republican", "region": "Spotsylvania, Stafford; Fredericksburg"},
    28: {"senator": "Bryce Reeves",             "party": "Republican", "region": "Culpeper, Fauquier, Greene, Madison, Orange, Rappahannock, Spotsylvania"},
    29: {"senator": "Jeremy McPike",            "party": "Democrat",   "region": "Prince William, Stafford"},
    30: {"senator": "Danica Roem",              "party": "Democrat",   "region": "Prince William; Manassas, Manassas Park"},
    31: {"senator": "Russet Perry",             "party": "Democrat",   "region": "Fauquier, Loudoun"},
    32: {"senator": "Kannan Srinivasan",        "party": "Democrat",   "region": "Loudoun"},
    33: {"senator": "Jennifer Carroll Foy",     "party": "Democrat",   "region": "Fairfax, Prince William"},
    34: {"senator": "Scott Surovell",           "party": "Democrat",   "region": "Fairfax"},
    35: {"senator": "Dave Marsden",             "party": "Democrat",   "region": "Fairfax"},
    36: {"senator": "Stella Pekarsky",          "party": "Democrat",   "region": "Fairfax"},
    37: {"senator": "Saddam Azlan Salim",       "party": "Democrat",   "region": "Fairfax; Fairfax City, Falls Church"},
    38: {"senator": "Jennifer Boysko",          "party": "Democrat",   "region": "Fairfax"},
    39: {"senator": "Elizabeth Bennett-Parker", "party": "Democrat",   "region": "Arlington, Fairfax; Alexandria"},
    40: {"senator": "Barbara Favola",           "party": "Democrat",   "region": "Arlington"},
}

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    district: str
    messages: list[ChatMessage]
    locality: str = ""
    hod_district: int | None = None
    sd_district: int | None = None

class ChatResponse(BaseModel):
    reply: str

@app.get("/election-map", response_class=HTMLResponse)
@app.get("/results-map", response_class=HTMLResponse)
def election_map(mode: str = "total"):
    if mode not in _election_maps:
        try:
            _election_maps[mode] = _build_election_map(mode)
        except Exception as e:
            print(f"Warning: could not build election map ({mode}): {e}")
            return f"<p style='font-family:sans-serif;padding:40px'>Map unavailable: {e}</p>"
    return _election_maps.get(mode, "")

@app.get("/results", response_class=HTMLResponse)
def results_page():
    with open(os.path.join(BASE_DIR, "templates", "results.html"), "r", encoding="utf-8") as f:
        return f.read()

@app.get("/api/results-data")
def results_data():
    with open(os.path.join(BASE_DIR, "data_2026_special.json"), encoding="utf-8") as f:
        data = json.load(f)

    def _group_counts(ballot_options, vote_name):
        opt = next((o for o in ballot_options if o["name"].upper() == vote_name), None)
        if not opt:
            return 0, 0, 0, 0
        groups = {g["groupName"]: g["voteCount"] for g in opt.get("groupResults", [])}
        return (
            opt["voteCount"],
            groups.get("Early Voting", 0),
            groups.get("Election Day", 0),
            groups.get("Mailed Absentee", 0),
        )

    statewide_opts = data.get("results", {}).get("ballotItems", [{}])[0].get("ballotOptions", [])
    yes_state, yes_early_s, yes_eday_s, yes_mail_s = _group_counts(statewide_opts, "YES")
    no_state,  no_early_s,  no_eday_s,  no_mail_s  = _group_counts(statewide_opts, "NO")

    local = []
    for j in data.get("localResults", []):
        yes_v = no_v = 0
        yes_early = no_early = yes_eday = no_eday = yes_mail = no_mail = 0
        for item in j.get("ballotItems", []):
            opts = item.get("ballotOptions", [])
            yv, ye, yd, ym = _group_counts(opts, "YES")
            nv, ne, nd, nm = _group_counts(opts, "NO")
            yes_v += yv; yes_early += ye; yes_eday += yd; yes_mail += ym
            no_v  += nv; no_early  += ne; no_eday  += nd; no_mail  += nm
        total = yes_v + no_v
        local.append({
            "name": j["name"].strip(),
            "yes": yes_v, "no": no_v, "total": total,
            "pct_yes": round(yes_v / total * 100, 1) if total else 50.0,
            "winner": "Yes" if yes_v >= no_v else "No",
            "early_yes": yes_early, "early_no": no_early,
            "eday_yes": yes_eday,   "eday_no": no_eday,
            "mail_yes": yes_mail,   "mail_no": no_mail,
        })

    return {
        "statewide": {
            "yes": yes_state, "no": no_state,
            "early": {"yes": yes_early_s, "no": no_early_s},
            "election_day": {"yes": yes_eday_s, "no": no_eday_s},
            "mail": {"yes": yes_mail_s, "no": no_mail_s},
        },
        "local": local,
    }

@app.get("/", response_class=HTMLResponse)
def home():
    with open(os.path.join(BASE_DIR, "templates", "index.html"), "r", encoding="utf-8") as f:
        return f.read()

@app.get("/api/lookup")
def lookup(address: str):
    result = find_district(address)
    if "error" in result:
        return {"district": result}

    hod_num = result.get("hod_district")
    sd_num  = result.get("sd_district")
    hod_info = HOD_CONTEXT.get(hod_num) if hod_num else None
    sd_info  = SD_CONTEXT.get(sd_num)  if sd_num  else None

    cd_num_raw = result.get("district_number", "")
    try:
        cd_key = f"VA-{int(cd_num_raw):02d}"
    except (ValueError, TypeError):
        cd_key = "VA-00"
    cd_info = DISTRICT_CONTEXT.get(cd_key, {})

    from address_lookup import _vb_council, _vb_school_board
    vb_num = result.get("vb_council_district")
    vb_info = _vb_council.get(vb_num) if vb_num is not None else None
    mayor_info = _vb_council.get(0)

    def _officer(key):
        o = _vb_council.get(key)
        return {"name": o["name"], "email": o["email"], "party": o.get("party", "")} if o else None

    return {
        "district": result,
        "vb_council": {
            "district_number": vb_num,
            "name": vb_info["name"],
            "email": vb_info["email"],
            "district": vb_info["district"],
            "party": vb_info.get("party", ""),
            "mayor": {"name": mayor_info["name"], "email": mayor_info["email"], "party": mayor_info.get("party", "")} if mayor_info else None,
            "sheriff": _officer("Sheriff"),
            "commonwealths_attorney": _officer("Commonwealth's Attorney"),
            "commissioner": _officer("Commissioner of the Revenue"),
            "treasurer": _officer("City Treasurer"),
            "clerk": _officer("Clerk of the Circuit Court"),
        } if vb_info else None,
        "vb_school_board": {
            "member": (lambda o: {"name": o["name"], "party": o.get("party", "")} if o else None)(_vb_school_board.get(vb_num)),
            "at_large": (lambda o: {"name": o["name"], "party": o.get("party", "")} if o else None)(_vb_school_board.get("at_large")),
        } if vb_num is not None else None,
        "us_rep": {
            "district_number": int(cd_num_raw) if cd_num_raw not in ("", "N/A") else None,
            "rep": cd_info.get("rep"),
            "party": cd_info.get("party"),
            "region": cd_info.get("region"),
        } if cd_info.get("rep") else None,
        "state_delegate": {
            "district_number": hod_num,
            "delegate": hod_info["delegate"],
            "party": hod_info["party"],
            "locality": hod_info["locality"],
        } if hod_info else None,
        "state_senator": {
            "district_number": sd_num,
            "senator": sd_info["senator"],
            "party": sd_info["party"],
            "region": sd_info["region"],
        } if sd_info else None,
        "us_senators": [
            {"name": "Mark Warner", "party": "Democrat", "title": "Virginia · Senior Senator"},
            {"name": "Tim Kaine",   "party": "Democrat", "title": "Virginia · Junior Senator"},
        ],
        "us_president": {"name": "Donald J. Trump", "party": "Republican", "title": "United States"},
        "va_statewide": [
            {"name": "Abigail Spanberger",  "party": "Democrat", "title": "Governor"},
            {"name": "Ghazala F. Hashmi",   "party": "Democrat", "title": "Lieutenant Governor"},
            {"name": "Jay Jones",           "party": "Democrat", "title": "Attorney General"},
        ],
    }

@app.get("/map", response_class=HTMLResponse)
def get_map(address: str):
    result = find_district(address)
    if "error" in result:
        return "<p>Address not found</p>"
    m = folium.Map(location=[result["lat"], result["lng"]], zoom_start=12)
    folium.Marker(location=[result["lat"], result["lng"]], popup=f"{result['district']}", icon=folium.Icon(color="red")).add_to(m)
    folium.GeoJson(_get_va_cd()).add_to(m)
    return m.get_root().render()


def _build_district_map(layer: str, user_lat: float = None, user_lng: float = None, district: int = None) -> str:
    """Build a party-shaded folium map for congressional, HOD, or SD districts."""
    global _va_hod_gdf, _va_sd_gdf

    import address_lookup as _al
    if layer == "hod":
        if _va_hod_gdf is None:
            # Reuse already-loaded GDF if address lookup already ran; otherwise load only HOD
            if _al.va_hod is not None:
                _va_hod_gdf = _al.va_hod
            else:
                _va_hod_gdf = gpd.read_file(os.path.join(BASE_DIR, "SCV Final 2021 Redistricting Plans", "SCV FINAL HOD.shp"))
                _va_hod_gdf = _va_hod_gdf.to_crs(epsg=4326)
                _al.va_hod = _va_hod_gdf  # share back so address_lookup can use it too
        if _va_hod_gdf is None:
            raise RuntimeError("HOD shapefile could not be loaded")
    if layer == "sd":
        if _va_sd_gdf is None:
            # Reuse already-loaded GDF if address lookup already ran; otherwise load only SD
            if _al.va_sd is not None:
                _va_sd_gdf = _al.va_sd
            else:
                _va_sd_gdf = gpd.read_file(os.path.join(BASE_DIR, "SCV Final 2021 Redistricting Plans", "SCV FINAL SD.shp"))
                _va_sd_gdf = _va_sd_gdf.to_crs(epsg=4326)
                _al.va_sd = _va_sd_gdf  # share back so address_lookup can use it too
        if _va_sd_gdf is None:
            raise RuntimeError("SD shapefile could not be loaded")

    m = folium.Map(location=[37.5, -79.0], zoom_start=7, tiles="CartoDB positron", min_zoom=6)
    map_var = m.get_name()

    if layer == "congressional":
        from shapely.geometry import mapping as _mapping
        va_cd = _get_va_cd()
        features = []
        for _, row in va_cd.iterrows():
            try:
                fp = str(int(row["CD118FP"]))
                district_key = f"VA-{fp.zfill(2)}"
                ctx = DISTRICT_CONTEXT.get(district_key, {})
                features.append({"type": "Feature",
                    "geometry": _mapping(row.geometry.simplify(0.005, preserve_topology=True)),
                    "properties": {
                        "_party": ctx.get("party", ""),
                        "_rep": ctx.get("rep", "Unknown"),
                        "_district": district_key,
                        "_region": ctx.get("region", ""),
                    }})
            except Exception:
                continue

        def cd_style(feat):
            party = feat["properties"].get("_party", "")
            fill = "#1a52c8" if party == "Democrat" else "#e03030" if party == "Republican" else "#aaaaaa"
            return {"fillColor": fill, "color": "#555", "weight": 1.0, "fillOpacity": 0.3}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=cd_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_rep", "_party", "_region"],
                aliases=["District:", "U.S. Representative:", "Party:", "Region:"],
                localize=True, sticky=True,
                style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        title = "Current Congressional Map — Virginia"

    elif layer == "hod":
        from shapely.geometry import mapping as _mapping
        features = []
        for _, row in _va_hod_gdf.iterrows():
            try:
                d = int(row["DISTRICT"])
                ctx = HOD_CONTEXT.get(d, {})
                features.append({"type": "Feature",
                    "geometry": _mapping(row.geometry.simplify(0.01, preserve_topology=True)),
                    "properties": {"DISTRICT": d,
                        "_district": f"HOD District {d}",
                        "_delegate": ctx.get("delegate", "Unknown"),
                        "_party": ctx.get("party", ""),
                        "_locality": ctx.get("locality", ""),
                        "_highlight": (d == district) if district else False}})
            except Exception:
                continue

        def hod_style(feat):
            party = feat["properties"].get("_party", "")
            hi = feat["properties"].get("_highlight", False)
            fill = "#0a3a9e" if party == "Democrat" else "#b01020" if party == "Republican" else "#888"
            return {"fillColor": fill, "color": "#333", "weight": 2 if hi else 0.6,
                    "fillOpacity": 0.85 if hi else 0.3}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=hod_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_delegate", "_party", "_locality"],
                aliases=["District:", "Delegate:", "Party:", "Area:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        ctx = HOD_CONTEXT.get(district, {}) if district else {}
        title = f"HOD District {district} — {ctx.get('delegate','')}" if district else "VA House of Delegates"

    else:  # sd
        from shapely.geometry import mapping as _mapping
        features = []
        for _, row in _va_sd_gdf.iterrows():
            try:
                d = int(row["DISTRICT"])
                ctx = SD_CONTEXT.get(d, {})
                features.append({"type": "Feature",
                    "geometry": _mapping(row.geometry.simplify(0.01, preserve_topology=True)),
                    "properties": {"DISTRICT": d,
                        "_district": f"Senate District {d}",
                        "_senator": ctx.get("senator", "Unknown"),
                        "_party": ctx.get("party", ""),
                        "_region": ctx.get("region", ""),
                        "_highlight": (d == district) if district else False}})
            except Exception:
                continue

        def sd_style(feat):
            party = feat["properties"].get("_party", "")
            hi = feat["properties"].get("_highlight", False)
            fill = "#0a3a9e" if party == "Democrat" else "#b01020" if party == "Republican" else "#888"
            return {"fillColor": fill, "color": "#333", "weight": 2 if hi else 0.6,
                    "fillOpacity": 0.85 if hi else 0.3}

        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=sd_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["_district", "_senator", "_party", "_region"],
                aliases=["District:", "Senator:", "Party:", "Region:"],
                localize=True, sticky=True, style="font-family:Arial;font-size:13px;",
            ),
        ).add_to(m)
        ctx = SD_CONTEXT.get(district, {}) if district else {}
        title = f"Senate District {district} — {ctx.get('senator','')}" if district else "VA State Senate"

    # Rep name label at centroid of the user's highlighted district
    highlighted = [f for f in features if f["properties"].get("_highlight")] if district else []
    if highlighted:
        from shapely.geometry import shape as _shape
        try:
            centroid = _shape(highlighted[0]["geometry"]).centroid
            name_field = "_delegate" if layer == "hod" else "_senator"
            rep_name = highlighted[0]["properties"].get(name_field, "")
            party = features[0]["properties"].get("_party", "")
            bg = "#1a52c8" if party == "Democrat" else "#e03030" if party == "Republican" else "#555"
            folium.Marker(
                location=[centroid.y, centroid.x],
                icon=folium.DivIcon(
                    html=f'<div style="font-family:Arial;font-size:12px;font-weight:700;color:#fff;'
                         f'background:{bg};padding:4px 10px;border-radius:4px;'
                         f'white-space:nowrap;box-shadow:1px 1px 4px rgba(0,0,0,0.3);">'
                         f'{rep_name}</div>',
                    icon_size=(200, 30),
                    icon_anchor=(100, 15),
                ),
            ).add_to(m)
        except Exception:
            pass

    # Fit map to the user's highlighted district (or all of Virginia if no district)
    target_features = [f for f in features if f["properties"].get("_highlight")] if district else features
    if target_features:
        from shapely.geometry import shape as _shape
        from shapely.ops import unary_union
        try:
            geoms = [_shape(f["geometry"]) for f in target_features]
            bounds = unary_union(geoms).bounds  # (minx, miny, maxx, maxy)
            m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])
        except Exception:
            pass

    # User location pin — use CircleMarker to avoid Font Awesome dependency
    if user_lat is not None and user_lng is not None:
        folium.CircleMarker(
            location=[user_lat, user_lng],
            radius=10,
            color="white",
            weight=3,
            fill=True,
            fill_color="#c8102e",
            fill_opacity=1.0,
            popup="Your Location",
            tooltip="📍 Your Location",
        ).add_to(m)

    m.get_root().html.add_child(folium.Element(f"""
    <div style="position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:1000;
         background:#0d1b2a;color:white;padding:8px 20px;border-radius:6px;
         box-shadow:2px 2px 8px rgba(0,0,0,.4);font-family:Arial;font-size:14px;
         font-weight:700;letter-spacing:0.05em;white-space:nowrap;pointer-events:none;">
      {title}
    </div>
    <div style="position:fixed;bottom:40px;left:40px;z-index:1000;
         background:white;padding:12px 16px;border-radius:8px;
         box-shadow:2px 2px 8px rgba(0,0,0,.3);font-family:Arial;font-size:13px;line-height:1.8">
      <span style="background:#1a52c8;color:white;padding:2px 10px;border-radius:3px">Democrat</span>
      &nbsp;
      <span style="background:#e03030;color:white;padding:2px 10px;border-radius:3px">Republican</span>
      <br><small style="color:#666">Hover for details</small>
    </div>
    """))
    rendered = m.get_root().render()
    bounds_js = (
        f"<script>"
        f"{map_var}.setMaxBounds([[35.9,-84.8],[39.7,-74.9]]);"
        f"{map_var}.options.maxBoundsViscosity=1.0;"
        f"</script>"
    )
    return rendered.replace("</html>", bounds_js + "</html>")


# District maps are built lazily on first request to keep startup memory low
_district_maps: dict[str, str] = {}


@app.get("/district-map", response_class=HTMLResponse)
def district_map(layer: str = "congressional", lat: float = None, lng: float = None, district: int = None):
    if layer == "congressional" and lat is None:
        if layer not in _district_maps:
            try:
                _district_maps[layer] = _build_district_map(layer)
            except Exception as e:
                return f"<p style='font-family:sans-serif;padding:40px'>Could not build map: {e}</p>"
        return _district_maps[layer]
    else:
        try:
            return _build_district_map(layer, user_lat=lat, user_lng=lng, district=district)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return f"<p style='font-family:sans-serif;padding:40px'>Could not build {layer} map: {e}</p>"


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    ctx = DISTRICT_CONTEXT.get(req.district)
    if not ctx:
        return ChatResponse(reply="Unknown district.")

    # Build election results context
    if _results:
        r = _results
        statewide_block = (
            f"STATEWIDE RESULT — April 21, 2026 Special Election:\n"
            f"  Outcome: {r['winner']}\n"
            f"  Yes (Approve): {r['yes']:,} votes ({r['yes_pct']}%)\n"
            f"  No  (Reject):  {r['no']:,} votes ({r['no_pct']}%)\n"
            f"  Early Voting:  Yes {r['early']['yes']:,} / No {r['early']['no']:,}\n"
            f"  Election Day:  Yes {r['election_day']['yes']:,} / No {r['election_day']['no']:,}\n"
            f"  Mail-In:       Yes {r['mail']['yes']:,} / No {r['mail']['no']:,}"
        )
        # Try to match the user's locality to a result
        locality_block = ""
        if req.locality:
            raw_key = req.locality.strip().upper()
            raw_key = raw_key[:-7].strip() if raw_key.endswith(" COUNTY") else \
                      raw_key[:-5].strip() if raw_key.endswith(" CITY") else raw_key
            loc = r["local"].get(raw_key)
            if loc:
                locality_block = (
                    f"\nUSER'S LOCALITY — {loc['display']}:\n"
                    f"  Yes: {loc['yes']:,} ({loc['pct_yes']}%)  |  "
                    f"No: {loc['no']:,}  |  Winner: {loc['winner']}"
                )
        election_context = statewide_block + locality_block
    else:
        election_context = "Election results data is currently unavailable."

    district_block = (
        f"USER'S CONGRESSIONAL DISTRICT: {req.district}\n"
        f"U.S. Representative: {ctx['rep']} ({ctx['party']})\n"
        f"Region: {ctx['region']}"
        if ctx["rep"] else
        "The user has not yet looked up their specific district. Answer statewide questions."
    )

    # Add state House of Delegates context
    hod_info = HOD_CONTEXT.get(req.hod_district) if req.hod_district else None
    if hod_info:
        district_block += (
            f"\nVA HOUSE OF DELEGATES DISTRICT: {req.hod_district}\n"
            f"Delegate: {hod_info['delegate']} ({hod_info['party']})\n"
            f"Locality: {hod_info['locality']}"
        )

    # Add state Senate context
    sd_info = SD_CONTEXT.get(req.sd_district) if req.sd_district else None
    if sd_info:
        district_block += (
            f"\nVA STATE SENATE DISTRICT: {req.sd_district}\n"
            f"Senator: {sd_info['senator']} ({sd_info['party']})\n"
            f"Region: {sd_info['region']}"
        )
    system_prompt = f"""You are VoteIQ, a nonpartisan civic assistant helping Virginia voters understand the April 21, 2026 statewide special election.

REFERENDUM: Should Virginia's General Assembly have authority to redraw congressional districts?
A YES vote lets the Democrat-controlled legislature's new map replace the bipartisan Redistricting Commission's current map for the 2026 elections.
A NO vote keeps the existing commission-drawn districts in place.

{election_context}

{district_block}

Answer questions about the election results, what the amendment means, redistricting, the representative, and voter info.
Keep answers 2-4 sentences. Be factual and nonpartisan. Suggest elections.virginia.gov for official voter info. Never tell people who to vote for."""
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        system=system_prompt,
        messages=[{"role": m.role, "content": m.content} for m in req.messages],
    )
    return ChatResponse(reply=response.content[0].text)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))