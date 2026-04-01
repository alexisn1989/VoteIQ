from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn
import folium
import geopandas as gpd
import os
from address_lookup import find_district
from fastapi.staticfiles import StaticFiles

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

# Load Virginia congressional districts
va_cd = gpd.read_file(os.path.join(BASE_DIR, "tl_2023_51_cd118.shp"))
va_cd = va_cd.to_crs(epsg=4326)
va_cd = va_cd[['NAMELSAD', 'CD118FP', 'geometry']]


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

    folium.Marker(
        location=[result["lat"], result["lng"]],
        popup=f"{result['district']}",
        icon=folium.Icon(color="red")
    ).add_to(m)

    folium.GeoJson(va_cd).add_to(m)

    return m.get_root().render()


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
(os.environ.get("PORT", 8000)))