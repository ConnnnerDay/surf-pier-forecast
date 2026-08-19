> **Status: accepted as sprint 1 evidence** (see `docs/CANONICAL_ROADMAP.md`'s
> sprint ledger). Recovered verbatim from closed PR #309, which predates the
> canonical roadmap (R0) and was never merged. Content below is unchanged from
> that PR — it's a point-in-time audit (dated 2026-08-07) of the legacy Flask
> app, kept as historical record. Per the roadmap's source-of-truth order,
> anything below that conflicts with a later decision in
> `docs/CANONICAL_ROADMAP.md` or a merged PR is superseded by that later
> source — none was found on review. Complementary to, not a duplicate of,
> `docs/R1_RECONCILIATION_AUDIT.md` (which audits the merged `/v2` prototype,
> not the legacy Flask app this document covers).

# Sprint 1: Legacy application baseline

Audit date: 2026-08-07

Audited revision: `a59a5c6` (`main`)

Audit environment: Windows 11, Python 3.14.5

## Purpose

This document freezes the observable state of the Flask prototype before the
product rewrite begins. It is an inventory and characterization record, not a
proposal to repair or extend the legacy application. Later sprints can use it
to decide what behavior deserves characterization tests before extraction.

## Reproduce the audit

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy . --no-error-summary
```

On Windows, the documented dependency install cannot collect the full test
suite because `tzdata` is not declared. To continue the characterization run
without changing the repository, the audit environment used:

```powershell
.\.venv\Scripts\python.exe -m pip install tzdata
.\.venv\Scripts\python.exe -m pytest -q
```

## Baseline results

| Check | Result | Evidence |
| --- | --- | --- |
| Clean dependency install | Pass | `requirements-dev.txt` installed successfully. |
| Clean test collection on Windows | Fail | `tests/test_astro.py` and `tests/test_notifications.py` raise `ZoneInfoNotFoundError` because `tzdata` is absent. |
| Tests after temporary `tzdata` install | Fail | 1,489 passed, 8 failed, 53 warnings in 85.07 seconds. |
| Ruff | Fail | 602 findings; 433 reported as automatically fixable. |
| Mypy | Fail | 23 errors. |
| GitHub Actions | Present but currently expected to fail | CI runs Ruff, tests, formatting, and Mypy on Python 3.10-3.12. |

The eight application test failures are:

- `tests/test_app.py::TestBasicRoutes::test_shared_forecast_renders_full_dashboard_shell`
- `tests/test_astro.py::TestComputeTwilightTimes::test_returns_all_expected_keys`
- `tests/test_astro.py::TestComputeTwilightTimes::test_golden_hour_format`
- `tests/test_auth_security.py::test_index_allowed_once_profile_exists`
- `tests/test_auth_security.py::test_user_can_access_dashboard_after_registration`
- `tests/test_forecast.py::TestAstronomyExtras::test_twilight_contains_golden_windows`
- `tests/test_views.py::TestSharedForecast::test_logged_in_user_with_incomplete_profile_can_view_shared_link`
- `tests/test_views.py::TestRenderForecastBranches::test_stale_forecast_triggers_background_refresh`

The common Windows failure in dashboard and astronomy paths is use of the
POSIX-only `strftime("%-I:%M %p")` format. The shared-forecast test also makes
live external requests and observed an NWS 404 during this run, so the suite is
not fully hermetic.

## Current architecture

The application is a server-rendered Flask monolith:

1. `app.py` creates the Flask application, configures sessions and security
   middleware, registers blueprints, initializes SQLite, and starts background
   maintenance and notification threads.
2. `web/` owns authentication, HTML routes, JSON routes, request schemas, rate
   limiting, and OpenAPI generation.
3. `domain/forecast.py` coordinates external calls, condition scoring,
   timelines, personalization, recommendations, and final response assembly.
4. `domain/species.py` owns the species catalogue and most ranking, bait, rig,
   spawning, and technique logic.
5. `services/` contains public-data adapters and several enrichment feeds.
6. `storage/` contains SQLite access, forecast caching, regulation scraping,
   species loading, and image caching.
7. Jinja templates and hand-written JavaScript/CSS provide the browser UI.
8. Gunicorn plus systemd is the documented production deployment; mutable data
   is stored beneath the local `data/` directory.

### Size and coupling indicators

- 47 Flask route decorators across the three blueprints.
- 47 Python test modules and 1,497 collected tests after timezone data is
  available.
- 20 Jinja templates and 109 files under `static/`.
- `domain/species.py`: 4,287 lines.
- `domain/forecast.py`: 4,166 lines.
- `locations.py`: 2,348 lines.
- `storage/reg_scraper.py`: 1,427 lines.
- `storage/sqlite.py`: 1,234 lines.
- `services/arcgis_live_feeds.py`: 1,058 lines.
- `web/api.py`: 948 lines.

These counts are not defects by themselves. They identify seams that require
characterization before extraction because UI, persistence, network fallback,
and domain decisions currently cross module boundaries.

## Route catalogue

### HTML and account routes

- Public/session entry: `/welcome`, `/login`, `/register`, `/logout`.
- Account: `/account`, `/account/settings`, `/account/change-password`,
  `/account/delete`.
- Product pages: `/`, `/live-cams`, `/fishing-log`, `/profile`.
- Location setup: `/setup`, `/setup/search`, `/setup/coords`,
  `/setup/select/<location_id>`, `/setup/favorite/<location_id>`.
- Sharing: `/f/<location_id>`.

### JSON routes

- Forecasts: `/api/forecast`, `/api/v1/forecast`, status, outlook, solunar, and
  refresh endpoints.
- User data: preferences, profile, page layout, catch log, and catch patterns.
- Notifications: push subscription, public key, unsubscribe, and test send.
- Regulations: lookup and refresh.
- Enrichment: timezone, community activity, share text, environmental context,
  map cards, combined weather, and geo-environmental data.
- API description: `/api/openapi.json` and `/api/v1/openapi.json`.

The rewrite should not reproduce every route. Its initial public contract is
limited to location search/resolution, forecasts, refresh, preferences, and
health endpoints.

## External data inventory

| Provider | Current purpose | Primary implementation |
| --- | --- | --- |
| National Weather Service | Forecast zones, point/grid forecasts, observations, and alerts | `services/nws.py` |
| NOAA CO-OPS | Tides, currents, water temperature, environmental metrics, and station metadata | `services/noaa.py`, `services/stations.py` |
| NOAA NDBC | Buoy observations, wind, waves, pressure, and active-station metadata | `services/ndbc.py`, `services/stations.py` |
| NOAA/NCEI ArcGIS | Bathymetry | `services/bathymetry.py` |
| ArcGIS public feeds | Storm, environmental, river, fire, aviation, and map enrichment | `services/arcgis_live_feeds.py` |
| EPA Water Quality Portal | Water-quality enrichment | `services/datagov.py` |
| FAO services | Fisheries-area and species enrichment | `services/hdx_fao.py` |
| Local astronomy calculations | Sun, twilight, moon, and solunar values | `services/astro.py` |
| Wikipedia/Wikimedia/NOAA Fisheries | Species images | `storage/species_images.py` |

Core rewrite candidates are NWS, NOAA CO-OPS, NDBC, and the pure astronomy
calculations. ArcGIS, EPA, FAO, image lookup, regulations, notifications, live
cams, and catch logging are outside the first launch contract.

## Reusable logic requiring characterization

- Parsing of NWS, NOAA CO-OPS, and NDBC payloads, including missing-value
  markers and unit conversions.
- Nearest-station catalogue loading and distance calculations.
- Dynamic location identifiers, timezone selection, and curated-location
  fallbacks.
- Tide interpolation and chart-independent tide calculations.
- Sunrise, sunset, twilight, moon, and solunar calculations.
- Condition scoring and explanation generation, after product rules identify
  which inputs are defensible.
- Forecast cache freshness semantics, as a behavior reference rather than a
  storage implementation to preserve.

Authentication, SQLite access, daemon threads, Flask request/session coupling,
HTML rendering, local uploads, notification delivery, and the broad enrichment
surface are not rewrite foundations.

## Known risks and gaps

- A clean cross-platform development setup is not currently reproducible.
- CI quality gates are configured but the checked-in code does not pass Ruff,
  formatting/type expectations, or the full test suite in this audit.
- Some tests perform live network calls, making results dependent on upstream
  availability and current provider behavior.
- A valid upstream partial failure can become a page-level 500 in the current
  synchronous forecast/render path.
- Fallback values and measured values share the assembled forecast structure,
  which makes provenance and confidence difficult to communicate reliably.
- SQLite, local uploads, generated secrets, and in-process background threads
  assume a persistent single-host filesystem and do not fit the selected
  managed deployment architecture.
- Route breadth exceeds the agreed v1 product surface and increases the number
  of auth, privacy, accessibility, and degraded-mode paths that must be tested.
- Very large domain modules make it difficult to distinguish parsing,
  normalization, scoring, presentation, and fallback policy.

## Sprint 1 exit criteria

- [x] Architecture and deployment shape documented.
- [x] Routes and external providers catalogued.
- [x] Test, lint, and type-check commands reproduced from a clean environment.
- [x] Failures recorded without modifying legacy behavior.
- [x] Candidate reusable logic separated from explicitly deferred features.
- [x] No application code, dependencies, schemas, or runtime behavior changed.
