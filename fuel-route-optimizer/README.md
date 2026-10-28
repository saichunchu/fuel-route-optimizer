# Fuel Route Optimizer

Django REST API that computes a fuel-efficient refueling plan along a US driving route using the supplied fuel-price CSV.

## What it does

- Accepts start and finish locations in the USA (address strings or lat/lon coordinates).
- Makes **one** call to OpenRouteService Directions for the road route — result cached 24 h in-process.
- Locates candidate fuel stations within a configurable corridor (default 5 miles) using a `scipy` `cKDTree` spatial index built from the pre-geocoded station CSV.
- Projects stations onto the route using **vectorised NumPy** operations (replaces a Python double-loop; typically 10–50× faster on real-world polylines).
- Runs a greedy fuel-purchasing strategy: 50-gallon tank, 500-mile range, 10 MPG.
- Returns route geometry, suggested fuel stops, purchase quantities, en-route fuel cost, and a clickable map URL.
- Caches route and geocoding results in-process (reset on restart; swap to Redis for multi-worker production).
- Includes offline station geocoding preprocessing and optimizer unit tests.

## API call budget per request

| Scenario | ORS Directions | ORS Geocoding |
|---|---|---|
| Coordinates provided, route cached | 0 | 0 |
| Coordinates provided, cold cache | **1** | 0 |
| Address strings provided, cold cache | **1** | up to **2** (one per endpoint) |

Maximum 3 external API calls; minimum 0 for repeat queries.

## Important modelling limitations

1. The supplied CSV has station addresses but no coordinates. Run the preprocessing script before the API can use the dataset.
2. Station access distance is approximated as `access_multiplier × lateral_distance`. This is not a road-network distance. Results are optimal only under the modelled route and distance assumptions.
3. Prices are from the supplied CSV, not live fuel prices.
4. The API assumes a full 50-gallon tank at the start. The initial fuel's historical cost is unknown and excluded from en-route expenditure.
5. The in-memory cache is per-process and resets on restart.

## Setup

Requires Python 3.12+.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # Windows
# cp .env.example .env   # macOS/Linux
```

Set `OPENROUTESERVICE_API_KEY` in `.env`:  
https://account.heigit.org/

Copy the provided dataset to `data/fuel-prices-for-be-assessment.csv`, then geocode the stations:

```bash
# Quick smoke test (first 20 unique addresses only):
python scripts/preprocess_stations.py --limit 20

# Full dataset (review your ORS quota first — ~8 k geocoding calls):
python scripts/preprocess_stations.py
```

The script writes:
- `data/processed/fuel_stations_geocoded.csv`  — stations with lat/lon
- `data/processed/geocode_cache.csv`  — resumable cache; re-run safely

Only rows with `geocode_status = matched` and coordinates inside the continental US bounding box are loaded by the API.

## Run

```bash
python manage.py check
python manage.py runserver
```

Health check:

```
GET http://127.0.0.1:8000/api/v1/health/
```

## API

### `POST /api/v1/fuel-routes/optimize/`

**By address** (triggers 1–2 ORS geocoding calls on first use, then cached):

```json
{
  "start": "Dallas, TX",
  "finish": "Denver, CO"
}
```

**By coordinates** (zero geocoding calls — fastest):

```json
{
  "start_coordinates": {"latitude": 32.7767, "longitude": -96.7970},
  "finish_coordinates": {"latitude": 39.7392, "longitude": -104.9903}
}
```

Exactly one start representation and one finish representation must be provided.

### Key response fields

| Field | Description |
|---|---|
| `map_url` | Clickable ORS map URL showing the route |
| `route.route_distance_miles` | Driving distance from the routing provider |
| `route.geometry` | `[[lon, lat], …]` polyline for rendering |
| `fuel_summary.en_route_fuel_cost_usd` | Total money spent on fuel |
| `fuel_summary.gallons_consumed` | Modelled distance ÷ 10 MPG |
| `fuel_stops[].purchase_cost_usd` | Cost at each recommended stop |
| `fuel_stops[].reason` | Why fuel was purchased here |
| `performance.routing_elapsed_ms` | Time for the ORS call (or cache read) |
| `performance.station_matching_elapsed_ms` | Time to project stations onto the route |

### Status codes

| Code | Meaning |
|---|---|
| `200` | Route plan created |
| `400` | Invalid request body |
| `422` | Address unresolvable or no feasible fuelling plan |
| `502` | Routing provider failure |
| `503` | Fuel dataset unavailable or not geocoded |

## Run tests

```bash
python manage.py test tests
# or
python -m unittest tests.test_optimizer
```

## Architecture overview

```
POST /api/v1/fuel-routes/optimize/
        │
        ▼
  OptimizeFuelRouteView  (routes/api/views.py)
        │  validates request body
        ▼
  plan_fuel_route()  (routes/services/planner.py)
        │
        ├─1─ resolve_address()        geocoding.py  → ORS geocode API (cached)
        ├─2─ get_route()              routing.py    → ORS directions API (cached)
        ├─3─ find_route_stations()    route_matching.py
        │       └── StationIndex.query_route_points()  station_index.py (lru_cache)
        │       └── _project_stations_numpy()          vectorised NumPy projection
        └─4─ optimize_fuel_stops()   optimizer.py  → greedy algorithm
```

## Production improvements

- Redis / shared cache for multi-worker deployments.
- Road-network matrix (ORS Matrix API) for accurate station access distances.
- PostGIS spatial index if the station dataset grows significantly.
- Integration tests with mocked ORS responses.
- API-level rate limiting and structured JSON logging.
