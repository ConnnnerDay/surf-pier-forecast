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

It does not yet have the `/v1` routes, the ported forecast domain
*logic*, or a Postgres connection. Those land in the Phase 2 sprints
listed in the roadmap's sprint ledger (23 onward), each behind its own
characterization tests, porting from the reconciliation audit
([`docs/R1_RECONCILIATION_AUDIT.md`](../../docs/R1_RECONCILIATION_AUDIT.md))
rather than copying `v2/backend` or the legacy Flask app verbatim.

If you change a model in `app/domain/models.py`, its schema snapshot test
will fail — regenerate deliberately and review the diff:

```bash
python -m scripts.generate_schema_snapshots
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
