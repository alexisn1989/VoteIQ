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
norfolk_combined_gdf = None    # Norfolk wards + superwards merged into one GDF
nn_council_gdf = None  # Newport News city council district boundaries
hampton_precinct_gdf = None  # Hampton voting precinct boundaries
portsmouth_precinct_gdf = None  # Portsmouth voting precinct boundaries
suffolk_precinct_gdf = None  # Suffolk voting precinct boundaries
_loaded  = False

# VB council lookup: district number (int) -> {name, email}
_vb_council = {}
_vb_school_board = {}  # int district -> entry, "at_large" -> entry
_norfolk_officials = {}  # "mayor" -> entry, ward entries
_chesapeake_officials = {}  # "mayor", "vice_mayor", "sheriff", etc. -> entry; "council" -> list
_portsmouth_officials = {}  # "mayor", "vice_mayor", "sheriff", etc. -> entry; "council" -> list
_hampton_officials = {}       # "mayor", "vice_mayor", "sheriff", etc. -> entry; "council" -> list
_newport_news_officials = {}  # "mayor", "vice_mayor", "sheriff", etc. -> entry; "council" -> list
_suffolk_officials = {}       # "mayor", "vice_mayor", "sheriff", etc. -> entry; "council" -> list
_polling_places_by_number = {}
_polling_places_by_name = {}


def _norm_text(value):
    import re
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _precinct_number(value):
    import re
    match = re.search(r"\d+", str(value or ""))
    return str(int(match.group(0))) if match else ""


def _precinct_name_alias(value):
    import re
    return _norm_text(re.sub(r"^\s*\d+\s*-?\s*", "", str(value or "")))


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
            elif _d == "Norfolk City Treasurer":
                _norfolk_officials["treasurer"] = _entry
            elif _d == "Norfolk Commissioner of the Revenue":
                _norfolk_officials["commissioner"] = _entry
            elif _d == "Chesapeake Mayor":
                _chesapeake_officials["mayor"] = _entry
            elif _d == "Chesapeake Vice Mayor":
                _chesapeake_officials["vice_mayor"] = _entry
            elif _d == "Chesapeake Sheriff":
                _chesapeake_officials["sheriff"] = _entry
            elif _d == "Chesapeake Commonwealth's Attorney":
                _chesapeake_officials["commonwealths_attorney"] = _entry
            elif _d == "Chesapeake Commissioner of the Revenue":
                _chesapeake_officials["commissioner"] = _entry
            elif _d == "Chesapeake City Treasurer":
                _chesapeake_officials["treasurer"] = _entry
            elif _d == "Chesapeake Clerk of the Circuit Court":
                _chesapeake_officials["clerk"] = _entry
            elif _d == "Chesapeake Council":
                _chesapeake_officials.setdefault("council", []).append(_entry)
            elif _d == "Chesapeake School Board":
                _chesapeake_officials.setdefault("school_board", []).append(_entry)
            elif _d == "Portsmouth Mayor":
                _portsmouth_officials["mayor"] = _entry
            elif _d == "Portsmouth Vice Mayor":
                _portsmouth_officials["vice_mayor"] = _entry
            elif _d == "Portsmouth Sheriff":
                _portsmouth_officials["sheriff"] = _entry
            elif _d == "Portsmouth Commonwealth's Attorney":
                _portsmouth_officials["commonwealths_attorney"] = _entry
            elif _d == "Portsmouth Commissioner of the Revenue":
                _portsmouth_officials["commissioner"] = _entry
            elif _d == "Portsmouth City Treasurer":
                _portsmouth_officials["treasurer"] = _entry
            elif _d == "Portsmouth Clerk of the Circuit Court":
                _portsmouth_officials["clerk"] = _entry
            elif _d == "Portsmouth Council":
                _portsmouth_officials.setdefault("council", []).append(_entry)
            elif _d == "Portsmouth School Board":
                _portsmouth_officials.setdefault("school_board", []).append(_entry)
            elif _d == "Hampton Mayor":
                _hampton_officials["mayor"] = _entry
            elif _d == "Hampton Vice Mayor":
                _hampton_officials["vice_mayor"] = _entry
            elif _d == "Hampton Sheriff":
                _hampton_officials["sheriff"] = _entry
            elif _d == "Hampton Commonwealth's Attorney":
                _hampton_officials["commonwealths_attorney"] = _entry
            elif _d == "Hampton Commissioner of the Revenue":
                _hampton_officials["commissioner"] = _entry
            elif _d == "Hampton City Treasurer":
                _hampton_officials["treasurer"] = _entry
            elif _d == "Hampton Clerk of the Circuit Court":
                _hampton_officials["clerk"] = _entry
            elif _d == "Hampton Council":
                _hampton_officials.setdefault("council", []).append(_entry)
            elif _d == "Hampton School Board":
                _hampton_officials.setdefault("school_board", []).append(_entry)
            elif _d == "Newport News Mayor":
                _newport_news_officials["mayor"] = _entry
            elif _d == "Newport News Vice Mayor":
                _newport_news_officials["vice_mayor"] = _entry
            elif _d == "Newport News Sheriff":
                _newport_news_officials["sheriff"] = _entry
            elif _d == "Newport News Commonwealth's Attorney":
                _newport_news_officials["commonwealths_attorney"] = _entry
            elif _d == "Newport News Commissioner of the Revenue":
                _newport_news_officials["commissioner"] = _entry
            elif _d == "Newport News City Treasurer":
                _newport_news_officials["treasurer"] = _entry
            elif _d == "Newport News Clerk of the Circuit Court":
                _newport_news_officials["clerk"] = _entry
            elif _d.startswith("Newport News Council "):
                _num = int(_d.replace("Newport News Council ", "").strip())
                _newport_news_officials.setdefault(f"council_{_num}", []).append(_entry)
            elif _d == "Newport News School Board At-Large":
                _newport_news_officials.setdefault("school_board_at_large", []).append(_entry)
            elif _d.startswith("Newport News School Board "):
                _num = int(_d.replace("Newport News School Board ", "").strip())
                _newport_news_officials.setdefault(f"school_board_{_num}", []).append(_entry)
            elif _d == "Suffolk Mayor":
                _suffolk_officials["mayor"] = _entry
            elif _d == "Suffolk Vice Mayor":
                _suffolk_officials["vice_mayor"] = _entry
            elif _d == "Suffolk Sheriff":
                _suffolk_officials["sheriff"] = _entry
            elif _d == "Suffolk Commonwealth's Attorney":
                _suffolk_officials["commonwealths_attorney"] = _entry
            elif _d == "Suffolk Commissioner of the Revenue":
                _suffolk_officials["commissioner"] = _entry
            elif _d == "Suffolk City Treasurer":
                _suffolk_officials["treasurer"] = _entry
            elif _d == "Suffolk Clerk of the Circuit Court":
                _suffolk_officials["clerk"] = _entry
            elif _d == "Suffolk Council":
                _suffolk_officials.setdefault("council", []).append(_entry)
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


try:
    with open(os.path.join(BASE_DIR, "polling_place_addresses.json"), encoding="utf-8") as _f:
        for _record in json.load(_f).get("records", []):
            _locality_key = _record.get("locality_key") or _norm_text(_record.get("locality"))
            _number = _record.get("precinct_number") or _precinct_number(_record.get("precinct"))
            if _locality_key and _number:
                _polling_places_by_number[(_locality_key, _number)] = _record
            _name_keys = {
                _record.get("precinct_key") or _norm_text(_record.get("precinct")),
                _precinct_name_alias(_record.get("precinct")),
                _norm_text(_record.get("location")),
            }
            for _name_key in _name_keys:
                if _locality_key and _name_key:
                    _polling_places_by_name[(_locality_key, _name_key)] = _record
except Exception as _e:
    print(f"address_lookup: could not load polling_place_addresses.json: {_e}")


def _polling_place_for(locality, precinct_number=None, precinct_name=None):
    locality_key = _norm_text(locality)
    if not locality_key:
        return None
    number = _precinct_number(precinct_number)
    if number:
        found = _polling_places_by_number.get((locality_key, number))
        if found:
            return found
    for name_key in {_norm_text(precinct_name), _precinct_name_alias(precinct_name)}:
        if name_key:
            found = _polling_places_by_name.get((locality_key, name_key))
            if found:
                return found
    return None


def _load_city_precincts(city_names):
    for city_name in city_names:
        direct_names = (
            f"{city_name.upper()}_CITY.shp",
            f"{city_name.title()}_city.shp",
            f"{city_name.lower()}_city.shp",
        )
        path = next(
            (
                os.path.join(BASE_DIR, name)
                for name in direct_names
                if os.path.exists(os.path.join(BASE_DIR, name))
            ),
            os.path.join(BASE_DIR, direct_names[0]),
        )
        if not os.path.exists(path):
            for root, _dirs, files in os.walk(BASE_DIR):
                if ".venv" in root.split(os.sep):
                    continue
                for file_name in files:
                    if file_name.lower().endswith(".shp") and city_name.lower() in file_name.lower():
                        path = os.path.join(root, file_name)
                        break
                if os.path.exists(path):
                    break
        if os.path.exists(path):
            gdf = gpd.read_file(path).to_crs(epsg=4326)
            print(f"address_lookup: {city_name.lower()}_precinct_gdf loaded from {path}")
            return gdf

    print(f"address_lookup: {'/'.join(city_names)} precinct shapefile not available")
    return None


def _match_precinct(gdf, point):
    if gdf is None:
        return None, None, None
    for _, row in gdf.iterrows():
        if row['geometry'].contains(point):
            precinct_name = row.get('PrecinctNa') or row.get('Precinct_1') or row.get('PrecinctDi')
            precinct_number = row.get('PrecinctNu') or row.get('PrecinctFI')
            polling_location = row.get('PollingLoc')
            return precinct_name, precinct_number, polling_location
    return None, None, None


def _load_shapefiles():
    global va_cd, vb_local, va_hod, va_sd, vb_council_gdf, norfolk_combined_gdf, nn_council_gdf, hampton_precinct_gdf, portsmouth_precinct_gdf, suffolk_precinct_gdf, _loaded
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
        nn_council_gdf = gpd.read_file(os.path.join(BASE_DIR, "City_Council_District.shp"))
        nn_council_gdf = nn_council_gdf.to_crs(epsg=4326)
        nn_council_gdf = nn_council_gdf[['DISTRICT', 'LONGNAME', 'geometry']]
        print("address_lookup: nn_council_gdf loaded")
    except Exception as e:
        print(f"address_lookup: could not load nn_council_gdf: {e}")

    try:
        hampton_precinct_gdf = _load_city_precincts(("HAMPTON", "Hampton"))
    except Exception as e:
        print(f"address_lookup: could not load hampton_precinct_gdf: {e}")

    try:
        portsmouth_precinct_gdf = _load_city_precincts(("PORTSMOUTH", "Portsmouth"))
    except Exception as e:
        print(f"address_lookup: could not load portsmouth_precinct_gdf: {e}")

    try:
        suffolk_precinct_gdf = _load_city_precincts(("SUFFOLK", "Suffolk"))
    except Exception as e:
        print(f"address_lookup: could not load suffolk_precinct_gdf: {e}")

    try:
        import geopandas as _gpd
        _wards = _gpd.read_file(os.path.join(BASE_DIR, "Wards.shp")).to_crs(epsg=4326)
        _sw    = _gpd.read_file(os.path.join(BASE_DIR, "Superwards.geojson"))
        rows = []
        for _, wr in _wards.iterrows():
            for _, sr in _sw.iterrows():
                inter = wr.geometry.intersection(sr.geometry)
                if not inter.is_empty:
                    rows.append({
                        "WARD": int(wr["WARD"]), "WARD_REP": wr["WARD_REP"], "WARD_SBM": wr["WARD_SBM"],
                        "SUPWARD": int(sr["SUPWARD"]), "SWARD_REP": sr["SWARD_REP"], "SWARD_SBM": sr["SWARD_SBM"],
                        "geometry": inter,
                    })
        norfolk_combined_gdf = _gpd.GeoDataFrame(rows, crs="EPSG:4326")
        print(f"address_lookup: norfolk_combined_gdf loaded ({len(rows)} polygons)")
    except Exception as e:
        print(f"address_lookup: could not build norfolk_combined_gdf: {e}")


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
        vb_polling_place = None
        if vb_local is not None:
            for idx, row in vb_local.iterrows():
                if row['geometry'].contains(point):
                    locality = row['LocalityNa']
                    precinct = row['PrecinctNa']
                    vb_polling_place = _polling_place_for(locality, precinct, precinct)
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
            parts = [p.strip() for p in display_name_raw.split(',')]
            if len(parts) == 4:
                # Census format: STREET, CITY, STATE, ZIP
                locality = parts[1]
            elif len(parts) >= 3:
                # Nominatim format: STREET, NEIGHBORHOOD, CITY, STATE, ...
                locality = parts[2]
            else:
                locality = parts[0]

        # Norfolk ward + superward from single combined GDF
        norfolk_ward = None
        norfolk_ward_rep = None
        norfolk_ward_sbm = None
        norfolk_superward = None
        norfolk_superward_rep = None
        norfolk_superward_sbm = None
        if "norfolk" in (locality or "").lower() and norfolk_combined_gdf is not None:
            for _, row in norfolk_combined_gdf.iterrows():
                if row['geometry'].contains(point):
                    norfolk_ward = row['WARD']
                    norfolk_ward_rep = row['WARD_REP']
                    norfolk_ward_sbm = row['WARD_SBM']
                    norfolk_superward = row['SUPWARD']
                    norfolk_superward_rep = row['SWARD_REP']
                    norfolk_superward_sbm = row['SWARD_SBM']
                    break

        nn_council_district = None
        nn_council_district_name = None
        if "newport news" in (locality or "").lower() and nn_council_gdf is not None:
            for _, row in nn_council_gdf.iterrows():
                if row['geometry'].contains(point):
                    nn_council_district = int(row['DISTRICT'])
                    nn_council_district_name = row['LONGNAME']
                    break

        hampton_precinct = None
        hampton_precinct_number = None
        hampton_polling_location = None
        hampton_polling_place = None
        if "hampton" in (locality or "").lower() and hampton_precinct_gdf is not None:
            hampton_precinct, hampton_precinct_number, hampton_polling_location = _match_precinct(hampton_precinct_gdf, point)
            hampton_polling_place = _polling_place_for(locality, hampton_precinct_number, hampton_precinct)
            if hampton_precinct and not precinct:
                precinct = hampton_precinct

        portsmouth_precinct = None
        portsmouth_precinct_number = None
        portsmouth_polling_location = None
        portsmouth_polling_place = None
        if "portsmouth" in (locality or "").lower() and portsmouth_precinct_gdf is not None:
            portsmouth_precinct, portsmouth_precinct_number, portsmouth_polling_location = _match_precinct(portsmouth_precinct_gdf, point)
            portsmouth_polling_place = _polling_place_for(locality, portsmouth_precinct_number, portsmouth_precinct)
            if portsmouth_precinct and not precinct:
                precinct = portsmouth_precinct

        suffolk_precinct = None
        suffolk_precinct_number = None
        suffolk_polling_location = None
        suffolk_polling_place = None
        if "suffolk" in (locality or "").lower() and suffolk_precinct_gdf is not None:
            suffolk_precinct, suffolk_precinct_number, suffolk_polling_location = _match_precinct(suffolk_precinct_gdf, point)
            suffolk_polling_place = _polling_place_for(locality, suffolk_precinct_number, suffolk_precinct)
            if suffolk_precinct and not precinct:
                precinct = suffolk_precinct

        return {
            "locality": locality or "Virginia",
            "district": district or "Not found",
            "district_number": district_number or "N/A",
            "hod_district": hod_district,
            "sd_district": sd_district,
            "vb_council_district": vb_council_district,
            "vb_polling_place": vb_polling_place,
            "norfolk_ward": norfolk_ward,
            "norfolk_ward_rep": norfolk_ward_rep,
            "norfolk_ward_sbm": norfolk_ward_sbm,
            "norfolk_superward": norfolk_superward,
            "norfolk_superward_rep": norfolk_superward_rep,
            "norfolk_superward_sbm": norfolk_superward_sbm,
            "nn_council_district": nn_council_district,
            "nn_council_district_name": nn_council_district_name,
            "hampton_precinct": hampton_precinct,
            "hampton_precinct_number": hampton_precinct_number,
            "hampton_polling_location": (hampton_polling_place or {}).get("location") or hampton_polling_location,
            "hampton_polling_place": hampton_polling_place,
            "portsmouth_precinct": portsmouth_precinct,
            "portsmouth_precinct_number": portsmouth_precinct_number,
            "portsmouth_polling_location": (portsmouth_polling_place or {}).get("location") or portsmouth_polling_location,
            "portsmouth_polling_place": portsmouth_polling_place,
            "suffolk_precinct": suffolk_precinct,
            "suffolk_precinct_number": suffolk_precinct_number,
            "suffolk_polling_location": (suffolk_polling_place or {}).get("location") or suffolk_polling_location,
            "suffolk_polling_place": suffolk_polling_place,
            "precinct": precinct or "Not found",
            "lat": lat,
            "lng": lng
        }

    except Exception as e:
        print(f"Error: {e}")
        return {"error": str(e)}
