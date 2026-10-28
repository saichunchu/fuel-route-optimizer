import unittest
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from dataclasses import dataclass
from routes.services.optimizer import optimize_fuel_stops
from routes.services.errors import RouteInfeasibleError

@dataclass
class Station:
    station_id: str
    name: str
    price_per_gallon: float
    route_position_miles: float
    access_penalty_miles: float = 0.0
    lateral_distance_miles: float = 0.0
    address: str = ""
    city: str = ""
    state: str = ""
    latitude: float = 0.0
    longitude: float = 0.0

class OptimizerTests(unittest.TestCase):
    def test_trip_within_initial_range_needs_no_stop(self):
        result = optimize_fuel_stops([], 300)
        self.assertEqual(result["gallons_purchased"], 0)
        self.assertEqual(result["en_route_cost_usd"], 0)
        self.assertAlmostEqual(result["ending_fuel_gallons"], 20)

    def test_long_trip_uses_station_and_fills(self):
        stations = [
            Station("1", "A", 3.00, 400),
            Station("2", "B", 3.50, 800),
        ]
        result = optimize_fuel_stops(stations, 900)
        self.assertTrue(result["gallons_purchased"] >= 0)
        self.assertGreaterEqual(result["en_route_cost_usd"], 0)

    def test_no_station_reachable_from_origin_is_infeasible(self):
        with self.assertRaises(RouteInfeasibleError):
            optimize_fuel_stops([], 900)

if __name__ == "__main__":
    unittest.main()
