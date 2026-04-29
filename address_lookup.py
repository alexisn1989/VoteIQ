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
norfolk_wards_gdf = None       # Norfolk wards 1-5
norfolk_superwards_gdf = None  # Norfolk superwards 6-7
_loaded  = False

# VB council lookup: district number (int) -> {name, email}
_vb_council = {}
_vb_school_board = {}  # int district -> entry, "at_large" -> entry
_norfolk_officials = {}  # "mayor" -> entry, future ward entries
try:
    with open(os.path.join(BASE_DIR, "voteiq_officials.json"), encoding="utf-8") as _f:
        for _m in json.load(_f):
            _d = _m["district"]
            _entry = {"name": _m["name"], "email": _m["email"], "district": _d, "party": _m.get("party", "")}
            if _d == "Mayor":
                _vb_council[0] = _entry
            elif _d in ("Sheriff", "Commonwealth's Attorney", "Commissioner of the Revenue", "City Treasurer", "Clerk of the Circuit Court"):
                _vb_council[_d] = _entry
            elif _d == "Norfolk Mayor":
                _norfolk_officials["mayor"] = _entry
            elif _d == "Norfolk Sheriff":
                _norfolk_officials["sheriff"] = _entry
            elif _d == "Norfolk Commonwealth's Attorney":
                _norfolk_officials["commonwealths_attorney"] = _entry
            elif _d == "Norfolk Commissioner of the Revenue":
                _norfolk_officials["commissioner"] = _entry
            elif _d == "School Board At-Large":
                _vb_school_board["at_large"] = _entry
            elif _d.startswith("School Board District"):
                _num = int(_d.replace("School Board District", "").strip())
                _vb_school_board[_num] = _entry
            else:
                _num = int(_d.replace("District", "").strip())
                _vb_council[_num] = _entry
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

    try:
        global norfolk_wards_gdf
        norfolk_wards_gdf = gpd.read_file(os.path.join(BASE_DIR, "Wards.shp"))
        norfolk_wards_gdf = norfolk_wards_gdf.to_crs(epsg=4326)
        norfolk_wards_gdf = norfolk_wards_gdf[['WARD', 'WARD_REP', 'WARD_SBM', 'geometry']]
        print("address_lookup: norfolk_wards_gdf loaded")
    except Exception as e:
        print(f"address_lookup: could not load norfolk_wards_gdf: {e}")

    try:
        global norfolk_superwards_gdf
        norfolk_superwards_gdf = gpd.read_file(os.path.join(BASE_DIR, "Superwards.geojson"))
        norfolk_superwards_gdf = norfolk_superwards_gdf[['SUPWARD', 'SWARD_REP', 'SWARD_SBM', 'geometry']]
        print("address_lookup: norfolk_superwards_gdf loaded")
    except Exception as e:
        print(f"address_lookup: could not load norfolk_superwards_gdf: {e}")


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

        # Get Norfolk ward / superward
        norfolk_ward = None
        norfolk_ward_rep = None
        norfolk_ward_sbm = None
        if "norfolk" in (locality or "").lower():
            if norfolk_wards_gdf is not None:
                for idx, row in norfolk_wards_gdf.iterrows():
                    if row['geometry'].contains(point):
                        norfolk_ward = int(row['WARD'])
                        norfolk_ward_rep = row['WARD_REP']
                        norfolk_ward_sbm = row['WARD_SBM']
                        break
            if norfolk_ward is None and norfolk_superwards_gdf is not None:
                for idx, row in norfolk_superwards_gdf.iterrows():
                    if row['geometry'].contains(point):
                        norfolk_ward = int(row['SUPWARD'])
                        norfolk_ward_rep = row['SWARD_REP']
                        norfolk_ward_sbm = row['SWARD_SBM']
                        break

        return {
            "locality": locality or "Virginia",
            "district": district or "Not found",
            "district_number": district_number or "N/A",
            "hod_district": hod_district,
            "sd_district": sd_district,
            "vb_council_district": vb_council_district,
            "norfolk_ward": norfolk_ward,
            "norfolk_ward_rep": norfolk_ward_rep,
            "norfolk_ward_sbm": norfolk_ward_sbm,
            "precinct": precinct or "Not found",
            "lat": lat,
            "lng": lng
        }

    except Exception as e:
        print(f"Error: {e}")
        return {"error": str(e)}
