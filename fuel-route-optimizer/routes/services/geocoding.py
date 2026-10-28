import requests
from django.conf import settings
from routes.services.cache import cache_get, cache_set, stable_key
from routes.services.errors import AddressResolutionError

ORS_GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"

def resolve_address(address: str) -> tuple[float, float]:
    """
    Resolve an endpoint address using OpenRouteService geocoding.
    Returns (longitude, latitude). Results are cached in-process.
    """
    normalized = " ".join(address.strip().split())
    key = stable_key("geocode", {"address": normalized.casefold()})
    cached = cache_get(key)
    if cached:
        return tuple(cached)

    api_key = settings.OPENROUTESERVICE_API_KEY
    if not api_key:
        raise AddressResolutionError(
            "OPENROUTESERVICE_API_KEY is not configured. Provide coordinates or configure a key."
        )

    try:
        response = requests.get(
            ORS_GEOCODE_URL,
            params={"api_key": api_key, "text": normalized, "boundary.country": "US", "size": 1},
            timeout=settings.ROUTING_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        features = payload.get("features", [])
        if not features:
            raise AddressResolutionError(f"Could not resolve US address: {address}")
        lon, lat = features[0]["geometry"]["coordinates"][:2]
        if not (-125 <= lon <= -66 and 24 <= lat <= 50):
            raise AddressResolutionError(f"Resolved location is outside the contiguous USA: {address}")
        result = (float(lon), float(lat))
        cache_set(key, result, settings.ROUTE_CACHE_TTL_SECONDS)
        return result
    except AddressResolutionError:
        raise
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        raise AddressResolutionError(f"Could not resolve address '{address}': {exc}") from exc
