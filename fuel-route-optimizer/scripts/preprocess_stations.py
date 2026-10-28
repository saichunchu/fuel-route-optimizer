"""
Offline station geocoding.

Important:
- The supplied CSV has no latitude/longitude.
- This script uses OpenRouteService geocoding and caches every result.
- Review the provider's current plan, quotas, and terms before running bulk geocoding.
- It is resumable: successful and failed lookups are persisted in a cache CSV.
- For 8,151 records, this can take significant time and may exceed a free-tier quota.
"""
import argparse
import csv
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ORS_GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"
SOURCE_COLUMNS = [
    "OPIS Truckstop ID", "Truckstop Name", "Address", "City", "State", "Rack ID", "Retail Price"
]

def normalize_address(row):
    parts = [row.get("Address", ""), row.get("City", ""), row.get("State", ""), "USA"]
    return ", ".join(" ".join(str(p).split()) for p in parts if str(p).strip())

def geocode(address, api_key, timeout=15):
    response = requests.get(
        ORS_GEOCODE_URL,
        params={"api_key": api_key, "text": address, "boundary.country": "US", "size": 1},
        timeout=timeout,
    )
    response.raise_for_status()
    features = response.json().get("features", [])
    if not features:
        return None
    lon, lat = features[0]["geometry"]["coordinates"][:2]
    if not (-125 <= lon <= -66 and 24 <= lat <= 50):
        return None
    return float(lat), float(lon)

def main():
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=os.getenv("FUEL_CSV_PATH", "data/fuel-prices-for-be-assessment.csv"))
    parser.add_argument("--output", default="data/processed/fuel_stations_geocoded.csv")
    parser.add_argument("--cache", default="data/processed/geocode_cache.csv")
    parser.add_argument("--sleep", type=float, default=float(os.getenv("GEOCODE_SLEEP_SECONDS", "1.0")))
    parser.add_argument("--limit", type=int, default=0, help="Process only N unique addresses; 0 means all.")
    args = parser.parse_args()

    api_key = os.getenv("OPENROUTESERVICE_API_KEY", "")
    if not api_key:
        sys.exit("Set OPENROUTESERVICE_API_KEY in .env before preprocessing.")

    input_path = Path(args.input)
    output_path = Path(args.output)
    cache_path = Path(args.cache)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    with input_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = set(SOURCE_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            sys.exit(f"CSV is missing columns: {sorted(missing)}")
        rows = list(reader)

    cache = {}
    if cache_path.exists():
        with cache_path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                cache[row["address"]] = (row.get("latitude", ""), row.get("longitude", ""), row.get("status", ""))

    unique_addresses = []
    seen = set()
    for row in rows:
        address = normalize_address(row)
        if address not in seen:
            seen.add(address)
            unique_addresses.append(address)
    if args.limit:
        allowed = set(unique_addresses[:args.limit])
    else:
        allowed = set(unique_addresses)

    cache_file = cache_path.open("w", encoding="utf-8", newline="")
    cache_writer = csv.DictWriter(cache_file, fieldnames=["address", "latitude", "longitude", "status"])
    cache_writer.writeheader()
    try:
        for i, address in enumerate(unique_addresses, 1):
            if address in cache:
                lat, lon, status = cache[address]
            elif address not in allowed:
                lat, lon, status = "", "", "not_processed_limit"
            else:
                try:
                    coords = geocode(address, api_key)
                    if coords:
                        lat, lon = coords
                        status = "matched"
                    else:
                        lat, lon, status = "", "", "not_found"
                except Exception as exc:
                    print(f"[{i}/{len(unique_addresses)}] ERROR {address}: {exc}", file=sys.stderr)
                    lat, lon, status = "", "", "error"
                time.sleep(max(0.0, args.sleep))
            cache[address] = (str(lat), str(lon), status)
            cache_writer.writerow({
                "address": address, "latitude": lat, "longitude": lon, "status": status
            })
            cache_file.flush()
            if i % 100 == 0:
                print(f"Processed {i}/{len(unique_addresses)} unique addresses")
    finally:
        cache_file.close()

    output_fields = SOURCE_COLUMNS + ["latitude", "longitude", "geocode_status"]
    matched = unmatched = 0
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=output_fields)
        writer.writeheader()
        for row in rows:
            address = normalize_address(row)
            lat, lon, status = cache.get(address, ("", "", "not_processed"))
            if status == "matched" and lat and lon:
                matched += 1
            else:
                unmatched += 1
            writer.writerow({
                **row,
                "latitude": lat,
                "longitude": lon,
                "geocode_status": status,
            })

    report = {
        "source_rows": len(rows),
        "unique_addresses": len(unique_addresses),
        "matched_rows": matched,
        "unmatched_rows": unmatched,
        "output": str(output_path),
        "cache": str(cache_path),
    }
    print(report)

if __name__ == "__main__":
    main()
