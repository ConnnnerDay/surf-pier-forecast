"""Characterization tests for app.domain.timing.build_hourly_outlook."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain.timing import (
    ActivityTag,
    FeedingBand,
    HourlyOutlook,
    SunEvent,
    TideEvent,
    build_hourly_outlook,
)
from app.providers.astronomy import (
    MoonPhaseName,
    SolunarPeriod,
    SolunarRating,
    SolunarTimes,
    SunTimes,
)
from app.providers.noaa_coops import TidePrediction

_TZ_NAME = "America/New_York"
_TZ = ZoneInfo(_TZ_NAME)
_TODAY = datetime(2024, 7, 15, tzinfo=_TZ).date()
_NOW = datetime(2024, 7, 15, 12, 0, tzinfo=UTC)


def _local(hour: int, minute: int = 0) -> datetime:
    return datetime(_TODAY.year, _TODAY.month, _TODAY.day, hour, minute, tzinfo=_TZ)


def _sun_times(sunrise_hour: int = 6, sunset_hour: int = 20) -> SunTimes:
    return SunTimes(sunrise=_local(sunrise_hour), sunset=_local(sunset_hour))


def _solunar(
    *,
    major_periods: list[SolunarPeriod] | None = None,
    minor_periods: list[SolunarPeriod] | None = None,
    illumination_pct: float = 30.0,
) -> SolunarTimes:
    return SolunarTimes(
        major_periods=major_periods or [],
        minor_periods=minor_periods or [],
        moon_phase=MoonPhaseName.FIRST_QUARTER,
        illumination_pct=illumination_pct,
        rating=SolunarRating.GOOD,
    )


def _outlook(
    *,
    sun_times: SunTimes | None = None,
    solunar: SolunarTimes | None = None,
    tide_predictions: list[TidePrediction] | None = None,
    wind_range: tuple[float, float] | None = None,
    now: datetime = _NOW,
) -> HourlyOutlook:
    return build_hourly_outlook(
        sun_times=sun_times or _sun_times(),
        solunar=solunar or _solunar(),
        tide_predictions=tide_predictions,
        wind_range=wind_range,
        now=now,
        tz_name=_TZ_NAME,
    )


class TestShape:
    def test_returns_24_hours_in_order(self) -> None:
        outlook = _outlook()
        assert [h.hour for h in outlook.hours] == list(range(24))

    def test_every_level_in_0_100(self) -> None:
        outlook = _outlook()
        assert all(0 <= h.level <= 100 for h in outlook.hours)

    def test_tag_matches_level_thresholds(self) -> None:
        outlook = _outlook()
        for hour in outlook.hours:
            if hour.level >= 75:
                assert hour.tag == ActivityTag.PRIME
            elif hour.level >= 50:
                assert hour.tag == ActivityTag.HIGH
            elif hour.level >= 30:
                assert hour.tag == ActivityTag.MED
            else:
                assert hour.tag == ActivityTag.LOW

    def test_no_tides_or_wind_does_not_crash(self) -> None:
        outlook = _outlook(tide_predictions=None, wind_range=None)
        assert len(outlook.hours) == 24
        assert all(h.tide_event is None for h in outlook.hours)


class TestIsNowAndPeak:
    def test_is_now_marks_exactly_the_current_local_hour(self) -> None:
        now = _local(9, 30).astimezone(UTC)
        outlook = _outlook(now=now)
        now_hours = [h.hour for h in outlook.hours if h.is_now]
        assert now_hours == [9]

    def test_peak_only_set_on_max_level_hours_at_or_above_50(self) -> None:
        outlook = _outlook()
        levels = [h.level for h in outlook.hours]
        peak_level = max(levels)
        for hour in outlook.hours:
            expected = hour.level == peak_level and peak_level >= 50
            assert hour.peak == expected


class TestDawnDusk:
    def test_sunrise_hour_tagged_as_sun_event(self) -> None:
        outlook = _outlook(sun_times=_sun_times(sunrise_hour=6, sunset_hour=20))
        assert outlook.hours[6].sun_event == SunEvent.SUNRISE
        assert outlook.hours[20].sun_event == SunEvent.SUNSET

    def test_dawn_boosts_activity_relative_to_an_unboosted_hour(self) -> None:
        outlook = _outlook(sun_times=_sun_times(sunrise_hour=6, sunset_hour=20))
        by_hour = {h.hour: h for h in outlook.hours}
        # Hour 13 sits far from dawn, dusk, and (in this fixture) any
        # solunar/tide overlay, so it isolates the unboosted baseline.
        assert by_hour[6].level > by_hour[13].level
        assert "Dawn" in by_hour[6].reasons

    def test_dusk_boosts_activity_relative_to_an_unboosted_hour(self) -> None:
        outlook = _outlook(sun_times=_sun_times(sunrise_hour=6, sunset_hour=20))
        by_hour = {h.hour: h for h in outlook.hours}
        assert by_hour[20].level > by_hour[13].level
        assert "Dusk" in by_hour[20].reasons


class TestSolunar:
    def test_major_period_sets_feeding_band_and_reason(self) -> None:
        outlook = _outlook(
            solunar=_solunar(
                major_periods=[SolunarPeriod(start=_local(10), end=_local(12))]
            )
        )
        by_hour = {h.hour: h for h in outlook.hours}
        for h in (10, 11, 12):
            assert by_hour[h].feeding == FeedingBand.MAJOR
            assert "Major solunar" in by_hour[h].reasons

    def test_minor_period_does_not_downgrade_an_overlapping_major_period(self) -> None:
        outlook = _outlook(
            solunar=_solunar(
                major_periods=[SolunarPeriod(start=_local(10), end=_local(12))],
                minor_periods=[SolunarPeriod(start=_local(11), end=_local(13))],
            )
        )
        by_hour = {h.hour: h for h in outlook.hours}
        assert by_hour[11].feeding == FeedingBand.MAJOR
        assert by_hour[13].feeding == FeedingBand.MINOR

    def test_reasons_are_deduplicated(self) -> None:
        outlook = _outlook(
            solunar=_solunar(
                major_periods=[
                    SolunarPeriod(start=_local(10), end=_local(11)),
                    SolunarPeriod(start=_local(10), end=_local(11)),
                ]
            )
        )
        by_hour = {h.hour: h for h in outlook.hours}
        assert by_hour[10].reasons.count("Major solunar") == 1

    def test_bright_moon_boosts_night_hours(self) -> None:
        dim = _outlook(solunar=_solunar(illumination_pct=30.0))
        bright = _outlook(solunar=_solunar(illumination_pct=90.0))
        by_hour_dim = {h.hour: h for h in dim.hours}
        by_hour_bright = {h.hour: h for h in bright.hours}
        assert by_hour_bright[1].level > by_hour_dim[1].level


class TestTides:
    def test_high_tide_hour_recorded_with_time_and_reason(self) -> None:
        prediction = TidePrediction(time=_local(14), kind="high", height_ft=3.2)
        outlook = _outlook(tide_predictions=[prediction])
        hour = outlook.hours[14]
        assert hour.tide_event == TideEvent.HIGH
        assert hour.tide_time == prediction.time
        assert "High tide" in hour.reasons

    def test_low_tide_boosts_less_than_high_tide(self) -> None:
        high = _outlook(
            tide_predictions=[
                TidePrediction(time=_local(14), kind="high", height_ft=3.2)
            ]
        )
        low = _outlook(
            tide_predictions=[
                TidePrediction(time=_local(14), kind="low", height_ft=0.4)
            ]
        )
        assert high.hours[14].level > low.hours[14].level

    def test_tide_prediction_on_a_different_day_is_ignored(self) -> None:
        tomorrow = _local(14) + timedelta(days=1)
        outlook = _outlook(
            tide_predictions=[TidePrediction(time=tomorrow, kind="high", height_ft=3.2)]
        )
        assert all(h.tide_event is None for h in outlook.hours)


class TestWind:
    def test_strong_wind_lowers_levels_relative_to_no_wind_data(self) -> None:
        calm = _outlook(wind_range=None)
        windy = _outlook(wind_range=(30.0, 35.0))
        for calm_hour, windy_hour in zip(calm.hours, windy.hours, strict=True):
            assert windy_hour.level <= calm_hour.level
        assert any(
            w.level < c.level for c, w in zip(calm.hours, windy.hours, strict=True)
        )

    def test_light_wind_matches_no_wind_data(self) -> None:
        calm = _outlook(wind_range=None)
        light = _outlook(wind_range=(2.0, 5.0))
        assert [h.level for h in calm.hours] == [h.level for h in light.hours]
