import geopandas as gpd
import folium
import json

# Load the shapefile
vb_map = gpd.read_file(r"C:\Users\Alexis\Vriginia_api_election\va_senate_distrcits.json\VIRGINIA_BEACH_CITY.shp")

# Convert to the right coordinate system
vb_map = vb_map.to_crs(epsg=4326)

# Keep only what we need
vb_map = vb_map[['LocalityNa', 'geometry']]

# Create a folium map centered on Virginia Beach
m = folium.Map(location=[36.8529, -75.9780], zoom_start=11)

# Add the shapefile data to the map
folium.GeoJson(vb_map).add_to(m)

# Save the map
m.save("voteiq_test.html")

print("Map saved! Open voteiq_test.html in your browser")