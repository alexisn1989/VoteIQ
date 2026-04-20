
# VoteIQ <img width="80" height="50" alt="image" src="https://github.com/user-attachments/assets/9eec75b2-edd9-42d6-a418-ee76ad65dd99" />

### Civic Intelligence Platform

A free civic data platform helping Hampton Roads, 
Virginia voters understand congressional 
redistricting before the April 21st, 2026 referendum.

## Live Demo
🌐 [voteiq.io](https://voteiq.io)

## What It Does
- Enter any Hampton Roads address
- See your congressional district instantly
- See your current U.S. representative
- View interactive map of your district
- Track live election results on April 21st

## Coverage
Covers 1.8 million voters across:
- Virginia Beach
- Norfolk
- Chesapeake
- Hampton
- Newport News
- Suffolk
- Portsmouth

## Built With
- Python 3.14
- FastAPI
- GeoPandas
- Folium
- Shapely
- Geopy/Nominatim
- Census TIGER Shapefiles
- Render (deployment)

## How It Works
1. User enters full Virginia address
2. Nominatim geocodes the address
3. GeoPandas spatial join finds congressional district
4. Folium renders interactive map
5. Results display instantly

## Local Development
```bash
git clone https://github.com/alexisn1989/VoteIQ
cd VoteIQ
pip install -r requirements.txt
uvicorn main:app --reload
```

## Data Sources
- Census TIGER Shapefiles (congressional districts)
- Virginia Beach City precinct shapefiles
- OpenStates API (representatives)

## Roadmap
- [ ] Hampton Roads precinct data
- [ ] State legislative districts
- [ ] Live election night results
- [ ] Color coded precinct maps
- [ ] Full Virginia coverage
      

## Author
Alexis — Python Developer | Civic Tech | GIS
[LinkedIn](www.linkedin.com/in/alexis-nieuwenhuys-370465214)
[VoteIQ](https://voteiq.io)

