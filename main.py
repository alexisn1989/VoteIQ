from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
import uvicorn
import folium
import geopandas as gpd
import os
import anthropic
from address_lookup import find_district

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

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