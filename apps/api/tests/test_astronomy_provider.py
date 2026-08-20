"""Tests for app.providers.astronomy.

Pure math, no network — all assertions are either exact (at the moon's
reference new-moon instant, where the formulas resolve to known values)
or structural (ordering, same-calendar-date, no-exception-at-extremes),
rather than pinned against an external "ground truth" ephemeris, since
this module's NOAA/synodic-month formulas are explicitly approximations.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.providers.astronomy import (
    MoonPhaseName,
    SolunarRating,
    compute_lunar_details,
    compute_solunar_times,
    compute_sun_times,
    compute_twilight_times,
)

# East coast: Wrightsville Beach, NC.
_ATLANTIC = (34.2104, -77.7964, "America/New_York")
# West coast: Half Moon Bay, CA.
_PACIFIC = (37.4636, -122.4286, "America/Los_Angeles")
# No DST anywhere in the year.
_NO_DST = (33.4484, -112.0740, "America/Phoenix")  # Phoenix, AZ

_REFERENCE_NEW_MOON_UTC = datetime(2000, 1, 6, 18, 14, tzinfo=ZoneInfo("UTC"))


def test_compute_sun_times_sunrise_before_sunset() -> None:
    lat, lng, tz_name = _ATLANTIC
    dt = datetime(2024, 7, 15, tzinfo=ZoneInfo(tz_name))

    result = compute_sun_times(dt, lat, lng, tz_name)

    assert result.sunrise < result.sunset
    assert result.sunrise.tzinfo is not None
    assert result.sunset.tzinfo is not None


def test_compute_sun_times_summer_day_longer_than_winter_day() -> None:
    lat, lng, tz_name = _ATLANTIC
    summer = compute_sun_times(
        datetime(2024, 6, 20, tzinfo=ZoneInfo(tz_name)), lat, lng, tz_name
    )
    winter = compute_sun_times(
        datetime(2024, 12, 20, tzinfo=ZoneInfo(tz_name)), lat, lng, tz_name
    )

    summer_daylight = summer.sunset - summer.sunrise
    winter_daylight = winter.sunset - winter.sunrise
    assert summer_daylight > winter_daylight


def test_compute_sun_times_naive_datetime_is_localized() -> None:
    lat, lng, tz_name = _ATLANTIC
    naive = datetime(2024, 7, 15)  # noqa: DTZ001 - deliberately naive to test localization

    result = compute_sun_times(naive, lat, lng, tz_name)

    assert result.sunrise.tzinfo is not None


def test_compute_sun_times_pacific_coast_different_offset_than_atlantic() -> None:
    atlantic_lat, atlantic_lng, atlantic_tz = _ATLANTIC
    pacific_lat, pacific_lng, pacific_tz = _PACIFIC

    atlantic = compute_sun_times(
        datetime(2024, 7, 15, tzinfo=ZoneInfo(atlantic_tz)),
        atlantic_lat,
        atlantic_lng,
        atlantic_tz,
    )
    pacific = compute_sun_times(
        datetime(2024, 7, 15, tzinfo=ZoneInfo(pacific_tz)),
        pacific_lat,
        pacific_lng,
        pacific_tz,
    )

    atlantic_offset = atlantic.sunrise.utcoffset()
    pacific_offset = pacific.sunrise.utcoffset()
    assert atlantic_offset is not None
    assert pacific_offset is not None
    assert atlantic_offset != pacific_offset


def test_compute_sun_times_no_dst_timezone_does_not_raise() -> None:
    lat, lng, tz_name = _NO_DST
    result = compute_sun_times(
        datetime(2024, 7, 15, tzinfo=ZoneInfo(tz_name)), lat, lng, tz_name
    )
    assert result.sunrise < result.sunset


def test_compute_sun_times_polar_latitude_clamps_without_raising() -> None:
    # Above the Arctic Circle near the summer solstice: cos_ha is clamped
    # to [-1, 1] rather than raising a math domain error from acos().
    tz_name = "UTC"
    result = compute_sun_times(
        datetime(2024, 6, 20, tzinfo=ZoneInfo(tz_name)), 75.0, 20.0, tz_name
    )
    assert result.sunrise.tzinfo is not None
    assert result.sunset.tzinfo is not None


def test_compute_twilight_times_ordering() -> None:
    lat, lng, tz_name = _ATLANTIC
    dt = datetime(2024, 7, 15, tzinfo=ZoneInfo(tz_name))

    result = compute_twilight_times(dt, lat, lng, tz_name)

    assert (
        result.astronomical_dawn
        < result.nautical_dawn
        < result.civil_dawn
        < result.sunrise
        < result.sunset
        < result.civil_dusk
        < result.nautical_dusk
        < result.astronomical_dusk
    )


def test_compute_lunar_details_distance_within_physical_bounds() -> None:
    _lat, lng, tz_name = _ATLANTIC
    dt = datetime(2024, 7, 15, tzinfo=ZoneInfo(tz_name))

    result = compute_lunar_details(dt, lng, tz_name)

    # Mean distance 384400 km +/- the 20905 km anomaly amplitude.
    assert 363000 <= result.distance_km <= 406000
    assert 0.0 <= result.age_days < 29.6
    assert result.moonrise.date() == dt.date()
    assert result.moonset.date() == dt.date()


def test_compute_lunar_details_age_zero_at_reference_new_moon() -> None:
    lng = _ATLANTIC[1]
    tz_name = "UTC"

    result = compute_lunar_details(_REFERENCE_NEW_MOON_UTC, lng, tz_name)

    assert result.age_days == 0.0


def test_compute_solunar_times_new_moon_at_reference_instant() -> None:
    lat, lng, tz_name = _ATLANTIC[0], _ATLANTIC[1], "UTC"

    result = compute_solunar_times(_REFERENCE_NEW_MOON_UTC, lat, lng, tz_name)

    assert result.moon_phase == MoonPhaseName.NEW
    assert result.rating == SolunarRating.EXCELLENT
    assert result.illumination_pct == 0.0


def test_compute_solunar_times_full_moon_half_synodic_month_later() -> None:
    lat, lng, tz_name = _ATLANTIC[0], _ATLANTIC[1], "UTC"
    half_synodic_days = 29.53058867 / 2
    full_moon_dt = _REFERENCE_NEW_MOON_UTC + timedelta(days=half_synodic_days)

    result = compute_solunar_times(full_moon_dt, lat, lng, tz_name)

    assert result.moon_phase == MoonPhaseName.FULL
    assert result.rating == SolunarRating.EXCELLENT
    assert result.illumination_pct >= 99.0


def test_compute_solunar_times_periods_sorted_and_same_calendar_date() -> None:
    lat, lng, tz_name = _ATLANTIC
    dt = datetime(2024, 7, 15, tzinfo=ZoneInfo(tz_name))

    result = compute_solunar_times(dt, lat, lng, tz_name)

    assert len(result.major_periods) == 2
    assert len(result.minor_periods) == 2
    assert result.major_periods[0].start <= result.major_periods[1].start
    assert result.minor_periods[0].start <= result.minor_periods[1].start
    for period in [*result.major_periods, *result.minor_periods]:
        assert period.start.date() == dt.date()
        assert period.end.date() == dt.date()


def test_compute_solunar_times_pacific_coast_also_produces_valid_periods() -> None:
    lat, lng, tz_name = _PACIFIC
    dt = datetime(2024, 12, 20, tzinfo=ZoneInfo(tz_name))

    result = compute_solunar_times(dt, lat, lng, tz_name)

    assert len(result.major_periods) == 2
    assert 0.0 <= result.illumination_pct <= 100.0
