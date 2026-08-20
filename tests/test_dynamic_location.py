"""Tests for dynamic (any-coordinate) coastal location resolution.

Network access to the NOAA/NDBC station catalogs is mocked so these tests run
offline and deterministically; they exercise the pure assembly/gating logic
plus the graceful fallback to curated stations when a catalog is unavailable.
"""

from __future__ import annotations

import pytest

import locations as L
import services.stations as stations
from domain.forecast import _wind_orientation, score_conditions
from domain.species import onshore_offshore_dirs


# Wrightsville Beach, NC — the project's default reference point.
_WB_LAT, _WB_LNG = 34.2104, -77.7964


@pytest.fixture
def fake_catalogs(monkeypatch):
    """Inject small in-memory CO-OPS and NDBC catalogs near Wrightsville Beach."""
    coops = [
        {
            "id": "8658163",
            "name": "Wrightsville Beach",
            "lat": 34.21,
            "lng": -77.79,
            "state": "NC",
        },
        {
            "id": "8656483",
            "name": "Beaufort",
            "lat": 34.72,
            "lng": -76.67,
            "state": "NC",
        },
        {
            "id": "9410230",
            "name": "La Jolla",
            "lat": 32.87,
            "lng": -117.26,
            "state": "CA",
        },
        {
            "id": "8771450",
            "name": "Galveston",
            "lat": 29.31,
            "lng": -94.79,
            "state": "TX",
        },
    ]
    ndbc = [
        {"id": "41110", "lat": 34.14, "lng": -77.71, "has_met": True},
        {"id": "41037", "lat": 33.99, "lng": -77.36, "has_met": True},
        {"id": "DRYA1", "lat": 30.0, "lng": -88.0, "has_met": False},
    ]
    # Water-temp-capable stations (a subset, sited differently from tide gauges).
    temp = [
        {
            "id": "8658163",
            "name": "Wrightsville Beach",
            "lat": 34.21,
            "lng": -77.79,
            "state": "NC",
        },
        {
            "id": "8775870",
            "name": "Bob Hall Pier",
            "lat": 27.58,
            "lng": -97.22,
            "state": "TX",
        },
    ]
    monkeypatch.setattr(stations, "_load_coops", lambda: coops)
    monkeypatch.setattr(stations, "_load_coops_temp", lambda: temp)
    monkeypatch.setattr(stations, "_load_ndbc", lambda: ndbc)
    return coops, ndbc


class TestIdEncoding:
    def test_round_trips(self):
        loc_id = L.format_dynamic_id(_WB_LAT, _WB_LNG)
        assert loc_id.startswith("pt_")
        lat, lng = L.parse_dynamic_id(loc_id)
        # Encoded at 3-decimal (~110 m) precision.
        assert lat == pytest.approx(_WB_LAT, abs=1e-3)
        assert lng == pytest.approx(_WB_LNG, abs=1e-3)

    def test_jitter_maps_to_same_id(self):
        # Two pinpoints ~25 m apart must collapse to one id (one cache entry).
        assert L.format_dynamic_id(34.2101, -77.7962) == L.format_dynamic_id(
            34.2103, -77.7961
        )

    def test_negative_longitude_preserved(self):
        # Longitude minus sign must survive the underscore split.
        lat, lng = L.parse_dynamic_id("pt_47.606_-122.332")
        assert lat == pytest.approx(47.606)
        assert lng == pytest.approx(-122.332)

    def test_rejects_curated_and_garbage_ids(self):
        assert L.parse_dynamic_id("wrightsville_beach") is None
        assert L.parse_dynamic_id("pt_garbage") is None
        assert L.parse_dynamic_id("pt_999_999") is None  # out of range


class TestNearestStations:
    def test_nearest_coops_picks_closest(self, fake_catalogs):
        result = stations.nearest_coops_station(_WB_LAT, _WB_LNG)
        assert result is not None
        assert result["id"] == "8658163"
        assert result["state"] == "NC"

    def test_nearest_ndbc_excludes_non_met(self, fake_catalogs):
        result = stations.nearest_ndbc_stations(_WB_LAT, _WB_LNG, n=2)
        ids = [s["id"] for s in result]
        assert ids == ["41110", "41037"]
        assert "DRYA1" not in ids

    def test_nearest_watertemp_station(self, fake_catalogs):
        result = stations.nearest_watertemp_station(_WB_LAT, _WB_LNG)
        assert result is not None and result["id"] == "8658163"

    def test_empty_catalog_returns_none(self, monkeypatch):
        monkeypatch.setattr(stations, "_load_coops", lambda: [])
        monkeypatch.setattr(stations, "_load_coops_temp", lambda: [])
        monkeypatch.setattr(stations, "_load_ndbc", lambda: [])
        assert stations.nearest_coops_station(_WB_LAT, _WB_LNG) is None
        assert stations.nearest_watertemp_station(_WB_LAT, _WB_LNG) is None
        assert stations.nearest_ndbc_stations(_WB_LAT, _WB_LNG) == []


class TestBuildDynamicLocation:
    def test_has_all_forecast_fields(self, fake_catalogs):
        loc = L.build_dynamic_location(_WB_LAT, _WB_LNG)
        for key in (
            "id",
            "name",
            "state",
            "lat",
            "lng",
            "timezone",
            "coops_station",
            "ndbc_stations",
            "nws_zone",
            "conditions_region",
            "temp_region",
            "fish_region",
        ):
            assert key in loc, f"missing {key}"
        assert loc["coops_station"] == "8658163"
        assert loc["water_temp_station"] == "8658163"
        assert loc["ndbc_stations"] == ["41110", "41037"]
        assert loc["dynamic"] is True

    def test_resolves_through_get_location(self, fake_catalogs):
        loc_id = L.format_dynamic_id(_WB_LAT, _WB_LNG)
        loc = L.get_location(loc_id)
        assert loc is not None
        assert loc["id"] == loc_id
        assert loc["coops_station"] == "8658163"

    def test_region_inherited_from_nearest_curated(self, fake_catalogs):
        # Near Wrightsville Beach the inherited coast must be the east coast.
        loc = L.build_dynamic_location(_WB_LAT, _WB_LNG)
        assert loc["conditions_region"].startswith(("atlantic", "gulf"))

    def test_falls_back_to_curated_stations_without_catalog(self, monkeypatch):
        # No catalog available → dynamic location still works, inheriting the
        # nearest curated location's stations (today's behaviour, never worse).
        monkeypatch.setattr(stations, "_load_coops", lambda: [])
        monkeypatch.setattr(stations, "_load_coops_temp", lambda: [])
        monkeypatch.setattr(stations, "_load_ndbc", lambda: [])
        loc = L.build_dynamic_location(_WB_LAT, _WB_LNG)
        assert loc["coops_station"]  # non-empty, from a curated neighbour
        assert loc["water_temp_station"]  # falls back to the tide/curated station
        assert loc["ndbc_stations"]


class TestTimezoneForPoint:
    def test_single_timezone_states(self):
        assert L.timezone_for_point("NC", -77.8) == "America/New_York"
        assert L.timezone_for_point("TX", -94.8) == "America/Chicago"
        assert L.timezone_for_point("CA", -117.3) == "America/Los_Angeles"
        assert L.timezone_for_point("AK", -149.9) == "America/Anchorage"
        assert L.timezone_for_point("HI", -157.8) == "Pacific/Honolulu"

    def test_florida_split_by_longitude(self):
        # Panhandle (Pensacola ~ -87.2) is Central; peninsula (Miami ~ -80.2) Eastern.
        assert L.timezone_for_point("FL", -87.2) == "America/Chicago"
        assert L.timezone_for_point("FL", -80.2) == "America/New_York"

    def test_unknown_state_returns_none(self):
        assert L.timezone_for_point("", -90.0) is None
        assert L.timezone_for_point("ZZ", -90.0) is None

    def test_dynamic_gulf_location_gets_central_time(self, fake_catalogs):
        # A TX Gulf point resolves to the Galveston station → state TX → Central,
        # even though regional fields are inherited from a different neighbour.
        loc = L.build_dynamic_location(29.3, -94.8)
        assert loc["state"] == "TX"
        assert loc["timezone"] == "America/Chicago"


class TestCoastalGate:
    def test_coastal_point_allowed(self, fake_catalogs):
        assert L.dynamic_location_for_point(_WB_LAT, _WB_LNG) is not None

    def test_inland_point_rejected(self, fake_catalogs):
        # Wichita, KS — hundreds of miles from any station or curated spot.
        assert L.dynamic_location_for_point(37.6872, -97.3301) is None


def _stub_builder():
    """A ForecastBuilder whose network services return fixed/empty data.

    Lets generate_forecast run fully offline while exercising the real domain
    logic (species, scoring, orientation, tips) for a dynamic location.
    """

    class _Marine:
        def get_marine_forecast(self, *_a, **_k):
            if _k.get("sources_used") is not None:
                _k["sources_used"].append("stub_marine")
            return (4.0, 8.0), (1.0, 2.0), "N"

    class _Tides:
        def get_tide_predictions(self, *_a, **_k):
            return {}

    class _Buoy:
        def get_barometric_pressure(self, *_a, **_k):
            return None

    class _Weather:
        def get_weather_alerts(self, *_a, **_k):
            return []

        def get_state_alerts(self, *_a, **_k):
            return []

        def get_current_weather(self, *_a, **_k):
            return None

    class _Env:
        def get_coops_environmental(self, *_a, **_k):
            return {}

        def get_currents(self, *_a, **_k):
            return []

        def get_current_observation(self, *_a, **_k):
            return None

    class _Astro:
        def get_sun_times(self, now, *_a, **_k):
            return now, now, "6:00 AM / 6:00 PM"

        def get_solunar_times(self, *_a, **_k):
            return {}

        def get_twilight_times(self, *_a, **_k):
            return {}

        def get_lunar_details(self, *_a, **_k):
            return {}

    class _Builder:
        def __init__(self):
            self.marine_service = _Marine()
            self.tide_service = _Tides()
            self.buoy_service = _Buoy()
            self.weather_service = _Weather()
            self.environment_service = _Env()
            self.astro_service = _Astro()

    return _Builder


class TestDynamicLocationEndToEnd:
    def test_gulf_dynamic_location_generates_full_forecast(self, monkeypatch):
        from domain import forecast as fc

        monkeypatch.setattr(fc, "ForecastBuilder", _stub_builder())
        monkeypatch.setattr(fc, "get_water_temp", lambda *_a, **_k: (74.0, True))
        monkeypatch.setattr(fc, "get_species_image", lambda *_a, **_k: None)

        # A Gulf coastal point near Galveston, TX. Build it directly so the test
        # doesn't depend on station catalogs (blocked in CI).
        loc = {
            "id": "pt_29.3000_-94.8000",
            "name": "Coastal spot (29.30, -94.80)",
            "state": "TX",
            "lat": 29.3,
            "lng": -94.8,
            "timezone": "America/Chicago",
            "coops_station": "8771450",
            "ndbc_stations": ["42035"],
            "nws_zone": "",
            "conditions_region": "gulf",
            "temp_region": "gulf_west",
            "fish_region": "gulf",
            "dynamic": True,
        }
        out = fc.generate_forecast(loc)
        assert out["location_id"] == "pt_29.3000_-94.8000"
        assert out["forecast_version"] == fc.FORECAST_VERSION
        # A full forecast was assembled (verdict computed, species present).
        assert out["conditions"]["verdict"] in {
            "Excellent",
            "Good",
            "Fair",
            "Challenging",
            "Poor",
            "Unknown",
        }
        assert isinstance(out["species"], list) and out["species"]


class TestCatalogCaching:
    def test_failed_fetch_cached_briefly(self, monkeypatch):
        # A failing fetch must be cached (empty) so an outage doesn't re-hammer
        # the endpoint on every dynamic-location lookup.
        stations._CACHES.clear()
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            return []

        monkeypatch.setattr(stations, "_fetch_ndbc_stations", boom)
        assert stations._load_ndbc() == []
        assert stations._load_ndbc() == []  # served from the negative cache
        assert calls["n"] == 1
        stations._CACHES.clear()

    def test_successful_fetch_cached(self, monkeypatch):
        stations._CACHES.clear()
        calls = {"n": 0}

        def ok():
            calls["n"] += 1
            return [{"id": "X", "lat": 0.0, "lng": 0.0, "has_met": True}]

        monkeypatch.setattr(stations, "_fetch_ndbc_stations", ok)
        stations._load_ndbc()
        stations._load_ndbc()
        assert calls["n"] == 1
        stations._CACHES.clear()


class TestWindOrientation:
    def test_region_to_orientation(self):
        assert _wind_orientation({"conditions_region": "atlantic_mid"}) == "east"
        assert _wind_orientation({"conditions_region": "pacific_south"}) == "west"
        assert _wind_orientation({"conditions_region": "gulf"}) == "gulf"
        assert _wind_orientation({"conditions_region": "hawaii_conditions"}) == "hawaii"
        assert _wind_orientation(None) == "east"

    def test_gulf_faces_south_not_east(self):
        on_gulf, off_gulf = onshore_offshore_dirs("gulf")
        # South-facing: northerly wind is offshore, southerly is onshore.
        assert "N" in off_gulf and "S" in on_gulf
        # East/west winds are alongshore on the Gulf — neither bonus nor penalty.
        assert "E" not in on_gulf and "E" not in off_gulf

    def test_hawaii_is_neutral(self):
        on_hi, off_hi = onshore_offshore_dirs("hawaii")
        assert on_hi == set() and off_hi == set()

    def test_unknown_orientation_defaults_east(self):
        assert onshore_offshore_dirs("nonsense") == onshore_offshore_dirs("east")


class TestGulfScoringDiffersFromEast:
    def _score(self, coast, wind_dir):
        return score_conditions((5.0, 8.0), (1.0, 2.0), wind_dir=wind_dir, coast=coast)[
            "score"
        ]

    def test_east_wind_penalises_atlantic_but_not_gulf(self):
        # An easterly is onshore (murkier water) on the Atlantic but merely
        # alongshore on the south-facing Gulf, so the Gulf shouldn't be docked.
        assert self._score("east", "E") < self._score("gulf", "E")

    def test_northerly_is_offshore_on_both(self):
        # A clean northerly is offshore for both an east- and south-facing coast.
        assert self._score("gulf", "N") == self._score("east", "N")
