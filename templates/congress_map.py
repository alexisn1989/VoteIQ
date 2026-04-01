import geopandas as gpd

# Load Virginia congressional districts
va_cd = gpd.read_file(r"C:\Users\Alexis\OneDrive\Desktop\Vriginia_api_election\tl_2023_51_cd118.shp")

print(va_cd.columns.tolist())
print(va_cd[['STATEFP', 'CD118FP', 'NAMELSAD']])
