from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class FuelStation:
    station_id: str
    name: str
    address: str
    city: str
    state: str
    price_per_gallon: float
    latitude: float
    longitude: float
    route_position_miles: float = 0.0
    lateral_distance_miles: float = 0.0
    access_penalty_miles: float = 0.0

@dataclass(frozen=True)
class RouteData:
    start_label: str
    finish_label: str
    start_lonlat: tuple[float, float]
    finish_lonlat: tuple[float, float]
    distance_miles: float
    duration_seconds: Optional[float]
    geometry: list[tuple[float, float]]  # [(lon, lat), ...]
    geometry_encoded: Optional[str] = None
    cache_hit: bool = False

@dataclass(frozen=True)
class PlannedStop:
    station: FuelStation
    route_position_miles: float
    gallons_to_purchase: float
    purchase_cost_usd: float
    fuel_after_departure_gallons: float
    modeled_detour_miles: float
    reason: str
