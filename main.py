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

# ── Election results map (built once at startup) ──────────────────────────────
_election_map_html: str | None = None

def _build_election_map() -> str:
    with open(os.path.join(BASE_DIR, "data_2026_special.json"), encoding="utf-8") as f:
        data = json.load(f)

    county_results, city_results = {}, {}
    for j in data.get("localResults", []):
        raw = j["name"].strip().upper()
        yes_v = no_v = 0
        for item in j.get("ballotItems", []):
            for opt in item.get("ballotOptions", []):
                if opt["name"].upper() == "YES": yes_v += opt["voteCount"]
                elif opt["name"].upper() == "NO": no_v += opt["voteCount"]
        total = yes_v + no_v
        info = {
            "yes": yes_v, "no": no_v, "total": total,
            "pct_yes": round(yes_v / total * 100, 1) if total else 50.0,
            "winner": "Yes" if yes_v >= no_v else "No",
            "display_name": j["name"].strip(),
        }
        if raw.endswith(" COUNTY"):
            county_results[raw[:-7].strip()] = info
        elif raw.endswith(" CITY"):
            city_results[raw[:-5].strip()] = info
        else:
            county_results[raw.replace("&", "AND").replace("  ", " ").strip()] = info

    resp = requests.get(
        "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json",
        timeout=60,
    )
    resp.raise_for_status()
    va_features = [f for f in resp.json()["features"] if f["properties"]["STATE"] == "51"]

    for feat in va_features:
        fips3 = int(feat["properties"]["GEO_ID"][-3:])
        name_up = feat["properties"]["NAME"].strip().upper()
        r = (county_results if fips3 <= 199 else city_results).get(name_up)
        feat["properties"].update(r if r else {
            "pct_yes": None, "winner": "No data",
            "yes": 0, "no": 0, "total": 0,
            "display_name": feat["properties"]["NAME"],
        })

    m = folium.Map(location=[37.5, -79.0], zoom_start=7, tiles="CartoDB positron")

    def style_fn(feat):
        pct = feat["properties"].get("pct_yes")
        if pct is None:
            return {"fillColor": "#cccccc", "color": "#888", "weight": 0.8, "fillOpacity": 0.5}
        # Minimum intensity of 80 so even near-50% results are clearly colored
        if pct >= 50:
            frac = (pct - 50) / 50
            intensity = int(80 + frac * 175)
            color = f"#{255-intensity:02x}{255-intensity:02x}ff"
        else:
            frac = (50 - pct) / 50
            intensity = int(80 + frac * 175)
            color = f"#ff{255-intensity:02x}{255-intensity:02x}"
        return {"fillColor": color, "color": "#444", "weight": 0.5, "fillOpacity": 0.85}

    folium.GeoJson(
        {"type": "FeatureCollection", "features": va_features},
        style_function=style_fn,
        tooltip=folium.GeoJsonTooltip(
            fields=["display_name", "yes", "no", "pct_yes", "winner"],
            aliases=["Jurisdiction:", "Yes votes:", "No votes:", "Yes %:", "Winner:"],
            localize=True, sticky=True,
            style="font-family:Arial;font-size:13px;",
        ),
    ).add_to(m)

    m.get_root().html.add_child(folium.Element("""
    <div style="position:fixed;bottom:40px;left:40px;z-index:1000;
         background:white;padding:12px 16px;border-radius:8px;
         box-shadow:2px 2px 8px rgba(0,0,0,.3);font-family:Arial;font-size:13px;line-height:1.8">
      <span style="background:#1a52c8;color:white;padding:2px 10px;border-radius:3px">Yes</span>
      &nbsp;
      <span style="background:#ff4444;color:white;padding:2px 10px;border-radius:3px">No</span>
      <br><small style="color:#666">Deeper color = larger margin</small>
    </div>
    """))

    return m.get_root().render()

try:
    _election_map_html = _build_election_map()
    print("Election results map ready.")
except Exception as e:
    print(f"Warning: could not build election map: {e}")

va_cd = gpd.read_file(os.path.join(BASE_DIR, "tl_2023_51_cd118.shp"))
va_cd = va_cd.to_crs(epsg=4326)
va_cd = va_cd[['NAMELSAD', 'CD118FP', 'geometry']]

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

DISTRICT_CONTEXT = {
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

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    district: str
    messages: list[ChatMessage]

class ChatResponse(BaseModel):
    reply: str

@app.get("/election-map", response_class=HTMLResponse)
@app.get("/results-map", response_class=HTMLResponse)
def election_map():
    if _election_map_html:
        return _election_map_html
    return "<p style='font-family:sans-serif;padding:40px'>Map is loading, please refresh in a moment.</p>"

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
    return {"district": result}

@app.get("/map", response_class=HTMLResponse)
def get_map(address: str):
    result = find_district(address)
    if "error" in result:
        return "<p>Address not found</p>"
    m = folium.Map(location=[result["lat"], result["lng"]], zoom_start=12)
    folium.Marker(location=[result["lat"], result["lng"]], popup=f"{result['district']}", icon=folium.Icon(color="red")).add_to(m)
    folium.GeoJson(va_cd).add_to(m)
    return m.get_root().render()

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    ctx = DISTRICT_CONTEXT.get(req.district)
    if not ctx:
        return ChatResponse(reply="Unknown district.")
    system_prompt = f"""You are the VoteIQ District Q&A assistant helping Virginia voters understand their congressional district.
Current district: {req.district}
Representative: {ctx['rep']} ({ctx['party']})
Region: {ctx['region']}
Answer questions about the representative, district geography, redistricting, elections, and voter info.
Keep answers 2-4 sentences, factual and nonpartisan. Suggest elections.virginia.gov if unsure. Never tell people who to vote for."""
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        system=system_prompt,
        messages=[{"role": m.role, "content": m.content} for m in req.messages],
    )
    return ChatResponse(reply=response.content[0].text)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))