"""Tests for storage.cache module."""

import json
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from zoneinfo import ZoneInfo

from storage.cache import (
    CACHE_MAX_AGE_HOURS,
    _cache_path,
    _forecast_age_minutes,
    _human_age,
    load_cached_forecast,
    save_forecast,
)


@pytest.fixture(autouse=True)
def tmp_cache_dir(tmp_path, monkeypatch):
    """Redirect cache directory to a temp folder for each test."""
    monkeypatch.setattr("storage.cache.CACHE_DIR", str(tmp_path))
    monkeypatch.setattr("storage.cache.CACHE_FILE", str(tmp_path / "forecast.json"))
    return tmp_path


class TestCachePath:
    def test_default_path(self, tmp_cache_dir):
        assert _cache_path("") == str(tmp_cache_dir / "forecast.json")

    def test_location_specific_path(self, tmp_cache_dir):
        assert _cache_path("wrightsville-beach-nc").endswith(
            "forecast_wrightsville-beach-nc.json"
        )


class TestSaveAndLoad:
    def test_roundtrip(self, tmp_cache_dir):
        data = {"location": "test", "temp": 72}
        save_forecast(data, "loc1")
        loaded = load_cached_forecast("loc1")
        assert loaded == data

    def test_load_missing_returns_none(self):
        assert load_cached_forecast("nonexistent") is None

    def test_load_corrupt_json_returns_none(self, tmp_cache_dir):
        path = tmp_cache_dir / "forecast_bad.json"
        path.write_text("not json at all {{{")
        with patch("storage.cache._cache_path", return_value=str(path)):
            assert load_cached_forecast("bad") is None


class TestForecastAge:
    def test_valid_age(self):
        now = datetime.now(ZoneInfo("America/New_York"))
        thirty_min_ago = now - timedelta(minutes=30)
        forecast = {"generated_at": thirty_min_ago.isoformat()}
        age = _forecast_age_minutes(forecast)
        assert age is not None
        assert 29 <= age <= 31

    def test_missing_field_returns_none(self):
        assert _forecast_age_minutes({}) is None

    def test_bad_format_returns_none(self):
        assert _forecast_age_minutes({"generated_at": "not-a-date"}) is None


class TestHumanAge:
    def test_none_returns_empty(self):
        assert _human_age(None) == ""

    def test_just_now(self):
        assert _human_age(0.5) == "just now"

    def test_minutes(self):
        assert _human_age(15) == "15 min ago"

    def test_one_hour(self):
        assert _human_age(60) == "1 hr ago"

    def test_multiple_hours(self):
        assert _human_age(180) == "3 hrs ago"

    def test_one_day(self):
        assert _human_age(1440) == "1 day ago"

    def test_multiple_days(self):
        assert _human_age(4320) == "3 days ago"
