"""
Station index — loads the pre-geocoded fuel station CSV and builds a spatial
KD-tree for fast proximity queries along route geometries.

Load is cached for the lifetime of the process (or until the CSV changes)
via functools.lru_cache keyed on (path, mtime).
"""

import csv
import math
import os
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from scipy.spatial import cKDTree
from routes.services.errors import FuelDataError

REQUIRED_COLUMNS = {
    "OPIS Truckstop ID", "Truckstop Name", "Address", "City", "State", "Retail Price"
}
EARTH_RADIUS_MILES = 3958.7613

# Continental US bounding box — matches the preprocessing validation bounds
_LON_MIN, _LON_MAX = -125.0, -66.0
_LAT_MIN, _LAT_MAX = 24.0, 50.0


def _valid_price(value):
    try:
        price = float(value)
        return math.isfinite(price) and price > 0
    except (TypeError, ValueError):
        return False


def _valid_us_coords(lat, lon):
    """True if the coordinates fall within the continental US bounding box."""
    return _LAT_MIN <= lat <= _LAT_MAX and _LON_MIN <= lon <= _LON_MAX


def _to_xyz(lat, lon):
    lat_r, lon_r = math.radians(lat), math.radians(lon)
    return (
        math.cos(lat_r) * math.cos(lon_r),
        math.cos(lat_r) * math.sin(lon_r),
        math.sin(lat_r),
    )


class StationIndex:
    def __init__(self, records):
        self.records = records
        self.xyz = [_to_xyz(s["latitude"], s["longitude"]) for s in records]
        self.tree = cKDTree(self.xyz) if self.xyz else None

    def query_route_points(self, lonlat_points, radius_miles):
        """
        Return all stations within *radius_miles* of any of the given route
        sample points. Final corridor filtering is done by route_matching.
        """
        if not self.tree:
            return []
        # Convert angular radius to unit-sphere chord length
        angular = radius_miles / EARTH_RADIUS_MILES
        chord = 2 * math.sin(angular / 2)
        found: set[int] = set()
        for lon, lat in lonlat_points:
            point = _to_xyz(lat, lon)
            found.update(self.tree.query_ball_point(point, chord))
        return [self.records[i] for i in sorted(found)]


@lru_cache(maxsize=4)
def _load_cached(path: str, mtime: float):
    """
    Parse the geocoded CSV and build a StationIndex.

    Deduplication strategy:
      - One row per (station_id, price_per_gallon) pair so that stations with
        multiple listed prices each appear as independent options.
      - Rows whose geocode_status != "matched" are skipped.
      - Rows with coordinates outside the continental US bounding box are skipped.
    """
    if not os.path.exists(path):
        raise FuelDataError(
            f"Fuel data file not found: {path}. "
            "Copy the supplied CSV to data/ or set FUEL_CSV_PATH."
        )

    # Prefer the pre-geocoded processed file if it exists alongside the source
    processed = Path(path).parent / "processed" / "fuel_stations_geocoded.csv"
    source = processed if processed.exists() else Path(path)

    records = []
    # Key: (station_id, price_per_gallon) — allows multiple prices per station
    # while still deduplicating identical rows
    seen_keys: set[tuple] = set()

    try:
        with source.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            columns = set(reader.fieldnames or [])

            if not REQUIRED_COLUMNS.issubset(columns):
                raise FuelDataError(
                    f"Fuel CSV missing required columns: "
                    f"{sorted(REQUIRED_COLUMNS - columns)}"
                )
            if not {"latitude", "longitude"}.issubset(columns):
                raise FuelDataError(
                    "Station coordinates are not available. "
                    "Run scripts/preprocess_stations.py first."
                )

            has_status_col = "geocode_status" in columns

            for row in reader:
                # Only use rows that were successfully geocoded
                if has_status_col:
                    status = str(row.get("geocode_status", "")).strip()
                    if status != "matched":
                        continue

                station_id = str(row.get("OPIS Truckstop ID", "")).strip()
                if not station_id:
                    continue

                if not _valid_price(row.get("Retail Price")):
                    continue

                price = float(row["Retail Price"])

                try:
                    lat = float(row["latitude"])
                    lon = float(row["longitude"])
                except (ValueError, TypeError, KeyError):
                    continue

                # Skip obviously bad coordinates (includes geocoding errors
                # like the Pacific-Northwest result for an Alabama address)
                if not _valid_us_coords(lat, lon):
                    continue

                key = (station_id, round(price, 6))
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                records.append({
                    "station_id": station_id,
                    "name": str(row.get("Truckstop Name", "")).strip(),
                    "address": str(row.get("Address", "")).strip(),
                    "city": str(row.get("City", "")).strip(),
                    "state": str(row.get("State", "")).strip(),
                    "price_per_gallon": price,
                    "latitude": lat,
                    "longitude": lon,
                })

    except FuelDataError:
        raise
    except OSError as exc:
        raise FuelDataError(f"Unable to read fuel data: {exc}") from exc

    if not records:
        raise FuelDataError(f"No usable geocoded station records found in {source}")

    return StationIndex(records)


def load_station_index() -> StationIndex:
    """Return the process-level cached station index."""
    path = str(Path(settings.FUEL_CSV_PATH).resolve())
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    return _load_cached(path, mtime)
