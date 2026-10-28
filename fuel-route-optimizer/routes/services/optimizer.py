from dataclasses import dataclass
from routes.services.errors import RouteInfeasibleError
from routes.services.models import PlannedStop

MPG = 10.0
TANK_GALLONS = 50.0
EPSILON = 1e-7

@dataclass(frozen=True)
class Node:
    kind: str  # origin, station, destination
    position: float
    price: float | None = None
    station: object | None = None
    access_penalty: float = 0.0

def _leg_distance(a: Node, b: Node) -> float:
    """
    Distance from a to b under the approximation model.

    Route progress is monotonic. A station's configured access penalty is
    charged only when that station is actually selected as the target, so
    skipped candidate stations do not add fictitious distance.
    """
    route_leg = max(0.0, b.position - a.position)
    return route_leg + (b.access_penalty if b.kind == "station" else 0.0)

def optimize_fuel_stops(stations, route_distance_miles, mpg=MPG, tank_gallons=TANK_GALLONS):
    """
    Greedy fuel purchasing with a full initial tank.

    The first cheaper station reachable on a full tank is preferred. If it is
    reachable with current fuel, buy only enough to reach it; otherwise buy
    enough to reach it. If no cheaper station is reachable, fill the tank
    unless the destination is closer.

    This is optimal for the classic continuous-refueling model when the
    modeled leg distances and station prices are correct. Here station access
    distances are approximate, so the guarantee is model-relative.
    """
    if route_distance_miles < 0:
        raise ValueError("Route distance must be non-negative.")
    if mpg <= 0 or tank_gallons <= 0:
        raise ValueError("MPG and tank capacity must be positive.")

    # Short routes can be completed on the initial full tank without visiting
    # a station. No station access detour should be charged in that case.
    if route_distance_miles <= tank_gallons * mpg + EPSILON:
        return {
            "stops": [],
            "gallons_purchased": 0.0,
            "en_route_cost_usd": 0.0,
            "modeled_distance_miles": route_distance_miles,
            "ending_fuel_gallons": max(0.0, tank_gallons - route_distance_miles / mpg),
        }

    nodes = [Node("origin", 0.0)]
    for station in stations:
        if station.route_position_miles <= EPSILON:
            continue
        if station.route_position_miles >= route_distance_miles - EPSILON:
            continue
        nodes.append(Node(
            "station",
            station.route_position_miles,
            float(station.price_per_gallon),
            station,
            max(0.0, float(station.access_penalty_miles)),
        ))
    nodes.append(Node("destination", route_distance_miles))
    nodes.sort(key=lambda n: (n.position, 0 if n.kind == "station" else 1))

    fuel = tank_gallons
    current_idx = 0
    stops = []
    total_purchased = 0.0
    total_cost = 0.0
    modeled_distance = 0.0

    while current_idx < len(nodes) - 1:
        current = nodes[current_idx]

        # If destination can be reached directly from this node, no more
        # fuel is required. Access to the current station was charged on arrival.
        destination_node = nodes[-1]
        direct_to_destination = _leg_distance(current, destination_node)
        if direct_to_destination <= fuel * mpg + EPSILON:
            fuel -= direct_to_destination / mpg
            modeled_distance += direct_to_destination
            break

        if current.kind == "origin":
            # Starting fuel is assumed paid for historically, so use it before
            # purchasing any additional fuel. Select the farthest station
            # reachable on initial fuel; skipped stations add no detour penalty.
            reachable = []
            for j in range(1, len(nodes) - 1):
                leg = _leg_distance(current, nodes[j])
                if leg <= fuel * mpg + EPSILON:
                    reachable.append(j)
                # Route positions are sorted; access penalties are non-negative.
                elif nodes[j].position - current.position > fuel * mpg:
                    break
            if not reachable:
                raise RouteInfeasibleError(
                    "No fuel station is reachable from the origin within the initial 500-mile range."
                )
            target_idx = reachable[-1]
            target = nodes[target_idx]
            travel = _leg_distance(current, target)
            fuel -= travel / mpg
            modeled_distance += travel
            current_idx = target_idx
            continue

        # At a paid station, find the first cheaper station reachable on a full tank.
        cheaper_idx = None
        farthest_reachable_idx = None
        for j in range(current_idx + 1, len(nodes)):
            target = nodes[j]
            travel = _leg_distance(current, target)
            if travel > tank_gallons * mpg + EPSILON:
                # Later stations are farther along the route, so stop scanning.
                break
            farthest_reachable_idx = j
            if target.kind == "station" and target.price < current.price - EPSILON:
                cheaper_idx = j
                break

        if cheaper_idx is not None:
            target_idx = cheaper_idx
            target = nodes[target_idx]
            travel = _leg_distance(current, target)
            needed = travel / mpg
            gallons_to_buy = max(0.0, needed - fuel)
            reason = "cheaper reachable station"
        else:
            destination_distance = direct_to_destination
            if destination_distance <= tank_gallons * mpg + EPSILON:
                target_idx = len(nodes) - 1
                target = nodes[target_idx]
                travel = destination_distance
                gallons_to_buy = max(0.0, travel / mpg - fuel)
                reason = "enough fuel to reach destination"
            else:
                if farthest_reachable_idx is None:
                    raise RouteInfeasibleError(
                        f"No station or destination is reachable from {current.station.name} "
                        "within a full tank."
                    )
                target_idx = farthest_reachable_idx
                target = nodes[target_idx]
                # No cheaper station is reachable: fill at the current price.
                gallons_to_buy = max(0.0, tank_gallons - fuel)
                reason = "no cheaper station reachable within range"

        capacity = tank_gallons - fuel
        if gallons_to_buy > capacity + EPSILON:
            raise RouteInfeasibleError("Calculated purchase exceeds tank capacity.")
        gallons_to_buy = min(max(0.0, gallons_to_buy), max(0.0, capacity))

        purchase_cost = gallons_to_buy * current.price
        fuel += gallons_to_buy
        total_purchased += gallons_to_buy
        total_cost += purchase_cost

        if gallons_to_buy > EPSILON:
            stops.append(PlannedStop(
                station=current.station,
                route_position_miles=current.position,
                gallons_to_purchase=gallons_to_buy,
                purchase_cost_usd=purchase_cost,
                fuel_after_departure_gallons=fuel,
                modeled_detour_miles=current.access_penalty,
                reason=reason,
            ))

        travel = _leg_distance(current, target)
        if travel > fuel * mpg + EPSILON:
            raise RouteInfeasibleError(
                f"Calculated plan cannot reach "
                f"{target.station.name if target.station else 'destination'}."
            )
        fuel -= travel / mpg
        fuel = max(0.0, fuel)
        modeled_distance += travel
        current_idx = target_idx

        if target.kind == "destination":
            break

    # Fuel conservation is the authoritative accounting invariant.
    expected_ending = tank_gallons + total_purchased - modeled_distance / mpg
    if expected_ending < -1e-4 or expected_ending > tank_gallons + 1e-4:
        raise RouteInfeasibleError("Fuel accounting invariant failed.")
    expected_ending = min(tank_gallons, max(0.0, expected_ending))

    return {
        "stops": stops,
        "gallons_purchased": total_purchased,
        "en_route_cost_usd": total_cost,
        "modeled_distance_miles": modeled_distance,
        "ending_fuel_gallons": expected_ending,
    }
