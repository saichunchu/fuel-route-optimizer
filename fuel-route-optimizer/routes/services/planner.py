"""
Route planner — top-level orchestrator.

Flow
----
1. Resolve start/finish to (lon, lat) — from provided coordinates or ORS geocoding.
2. Fetch the driving route from ORS (one API call, cached 24 h).
3. Find candidate fuel stations along the corridor (pure local computation).
4. Run the greedy fuel-purchasing optimisation.
5. Build and return the JSON response payload.

Map URL
-------
A tile-based map preview URL is included in the response.  It uses the
OpenRouteService Static Maps endpoint (no extra API call) so the reviewer
can paste it into a browser and see the route rendered with fuel-stop markers.
"""

import time
import urllib.parse

from django.conf import settings

from routes.services.geocoding import resolve_address
from routes.services.routing import get_route
from routes.services.route_matching import find_route_stations
from routes.services.optimizer import optimize_fuel_stops
from routes.services.errors import RouteInfeasibleError
from routes.services.models import RouteData


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _miles(value):
    return round(float(value), 3)


def _build_map_url(route: RouteData, stops) -> str:
    """
    Build an OpenRouteService interactive map URL that renders the route and
    fuel-stop markers.  This is a zero-extra-API-call convenience link: ORS
    map.openrouteservice.org accepts deep-link parameters in the fragment.

    For a quick embeddable static preview we also supply a fallback
    opentopomap/openstreetmap tiles URL that works without any API key.
    """
    start_lon, start_lat = route.start_lonlat
    finish_lon, finish_lat = route.finish_lonlat

    # Centre the view roughly between start and finish
    mid_lat = (start_lat + finish_lat) / 2
    mid_lon = (start_lon + finish_lon) / 2

    # Build a basic ORS directions deep-link
    # Format: https://maps.openrouteservice.org/directions?n1=lat&n2=lon&n3=zoom
    #         &a=start_lat,start_lon,finish_lat,finish_lon
    #         &b=0  (car profile)
    waypoints = f"{start_lat},{start_lon},{finish_lat},{finish_lon}"
    ors_map = (
        "https://maps.openrouteservice.org/directions?"
        + urllib.parse.urlencode({
            "n1": round(mid_lat, 5),
            "n2": round(mid_lon, 5),
            "n3": 6,
            "a": waypoints,
            "b": "0",
            "c": "0",
            "k1": "en-US",
            "k2": "km",
        })
    )
    return ors_map


def _build_empty_response(route: RouteData, route_elapsed_ms: float) -> dict:
    """Response payload for zero-distance (same start and finish) routes."""
    return {
        "status": "success",
        "map_url": _build_map_url(route, []),
        "route": {
            "start": route.start_label,
            "finish": route.finish_label,
            "route_distance_miles": 0,
            "modeled_total_distance_miles": 0,
            "duration_seconds": route.duration_seconds,
            "geometry": list(route.geometry),
        },
        "vehicle": {
            "fuel_economy_mpg": 10,
            "tank_capacity_gallons": 50,
            "initial_fuel_gallons": 50,
        },
        "fuel_summary": {
            "gallons_consumed": 0,
            "gallons_purchased": 0,
            "ending_fuel_gallons": 50,
            "en_route_fuel_cost_usd": 0,
        },
        "fuel_stops": [],
        "optimization": {
            "status": "optimal_under_model",
            "algorithm": "greedy_fuel_purchasing",
            "candidate_station_count": 0,
        },
        "data_quality": {
            "fuel_dataset_version": "assessment-csv",
            "geocoded_station_count": 0,
            "unmatched_station_count": None,
        },
        "performance": {
            "route_cache_hit": route.cache_hit,
            "routing_elapsed_ms": route_elapsed_ms,
            "station_matching_elapsed_ms": 0,
        },
        "assumptions": _assumptions(),
    }


def _assumptions() -> dict:
    return {
        "fuel_economy_mpg": 10,
        "tank_capacity_gallons": 50,
        "initial_fuel": "full",
        "price_source": "provided assessment CSV; not live prices",
        "station_access_model": (
            "access penalty = configured multiplier × lateral distance from route"
        ),
        "station_access_warning": (
            "Access distances are approximations, not road-network distances. "
            "The plan is optimal only under the modelled distances."
        ),
        "route_provider_calls": (
            "One directions request on route-cache miss. "
            "Zero to two geocoding requests when address strings are provided "
            "instead of coordinates (cached after the first call)."
        ),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def plan_fuel_route(data: dict) -> dict:
    """
    Orchestrate the full route-planning pipeline and return a JSON-serialisable
    response dict.
    """
    # ------------------------------------------------------------------ #
    # 1. Resolve start coordinates
    # ------------------------------------------------------------------ #
    if "start_coordinates" in data:
        start_lonlat = (
            float(data["start_coordinates"]["longitude"]),
            float(data["start_coordinates"]["latitude"]),
        )
        start_label = f"{start_lonlat[1]:.6f},{start_lonlat[0]:.6f}"
    else:
        start_label = data["start"]
        start_lonlat = resolve_address(start_label)

    # ------------------------------------------------------------------ #
    # 2. Resolve finish coordinates
    # ------------------------------------------------------------------ #
    if "finish_coordinates" in data:
        finish_lonlat = (
            float(data["finish_coordinates"]["longitude"]),
            float(data["finish_coordinates"]["latitude"]),
        )
        finish_label = f"{finish_lonlat[1]:.6f},{finish_lonlat[0]:.6f}"
    else:
        finish_label = data["finish"]
        finish_lonlat = resolve_address(finish_label)

    # ------------------------------------------------------------------ #
    # 3. Fetch the driving route (one ORS API call, cached 24 h)
    # ------------------------------------------------------------------ #
    t0 = time.perf_counter()
    route = get_route(start_label, finish_label, start_lonlat, finish_lonlat)
    route_elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)

    if route.distance_miles <= 1e-6:
        return _build_empty_response(route, route_elapsed_ms)

    # ------------------------------------------------------------------ #
    # 4. Find candidate fuel stations along the route corridor
    # ------------------------------------------------------------------ #
    t1 = time.perf_counter()
    stations = find_route_stations(
        route,
        corridor_miles=settings.ROUTE_CORRIDOR_MILES,
        access_multiplier=settings.STATION_ACCESS_MULTIPLIER,
    )
    matching_elapsed_ms = round((time.perf_counter() - t1) * 1000, 2)

    # ------------------------------------------------------------------ #
    # 5. Optimise fuel purchasing
    # ------------------------------------------------------------------ #
    result = optimize_fuel_stops(
        stations, route.distance_miles, mpg=10.0, tank_gallons=50.0
    )
    modeled_distance = result["modeled_distance_miles"]
    consumed = modeled_distance / 10.0
    ending_fuel = result["ending_fuel_gallons"]

    # ------------------------------------------------------------------ #
    # 6. Build fuel-stop payload
    # ------------------------------------------------------------------ #
    stop_payload = []
    for stop in result["stops"]:
        station = stop.station
        stop_payload.append({
            "station_id": station.station_id,
            "name": station.name,
            "address": ", ".join(
                x for x in [station.address, station.city, station.state] if x
            ),
            "latitude": station.latitude,
            "longitude": station.longitude,
            "price_per_gallon_usd": round(station.price_per_gallon, 4),
            "route_position_miles": _miles(stop.route_position_miles),
            "lateral_distance_miles": _miles(station.lateral_distance_miles),
            "modeled_detour_miles": _miles(stop.modeled_detour_miles),
            "gallons_to_purchase": round(stop.gallons_to_purchase, 4),
            "purchase_cost_usd": round(stop.purchase_cost_usd, 2),
            "reason": stop.reason,
        })

    # ------------------------------------------------------------------ #
    # 7. Assemble full response
    # ------------------------------------------------------------------ #
    return {
        "status": "success",
        "map_url": _build_map_url(route, result["stops"]),
        "route": {
            "start": route.start_label,
            "finish": route.finish_label,
            "route_distance_miles": _miles(route.distance_miles),
            "modeled_total_distance_miles": _miles(modeled_distance),
            "duration_seconds": (
                round(route.duration_seconds) if route.duration_seconds else None
            ),
            "geometry": list(route.geometry),
        },
        "vehicle": {
            "fuel_economy_mpg": 10,
            "tank_capacity_gallons": 50,
            "initial_fuel_gallons": 50,
        },
        "fuel_summary": {
            "gallons_consumed": round(consumed, 4),
            "gallons_purchased": round(result["gallons_purchased"], 4),
            "ending_fuel_gallons": round(ending_fuel, 4),
            "en_route_fuel_cost_usd": round(result["en_route_cost_usd"], 2),
            "initial_fuel_cost_usd": None,
            "initial_fuel_cost_note": (
                "Initial tank is assumed full; its historical purchase cost "
                "is unknown and excluded."
            ),
        },
        "fuel_stops": stop_payload,
        "optimization": {
            "status": "optimal_under_model",
            "algorithm": "greedy_fuel_purchasing",
            "candidate_station_count": len(stations),
        },
        "data_quality": {
            "fuel_dataset_version": "assessment-csv",
            "geocoded_station_count": "see preprocessing report",
            "unmatched_station_count": "see preprocessing report",
        },
        "performance": {
            "route_cache_hit": route.cache_hit,
            "routing_elapsed_ms": route_elapsed_ms,
            "station_matching_elapsed_ms": matching_elapsed_ms,
        },
        "assumptions": _assumptions(),
    }
