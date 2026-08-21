"""Cached forecast assembly (wiring added post-sprint-25, still not a
numbered sprint — the third and last piece of the "wire sprints 22/23/24
into `assemble_forecast`" follow-up named since sprint 25).

Wraps sprint 21's `assemble_forecast` in sprint 24's `SnapshotCache`,
keyed by location id, giving `GET /v1/forecasts/{id}` (sprint 25) its
freshness-window behavior and finally giving
`POST /v1/forecasts/{id}/refresh` the distinguishing job sprint 25's
router docstring promised it: bypass the cache and force a live
assemble.

**Why `ForecastState.STALE` is documented but practically dormant
today.** `SnapshotCache` only serves a stale cached entry when the
wrapped fetch raises. `assemble_forecast` (sprint 21) is deliberately
built to *never* raise — every fallible source is caught and degraded
internally, per the product contract's Reliability bullet ("one
upstream failure must not blank the forecast") — so in normal operation
there is nothing for `SnapshotCache`'s fallback path to catch. This
module still wires that path correctly and relabels the result
`ForecastState.STALE` when it does fire (an unexpected exception from a
future change, a genuine bug, a lifespan/dependency wiring error — not
a "no legacy precedent" excuse to skip it), rather than leaving the
branch unimplemented. `tests/test_forecast_cache.py` exercises it
directly by passing a *deliberately failing* stand-in for the
`assemble` parameter both functions accept below — `assemble_forecast`
itself can't be made to raise by design, so a substitutable seam is
what makes that path testable at all, the same "test the seam
directly" approach sprint 24's own test suite already takes with
`SnapshotCache`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from app.domain.assembly import assemble_forecast
from app.domain.models import ForecastEnvelope, ForecastState
from app.infra.http_client import BoundedHTTPClient
from app.infra.snapshot_cache import SnapshotCache
from app.providers.locations import ResolvedLocation

_Assemble = Callable[..., Awaitable[ForecastEnvelope]]


def _relabel_stale(envelope: ForecastEnvelope) -> ForecastEnvelope:
    return envelope.model_copy(update={"state": ForecastState.STALE})


async def get_or_assemble_forecast(
    cache: SnapshotCache[ForecastEnvelope],
    location: ResolvedLocation,
    client: BoundedHTTPClient,
    water_temp_profiles: dict[str, dict[int, float]],
    *,
    now: datetime,
    assemble: _Assemble = assemble_forecast,
) -> ForecastEnvelope:
    """`GET /v1/forecasts/{id}`'s path: serve a cached envelope if one is
    still fresh; otherwise assemble a new one, subject to
    `SnapshotCache`'s fresh/stale/miss/expiry/fallback policy. A
    fallback hit is relabeled `ForecastState.STALE` — see the module
    docstring for why that path is dormant in practice. *assemble*
    defaults to the real `assemble_forecast`; tests substitute a
    deliberately failing stand-in to exercise the fallback path.
    """
    snapshot = await cache.get_or_refresh(
        location.id,
        lambda: assemble(location, client, water_temp_profiles, now=now),
    )
    return _relabel_stale(snapshot.value) if snapshot.is_fallback else snapshot.value


async def refresh_and_assemble_forecast(
    cache: SnapshotCache[ForecastEnvelope],
    location: ResolvedLocation,
    client: BoundedHTTPClient,
    water_temp_profiles: dict[str, dict[int, float]],
    *,
    now: datetime,
    assemble: _Assemble = assemble_forecast,
) -> ForecastEnvelope:
    """`POST /v1/forecasts/{id}/refresh`'s path: force a live assemble
    regardless of the cached entry's age, repopulating the cache for
    subsequent `GET`s. Falls back to the existing cached envelope
    (relabeled `ForecastState.STALE`) only if the forced assemble itself
    fails — see the module docstring for why that's dormant in
    practice. *assemble* is the same test seam `get_or_assemble_forecast`
    exposes.
    """
    snapshot = await cache.force_refresh(
        location.id,
        lambda: assemble(location, client, water_temp_profiles, now=now),
    )
    return _relabel_stale(snapshot.value) if snapshot.is_fallback else snapshot.value
