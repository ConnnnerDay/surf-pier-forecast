# apps/api

The canonical FastAPI service named by
[`docs/CANONICAL_ROADMAP.md`](../../docs/CANONICAL_ROADMAP.md)'s technical
contract: versioned `/v1` endpoints, deployed on an always-on Render
service, signed and called only from `apps/web`'s backend-for-frontend —
never directly from a browser.

## Status

Boots, has real CI, and now has:

- The canonical typed domain models (`app/domain/models.py` —
  `Location`, `Observation`, `SourceStatus`, `Confidence`, `Warning`,
  `ForecastEnvelope`; see the module docstring and `docs/architecture.md`'s
  ADR-003).
- A shared HTTP client policy for external providers
  (`app/infra/http_client.py` — `BoundedHTTPClient`): explicit
  connect/read timeouts, bounded exponential-backoff retries limited to
  transient failures (connection errors, timeouts, 429/502/503/504 — never
  4xx), a streamed response-size limit, a fixed identifying User-Agent,
  and structured `ProviderError` subclasses instead of leaking raw
  `httpx` exceptions, plus `get_json`/`get_text` for JSON and plain-text
  providers alike (`get_text` added in sprint 15 for NDBC). Every future
  provider adapter (NWS, NOAA CO-OPS, NDBC — sprints 13-15) is built on
  this, per ADR-003.
- The first provider adapter, NWS (`app/providers/nws.py`): typed
  marine-zone wind/wave/direction parsing and fetch
  (`parse_marine_zone_conditions`/`fetch_marine_zone_conditions`), and
  active-alerts parsing and fetch for a point or a state
  (`fetch_point_alerts`/`fetch_state_alerts`), ported from the legacy
  `services/nws.py` behind characterization tests, not a verbatim
  carry-over — see the module docstring for what's deliberately deferred
  (gridpoint wind fallback, current-weather observations).
- The second provider adapter, NOAA CO-OPS
  (`app/providers/noaa_coops.py`): water temperature
  (`fetch_water_temperature`) and tide predictions
  (`fetch_tide_predictions`), ported from the legacy `services/noaa.py`
  behind characterization tests, including DST-transition timestamp
  parsing via `zoneinfo`. See the module docstring for what's
  deliberately deferred (wind/currents/environmental-metrics fetches,
  the tide-chart SVG rendering helper) and why fallback-to-monthly-average
  policy isn't ported here.
- The third provider adapter, NDBC (`app/providers/ndbc.py`): buoy
  wind/wave/pressure parsing (`parse_realtime_text`) and fetch
  (`fetch_buoy_observation`) from the fixed-width `realtime2` text feed,
  ported from the legacy `services/ndbc.py` behind characterization
  tests covering missing columns (a buoy that doesn't report a field at
  all) and missing-value markers (`MM`, `99.0`, ...) distinctly. See the
  module docstring for what's deliberately deferred (pressure trend and
  fishing-impact narrative — scoring concerns, not a provider adapter's).
- The fourth provider adapter, astronomy (`app/providers/astronomy.py`):
  sunrise/sunset, civil/nautical/astronomical twilight, lunar details,
  and solunar major/minor fishing periods — pure math (NOAA's
  simplified solar-position algorithm, a synodic-month lunar-phase
  approximation), no network calls, unlike the other three adapters.
  Ported from the legacy `services/astro.py` behind characterization
  tests spanning coasts (Atlantic/Pacific), seasons, and timezones
  (including a non-DST-observing zone and a polar-latitude clamping
  case), with typed timezone-aware `datetime`s instead of pre-formatted
  12-hour strings. See the module docstring for the other adaptations
  (removed duplicate formula, removed a lat/lng-== 0 "unset" sentinel
  that was a latent bug, and the day-boundary approximation carried
  over unchanged from the legacy math). `app/infra/timezones.py` (new)
  holds the `ZoneInfo`-with-fallback helper this adapter shares with
  sprint 14's NOAA CO-OPS adapter, which was refactored to use it too.
- Station catalog resolution (`app/providers/stations.py`): fetches the
  public NOAA CO-OPS (tide-prediction and water-temperature) and NDBC
  station catalogs, and pure `nearest_coops_station`/
  `nearest_ndbc_stations` distance-ranking functions over an
  already-fetched catalog — the metadata that lets the other three
  network adapters be pointed at *any* US coastal coordinate, not just
  curated locations. Catalog fetches degrade to `[]` on failure rather
  than raising (metadata-for-routing, not a decision-relevant reading —
  see the module docstring for the contrast with sprint 14). Adds
  `StationCatalogCache`, an explicit, injectable-clock, idempotent TTL
  cache (a positive TTL for a successful fetch, a short negative TTL
  for a degraded one) replacing the legacy module's implicit
  module-level `dict` + lock, characterized in
  `apps/api/tests/test_stations_provider.py` without sleeping in tests.
- Coastal coordinate validation (`app/providers/coastal_bounds.py`):
  `is_valid_coordinate` (lat/lng range check) and
  `classify_coast_region` (a coarse bounding box per supported coast —
  Atlantic, Gulf, Pacific, Alaska, Hawaii — both pure, offline, no
  station lookup needed), plus `gate_coastal_point`, the actual
  inland-rejection mechanism ported from `locations.py`'s
  `_DYN_GATE_MILES` gate: a point counts as coastal only if it's
  within `max_miles` (default 60) of a real station in sprint 17's
  catalogs. See the module docstring for what's deliberately deferred
  (the legacy gate's additional curated-location fallback, which
  needs the curated-locations dataset — sprint 19's job).
- Location resolution (`app/providers/locations.py`): the curated
  101-spot location dataset and dynamic (any-coordinate) location
  resolution, ported from `locations.py`. `app/data/coastal_locations.json`
  and `app/data/water_temps.json` are a **documented mechanical move**,
  not hand-transcribed — generated by `ast.literal_eval`-parsing the
  legacy module's `COASTAL_LOCATIONS`/`_WATER_TEMPS` literals directly,
  guaranteeing byte-for-byte fidelity. `find_nearest_locations`,
  `timezone_for_point`, `monthly_water_temps_for_region`,
  `format_dynamic_id`/`parse_dynamic_id`, and `resolve_dynamic_location`
  are pure functions over already-loaded data, golden-tested in
  `apps/api/tests/test_locations_provider.py` against known curated
  spots (Montauk NY, Wrightsville Beach NC — the same fixture
  coordinates used throughout sprints 13-18's tests — and Poipu HI).
  `resolve_dynamic_location` composes sprint 17's station catalogs with
  the curated dataset the same way the legacy
  `_resolve_dynamic_location` did, just decoupled from fetching them.
- Observation normalization (`app/domain/normalize.py`): wraps sprints
  13-15's provider-specific typed outputs (NWS, NOAA CO-OPS, NDBC) into
  `app.domain.models.Observation`, the canonical provenance-carrying
  vocabulary from sprint 11's ADR-003 domain models. A domain-layer
  concern, not a provider one — see the module docstring for what's
  deliberately out of scope (astronomy isn't measured so isn't
  wrapped; categorical fields like wind direction have no unit;
  NWS's forecast ranges become an `ObservationRange` of paired
  low/high `Observation`s rather than one lossily-collapsed value;
  fallback-value substitution stays forecast-assembly's job, sprint
  21, as repeatedly deferred since sprint 14).
- Forecast assembly (`app/domain/assembly.py`, `assemble_forecast`):
  concurrently fans out to NWS, NOAA CO-OPS, and NDBC (sprints 13-15)
  plus astronomy (sprint 16), and assembles a typed `ForecastEnvelope`
  — the "one upstream failure doesn't blank the forecast" mechanism.
  Designs `ForecastEnvelope.conditions` (sprint 11 left it opaque for
  this sprint to own): each provider's normalized output as its own
  field, not merged into one reconciled value — that's forecast
  *scoring*'s job (sprint 22). Water temperature finally gets the
  fallback-to-monthly-average substitution repeatedly deferred here
  since sprint 14, labeled via `Observation.is_fallback`/
  `fallback_reason`, never presented as a live reading. `ForecastState`/
  `Confidence` are intentionally basic — full distance/age/fallback
  degradation policy is sprint 23's job. `apps/api/tests/
  test_assembly.py` exercises the full 2**3 = 8 present/absent matrix
  across the three fallible sources.

- Forecast scoring (`app/domain/scoring.py`, `score_conditions`): the
  0-100 go/no-go index and plain-language explanation, ported from the
  legacy `domain/forecast.py:score_conditions` plus the wind-orientation/
  onshore-offshore-direction mapping from `domain/species.py` (carried
  over verbatim per that module's own "single authoritative function"
  warning). Deliberately decoupled from sprint 21's `ForecastConditions`:
  it scores an already-reconciled `wind_range`/`wave_range` pair (plus
  optional wind direction, water temperature, sun times, and solunar
  data), not the assembly envelope directly — picking a source when both
  NWS and NDBC report is a future wiring step, not this sprint's job.
  Scores wind speed, wave height, wind direction (onshore/offshore by
  coastline orientation), water-temperature comfort band (with a small
  bonus for a live, non-fallback reading), dawn/dusk light window, and
  solunar rating/illumination — all thresholds and verdict tiers
  unchanged from the legacy function. See the module docstring for what's
  deliberately deferred and why: tide-based bonuses (sprint 34, no tide
  data fetched yet), fishing-type personalization and angler comfort
  thresholds (sprint 36, no user preferences yet), and water-quality/HAB
  signals (still-open product question from
  `docs/R1_RECONCILIATION_AUDIT.md`, re-flagged rather than silently
  ported or dropped). `apps/api/tests/test_scoring.py` characterizes
  every threshold band, the four coastline orientations, the verdict
  tiers, and the `None`-input → `Unknown`-verdict path.

- Confidence model (`app/domain/confidence.py`, `assess_confidence`): a
  four-factor evidence-quality score with **no legacy precedent** —
  `docs/product-definition.md`'s Confidence section ("derived from
  source coverage, observation age, station distance, and fallback
  use") was never implemented in the legacy Flask app at all, so this
  is this recovery's own design against the product contract, replacing
  sprint 21's interim HIGH/MEDIUM/LOW-from-liveness stub. Starts at 100
  points and applies an independent, documented penalty per degrading
  factor found — an unavailable/degraded `SourceStatus` per source, an
  aging/stale observation per two age thresholds, a fallback-flagged
  observation, and a far/very-far station distance (the "far" bound
  deliberately sits inside sprint 18's 60-mile coastal-gate cutoff) —
  reporting each as a reason code in `Confidence.reasons` so the
  product contract's "always show the reasons for reduced confidence"
  requirement has something to render. Deliberately decoupled from
  `app.domain.assembly`, matching sprint 22's scoring module: takes
  already-computed source statuses, observations, and station distances
  as explicit parameters rather than reaching into `ForecastEnvelope`
  itself — wiring this into `assemble_forecast` in place of its interim
  stub is a follow-up, not this sprint's job. `apps/api/tests/
  test_confidence.py` covers every factor independently, boundary ages
  and distances, mutual exclusivity within each two-tier band, multiple
  factors combining, and score clamping at zero.

- Snapshot caching (`app/infra/snapshot_cache.py`, `SnapshotCache[T]`):
  an injectable-clock, per-key, single-flight in-memory cache
  generalizing sprint 17's `StationCatalogCache[T]` (single global
  value, one TTL) to multiple keys and a two-tier freshness policy.
  `fresh_ttl_seconds` defaults to 4 hours, matching the legacy
  `forecast_cache` cadence named in the sprint ledger; `stale_ttl_seconds`
  and everything below it have no legacy precedent — legacy's cache
  only ever had "hit" or "miss," never a policy for what to serve when
  a refresh itself fails. Built from `docs/product-definition.md`'s
  Stale-state definition and the product contract's Reliability bullet,
  extended from "one upstream source failing" (sprints 21-23) to "the
  whole refresh failing": a cache entry younger than `fresh_ttl_seconds`
  is a fresh hit (no fetch called); between the two TTLs it's a stale
  hit that attempts a refresh; at or past `stale_ttl_seconds` it's
  evicted and treated as a miss; and if a refresh fetch raises while a
  still-eligible entry exists, that entry is returned labeled
  `is_fallback=True` instead of propagating the error (a true miss, or
  an entry already evicted for being past `stale_ttl_seconds`, has
  nothing to fall back to and still propagates). `get_or_refresh` is
  single-flight per key via a per-key `asyncio.Lock`. This sprint does
  not replicate legacy's SQLite storage layer (no Postgres connection
  yet) or its background-daemon refresh — see the module docstring —
  and wiring this cache around `assemble_forecast`, keyed by location
  id and producing `ForecastState.STALE` on a fallback hit, remains a
  follow-up alongside sprints 22/23's scoring/confidence wiring.
  `apps/api/tests/test_snapshot_cache.py` covers fresh hit, stale hit,
  miss, expiry, fallback-on-fetch-failure, and single-flight
  concurrency (both same-key serialization and different-key
  non-blocking) independently.

- The `/v1` HTTP surface (`app/api/v1/locations.py`,
  `app/api/v1/forecasts.py`): `GET /v1/locations/search`,
  `POST /v1/locations/resolve`, `GET /v1/forecasts/{location_id}`, and
  `POST /v1/forecasts/{location_id}/refresh` — four of the six routes
  the canonical roadmap's "Required API surface" names. `GET`/
  `PATCH /v1/me/preferences` are deliberately not attempted: they need
  Better Auth (sprint 28) and a Postgres-backed preferences store,
  neither of which exists yet. No new domain logic — `search`/`resolve`
  are thin wrappers over sprint 19's location functions (plus a new
  `search_curated_locations`/`find_curated_location` pair), and
  `GET /v1/forecasts/{id}` returns exactly the `ForecastEnvelope`
  sprint 21's `assemble_forecast` already builds. `refresh` is
  deliberately identical to `GET` today — `assemble_forecast` doesn't
  cache anything yet (sprint 24's `SnapshotCache` isn't wired into it,
  a named follow-up), so there's nothing yet for `refresh` to force
  bypassing. `app/api/deps.py` holds the app-lifetime `AppState`
  (pooled `BoundedHTTPClient` + the three station-catalog caches,
  created in `app/main.py`'s FastAPI `lifespan` and injected via
  `Depends`) and the shared `resolve_location_id` helper both routers
  use, so a location resolved explicitly and one resolved implicitly by
  a forecast lookup give the same answer for the same id.
- An OpenAPI breaking-change guard (`tests/openapi_snapshot.json`,
  `scripts/generate_openapi_snapshot.py`): mirrors sprint 11's
  domain-model schema-snapshot pattern — any route or schema change
  that isn't a deliberate, reviewed regeneration fails
  `test_openapi_snapshot.py`. Verified against a real running server
  (`uvicorn app.main:app`), not just `TestClient`: `/health/live`,
  `/v1/locations/search`, and `/openapi.json` all responded correctly
  and the live OpenAPI schema matched the committed snapshot exactly.

It does not yet have the ported forecast domain *scoring/confidence
refinement* wired into the API, or a Postgres connection. Those land in
the Phase 2 sprints listed in the roadmap's sprint ledger (26 onward),
each behind its own characterization tests, porting from the
reconciliation audit
([`docs/R1_RECONCILIATION_AUDIT.md`](../../docs/R1_RECONCILIATION_AUDIT.md))
rather than copying `v2/backend` or the legacy Flask app verbatim.

If you change a model in `app/domain/models.py`, its schema snapshot test
will fail — regenerate deliberately and review the diff:

```bash
python -m scripts.generate_schema_snapshots
```

Likewise, if you change a `/v1` route or a model it returns, the OpenAPI
snapshot test will fail — regenerate deliberately and review the diff:

```bash
python -m scripts.generate_openapi_snapshot
```

## Local dev

```bash
cd apps/api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # includes runtime deps + ruff/mypy/pytest/httpx
uvicorn app.main:app --reload --port 8000
```

Smoke test:

```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
# both return {"status": "ok"}
```

Interactive docs: http://localhost:8000/docs

## Checks

Run these from `apps/api` with the dev venv active — they mirror
`.github/workflows/apps-ci.yml`:

```bash
ruff check .
ruff format --check .
mypy .
pytest -q
```
