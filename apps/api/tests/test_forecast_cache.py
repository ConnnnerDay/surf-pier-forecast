"""Tests for app.domain.forecast_cache.

Exercises the caching *wiring* around assemble_forecast, not
SnapshotCache's own fresh/stale/miss/expiry/fallback policy (that's
test_snapshot_cache.py's job) or assemble_forecast's own behavior
(test_assembly.py's job). A fake clock drives freshness deterministically;
a substituted `assemble` callable — real assemble_forecast can't be made
to raise by design — drives the fallback path.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.forecast_cache import (
    get_or_assemble_forecast,
    refresh_and_assemble_forecast,
)
from app.domain.models import (
    Confidence,
    ConfidenceLevel,
    ForecastEnvelope,
    ForecastState,
    Location,
)
from app.infra.http_client import BoundedHTTPClient
from app.infra.snapshot_cache import SnapshotCache
from app.providers.locations import ResolvedLocation

_Assemble = Callable[..., Awaitable[ForecastEnvelope]]

_START = datetime(2024, 7, 15, 12, 0, tzinfo=UTC)
_CLIENT = BoundedHTTPClient()

_LOCATION = ResolvedLocation(
    id="wrightsville-beach-nc",
    name="Wrightsville Beach",
    state="NC",
    lat=34.2104,
    lng=-77.7964,
    timezone="America/New_York",
    coops_station="8658163",
    water_temp_station="8658163",
    ndbc_stations=["41110"],
    nws_zone="AMZ158",
    temp_region="nc_south",
    conditions_region="atlantic_mid",
    temp_offset=0,
    is_dynamic=False,
)


class _FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _envelope(state: ForecastState = ForecastState.FRESH) -> ForecastEnvelope:
    return ForecastEnvelope(
        location=Location(
            id=_LOCATION.id,
            label=_LOCATION.name,
            lat=_LOCATION.lat,
            lng=_LOCATION.lng,
            timezone=_LOCATION.timezone,
        ),
        generated_at=_START,
        state=state,
        sources=[],
        confidence=Confidence(level=ConfidenceLevel.HIGH, reasons=[]),
        warnings=[],
        conditions={},
        tides=None,
        hourly_outlook=None,
        recommendations=None,
    )


def _make_cache(clock: _FakeClock, **kwargs: float) -> SnapshotCache[ForecastEnvelope]:
    return SnapshotCache(clock=clock, **kwargs)


def _assembler(*envelopes: ForecastEnvelope) -> tuple[_Assemble, list[int]]:
    calls = [0]
    iterator = iter(envelopes)

    async def assemble(
        location: object, client: object, profiles: object, *, now: object
    ) -> ForecastEnvelope:
        calls[0] += 1
        return next(iterator)

    return assemble, calls


def _failing_assembler() -> tuple[_Assemble, list[int]]:
    calls = [0]

    async def assemble(
        location: object, client: object, profiles: object, *, now: object
    ) -> ForecastEnvelope:
        calls[0] += 1
        raise RuntimeError("upstream failure")

    return assemble, calls


class TestGetOrAssembleForecast:
    @pytest.mark.asyncio
    async def test_miss_assembles_and_caches(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock)
        assemble, calls = _assembler(_envelope())

        result = await get_or_assemble_forecast(
            cache, _LOCATION, _CLIENT, {}, now=_START, assemble=assemble
        )

        assert calls[0] == 1
        assert result.state == ForecastState.FRESH

    @pytest.mark.asyncio
    async def test_fresh_hit_does_not_reassemble(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock, fresh_ttl_seconds=3600.0)
        first, _ = _assembler(_envelope())
        await get_or_assemble_forecast(
            cache, _LOCATION, _CLIENT, {}, now=_START, assemble=first
        )

        clock.advance(60.0)
        second, second_calls = _assembler(_envelope())
        result = await get_or_assemble_forecast(
            cache, _LOCATION, _CLIENT, {}, now=clock.now, assemble=second
        )

        assert second_calls[0] == 0
        assert result.state == ForecastState.FRESH

    @pytest.mark.asyncio
    async def test_expired_entry_triggers_reassembly(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock, fresh_ttl_seconds=3600.0, stale_ttl_seconds=7200.0)
        first, _ = _assembler(_envelope())
        await get_or_assemble_forecast(
            cache, _LOCATION, _CLIENT, {}, now=_START, assemble=first
        )

        clock.advance(4000.0)
        second, second_calls = _assembler(_envelope())
        await get_or_assemble_forecast(
            cache, _LOCATION, _CLIENT, {}, now=clock.now, assemble=second
        )

        assert second_calls[0] == 1

    @pytest.mark.asyncio
    async def test_fetch_failure_falls_back_to_stale_cached_envelope(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock, fresh_ttl_seconds=100.0, stale_ttl_seconds=7200.0)
        populate, _ = _assembler(_envelope(ForecastState.FRESH))
        await get_or_assemble_forecast(
            cache, _LOCATION, _CLIENT, {}, now=_START, assemble=populate
        )

        clock.advance(200.0)  # past fresh_ttl, still within stale_ttl
        failing, failing_calls = _failing_assembler()
        result = await get_or_assemble_forecast(
            cache, _LOCATION, _CLIENT, {}, now=clock.now, assemble=failing
        )

        assert failing_calls[0] == 1
        assert result.state == ForecastState.STALE

    @pytest.mark.asyncio
    async def test_fetch_failure_with_no_cached_entry_propagates(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock)
        failing, _ = _failing_assembler()

        with pytest.raises(RuntimeError, match="upstream failure"):
            await get_or_assemble_forecast(
                cache, _LOCATION, _CLIENT, {}, now=_START, assemble=failing
            )


class TestRefreshAndAssembleForecast:
    @pytest.mark.asyncio
    async def test_bypasses_a_fresh_cached_entry(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock, fresh_ttl_seconds=3600.0)
        populate, _ = _assembler(_envelope())
        await get_or_assemble_forecast(
            cache, _LOCATION, _CLIENT, {}, now=_START, assemble=populate
        )

        # Entry is still fresh — get_or_assemble_forecast wouldn't
        # reassemble, but refresh should anyway.
        force, force_calls = _assembler(_envelope())
        result = await refresh_and_assemble_forecast(
            cache, _LOCATION, _CLIENT, {}, now=clock.now, assemble=force
        )

        assert force_calls[0] == 1
        assert result.state == ForecastState.FRESH

    @pytest.mark.asyncio
    async def test_repopulates_cache_for_subsequent_get(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock, fresh_ttl_seconds=3600.0)
        populate, _ = _assembler(_envelope())
        await get_or_assemble_forecast(
            cache, _LOCATION, _CLIENT, {}, now=_START, assemble=populate
        )

        force, _ = _assembler(_envelope())
        await refresh_and_assemble_forecast(
            cache, _LOCATION, _CLIENT, {}, now=clock.now, assemble=force
        )

        never_called, never_called_calls = _assembler(_envelope())
        await get_or_assemble_forecast(
            cache, _LOCATION, _CLIENT, {}, now=clock.now, assemble=never_called
        )
        assert never_called_calls[0] == 0

    @pytest.mark.asyncio
    async def test_forced_fetch_failure_falls_back_to_stale_cached_envelope(
        self,
    ) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock)
        populate, _ = _assembler(_envelope(ForecastState.FRESH))
        await get_or_assemble_forecast(
            cache, _LOCATION, _CLIENT, {}, now=_START, assemble=populate
        )

        failing, failing_calls = _failing_assembler()
        result = await refresh_and_assemble_forecast(
            cache, _LOCATION, _CLIENT, {}, now=clock.now, assemble=failing
        )

        assert failing_calls[0] == 1
        assert result.state == ForecastState.STALE

    @pytest.mark.asyncio
    async def test_forced_fetch_failure_with_no_cached_entry_propagates(self) -> None:
        clock = _FakeClock(_START)
        cache = _make_cache(clock)
        failing, _ = _failing_assembler()

        with pytest.raises(RuntimeError, match="upstream failure"):
            await refresh_and_assemble_forecast(
                cache, _LOCATION, _CLIENT, {}, now=_START, assemble=failing
            )
