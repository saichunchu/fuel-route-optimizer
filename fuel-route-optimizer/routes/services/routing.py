import requests
from django.conf import settings
from routes.services.cache import cache_get, cache_set, stable_key
from routes.services.errors import RouteProviderError
from routes.services.models import RouteData

ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/{profile}/geojson"
METERS_PER_MILE = 1609.344

def get_route(start_label, finish_label, start_lonlat, finish_lonlat):
    key = stable_key("route", {
        "profile": settings.ORS_PROFILE,
        "start": [round(start_lonlat[0], 6), round(start_lonlat[1], 6)],
        "finish": [round(finish_lonlat[0], 6), round(finish_lonlat[1], 6)],
    })
    cached = cache_get(key)
    if cached:
        return RouteData(**{**cached, "geometry": [tuple(x) for x in cached["geometry"]], "cache_hit": True})

    if not settings.OPENROUTESERVICE_API_KEY:
        raise RouteProviderError("OPENROUTESERVICE_API_KEY is not configured.")

    try:
        response = requests.post(
            ORS_DIRECTIONS_URL.format(profile=settings.ORS_PROFILE),
            headers={
                "Authorization": settings.OPENROUTESERVICE_API_KEY,
                "Content-Type": "application/json",
            },
            json={"coordinates": [list(start_lonlat), list(finish_lonlat)], "instructions": False},
            timeout=settings.ROUTING_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        feature = payload["features"][0]
        props = feature["properties"]["summary"]
        geometry = feature["geometry"]["coordinates"]
        distance_miles = float(props["distance"]) / METERS_PER_MILE
        duration = float(props.get("duration", 0))
        route = RouteData(
            start_label=start_label,
            finish_label=finish_label,
            start_lonlat=tuple(start_lonlat),
            finish_lonlat=tuple(finish_lonlat),
            distance_miles=distance_miles,
            duration_seconds=duration,
            geometry=[tuple(x[:2]) for x in geometry],
            geometry_encoded=None,
            cache_hit=False,
        )
        cache_payload = {
            "start_label": route.start_label,
            "finish_label": route.finish_label,
            "start_lonlat": route.start_lonlat,
            "finish_lonlat": route.finish_lonlat,
            "distance_miles": route.distance_miles,
            "duration_seconds": route.duration_seconds,
            "geometry": route.geometry,
            "geometry_encoded": None,
            "cache_hit": False,
        }
        cache_set(key, cache_payload, settings.ROUTE_CACHE_TTL_SECONDS)
        return route
    except requests.RequestException as exc:
        detail = "Routing provider request failed"
        try:
            detail += f": {response.text[:300]}"
        except Exception:
            detail += f": {exc}"
        raise RouteProviderError(detail) from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RouteProviderError(f"Unexpected routing response format: {exc}") from exc
