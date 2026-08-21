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
- Sprint 22's scoring wired into `app/domain/assembly.py`:
  `ForecastConditions.score` is a real `ForecastScore` on every
  forecast, computed by a source-reconciliation policy assembly now
  owns — the NWS marine-zone range is preferred over the NDBC buoy's
  single live reading when both are present (the buoy's value becomes
  a degenerate zero-width range only as a fallback), and wind direction
  prefers NWS's parsed value over the buoy's. `GET /v1/forecasts/{id}`
  therefore returns a populated go/no-go score, not just raw
  per-source readings. Discovered via this wiring's manual real-server
  smoke test (a live network call this sandbox's proxy rejects with
  `httpx.ProxyError`, not `httpx.ConnectError`): `app/infra/
  http_client.py`'s `BoundedHTTPClient` only caught `ConnectError`, not
  the broader `httpx.TransportError` family, so a proxy/read/write/
  pool-timeout failure would have escaped as an unhandled exception
  instead of degrading one source gracefully — fixed and characterized
  in `test_http_client.py`.
- Sprint 23's confidence model wired in too: `ForecastEnvelope.confidence`
  now comes from a real `assess_confidence` call instead of assembly's
  old liveness-only stub, given per-source status, one `AgedObservation`
  for water temperature (age + fallback status), and — for dynamic
  points — a `StationDistance` built from `location.anchor_miles`.
  That required a small plumbing addition: `ResolvedLocation` (`app/
  providers/locations.py`) gained an `anchor_miles: float | None` field,
  populated by `resolve_dynamic_location` (previously computed and
  discarded) and left `None` for curated locations (they *are* the
  named station, so no distance factor applies) and for points where no
  anchor was found at all. This is a deliberate simplification, not a
  full per-source distance breakdown: `resolve_dynamic_location` only
  ever returns the single *nearest* anchor's distance, not one per
  fallible source — documented in `assembly.py`'s module docstring.
  `POST /v1/locations/resolve`'s response schema picked up the new
  field, regenerating `tests/openapi_snapshot.json` — reviewed as a
  one-field, additive diff, exactly the deliberate-regeneration flow
  the breaking-change guard exists for.
- Sprint 24's `SnapshotCache` wired in too, closing the last of the
  three unassigned wiring pieces: `app/domain/forecast_cache.py` wraps
  sprint 21's `assemble_forecast` in the cache, keyed by location id,
  not inside `assemble_forecast` itself — assembly stays a pure
  "assemble one envelope right now" function. `GET /v1/forecasts/{id}`
  (`get_or_assemble_forecast`) serves a fresh cached envelope when one
  exists; `POST /v1/forecasts/{id}/refresh`
  (`refresh_and_assemble_forecast`) bypasses the freshness check
  entirely and forces a live assemble, repopulating the cache — the
  distinguishing behavior sprint 25's router docstring promised it once
  this landed. `SnapshotCache` gained a `force_refresh` method for
  exactly that ("bypass the TTL, still fall back to the last known-good
  value on failure"), sharing `get_or_refresh`'s fallback/single-flight
  logic via a new private `_fetch_and_store` helper. A fallback hit
  (the wrapped assemble raising) is relabeled `ForecastState.STALE` —
  documented as a **practically dormant path**: `assemble_forecast`
  never raises by design (every fallible source degrades internally),
  so there's normally nothing for the cache's fallback branch to catch.
  Both cache-wrapper functions accept an injectable `assemble` callable
  (defaulting to the real one) specifically so `tests/
  test_forecast_cache.py` can substitute a deliberately failing
  stand-in and actually exercise that dormant path — the same "test the
  seam directly" approach sprint 24's own `SnapshotCache` tests already
  use. Router-level tests confirm the wiring end-to-end: a second `GET`
  doesn't re-hit any upstream, and `refresh` does even immediately
  after a `GET`.
- Sprint 26, performance budget ("bounded parallel calls, no
  duplicates, warm p95 under 750 ms"): `BoundedHTTPClient`
  (`app/infra/http_client.py`) now sets `httpx.Limits` explicitly
  (`max_connections`/`max_keepalive_connections`, defaults 20/10) —
  despite ADR-003 naming "bounded concurrency" as part of the design
  from the start, the client had silently relied on httpx's own
  unexamined defaults until now. **Only takes effect when httpx builds
  its own transport** — a caller-supplied `transport` (every test in
  this codebase, via `httpx.MockTransport`) bypasses `limits` entirely,
  so `test_http_client.py` characterizes it by constructing a
  no-mock-transport client and inspecting the real transport's
  connection pool, not by exercising it through a mock. "No duplicates"
  under genuine concurrent HTTP requests (not just sequential) and
  "warm p95" are both characterized in the new
  `tests/test_performance_budget.py`: `httpx.AsyncClient` +
  `httpx.ASGITransport` drives the real FastAPI app with real
  `asyncio.gather`-driven concurrency (Starlette's synchronous
  `TestClient` can't produce genuine overlapping requests) — three
  concurrent `GET`s for the same location produce exactly one upstream
  marine-zone call, proving sprint 24's single-flight caching holds at
  the HTTP layer, not just the domain layer sprint 24/the caching-wiring
  follow-up already tested. Warm p95 is measured with real wall-clock
  timing over 20 already-cached requests (no fake clock — "warm" means
  no network call should happen at all, which this test also verifies
  directly): measured p95 ≈ 2.8ms against the 750ms budget, roughly
  270x headroom. This measures the warm path's *local* processing
  latency only — a real end-to-end cold-path p95 against live upstreams
  can't be measured in this sandboxed CI environment
  (`docs/R2_CI_BASELINE.md`'s no-live-provider-dependence rule) and
  remains open acceptance evidence for whenever this runs somewhere
  with real network access.
- Sprint 13's own deferred gridpoint-wind fallback, picked up now that
  Phase 2 is otherwise closed out: `app/providers/nws.py` gained
  `fetch_gridpoint_wind`/`parse_gridpoint_wind` (`GridpointWindForecast`
  — wind-only, no wave data, since it's a land-point forecast), ported
  from the legacy `_try_nws_gridpoint`. `app/domain/assembly.py` wires
  it in as a *last-resort* wind source: fetched only when neither the
  marine-zone forecast nor the NDBC buoy provide wind, as one extra
  sequential call (not part of the initial `asyncio.gather`) rather
  than on every request, per sprint 26's "bounded parallel calls, no
  duplicates" performance-budget discipline. A successful gridpoint
  fetch rescues `ForecastState` (FRESH instead of PARTIAL) and feeds a
  `SourceStatus`/warning into the confidence model, but — being
  wave-blind by nature — **cannot by itself rescue `score`**:
  `score_conditions` still needs both wind and wave to produce a
  number (sprint 22's contract, unchanged), so `score` stays
  `None`/`UNKNOWN` whenever wave is unavailable from every source,
  gridpoint included — documented explicitly rather than left as a
  surprising gap. The legacy module's current-weather observations
  (`fetch_current_weather` — air temp, humidity, heat index, recent
  precipitation) remain deliberately deferred: nothing in the canonical
  roadmap's required `ForecastConditions` shape names them, so porting
  now would be inventing product scope, not closing a named gap.
  `tests/test_nws_provider.py` gained 8 new tests for the parser/fetch
  functions; `tests/test_assembly.py` gained coverage for the rescue
  case, the state-vs-score distinction above, and that gridpoint is
  never fetched when marine-zone wind is already available (proven
  with a mock transport that raises on any unexpected `/points/`
  request, not by absence of assertions).
- Sprint 14's own deferred CO-OPS wind fallback, picked up right after
  sprint 13's gridpoint fallback: `app/providers/noaa_coops.py` gained
  `fetch_coops_wind` (`CoopsWindReading`), ported from the legacy
  `_try_coops_wind` — the latest wind reading from the same CO-OPS
  station already used for water temperature. `app/domain/assembly.py`
  now tries a genuine **two-step last-resort chain** in the exact
  priority order the legacy `domain/forecast.py:get_marine_conditions`
  used: CO-OPS wind first, then NWS gridpoint wind only if that also
  comes up empty — stopping as soon as one succeeds, so the typical
  case (marine-zone or buoy already has wind) never pays for either.
  Same non-critical resilience posture as gridpoint wind (degrades to
  `None`, doesn't raise) and the same score-vs-state distinction (can
  rescue `ForecastState` to FRESH, can't rescue `score` — no wave data
  from either fallback). Currents, environmental metrics (air temp,
  humidity, visibility, pressure, salinity, conductivity), and the
  tide-chart SVG helper remain deliberately deferred: nothing in the
  required `ForecastConditions` shape or `docs/product-definition.md`'s
  dashboard-hierarchy list names tidal currents or these metrics.
  `tests/test_noaa_coops_provider.py` gained 5 tests for the new fetch
  function; `tests/test_assembly.py` gained a `coops_wind_ok` client
  parameter (matching `gridpoint_ok`'s pattern), a dedicated CO-OPS-
  wind rescue test proving gridpoint is never reached when CO-OPS wind
  already succeeded, and the existing gridpoint tests were updated to
  isolate gridpoint's own behavior with `coops_wind_ok=False`.

- Sprint 44's internal-signature verification primitive (partial):
  `app/infra/internal_signature.py` implements ADR-004's
  (`docs/architecture.md`) HMAC-SHA-256 request-signing contract for the
  Next.js BFF → FastAPI boundary — canonical method/path (including query
  string)/body-digest/user-id/issued-at/expires-at/request-id/key-id,
  constant-time signature comparison, a clock-skew window, expiration,
  and replay detection by request ID with an injectable-clock
  `ReplayGuard` (per-entry expiry, same pattern as `SnapshotCache`).
  `app/api/internal_auth.py` wraps it as a FastAPI dependency
  (`InternalAuthDependency`, constructor-injected keys/clock for
  testability, matching `SnapshotCache`/`StationCatalogCache`'s style)
  that fails closed — no configured signing keys is a 500, never a
  silent pass-through. No legacy precedent: the legacy Flask app is
  single-process with no internal service boundary to sign across.
  `tests/test_internal_signature.py` covers the pure primitive (valid
  signature, tampered body/method/path, expired, clock-skew, validity-
  window-too-long, unknown key id, previous-key rotation, replay
  detection, replay-guard pruning); `tests/test_internal_auth.py` covers
  the FastAPI dependency layer against a throwaway app (missing headers,
  invalid signature, body tampered in transit, expired, malformed
  timestamp header, replay, and fail-closed with no configured keys).
- **Sprint 44's signature requirement is now wired onto the real `/v1`
  routers** (`app/api/v1/locations.py`/`forecasts.py`, via each
  `APIRouter`'s `dependencies=[Depends(require_internal_signature)]`):
  `apps/web` grew a matching signer
  (`lib/internal-api-client.ts`/`internal-signature.ts`) that computes
  the identical canonical string and HMAC, so the check is no longer
  unsatisfiable by its only real client. A plain function `Depends()`
  adds nothing to the OpenAPI schema (FastAPI only documents
  `fastapi.security` classes), so `tests/openapi_snapshot.json` is
  unaffected. Every other router test in this suite overrides
  `require_internal_signature` to a no-op via `app.dependency_overrides`
  (matching `get_app_state`'s existing pattern) — they're about
  router/domain behavior, already covered elsewhere. The one exception,
  `tests/test_internal_api_wiring.py`, deliberately does *not* override
  it: it monkeypatches real signing-key env vars and proves, against the
  real app, that an unsigned request is rejected (401), a correctly
  signed one succeeds (200), and no configured keys fails closed (500).
  `user_id` stays empty on every request for now — no Better Auth
  (sprint 28) session exists yet to source a real one from, and ADR-004
  only requires it "when required," which nothing here does until
  accounts exist. Verified against two real running servers (`uvicorn`
  + `next dev`) with matching dev signing keys: an unsigned curl to
  `apps/api` got a real 401, and `apps/web`'s forecast page (see that
  app's README) rendered a real, gracefully degraded forecast
  end-to-end through the signed path.
- Sprint 34's backend half (tide predictions, not the accessible-chart/
  text-alternative rendering, which is `apps/web`'s job):
  `app/domain/assembly.py` fetches tide predictions
  (`app.providers.noaa_coops.fetch_tide_predictions`, already built)
  in the same `asyncio.gather` as the other independent sources, for a
  local-date window (today through two days out, computed in the
  *location's* timezone via `app.infra.timezones.safe_zone` — not a
  naive UTC date, which could be off by a day). `ForecastTides`
  (station id + upcoming high/low predictions) is the sprint-34-owned
  shape of `ForecastEnvelope.tides`, populated on success and left
  `None` with an advisory `Warning` on failure — there's no fallback
  substitute for tide predictions the way water temperature has a
  monthly average, so "unavailable" is the honest answer. Verified
  against a real running server: the `noaa_coops:tides` source
  correctly reports `unavailable` with a real "could not connect to
  api.tidesandcurrents.noaa.gov" detail in this sandboxed environment
  (upstream calls are blocked per `docs/R2_CI_BASELINE.md`), proving
  the degrade-gracefully path end-to-end, not just in mocked tests.
  `tests/test_assembly.py` covers presence, the local-date-window
  computation, and degrade-on-failure independently.

It does not yet have a Postgres connection. That lands in whichever
Phase 2 sprint or infra work adds it, behind its own characterization
tests, porting from the reconciliation audit
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

Every `/v1` route now requires ADR-004's internal request signature (see
"Status" above) and fails closed with a 500 if no signing key is
configured. Set matching values on both `apps/api` and `apps/web` for
local dev — anything works as long as both sides agree:

```bash
cd apps/api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # includes runtime deps + ruff/mypy/pytest/httpx
INTERNAL_SIGNING_KEY_ID=dev-key INTERNAL_SIGNING_KEY_SECRET=dev-secret-please-change \
  uvicorn app.main:app --reload --port 8000
```

Health checks don't require a signature:

```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
# both return {"status": "ok"}
```

`/v1` routes do — an unsigned request correctly gets a 401:

```bash
curl -i http://localhost:8000/v1/locations/search?q=wrightsville
```

See `apps/web/README.md` for the matching `apps/web` env vars and the
`/forecast/demo` page that exercises this end-to-end.

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
