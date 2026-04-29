from geopy.geocoders import Nominatim
import geopandas as gpd
from shapely.geometry import Point
import os
import time
import json
import requests as _requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# All shapefiles are loaded lazily on first call to keep startup memory low
va_cd    = None
vb_local = None
va_hod   = None
va_sd    = None
vb_council_gdf = None  # VB city council district boundaries
_loaded  = False

# VB council lookup: district number (int) -> {name, email}
_vb_council = {}
try:
    with open(os.path.join(BASE_DIR, "voteiq_officials.json"), encoding="utf-8") as _f:
        for _m in json.load(_f):
            if _m["district"] == "Mayor":
                _vb_council[0] = {"name": _m["name"], "email": _m["email"], "district": "Mayor", "party": _m.get("party", "")}
            else:
                _num = int(_m["district"].replace("District", "").strip())
                _vb_council[_num] = {"name": _m["name"], "email": _m["email"], "district": _m["district"], "party": _m.get("party", "")}
except Exception as _e:
    print(f"address_lookup: could not load voteiq_officials.json: {_e}")


def _load_shapefiles():
    global va_cd, vb_local, va_hod, va_sd, vb_council_gdf, _loaded
    if _loaded:
        return
    _loaded = True

    try:
        va_cd = gpd.read_file(os.path.join(BASE_DIR, "tl_2023_51_cd118.shp"))
        va_cd = va_cd.to_crs(epsg=4326)
        va_cd = va_cd[['NAMELSAD', 'CD118FP', 'geometry']]
        print("address_lookup: va_cd loaded")
    except Exception as e:
        print(f"address_lookup: could not load va_cd: {e}")

    try:
        vb_local = gpd.read_file(os.path.join(BASE_DIR, "va_senate_distrcits.json", "VIRGINIA_BEACH_CITY.shp"))
        vb_local = vb_local.to_crs(epsg=4326)
        vb_local = vb_local[['LocalityNa', 'PrecinctNa', 'geometry']]
        print("address_lookup: vb_local loaded")
    except Exception as e:
        print(f"address_lookup: vb_local not available: {e}")

    try:
        va_hod = gpd.read_file(os.path.join(BASE_DIR, "SCV Final 2021 Redistricting Plans", "SCV FINAL HOD.shp"))
        va_hod = va_hod.to_crs(epsg=4326)
        print("address_lookup: va_hod loaded")
    except Exception as e:
        print(f"address_lookup: could not load va_hod: {e}")

    try:
        va_sd = gpd.read_file(os.path.join(BASE_DIR, "SCV Final 2021 Redistricting Plans", "SCV FINAL SD.shp"))
        va_sd = va_sd.to_crs(epsg=4326)
        print("address_lookup: va_sd loaded")
    except Exception as e:
        print(f"address_lookup: could not load va_sd: {e}")

    try:
        vb_council_gdf = gpd.read_file(os.path.join(BASE_DIR, "City_Council_Districts.shp"))
        vb_council_gdf = vb_council_gdf.to_crs(epsg=4326)
        print("address_lookup: vb_council_gdf loaded")
    except Exception as e:
        print(f"address_lookup: could not load vb_council_gdf: {e}")


def find_district(address):
    _load_shapefiles()

    try:
        lat, lng, display_name_raw = None, None, ""

        # Primary: US Census Geocoder — free, no key, accurate for US addresses
        try:
            r = _requests.get(
                "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
                params={"address": address, "benchmark": "Public_AR_Current", "format": "json"},
                timeout=10,
            )
            matches = r.json().get("result", {}).get("addressMatches", [])
            if matches:
                coords = matches[0]["coordinates"]
                lat, lng = float(coords["y"]), float(coords["x"])
                display_name_raw = matches[0].get("matchedAddress", address)
        except Exception:
            pass

        # Fallback: Nominatim
        if lat is None:
            time.sleep(1)
            geolocator = Nominatim(user_agent="voteiq_civic_platform_v1", timeout=15)
            location = geolocator.geocode(address + ", Virginia, USA")
            if location:
                lat, lng = location.latitude, location.longitude
                display_name_raw = location.raw.get("display_name", "")

        if lat is None:
            return {"error": "Address not found"}

        point = Point(lng, lat)

        # Get congressional district
        district = None
        district_number = None
        if va_cd is not None:
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

        # Get Virginia Beach city council district
        vb_council_district = None
        if vb_council_gdf is not None:
            for idx, row in vb_council_gdf.iterrows():
                if row['geometry'].contains(point):
                    # Try common column names for district number
                    try:
                        vb_council_district = int(row['District'])
                    except (ValueError, TypeError, KeyError):
                        pass
                    break

        # Extract city from geocoded address if locality not found
        if not locality:
            parts = display_name_raw.split(',')
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
            "vb_council_district": vb_council_district,
            "precinct": precinct or "Not found",
            "lat": lat,
            "lng": lng
        }

    except Exception as e:
        print(f"Error: {e}")
        return {"error": str(e)}
