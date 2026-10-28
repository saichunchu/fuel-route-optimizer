"""
Route matching — finds fuel stations within a corridor of the route geometry.

Performance path:
  1. KD-tree query (station_index) on sampled route points → candidate set
  2. Vectorised NumPy projection of every candidate onto the full polyline
  3. Discard stations whose perpendicular distance exceeds corridor_miles

The vectorised projection replaces a Python double-loop
(O(segments × candidates)) with a batched NumPy operation that is typically
10–50× faster on real-world polylines.
"""

import math
from routes.services.models import FuelStation
from routes.services.station_index import load_station_index

try:
    import numpy as np
    _NUMPY = True
except ImportError:  # pragma: no cover
    _NUMPY = False

MILES_PER_DEG_LAT = 69.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _haversine_miles(a, b):
    lon1, lat1 = a
    lon2, lat2 = b
    r = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _route_cumulative_miles(coords):
    cumulative = [0.0]
    for i in range(1, len(coords)):
        cumulative.append(cumulative[-1] + _haversine_miles(coords[i - 1], coords[i]))
    return cumulative


# ---------------------------------------------------------------------------
# Vectorised projection (NumPy)
# ---------------------------------------------------------------------------

def _project_stations_numpy(station_lonlats, route_coords, cumulative):
    """
    Project all stations onto the route in one batched NumPy operation.

    Returns two 1-D arrays: (route_positions_miles, lateral_distances_miles)
    with one value per station.
    """
    mean_lat = sum(lat for _, lat in route_coords) / len(route_coords)
    cos_lat = max(0.01, math.cos(math.radians(mean_lat)))
    sx = 69.172 * cos_lat  # miles per degree longitude at mean lat

    # Route in projected miles
    rx = np.array([lon * sx for lon, lat in route_coords])   # shape (N,)
    ry = np.array([lat * MILES_PER_DEG_LAT for _, lat in route_coords])

    seg_ax, seg_ay = rx[:-1], ry[:-1]   # segment starts  (N-1,)
    seg_bx, seg_by = rx[1:],  ry[1:]    # segment ends    (N-1,)
    seg_dx = seg_bx - seg_ax
    seg_dy = seg_by - seg_ay
    seg_len2 = seg_dx * seg_dx + seg_dy * seg_dy   # |seg|² for each segment

    cum = np.array(cumulative)  # shape (N,)
    seg_span = cum[1:] - cum[:-1]  # miles for each segment

    # Station coordinates in projected space — shape (S,)
    px = np.array([lon * sx for lon, lat in station_lonlats])
    py = np.array([lat * MILES_PER_DEG_LAT for lon, lat in station_lonlats])

    best_dist = np.full(len(px), np.inf)
    best_pos  = np.zeros(len(px))

    # Process in chunks of segments to stay memory-efficient on long routes
    CHUNK = 512
    n_segs = len(seg_ax)
    for start in range(0, n_segs, CHUNK):
        end = min(start + CHUNK, n_segs)
        # Broadcast: stations (S,1)  ×  segments (1, chunk)
        spx = px[:, None] - seg_ax[None, start:end]   # (S, chunk)
        spy = py[:, None] - seg_ay[None, start:end]

        dxc = seg_dx[None, start:end]   # (1, chunk)
        dyc = seg_dy[None, start:end]
        l2c = seg_len2[None, start:end]

        dot = spx * dxc + spy * dyc
        t = np.where(l2c > 0, np.clip(dot / l2c, 0.0, 1.0), 0.0)

        qx = seg_ax[None, start:end] + t * dxc
        qy = seg_ay[None, start:end] + t * dyc

        dist = np.hypot(px[:, None] - qx, py[:, None] - qy)  # (S, chunk)

        # Best segment in this chunk for each station
        local_best_idx = np.argmin(dist, axis=1)           # (S,)
        local_best_dist = dist[np.arange(len(px)), local_best_idx]  # (S,)

        improve = local_best_dist < best_dist
        if not np.any(improve):
            continue

        global_seg = start + local_best_idx                 # absolute seg index
        t_best = t[np.arange(len(px)), local_best_idx]

        # Route position = cum[global_seg] + t * seg_span[global_seg]
        pos = cum[global_seg] + t_best * seg_span[global_seg]

        best_dist = np.where(improve, local_best_dist, best_dist)
        best_pos  = np.where(improve, pos,             best_pos)

    return best_pos, best_dist  # both shape (S,)


# ---------------------------------------------------------------------------
# Pure-Python fallback (no NumPy)
# ---------------------------------------------------------------------------

def _project_station_to_route_py(station_lonlat, route_coords, cumulative):
    mean_lat = sum(lat for _, lat in route_coords) / len(route_coords)
    cos_lat = max(0.01, math.cos(math.radians(mean_lat)))
    sx = 69.172 * cos_lat

    route_xy = [(lon * sx, lat * MILES_PER_DEG_LAT) for lon, lat in route_coords]
    px = station_lonlat[0] * sx
    py = station_lonlat[1] * MILES_PER_DEG_LAT

    best_dist = float("inf")
    best_position = 0.0
    for i in range(1, len(route_xy)):
        ax, ay = route_xy[i - 1]
        bx, by = route_xy[i]
        dx, dy = bx - ax, by - ay
        denom = dx * dx + dy * dy
        t = 0.0 if denom == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
        qx, qy = ax + t * dx, ay + t * dy
        dist = math.hypot(px - qx, py - qy)
        if dist < best_dist:
            best_dist = dist
            seg_miles = cumulative[i] - cumulative[i - 1]
            best_position = cumulative[i - 1] + t * seg_miles
    return best_position, best_dist


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_route_stations(route, corridor_miles=5.0, access_multiplier=2.0):
    """
    Return a list of FuelStation instances within *corridor_miles* of the route,
    annotated with their along-route position and access penalty.
    """
    index = load_station_index()
    coords = route.geometry
    if len(coords) < 2:
        return []

    # Sub-sample for the KD-tree candidate query (keeps it fast on dense polylines)
    sample_step = max(1, len(coords) // 400)
    sampled = coords[::sample_step]
    if sampled[-1] != coords[-1]:
        sampled = list(sampled) + [coords[-1]]

    candidates = index.query_route_points(sampled, corridor_miles + 1.0)
    if not candidates:
        return []

    cumulative = _route_cumulative_miles(coords)

    # Deduplicate candidates by station_id (the KD-tree may return the same
    # station from multiple sampled points)
    seen = {}
    for row in candidates:
        sid = row["station_id"]
        if sid not in seen:
            seen[sid] = row
    unique_candidates = list(seen.values())

    # Project all candidate stations onto the full route
    if _NUMPY and len(unique_candidates) > 1:
        lonlats = [(r["longitude"], r["latitude"]) for r in unique_candidates]
        positions, laterals = _project_stations_numpy(lonlats, coords, cumulative)
    else:
        positions, laterals = [], []
        for row in unique_candidates:
            pos, lat = _project_station_to_route_py(
                (row["longitude"], row["latitude"]), coords, cumulative
            )
            positions.append(pos)
            laterals.append(lat)

    projected = []
    for row, position, lateral in zip(unique_candidates, positions, laterals):
        if lateral > corridor_miles:
            continue
        access = access_multiplier * lateral
        projected.append(FuelStation(
            station_id=row["station_id"],
            name=row["name"],
            address=row["address"],
            city=row["city"],
            state=row["state"],
            price_per_gallon=row["price_per_gallon"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            route_position_miles=float(position),
            lateral_distance_miles=float(lateral),
            access_penalty_miles=float(access),
        ))

    projected.sort(key=lambda s: (s.route_position_miles, s.price_per_gallon, s.station_id))
    return projected
