from geopy.geocoders import Nominatim
import geopandas as gpd
from shapely.geometry import Point
import os
import time


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load Virginia Congressional Districts
va_cd = gpd.read_file(os.path.join(BASE_DIR, "tl_2023_51_cd118.shp"))
va_cd = va_cd.to_crs(epsg=4326)
va_cd = va_cd[['NAMELSAD', 'CD118FP', 'geometry']]

# Load Virginia Beach local data
try:
    vb_local = gpd.read_file(os.path.join(BASE_DIR, "va_senate_distrcits.json", "VIRGINIA_BEACH_CITY.shp"))
    vb_local = vb_local.to_crs(epsg=4326)
    vb_local = vb_local[['LocalityNa', 'PrecinctNa', 'geometry']]
except Exception:
    vb_local = None

# Load Virginia House of Delegates districts (2021 redistricting)
try:
    va_hod = gpd.read_file(os.path.join(BASE_DIR, "SCV Final 2021 Redistricting Plans", "SCV FINAL HOD.shp"))
    va_hod = va_hod.to_crs(epsg=4326)
except Exception as e:
    print(f"Warning: could not load HOD shapefile: {e}")
    va_hod = None

# Load Virginia State Senate districts (2021 redistricting)
try:
    va_sd = gpd.read_file(os.path.join(BASE_DIR, "SCV Final 2021 Redistricting Plans", "SCV FINAL SD.shp"))
    va_sd = va_sd.to_crs(epsg=4326)
except Exception as e:
    print(f"Warning: could not load SD shapefile: {e}")
    va_sd = None


def find_district(address):
    try:
        geolocator = Nominatim(user_agent="voteiq_civic_platform_v1", timeout=15)
        time.sleep(1)
        location = geolocator.geocode(address)
        if not location:
            return {"error": "Address not found"}

        point = Point(location.longitude, location.latitude)

        # Get congressional district
        district = None
        district_number = None
        for idx, row in va_cd.iterrows():
            if row['geometry'].contains(point):
                district = row['NAMELSAD']
                district_number = row['CD118FP']
                break

        # Get House of Delegates district
        hod_district = None
        if va_hod is not None:
            for idx, row in va_hod.iterrows():
                if row['geometry'].contains(point):
                    hod_district = int(row['DISTRICT'])
                    break

        # Get State Senate district
        sd_district = None
        if va_sd is not None:
            for idx, row in va_sd.iterrows():
                if row['geometry'].contains(point):
                    sd_district = int(row['DISTRICT'])
                    break

        # Get locality and precinct
        locality = None
        precinct = None
        if vb_local is not None:
            for idx, row in vb_local.iterrows():
                if row['geometry'].contains(point):
                    locality = row['LocalityNa']
                    precinct = row['PrecinctNa']
                    break

        # Extract city from geocoded address if locality not found
        if not locality:
            display_name = location.raw.get('display_name', '')
            parts = display_name.split(',')
            if len(parts) >= 3:
                locality = parts[2].strip()
            else:
                locality = parts[0].strip()

        return {
            "locality": locality or "Virginia",
            "district": district or "Not found",
            "district_number": district_number or "N/A",
            "hod_district": hod_district,
            "sd_district": sd_district,
            "precinct": precinct or "Not found",
            "lat": location.latitude,
            "lng": location.longitude
        }

    except Exception as e:
        print(f"Error: {e}")
        return {"error": str(e)}
