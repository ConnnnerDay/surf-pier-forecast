"""Coastal fishing location database and utilities.

Pre-mapped locations along the US East Coast, Gulf Coast, West Coast,
and Hawaii.  Each location includes NOAA CO-OPS station IDs, NDBC buoy IDs,
NWS marine zones, coordinates, and monthly water temperature averages
so the forecast engine can generate location-specific reports.

Zip code geocoding uses the free zippopotam.us API (no key required)
with a haversine distance calculation to find the nearest locations.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import requests


# ---------------------------------------------------------------------------
# Regional monthly water temperature profiles (°F), indexed Jan(1)–Dec(12).
# Each location references one of these and may apply a small offset.
# ---------------------------------------------------------------------------

_WATER_TEMPS: Dict[str, Dict[int, float]] = {
    "northeast": {
        1: 40, 2: 38, 3: 40, 4: 48, 5: 56, 6: 64,
        7: 72, 8: 74, 9: 70, 10: 62, 11: 53, 12: 45,
    },
    "midatlantic": {
        1: 43, 2: 41, 3: 44, 4: 52, 5: 60, 6: 70,
        7: 77, 8: 78, 9: 74, 10: 65, 11: 55, 12: 47,
    },
    "nc_outer_banks": {
        1: 48, 2: 47, 3: 50, 4: 58, 5: 67, 6: 76,
        7: 80, 8: 81, 9: 78, 10: 70, 11: 60, 12: 52,
    },
    "nc_south": {
        1: 50, 2: 50, 3: 54, 4: 62, 5: 70, 6: 78,
        7: 82, 8: 83, 9: 80, 10: 72, 11: 62, 12: 54,
    },
    "sc_ga": {
        1: 52, 2: 52, 3: 56, 4: 63, 5: 71, 6: 79,
        7: 83, 8: 84, 9: 81, 10: 74, 11: 64, 12: 56,
    },
    "fl_northeast": {
        1: 58, 2: 58, 3: 62, 4: 68, 5: 75, 6: 81,
        7: 84, 8: 85, 9: 82, 10: 76, 11: 68, 12: 61,
    },
    "fl_central_east": {
        1: 65, 2: 65, 3: 68, 4: 73, 5: 78, 6: 82,
        7: 84, 8: 85, 9: 84, 10: 79, 11: 73, 12: 67,
    },
    "fl_south": {
        1: 73, 2: 73, 3: 75, 4: 78, 5: 81, 6: 84,
        7: 86, 8: 87, 9: 86, 10: 82, 11: 77, 12: 74,
    },
    "fl_keys": {
        1: 73, 2: 74, 3: 76, 4: 79, 5: 82, 6: 85,
        7: 87, 8: 87, 9: 86, 10: 82, 11: 78, 12: 75,
    },
    "fl_gulf_south": {
        1: 64, 2: 65, 3: 69, 4: 74, 5: 80, 6: 85,
        7: 87, 8: 87, 9: 85, 10: 79, 11: 72, 12: 66,
    },
    "fl_gulf_north": {
        1: 58, 2: 58, 3: 63, 4: 69, 5: 77, 6: 83,
        7: 86, 8: 86, 9: 83, 10: 75, 11: 66, 12: 60,
    },
    "gulf_central": {
        1: 55, 2: 56, 3: 62, 4: 69, 5: 77, 6: 84,
        7: 86, 8: 87, 9: 84, 10: 76, 11: 66, 12: 58,
    },
    "gulf_west": {
        1: 57, 2: 58, 3: 64, 4: 71, 5: 78, 6: 84,
        7: 86, 8: 86, 9: 84, 10: 77, 11: 68, 12: 60,
    },
    # ── Pacific Coast ────────────────────────────────────────────────────
    "pacific_nw": {
        1: 48, 2: 47, 3: 48, 4: 50, 5: 52, 6: 54,
        7: 56, 8: 57, 9: 57, 10: 55, 11: 52, 12: 49,
    },
    "pacific_norcal": {
        1: 51, 2: 50, 3: 50, 4: 51, 5: 52, 6: 54,
        7: 56, 8: 57, 9: 58, 10: 57, 11: 55, 12: 52,
    },
    "pacific_central_cal": {
        1: 54, 2: 53, 3: 54, 4: 55, 5: 57, 6: 59,
        7: 61, 8: 62, 9: 63, 10: 62, 11: 59, 12: 56,
    },
    "pacific_socal": {
        1: 58, 2: 57, 3: 58, 4: 60, 5: 62, 6: 65,
        7: 68, 8: 70, 9: 70, 10: 67, 11: 63, 12: 59,
    },
    "pacific_san_diego": {
        1: 59, 2: 58, 3: 59, 4: 61, 5: 63, 6: 66,
        7: 70, 8: 72, 9: 72, 10: 68, 11: 64, 12: 60,
    },
    "hawaii": {
        1: 76, 2: 75, 3: 75, 4: 76, 5: 77, 6: 78,
        7: 79, 8: 80, 9: 80, 10: 79, 11: 78, 12: 77,
    },
}

# ---------------------------------------------------------------------------
# Regional monthly fallback conditions (wind knots, wave feet).
# Used when live data is unavailable.  Format: {month: ((wind_lo, wind_hi), (wave_lo, wave_hi))}
# ---------------------------------------------------------------------------

_FALLBACK_CONDITIONS: Dict[str, Dict[int, Tuple[Tuple[float, float], Tuple[float, float]]]] = {
    "atlantic_north": {
        1: ((12, 28), (4, 8)), 2: ((12, 28), (4, 8)), 3: ((10, 24), (3, 7)),
        4: ((8, 20), (2, 5)), 5: ((7, 16), (2, 4)), 6: ((6, 14), (1, 3)),
        7: ((6, 12), (1, 3)), 8: ((6, 12), (1, 3)), 9: ((8, 18), (2, 5)),
        10: ((10, 22), (3, 6)), 11: ((10, 24), (3, 7)), 12: ((12, 26), (4, 8)),
    },
    "atlantic_mid": {
        1: ((10, 25), (3, 7)), 2: ((10, 25), (3, 7)), 3: ((9, 22), (3, 6)),
        4: ((8, 18), (2, 5)), 5: ((7, 15), (1, 4)), 6: ((6, 13), (1, 3)),
        7: ((5, 12), (1, 2)), 8: ((5, 12), (1, 2)), 9: ((7, 16), (2, 4)),
        10: ((8, 18), (2, 5)), 11: ((9, 20), (3, 6)), 12: ((10, 24), (3, 7)),
    },
    "atlantic_south": {
        1: ((8, 18), (2, 5)), 2: ((8, 18), (2, 5)), 3: ((8, 16), (2, 4)),
        4: ((7, 14), (1, 3)), 5: ((6, 12), (1, 3)), 6: ((5, 11), (1, 2)),
        7: ((5, 10), (1, 2)), 8: ((5, 10), (1, 2)), 9: ((6, 14), (1, 3)),
        10: ((7, 15), (2, 4)), 11: ((7, 16), (2, 4)), 12: ((8, 18), (2, 5)),
    },
    "gulf": {
        1: ((8, 18), (1, 4)), 2: ((8, 18), (1, 4)), 3: ((8, 16), (1, 3)),
        4: ((7, 14), (1, 3)), 5: ((6, 12), (1, 2)), 6: ((5, 10), (1, 2)),
        7: ((5, 10), (1, 2)), 8: ((5, 10), (1, 2)), 9: ((6, 14), (1, 3)),
        10: ((7, 15), (1, 3)), 11: ((7, 16), (1, 3)), 12: ((8, 18), (1, 4)),
    },
    "pacific": {
        1: ((10, 25), (4, 9)), 2: ((10, 24), (4, 8)), 3: ((10, 22), (3, 7)),
        4: ((8, 18), (3, 6)), 5: ((8, 16), (2, 5)), 6: ((7, 15), (2, 4)),
        7: ((6, 14), (2, 4)), 8: ((6, 14), (2, 4)), 9: ((7, 16), (2, 5)),
        10: ((8, 18), (3, 6)), 11: ((10, 22), (3, 7)), 12: ((10, 25), (4, 9)),
    },
    "pacific_south": {
        1: ((6, 16), (3, 6)), 2: ((6, 16), (3, 6)), 3: ((6, 14), (2, 5)),
        4: ((5, 12), (2, 4)), 5: ((5, 10), (2, 4)), 6: ((4, 10), (1, 3)),
        7: ((4, 8), (1, 3)), 8: ((4, 8), (1, 3)), 9: ((5, 10), (2, 4)),
        10: ((5, 12), (2, 5)), 11: ((6, 14), (3, 6)), 12: ((6, 16), (3, 6)),
    },
    "hawaii_conditions": {
        1: ((10, 22), (3, 8)), 2: ((10, 22), (3, 8)), 3: ((10, 20), (2, 6)),
        4: ((8, 18), (2, 5)), 5: ((8, 16), (2, 4)), 6: ((8, 16), (1, 3)),
        7: ((8, 16), (1, 3)), 8: ((8, 16), (1, 3)), 9: ((8, 16), (2, 4)),
        10: ((8, 18), (2, 5)), 11: ((10, 20), (3, 6)), 12: ((10, 22), (3, 8)),
    },
}

_FALLBACK_WIND_DIR: Dict[str, Dict[int, str]] = {
    "atlantic_north": {
        1: "NW", 2: "NW", 3: "NW", 4: "SW", 5: "SW", 6: "SW",
        7: "SW", 8: "SW", 9: "NE", 10: "NE", 11: "NW", 12: "NW",
    },
    "atlantic_mid": {
        1: "NW", 2: "NW", 3: "SW", 4: "SW", 5: "SW", 6: "SW",
        7: "SW", 8: "SW", 9: "NE", 10: "NE", 11: "NW", 12: "NW",
    },
    "atlantic_south": {
        1: "NW", 2: "NW", 3: "SW", 4: "SE", 5: "SE", 6: "SE",
        7: "SE", 8: "SE", 9: "NE", 10: "NE", 11: "NW", 12: "NW",
    },
    "gulf": {
        1: "N", 2: "N", 3: "SE", 4: "SE", 5: "SE", 6: "S",
        7: "S", 8: "S", 9: "SE", 10: "N", 11: "N", 12: "N",
    },
    "pacific": {
        1: "NW", 2: "NW", 3: "NW", 4: "NW", 5: "NW", 6: "NW",
        7: "NW", 8: "NW", 9: "NW", 10: "NW", 11: "NW", 12: "NW",
    },
    "pacific_south": {
        1: "W", 2: "W", 3: "W", 4: "W", 5: "W", 6: "W",
        7: "W", 8: "W", 9: "W", 10: "W", 11: "W", 12: "W",
    },
    "hawaii_conditions": {
        1: "NE", 2: "NE", 3: "NE", 4: "NE", 5: "NE", 6: "NE",
        7: "NE", 8: "NE", 9: "NE", 10: "NE", 11: "NE", 12: "NE",
    },
}


# ---------------------------------------------------------------------------
# Coastal location database
# ---------------------------------------------------------------------------

COASTAL_LOCATIONS: List[Dict[str, Any]] = [
    # ── Northeast ──────────────────────────────────────────────────────────
    {
        "id": "montauk-ny",
        "name": "Montauk",
        "state": "NY",
        "lat": 41.0713,
        "lng": -71.9544,
        "timezone": "America/New_York",
        "coops_station": "8510560",
        "ndbc_stations": ["44025", "44017"],
        "nws_zone": "ANZ338",
        "temp_region": "northeast",
        "conditions_region": "atlantic_north",
    },
    {
        "id": "sandy-hook-nj",
        "name": "Sandy Hook",
        "state": "NJ",
        "lat": 40.4669,
        "lng": -74.0089,
        "timezone": "America/New_York",
        "coops_station": "8531680",
        "ndbc_stations": ["44025", "44065"],
        "nws_zone": "ANZ355",
        "temp_region": "northeast",
        "conditions_region": "atlantic_north",
    },
    {
        "id": "long-beach-island-nj",
        "name": "Long Beach Island",
        "state": "NJ",
        "lat": 39.6373,
        "lng": -74.1895,
        "timezone": "America/New_York",
        "coops_station": "8534720",
        "ndbc_stations": ["44025", "44009"],
        "nws_zone": "ANZ450",
        "temp_region": "northeast",
        "conditions_region": "atlantic_north",
        "temp_offset": 1,
    },
    {
        "id": "atlantic-city-nj",
        "name": "Atlantic City",
        "state": "NJ",
        "lat": 39.3643,
        "lng": -74.4229,
        "timezone": "America/New_York",
        "coops_station": "8534720",
        "ndbc_stations": ["44009", "44025"],
        "nws_zone": "ANZ451",
        "temp_region": "northeast",
        "conditions_region": "atlantic_north",
        "temp_offset": 2,
    },
    {
        "id": "cape-may-nj",
        "name": "Cape May",
        "state": "NJ",
        "lat": 38.9351,
        "lng": -74.9060,
        "timezone": "America/New_York",
        "coops_station": "8536110",
        "ndbc_stations": ["44009"],
        "nws_zone": "ANZ452",
        "temp_region": "midatlantic",
        "conditions_region": "atlantic_north",
    },

    # ── Mid-Atlantic ───────────────────────────────────────────────────────
    {
        "id": "ocean-city-md",
        "name": "Ocean City",
        "state": "MD",
        "lat": 38.3365,
        "lng": -75.0849,
        "timezone": "America/New_York",
        "coops_station": "8570283",
        "ndbc_stations": ["44009", "44014"],
        "nws_zone": "AMZ158",
        "temp_region": "midatlantic",
        "conditions_region": "atlantic_mid",
    },
    {
        "id": "virginia-beach-va",
        "name": "Virginia Beach",
        "state": "VA",
        "lat": 36.8529,
        "lng": -75.9780,
        "timezone": "America/New_York",
        "coops_station": "8638610",
        "ndbc_stations": ["44014", "44099"],
        "nws_zone": "AMZ152",
        "temp_region": "midatlantic",
        "conditions_region": "atlantic_mid",
        "temp_offset": 2,
    },

    # ── North Carolina ─────────────────────────────────────────────────────
    {
        "id": "outer-banks-nc",
        "name": "Outer Banks (Nags Head)",
        "state": "NC",
        "lat": 35.9582,
        "lng": -75.6249,
        "timezone": "America/New_York",
        "coops_station": "8651370",
        "ndbc_stations": ["41025"],
        "nws_zone": "AMZ150",
        "temp_region": "nc_outer_banks",
        "conditions_region": "atlantic_mid",
    },
    {
        "id": "cape-hatteras-nc",
        "name": "Cape Hatteras",
        "state": "NC",
        "lat": 35.2230,
        "lng": -75.6350,
        "timezone": "America/New_York",
        "coops_station": "8654467",
        "ndbc_stations": ["41025"],
        "nws_zone": "AMZ152",
        "temp_region": "nc_outer_banks",
        "conditions_region": "atlantic_mid",
        "temp_offset": 1,
    },
    {
        "id": "atlantic-beach-nc",
        "name": "Atlantic Beach / Morehead City",
        "state": "NC",
        "lat": 34.6984,
        "lng": -76.7403,
        "timezone": "America/New_York",
        "coops_station": "8656483",
        "ndbc_stations": ["41036", "41025"],
        "nws_zone": "AMZ154",
        "temp_region": "nc_south",
        "conditions_region": "atlantic_mid",
        "temp_offset": -1,
    },
    {
        "id": "wrightsville-beach-nc",
        "name": "Wrightsville Beach",
        "state": "NC",
        "lat": 34.2104,
        "lng": -77.7964,
        "timezone": "America/New_York",
        "coops_station": "8658163",
        "ndbc_stations": ["41110", "41037"],
        "nws_zone": "AMZ158",
        "temp_region": "nc_south",
        "conditions_region": "atlantic_mid",
    },
    {
        "id": "carolina-beach-nc",
        "name": "Carolina Beach / Kure Beach",
        "state": "NC",
        "lat": 34.0352,
        "lng": -77.8936,
        "timezone": "America/New_York",
        "coops_station": "8658163",
        "ndbc_stations": ["41110", "41037"],
        "nws_zone": "AMZ158",
        "temp_region": "nc_south",
        "conditions_region": "atlantic_mid",
    },

    # ── South Carolina ─────────────────────────────────────────────────────
    {
        "id": "myrtle-beach-sc",
        "name": "Myrtle Beach",
        "state": "SC",
        "lat": 33.6891,
        "lng": -78.8867,
        "timezone": "America/New_York",
        "coops_station": "8661070",
        "ndbc_stations": ["41004"],
        "nws_zone": "AMZ252",
        "temp_region": "sc_ga",
        "conditions_region": "atlantic_mid",
    },
    {
        "id": "charleston-sc",
        "name": "Charleston",
        "state": "SC",
        "lat": 32.7765,
        "lng": -79.9311,
        "timezone": "America/New_York",
        "coops_station": "8665530",
        "ndbc_stations": ["41004"],
        "nws_zone": "AMZ254",
        "temp_region": "sc_ga",
        "conditions_region": "atlantic_south",
    },
    {
        "id": "hilton-head-sc",
        "name": "Hilton Head",
        "state": "SC",
        "lat": 32.2163,
        "lng": -80.7526,
        "timezone": "America/New_York",
        "coops_station": "8669100",
        "ndbc_stations": ["41004", "41008"],
        "nws_zone": "AMZ256",
        "temp_region": "sc_ga",
        "conditions_region": "atlantic_south",
    },

    # ── Georgia ────────────────────────────────────────────────────────────
    {
        "id": "tybee-island-ga",
        "name": "Tybee Island (Savannah)",
        "state": "GA",
        "lat": 32.0004,
        "lng": -80.8454,
        "timezone": "America/New_York",
        "coops_station": "8670870",
        "ndbc_stations": ["41008"],
        "nws_zone": "AMZ330",
        "temp_region": "sc_ga",
        "conditions_region": "atlantic_south",
    },

    # ── Florida East Coast ─────────────────────────────────────────────────
    {
        "id": "jacksonville-beach-fl",
        "name": "Jacksonville Beach",
        "state": "FL",
        "lat": 30.2866,
        "lng": -81.3930,
        "timezone": "America/New_York",
        "coops_station": "8720218",
        "ndbc_stations": ["41112"],
        "nws_zone": "AMZ450",
        "temp_region": "fl_northeast",
        "conditions_region": "atlantic_south",
    },
    {
        "id": "st-augustine-fl",
        "name": "St. Augustine",
        "state": "FL",
        "lat": 29.9012,
        "lng": -81.3124,
        "timezone": "America/New_York",
        "coops_station": "8720587",
        "ndbc_stations": ["41112"],
        "nws_zone": "AMZ452",
        "temp_region": "fl_northeast",
        "conditions_region": "atlantic_south",
    },
    {
        "id": "daytona-beach-fl",
        "name": "Daytona Beach",
        "state": "FL",
        "lat": 29.2108,
        "lng": -81.0228,
        "timezone": "America/New_York",
        "coops_station": "8721120",
        "ndbc_stations": ["41009", "41112"],
        "nws_zone": "AMZ454",
        "temp_region": "fl_northeast",
        "conditions_region": "atlantic_south",
        "temp_offset": 2,
    },
    {
        "id": "cocoa-beach-fl",
        "name": "Cocoa Beach / Port Canaveral",
        "state": "FL",
        "lat": 28.3200,
        "lng": -80.6076,
        "timezone": "America/New_York",
        "coops_station": "8721604",
        "ndbc_stations": ["41009"],
        "nws_zone": "AMZ550",
        "temp_region": "fl_central_east",
        "conditions_region": "atlantic_south",
    },
    {
        "id": "fort-pierce-fl",
        "name": "Fort Pierce",
        "state": "FL",
        "lat": 27.4467,
        "lng": -80.3256,
        "timezone": "America/New_York",
        "coops_station": "8722670",
        "ndbc_stations": ["41114"],
        "nws_zone": "AMZ552",
        "temp_region": "fl_central_east",
        "conditions_region": "atlantic_south",
        "temp_offset": 2,
    },
    {
        "id": "palm-beach-fl",
        "name": "Palm Beach / Jupiter",
        "state": "FL",
        "lat": 26.7056,
        "lng": -80.0364,
        "timezone": "America/New_York",
        "coops_station": "8722588",
        "ndbc_stations": ["41114"],
        "nws_zone": "AMZ554",
        "temp_region": "fl_south",
        "conditions_region": "atlantic_south",
        "temp_offset": -2,
    },
    {
        "id": "fort-lauderdale-fl",
        "name": "Fort Lauderdale",
        "state": "FL",
        "lat": 26.1224,
        "lng": -80.1373,
        "timezone": "America/New_York",
        "coops_station": "8722956",
        "ndbc_stations": ["41114"],
        "nws_zone": "AMZ556",
        "temp_region": "fl_south",
        "conditions_region": "atlantic_south",
    },
    {
        "id": "miami-beach-fl",
        "name": "Miami Beach",
        "state": "FL",
        "lat": 25.7907,
        "lng": -80.1300,
        "timezone": "America/New_York",
        "coops_station": "8723214",
        "ndbc_stations": ["41047"],
        "nws_zone": "AMZ610",
        "temp_region": "fl_south",
        "conditions_region": "atlantic_south",
    },

    # ── Florida Keys ───────────────────────────────────────────────────────
    {
        "id": "key-west-fl",
        "name": "Key West",
        "state": "FL",
        "lat": 24.5551,
        "lng": -81.7800,
        "timezone": "America/New_York",
        "coops_station": "8724580",
        "ndbc_stations": ["SMKF1"],
        "nws_zone": "AMZ651",
        "temp_region": "fl_keys",
        "conditions_region": "atlantic_south",
    },

    # ── Florida Gulf Coast ─────────────────────────────────────────────────
    {
        "id": "naples-fl",
        "name": "Naples",
        "state": "FL",
        "lat": 26.1420,
        "lng": -81.7948,
        "timezone": "America/New_York",
        "coops_station": "8725110",
        "ndbc_stations": ["42013"],
        "nws_zone": "GMZ830",
        "temp_region": "fl_gulf_south",
        "conditions_region": "gulf",
    },
    {
        "id": "fort-myers-beach-fl",
        "name": "Fort Myers Beach",
        "state": "FL",
        "lat": 26.4520,
        "lng": -81.9495,
        "timezone": "America/New_York",
        "coops_station": "8725520",
        "ndbc_stations": ["42013"],
        "nws_zone": "GMZ830",
        "temp_region": "fl_gulf_south",
        "conditions_region": "gulf",
    },
    {
        "id": "sarasota-fl",
        "name": "Sarasota",
        "state": "FL",
        "lat": 27.3364,
        "lng": -82.5307,
        "timezone": "America/New_York",
        "coops_station": "8726384",
        "ndbc_stations": ["42036"],
        "nws_zone": "GMZ836",
        "temp_region": "fl_gulf_south",
        "conditions_region": "gulf",
    },
    {
        "id": "clearwater-fl",
        "name": "Clearwater / St. Petersburg",
        "state": "FL",
        "lat": 27.9659,
        "lng": -82.8001,
        "timezone": "America/New_York",
        "coops_station": "8726724",
        "ndbc_stations": ["42036"],
        "nws_zone": "GMZ850",
        "temp_region": "fl_gulf_south",
        "conditions_region": "gulf",
        "temp_offset": -1,
    },
    {
        "id": "panama-city-beach-fl",
        "name": "Panama City Beach",
        "state": "FL",
        "lat": 30.1766,
        "lng": -85.8055,
        "timezone": "America/Chicago",
        "coops_station": "8729108",
        "ndbc_stations": ["42039"],
        "nws_zone": "GMZ850",
        "temp_region": "fl_gulf_north",
        "conditions_region": "gulf",
    },
    {
        "id": "destin-fl",
        "name": "Destin / Fort Walton Beach",
        "state": "FL",
        "lat": 30.3935,
        "lng": -86.4958,
        "timezone": "America/Chicago",
        "coops_station": "8729210",
        "ndbc_stations": ["42039"],
        "nws_zone": "GMZ855",
        "temp_region": "fl_gulf_north",
        "conditions_region": "gulf",
    },
    {
        "id": "pensacola-fl",
        "name": "Pensacola",
        "state": "FL",
        "lat": 30.3500,
        "lng": -87.1600,
        "timezone": "America/Chicago",
        "coops_station": "8729840",
        "ndbc_stations": ["42040", "42039"],
        "nws_zone": "GMZ855",
        "temp_region": "fl_gulf_north",
        "conditions_region": "gulf",
    },

    # ── Gulf Coast (AL / MS / LA / TX) ─────────────────────────────────────
    {
        "id": "gulf-shores-al",
        "name": "Gulf Shores / Orange Beach",
        "state": "AL",
        "lat": 30.2460,
        "lng": -87.7008,
        "timezone": "America/Chicago",
        "coops_station": "8735180",
        "ndbc_stations": ["42012", "42040"],
        "nws_zone": "GMZ630",
        "temp_region": "gulf_central",
        "conditions_region": "gulf",
    },
    {
        "id": "biloxi-ms",
        "name": "Biloxi / Gulfport",
        "state": "MS",
        "lat": 30.3960,
        "lng": -88.8853,
        "timezone": "America/Chicago",
        "coops_station": "8747437",
        "ndbc_stations": ["42007", "42012"],
        "nws_zone": "GMZ634",
        "temp_region": "gulf_central",
        "conditions_region": "gulf",
    },
    {
        "id": "grand-isle-la",
        "name": "Grand Isle",
        "state": "LA",
        "lat": 29.2633,
        "lng": -89.9873,
        "timezone": "America/Chicago",
        "coops_station": "8761724",
        "ndbc_stations": ["42041", "42007"],
        "nws_zone": "GMZ536",
        "temp_region": "gulf_central",
        "conditions_region": "gulf",
    },
    {
        "id": "galveston-tx",
        "name": "Galveston",
        "state": "TX",
        "lat": 29.3013,
        "lng": -94.7977,
        "timezone": "America/Chicago",
        "coops_station": "8771450",
        "ndbc_stations": ["42035"],
        "nws_zone": "GMZ335",
        "temp_region": "gulf_west",
        "conditions_region": "gulf",
    },
    {
        "id": "port-aransas-tx",
        "name": "Port Aransas",
        "state": "TX",
        "lat": 27.8339,
        "lng": -97.0611,
        "timezone": "America/Chicago",
        "coops_station": "8775241",
        "ndbc_stations": ["42020", "42035"],
        "nws_zone": "GMZ245",
        "temp_region": "gulf_west",
        "conditions_region": "gulf",
    },
    {
        "id": "south-padre-island-tx",
        "name": "South Padre Island",
        "state": "TX",
        "lat": 26.1118,
        "lng": -97.1681,
        "timezone": "America/Chicago",
        "coops_station": "8779770",
        "ndbc_stations": ["42020"],
        "nws_zone": "GMZ255",
        "temp_region": "gulf_west",
        "conditions_region": "gulf",
    },

    # ── California (South) ────────────────────────────────────────────────
    {
        "id": "san-diego-ca",
        "name": "San Diego / Ocean Beach",
        "state": "CA",
        "lat": 32.7496,
        "lng": -117.2551,
        "timezone": "America/Los_Angeles",
        "coops_station": "9410170",
        "ndbc_stations": ["46258", "46225"],
        "nws_zone": "PZZ750",
        "temp_region": "pacific_san_diego",
        "conditions_region": "pacific_south",
    },
    {
        "id": "imperial-beach-ca",
        "name": "Imperial Beach",
        "state": "CA",
        "lat": 32.5790,
        "lng": -117.1342,
        "timezone": "America/Los_Angeles",
        "coops_station": "9410170",
        "ndbc_stations": ["46258", "46225"],
        "nws_zone": "PZZ750",
        "temp_region": "pacific_san_diego",
        "conditions_region": "pacific_south",
        "temp_offset": -1,
    },
    {
        "id": "oceanside-ca",
        "name": "Oceanside",
        "state": "CA",
        "lat": 33.1959,
        "lng": -117.3795,
        "timezone": "America/Los_Angeles",
        "coops_station": "9410230",
        "ndbc_stations": ["46258", "46225"],
        "nws_zone": "PZZ750",
        "temp_region": "pacific_san_diego",
        "conditions_region": "pacific_south",
    },
    {
        "id": "dana-point-ca",
        "name": "Dana Point",
        "state": "CA",
        "lat": 33.4594,
        "lng": -117.6981,
        "timezone": "America/Los_Angeles",
        "coops_station": "9410580",
        "ndbc_stations": ["46242", "46225"],
        "nws_zone": "PZZ750",
        "temp_region": "pacific_socal",
        "conditions_region": "pacific_south",
    },
    {
        "id": "huntington-beach-ca",
        "name": "Huntington Beach",
        "state": "CA",
        "lat": 33.6603,
        "lng": -118.0009,
        "timezone": "America/Los_Angeles",
        "coops_station": "9410580",
        "ndbc_stations": ["46242", "46222"],
        "nws_zone": "PZZ670",
        "temp_region": "pacific_socal",
        "conditions_region": "pacific_south",
    },
    {
        "id": "long-beach-ca",
        "name": "Long Beach / Belmont Pier",
        "state": "CA",
        "lat": 33.7566,
        "lng": -118.1170,
        "timezone": "America/Los_Angeles",
        "coops_station": "9410660",
        "ndbc_stations": ["46222"],
        "nws_zone": "PZZ670",
        "temp_region": "pacific_socal",
        "conditions_region": "pacific_south",
        "temp_offset": 1,
    },
    {
        "id": "santa-monica-ca",
        "name": "Santa Monica",
        "state": "CA",
        "lat": 34.0094,
        "lng": -118.4973,
        "timezone": "America/Los_Angeles",
        "coops_station": "9410840",
        "ndbc_stations": ["46221", "46222"],
        "nws_zone": "PZZ670",
        "temp_region": "pacific_socal",
        "conditions_region": "pacific_south",
    },
    {
        "id": "malibu-ca",
        "name": "Malibu",
        "state": "CA",
        "lat": 34.0367,
        "lng": -118.6884,
        "timezone": "America/Los_Angeles",
        "coops_station": "9410840",
        "ndbc_stations": ["46221"],
        "nws_zone": "PZZ670",
        "temp_region": "pacific_socal",
        "conditions_region": "pacific_south",
        "temp_offset": -1,
    },
    {
        "id": "ventura-ca",
        "name": "Ventura",
        "state": "CA",
        "lat": 34.2746,
        "lng": -119.2637,
        "timezone": "America/Los_Angeles",
        "coops_station": "9411270",
        "ndbc_stations": ["46053", "46221"],
        "nws_zone": "PZZ645",
        "temp_region": "pacific_socal",
        "conditions_region": "pacific_south",
        "temp_offset": -2,
    },
    {
        "id": "santa-barbara-ca",
        "name": "Santa Barbara",
        "state": "CA",
        "lat": 34.4048,
        "lng": -119.6931,
        "timezone": "America/Los_Angeles",
        "coops_station": "9411340",
        "ndbc_stations": ["46053", "46054"],
        "nws_zone": "PZZ645",
        "temp_region": "pacific_central_cal",
        "conditions_region": "pacific_south",
        "temp_offset": 2,
    },

    # ── California (Central) ──────────────────────────────────────────────
    {
        "id": "pismo-beach-ca",
        "name": "Pismo Beach",
        "state": "CA",
        "lat": 35.1428,
        "lng": -120.6413,
        "timezone": "America/Los_Angeles",
        "coops_station": "9412110",
        "ndbc_stations": ["46011", "46054"],
        "nws_zone": "PZZ640",
        "temp_region": "pacific_central_cal",
        "conditions_region": "pacific_south",
    },
    {
        "id": "morro-bay-ca",
        "name": "Morro Bay",
        "state": "CA",
        "lat": 35.3659,
        "lng": -120.8499,
        "timezone": "America/Los_Angeles",
        "coops_station": "9412110",
        "ndbc_stations": ["46011", "46028"],
        "nws_zone": "PZZ640",
        "temp_region": "pacific_central_cal",
        "conditions_region": "pacific_south",
    },
    {
        "id": "monterey-ca",
        "name": "Monterey",
        "state": "CA",
        "lat": 36.6002,
        "lng": -121.8947,
        "timezone": "America/Los_Angeles",
        "coops_station": "9413450",
        "ndbc_stations": ["46042", "46236"],
        "nws_zone": "PZZ535",
        "temp_region": "pacific_central_cal",
        "conditions_region": "pacific",
    },
    {
        "id": "santa-cruz-ca",
        "name": "Santa Cruz",
        "state": "CA",
        "lat": 36.9624,
        "lng": -122.0236,
        "timezone": "America/Los_Angeles",
        "coops_station": "9413745",
        "ndbc_stations": ["46042", "46236"],
        "nws_zone": "PZZ535",
        "temp_region": "pacific_central_cal",
        "conditions_region": "pacific",
        "temp_offset": 1,
    },
    {
        "id": "half-moon-bay-ca",
        "name": "Half Moon Bay",
        "state": "CA",
        "lat": 37.4636,
        "lng": -122.4286,
        "timezone": "America/Los_Angeles",
        "coops_station": "9414290",
        "ndbc_stations": ["46012", "46026"],
        "nws_zone": "PZZ530",
        "temp_region": "pacific_norcal",
        "conditions_region": "pacific",
    },

    # ── California (North) / San Francisco Bay ────────────────────────────
    {
        "id": "san-francisco-ca",
        "name": "San Francisco / Pacifica",
        "state": "CA",
        "lat": 37.7604,
        "lng": -122.5107,
        "timezone": "America/Los_Angeles",
        "coops_station": "9414290",
        "ndbc_stations": ["46026", "46012"],
        "nws_zone": "PZZ530",
        "temp_region": "pacific_norcal",
        "conditions_region": "pacific",
    },
    {
        "id": "bodega-bay-ca",
        "name": "Bodega Bay",
        "state": "CA",
        "lat": 38.3332,
        "lng": -123.0481,
        "timezone": "America/Los_Angeles",
        "coops_station": "9415020",
        "ndbc_stations": ["46013", "46014"],
        "nws_zone": "PZZ455",
        "temp_region": "pacific_norcal",
        "conditions_region": "pacific",
    },
    {
        "id": "eureka-ca",
        "name": "Eureka / Trinidad",
        "state": "CA",
        "lat": 40.7668,
        "lng": -124.2026,
        "timezone": "America/Los_Angeles",
        "coops_station": "9418767",
        "ndbc_stations": ["46022", "46244"],
        "nws_zone": "PZZ450",
        "temp_region": "pacific_norcal",
        "conditions_region": "pacific",
        "temp_offset": -1,
    },

    # ── Oregon ────────────────────────────────────────────────────────────
    {
        "id": "brookings-or",
        "name": "Brookings",
        "state": "OR",
        "lat": 42.0526,
        "lng": -124.2840,
        "timezone": "America/Los_Angeles",
        "coops_station": "9430104",
        "ndbc_stations": ["46027", "46015"],
        "nws_zone": "PZZ350",
        "temp_region": "pacific_nw",
        "conditions_region": "pacific",
        "temp_offset": 2,
    },
    {
        "id": "charleston-or",
        "name": "Charleston / Coos Bay",
        "state": "OR",
        "lat": 43.3457,
        "lng": -124.3326,
        "timezone": "America/Los_Angeles",
        "coops_station": "9432780",
        "ndbc_stations": ["46015", "46229"],
        "nws_zone": "PZZ350",
        "temp_region": "pacific_nw",
        "conditions_region": "pacific",
        "temp_offset": 1,
    },
    {
        "id": "florence-or",
        "name": "Florence",
        "state": "OR",
        "lat": 43.9826,
        "lng": -124.1004,
        "timezone": "America/Los_Angeles",
        "coops_station": "9432780",
        "ndbc_stations": ["46229"],
        "nws_zone": "PZZ350",
        "temp_region": "pacific_nw",
        "conditions_region": "pacific",
    },
    {
        "id": "newport-or",
        "name": "Newport",
        "state": "OR",
        "lat": 44.6368,
        "lng": -124.0535,
        "timezone": "America/Los_Angeles",
        "coops_station": "9435380",
        "ndbc_stations": ["46050", "46229"],
        "nws_zone": "PZZ255",
        "temp_region": "pacific_nw",
        "conditions_region": "pacific",
    },
    {
        "id": "lincoln-city-or",
        "name": "Lincoln City",
        "state": "OR",
        "lat": 44.9582,
        "lng": -124.0176,
        "timezone": "America/Los_Angeles",
        "coops_station": "9435380",
        "ndbc_stations": ["46050"],
        "nws_zone": "PZZ255",
        "temp_region": "pacific_nw",
        "conditions_region": "pacific",
    },
    {
        "id": "astoria-or",
        "name": "Astoria / Fort Stevens",
        "state": "OR",
        "lat": 46.1879,
        "lng": -123.8313,
        "timezone": "America/Los_Angeles",
        "coops_station": "9439040",
        "ndbc_stations": ["46029"],
        "nws_zone": "PZZ250",
        "temp_region": "pacific_nw",
        "conditions_region": "pacific",
        "temp_offset": -1,
    },

    # ── Washington ────────────────────────────────────────────────────────
    {
        "id": "long-beach-wa",
        "name": "Long Beach",
        "state": "WA",
        "lat": 46.3523,
        "lng": -124.0543,
        "timezone": "America/Los_Angeles",
        "coops_station": "9440910",
        "ndbc_stations": ["46029"],
        "nws_zone": "PZZ210",
        "temp_region": "pacific_nw",
        "conditions_region": "pacific",
    },
    {
        "id": "westport-wa",
        "name": "Westport",
        "state": "WA",
        "lat": 46.8879,
        "lng": -124.1049,
        "timezone": "America/Los_Angeles",
        "coops_station": "9441102",
        "ndbc_stations": ["46029", "46041"],
        "nws_zone": "PZZ210",
        "temp_region": "pacific_nw",
        "conditions_region": "pacific",
    },
    {
        "id": "la-push-wa",
        "name": "La Push / Olympic Coast",
        "state": "WA",
        "lat": 47.9084,
        "lng": -124.6355,
        "timezone": "America/Los_Angeles",
        "coops_station": "9442396",
        "ndbc_stations": ["46041", "46087"],
        "nws_zone": "PZZ150",
        "temp_region": "pacific_nw",
        "conditions_region": "pacific",
        "temp_offset": -1,
    },
    {
        "id": "seattle-wa",
        "name": "Seattle / Puget Sound",
        "state": "WA",
        "lat": 47.6023,
        "lng": -122.3391,
        "timezone": "America/Los_Angeles",
        "coops_station": "9447130",
        "ndbc_stations": ["46120"],
        "nws_zone": "PZZ135",
        "temp_region": "pacific_nw",
        "conditions_region": "pacific",
        "temp_offset": 2,
    },

    # ── Hawaii ────────────────────────────────────────────────────────────
    {
        "id": "honolulu-hi",
        "name": "Honolulu / Waikiki",
        "state": "HI",
        "lat": 21.3069,
        "lng": -157.8583,
        "timezone": "Pacific/Honolulu",
        "coops_station": "1612340",
        "ndbc_stations": ["51201", "51202"],
        "nws_zone": "PHZ110",
        "temp_region": "hawaii",
        "conditions_region": "hawaii_conditions",
    },
    {
        "id": "north-shore-hi",
        "name": "North Shore (Oahu)",
        "state": "HI",
        "lat": 21.5790,
        "lng": -158.1041,
        "timezone": "Pacific/Honolulu",
        "coops_station": "1612340",
        "ndbc_stations": ["51201", "51202"],
        "nws_zone": "PHZ110",
        "temp_region": "hawaii",
        "conditions_region": "hawaii_conditions",
        "temp_offset": -1,
    },
    {
        "id": "kailua-kona-hi",
        "name": "Kailua-Kona (Big Island)",
        "state": "HI",
        "lat": 19.6400,
        "lng": -155.9969,
        "timezone": "Pacific/Honolulu",
        "coops_station": "1617433",
        "ndbc_stations": ["51003"],
        "nws_zone": "PHZ120",
        "temp_region": "hawaii",
        "conditions_region": "hawaii_conditions",
        "temp_offset": 1,
    },
    {
        "id": "kahului-hi",
        "name": "Kahului (Maui)",
        "state": "HI",
        "lat": 20.8947,
        "lng": -156.4700,
        "timezone": "Pacific/Honolulu",
        "coops_station": "1615680",
        "ndbc_stations": ["51202"],
        "nws_zone": "PHZ114",
        "temp_region": "hawaii",
        "conditions_region": "hawaii_conditions",
    },
]


# ---------------------------------------------------------------------------
# Build a fast lookup by id
# ---------------------------------------------------------------------------

_LOCATION_MAP: Dict[str, Dict[str, Any]] = {loc["id"]: loc for loc in COASTAL_LOCATIONS}


def get_location(location_id: str) -> Optional[Dict[str, Any]]:
    """Look up a location by its ID string."""
    return _LOCATION_MAP.get(location_id)


def get_monthly_water_temps(location: Dict[str, Any]) -> Dict[int, float]:
    """Return the monthly average water temp dict for a location.

    Applies the optional ``temp_offset`` to the regional base temps.
    """
    region = location.get("temp_region", "nc_south")
    base = _WATER_TEMPS.get(region, _WATER_TEMPS["nc_south"])
    offset = location.get("temp_offset", 0)
    if offset:
        return {m: t + offset for m, t in base.items()}
    return dict(base)


def get_fallback_conditions(
    location: Dict[str, Any], month: int,
) -> Tuple[Tuple[float, float], Tuple[float, float], str]:
    """Return (wind_range, wave_range, wind_dir) fallback for the given month."""
    region = location.get("conditions_region", "atlantic_mid")
    cond = _FALLBACK_CONDITIONS.get(region, _FALLBACK_CONDITIONS["atlantic_mid"])
    dirs = _FALLBACK_WIND_DIR.get(region, _FALLBACK_WIND_DIR["atlantic_mid"])
    wind, waves = cond[month]
    return wind, waves, dirs[month]


# ---------------------------------------------------------------------------
# Geocoding + nearest location search
# ---------------------------------------------------------------------------

def _haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in miles between two lat/lng points."""
    R = 3958.8  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def geocode_zip(zipcode: str) -> Optional[Tuple[float, float]]:
    """Convert a US zip code to (latitude, longitude).

    Uses the free zippopotam.us API (no key required).
    Returns None if the zip code is invalid or the service is down.
    """
    zipcode = zipcode.strip()
    if not zipcode.isdigit() or len(zipcode) != 5:
        return None
    try:
        resp = requests.get(
            f"https://api.zippopotam.us/us/{zipcode}",
            timeout=8,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        places = data.get("places", [])
        if not places:
            return None
        lat = float(places[0]["latitude"])
        lng = float(places[0]["longitude"])
        return lat, lng
    except Exception:
        return None


def find_nearest_locations(
    lat: float,
    lng: float,
    n: int = 5,
    max_miles: float = 300.0,
) -> List[Dict[str, Any]]:
    """Find the nearest N coastal locations within max_miles.

    Returns a list of location dicts with an added ``distance_miles`` field,
    sorted by distance ascending.
    """
    results = []
    for loc in COASTAL_LOCATIONS:
        d = _haversine_miles(lat, lng, loc["lat"], loc["lng"])
        if d <= max_miles:
            entry = dict(loc)
            entry["distance_miles"] = round(d, 1)
            results.append(entry)

    results.sort(key=lambda x: x["distance_miles"])
    return results[:n]


def all_locations_sorted() -> List[Dict[str, Any]]:
    """Return all locations sorted by state then name (for browse view)."""
    return sorted(COASTAL_LOCATIONS, key=lambda l: (l["state"], l["name"]))
