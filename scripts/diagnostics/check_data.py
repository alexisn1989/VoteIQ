import geopandas as gpd

vb_map = gpd.read_file(r"C:\Users\Alexis\OneDrive\Desktop\Vriginia_api_election\va_senate_distrcits.json\VIRGINIA_BEACH_CITY.shp")

# Show all column names
print("COLUMNS:")
print(vb_map.columns.tolist())

# Show first row of data
print("\nFIRST ROW:")
print(vb_map.iloc[0])