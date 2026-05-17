import json
import folium
import requests

# ── 1. Parse election results ────────────────────────────────────────────────
print("Parsing election results...")
with open(r"C:\Users\Alexis\Downloads\export-2026-April-21-Special.json", encoding="utf-8") as f:
    data = json.load(f)

results = {}   # normalised_name -> {yes, no, pct_yes, winner}
for jurisdiction in data.get("localResults", []):
    raw_name = jurisdiction["name"].strip().upper()
    # "GILES COUNTY" -> "GILES", "RICHMOND CITY" stays as-is
    if raw_name.endswith(" COUNTY"):
        norm = raw_name[:-7].strip()
    else:
        norm = raw_name

    yes_votes = no_votes = 0
    for item in jurisdiction.get("ballotItems", []):
        for opt in item.get("ballotOptions", []):
            if opt["name"].upper() == "YES":
                yes_votes += opt["voteCount"]
            elif opt["name"].upper() == "NO":
                no_votes += opt["voteCount"]

    total = yes_votes + no_votes
    pct_yes = (yes_votes / total * 100) if total else 50
    results[norm] = {
        "yes": yes_votes,
        "no": no_votes,
        "total": total,
        "pct_yes": round(pct_yes, 1),
        "winner": "Yes" if yes_votes >= no_votes else "No",
        "display_name": jurisdiction["name"].strip(),
    }

print(f"  Loaded {len(results)} jurisdictions")

# ── 2. Fetch Virginia county GeoJSON ─────────────────────────────────────────
print("Fetching Virginia county boundaries...")
geo_url = (
    "https://raw.githubusercontent.com/plotly/datasets/master/"
    "geojson-counties-fips.json"
)
resp = requests.get(geo_url, timeout=60)
resp.raise_for_status()
all_counties = resp.json()

va_features = [
    f for f in all_counties["features"]
    if f["properties"]["STATE"] == "51"
]
print(f"  Found {len(va_features)} Virginia features in GeoJSON")

# ── 3. Match GeoJSON names to election results ────────────────────────────────
# GeoJSON NAME examples: "Giles", "Richmond city", "Fairfax"
matched = unmatched_geo = 0
for feat in va_features:
    geo_name = feat["properties"]["NAME"].strip().upper()
    r = results.get(geo_name)
    if r:
        feat["properties"].update(r)
        matched += 1
    else:
        feat["properties"]["pct_yes"] = None
        feat["properties"]["winner"] = "No data"
        feat["properties"]["yes"] = 0
        feat["properties"]["no"] = 0
        feat["properties"]["total"] = 0
        feat["properties"]["display_name"] = feat["properties"]["NAME"]
        unmatched_geo += 1

print(f"  Matched {matched}, unmatched {unmatched_geo}")

va_geojson = {"type": "FeatureCollection", "features": va_features}

# ── 4. Build Folium map ───────────────────────────────────────────────────────
print("Building map...")
m = folium.Map(
    location=[37.5, -79.0],
    zoom_start=7,
    tiles="CartoDB positron",
)

def style_fn(feat):
    pct = feat["properties"].get("pct_yes")
    if pct is None:
        return {"fillColor": "#cccccc", "color": "#999", "weight": 0.8, "fillOpacity": 0.6}
    # gradient: deep red (No) -> white (50%) -> deep blue (Yes)
    if pct >= 50:
        intensity = int((pct - 50) / 50 * 200)
        r, g, b = 255 - intensity, 255 - intensity, 255
    else:
        intensity = int((50 - pct) / 50 * 200)
        r, g, b = 255, 255 - intensity, 255 - intensity
    color = f"#{r:02x}{g:02x}{b:02x}"
    return {"fillColor": color, "color": "#555", "weight": 0.6, "fillOpacity": 0.85}

folium.GeoJson(
    va_geojson,
    style_function=style_fn,
    tooltip=folium.GeoJsonTooltip(
        fields=["display_name", "yes", "no", "pct_yes", "winner"],
        aliases=["Jurisdiction", "Yes votes", "No votes", "Yes %", "Winner"],
        localize=True,
        sticky=True,
    ),
).add_to(m)

# Legend
legend_html = """
<div style="position:fixed; bottom:40px; left:40px; z-index:1000;
     background:white; padding:12px 18px; border-radius:8px;
     box-shadow:2px 2px 6px rgba(0,0,0,.3); font-family:Arial; font-size:13px">
  <b>2026 Virginia Special Election</b><br>
  Proposed Constitutional Amendment<br><br>
  <span style="background:#0000c8;color:white;padding:2px 8px;border-radius:3px">Yes</span>
  &nbsp;&nbsp;
  <span style="background:#c80000;color:white;padding:2px 8px;border-radius:3px">No</span>
  <br><small style="color:#666">Color intensity = margin</small><br><br>
  <b>Statewide:</b><br>
  Yes: 1,575,367 &nbsp;&nbsp; No: 1,486,451
</div>
"""
m.get_root().html.add_child(folium.Element(legend_html))

out_path = r"C:\Users\Alexis\Downloads\Virginia_Election_Api\election_map.html"
m.save(out_path)
print(f"\nMap saved to: {out_path}")
print("Open election_map.html in your browser to view the interactive map.")
