"""Snapshot caching (sprint 24).

The legacy Flask app's `domain/forecast.py:generate_forecast()` caches
computed forecasts to a SQLite `forecast_cache` table behind a flat
4-hour TTL, with background daemon threads doing async refresh of stale
entries (see `CLAUDE.md`'s data-flow section). `apps/api` has no Postgres
connection yet (`apps/api/README.md`) and no background-daemon runtime,
so this sprint doesn't replicate legacy's storage layer or its scheduled
refresh — it builds the storage-and-transport-agnostic cache *policy*
only: an injectable-clock, per-key, single-flight, in-memory
`SnapshotCache[T]`, generalizing sprint 17's `StationCatalogCache[T]`
(single global value, one TTL) to multiple keys and a two-tier
freshness policy.

`fresh_ttl_seconds` defaults to 4 hours to match the legacy cadence
named in the sprint ledger. `stale_ttl_seconds` and the fallback-on-
fetch-failure behavior below it have **no legacy precedent** — the
legacy cache only ever had "hit" or "miss," never a policy for what to
serve when a refresh fetch itself fails. This is built directly from
`docs/product-definition.md`'s Stale-state definition ("a previously
valid snapshot is outside its freshness window but is still useful as
clearly aged fallback information") and the product contract's
Reliability bullet, extended from "one upstream source failing"
(sprints 21-23) to "the whole refresh failing."

Five states, each independently testable in
`apps/api/tests/test_snapshot_cache.py`:

- **Fresh hit** — a cached entry younger than `fresh_ttl_seconds` is
  returned immediately; *fetch* is never called.
- **Stale hit** — a cached entry between `fresh_ttl_seconds` and
  `stale_ttl_seconds` old triggers a refresh *fetch*; on success, the
  new value replaces the cache and is returned as fresh.
- **Miss** — no cached entry at all triggers a *fetch*; on success, the
  cache is populated.
- **Expiry** — a cached entry at or past `stale_ttl_seconds` is treated
  as gone: evicted before the refresh attempt, so it behaves exactly
  like a miss going forward — including that a *subsequent* fetch
  failure has nothing left to fall back to and propagates.
- **Fallback** — if the refresh *fetch* raises while a still-eligible
  (younger than `stale_ttl_seconds`) cached entry exists, that entry is
  returned instead of propagating the error, labeled
  `is_fallback=True`. A fetch failure with no eligible entry to fall
  back to (a true miss, or an entry already evicted for being past
  `stale_ttl_seconds`) propagates — there is nothing to invent.

**Concurrency**: `get_or_refresh` is single-flight per key via a
per-key `asyncio.Lock` — concurrent callers for the same key while a
refresh is in flight serialize on that one fetch rather than issuing
duplicate requests; callers for *different* keys never block each
other. Locks are never removed once created, an accepted simplification
for this sprint's key space (bounded by the number of resolved
locations in practice).

`force_refresh` (added when `app.domain.forecast_cache` wired this
cache around forecast assembly, still not a numbered sprint) skips the
freshness check entirely and always calls *fetch*, for a caller that
needs to force a live refetch regardless of the cached entry's age —
`POST /v1/forecasts/{id}/refresh`'s reason to exist, as opposed to
`GET`'s `get_or_refresh`. It shares `get_or_refresh`'s fallback-on-
failure and single-flight-per-key behavior via the private
`_fetch_and_store` helper both methods call while holding the same
per-key lock.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, TypeVar

_DEFAULT_FRESH_TTL_SECONDS = 4 * 3600
_DEFAULT_STALE_TTL_SECONDS = 24 * 3600

T = TypeVar("T")


@dataclass
class CachedSnapshot(Generic[T]):
    """The result of `SnapshotCache.get_or_refresh`.

    `is_fresh=True` means *value* came from a successful fetch (a fresh
    hit, or a stale-hit/miss/expiry that refreshed successfully).
    `is_fallback=True` means *value* is a previously-cached entry
    served because a refresh attempt failed — never both `True` at
    once.
    """

    value: T
    fetched_at: datetime
    is_fresh: bool
    is_fallback: bool


@dataclass
class _Entry(Generic[T]):
    value: T
    fetched_at: datetime


class SnapshotCache(Generic[T]):
    """See the module docstring for the full fresh/stale/miss/expiry/
    fallback/concurrency policy.
    """

    def __init__(
        self,
        *,
        fresh_ttl_seconds: float = _DEFAULT_FRESH_TTL_SECONDS,
        stale_ttl_seconds: float = _DEFAULT_STALE_TTL_SECONDS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if stale_ttl_seconds < fresh_ttl_seconds:
            raise ValueError("stale_ttl_seconds must be >= fresh_ttl_seconds")
        self._fresh_ttl_seconds = fresh_ttl_seconds
        self._stale_ttl_seconds = stale_ttl_seconds
        self._clock = clock
        self._entries: dict[str, _Entry[T]] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def get_or_refresh(
        self, key: str, fetch: Callable[[], Awaitable[T]]
    ) -> CachedSnapshot[T]:
        async with self._locks[key]:
            entry = self._entries.get(key)
            now = self._clock()

            if entry is not None:
                age_seconds = (now - entry.fetched_at).total_seconds()
                if age_seconds < self._fresh_ttl_seconds:
                    return CachedSnapshot(
                        value=entry.value,
                        fetched_at=entry.fetched_at,
                        is_fresh=True,
                        is_fallback=False,
                    )
                if age_seconds >= self._stale_ttl_seconds:
                    del self._entries[key]
                    entry = None

            return await self._fetch_and_store(key, fetch, entry, now)

    async def force_refresh(
        self, key: str, fetch: Callable[[], Awaitable[T]]
    ) -> CachedSnapshot[T]:
        """Unconditionally call *fetch*, bypassing the freshness check
        entirely, and replace the cached entry with the result. Still
        single-flight per key. For a caller that needs to force a live
        fetch regardless of how fresh the cached entry is (e.g. a
        "refresh" API endpoint) — as opposed to `get_or_refresh`, which
        only fetches when the entry is missing or past
        `fresh_ttl_seconds`. A fetch failure still falls back to the
        existing entry if one exists, exactly as `get_or_refresh` does:
        forcing a refresh doesn't mean discarding the last known-good
        value on failure.
        """
        async with self._locks[key]:
            entry = self._entries.get(key)
            now = self._clock()
            return await self._fetch_and_store(key, fetch, entry, now)

    async def _fetch_and_store(
        self,
        key: str,
        fetch: Callable[[], Awaitable[T]],
        fallback_entry: _Entry[T] | None,
        now: datetime,
    ) -> CachedSnapshot[T]:
        """Call *fetch* and cache the result, or fall back to
        *fallback_entry* on failure. Callers must already hold
        `self._locks[key]`.
        """
        try:
            value = await fetch()
        except Exception:
            if fallback_entry is not None:
                return CachedSnapshot(
                    value=fallback_entry.value,
                    fetched_at=fallback_entry.fetched_at,
                    is_fresh=False,
                    is_fallback=True,
                )
            raise

        self._entries[key] = _Entry(value=value, fetched_at=now)
        return CachedSnapshot(
            value=value, fetched_at=now, is_fresh=True, is_fallback=False
        )
