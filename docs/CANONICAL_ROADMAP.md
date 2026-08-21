# Surf & Pier Forecast canonical product roadmap

Last updated: 2026-08-17
Master tracker: [GitHub issue #318](https://github.com/ConnnnerDay/surf-pier-forecast/issues/318)

## Why this document exists

This is the durable contract for taking the broken Flask prototype to a
working online product. It is intentionally independent of any one AI chat.
Codex, Claude, a human developer, or another agent should be able to resume
work from this document and issue #318 without guessing what was agreed.

Several early sprint PRs were closed without being merged. Later PRs merged a
substantial `/v2`, but it uses a different frontend, authentication model,
database, request path, and deployment model. That merged code is valuable
implementation evidence; it is not permission to silently change the product
contract.

## Source-of-truth order

When sources disagree, use this order:

1. Explicit decisions in this document and master issue #318.
2. A later decision recorded in a merged decision-record PR and linked from
   issue #318.
3. Merged code and tests on `main`.
4. Closed or open PRs, archived plans, and previous AI conversations.

Changing a canonical decision requires a small decision-record PR and explicit
user approval. Updating code alone does not change the decision.

## Product contract

- Audience: recreational US surf and pier anglers — a public, general-
  audience product intended to grow beyond a private circle, not a personal
  or friends-only tool. Plan for real (if initially modest) public traffic.
- Experience: polished, marine-utility, mobile-first installable web product.
  Minimum support target: the latest two versions of Chrome, Safari, Firefox,
  and Edge, on both mobile and desktop. No legacy-browser support.
- Coverage: any valid US coastal coordinate, including Atlantic, Gulf,
  Pacific, Alaska, and Hawaii, at v1 launch — coverage breadth is part of the
  pitch and is not phased in regionally.
- Access: an account is required for the core forecast experience.
- First-run success: a new user can register, choose a coastal point, and
  quickly understand whether and when to fish.
- Reliability: a single upstream provider failure must not blank the forecast.
- Integrity: missing observations are never turned into invented measurements.
- Result vocabulary: `fresh`, `stale`, `partial`, and `unavailable` have
  documented, testable meanings.
- Data minimization: save resolved location identifiers and appropriately
  rounded coordinates rather than unnecessary precise location history.
- Cost: near-free is a target, but not at the expense of minute-long cold
  starts or disposable production data. Observability/analytics tooling
  should default to pragmatic, free-tier services rather than a named vendor
  commitment.
- Business model: v1 ships free. Don't build payment infrastructure or
  subscription legal pages yet, but don't foreclose a future low-price
  (~$1/month) subscription tier either. The expansion-decision sprints
  (61-63) own the actual call.
- Trust and liability: any fishing-regulations or legal-catch guidance must
  carry a clear "informational only — verify with official sources"
  disclaimer before it ships publicly. Treat the legacy 851-species/
  regulations dataset as provisional; the long-term intent is to source
  regulations data from official state/NOAA feeds rather than trust the
  inherited dataset indefinitely.
- Support: a minimal in-app feedback mechanism is part of the launch-
  readiness bar (sprint 50), not an optional add-on.
- Legacy app: the current self-hosted Flask app is retired outright,
  effective immediately, not kept running in parallel during the rewrite. No
  live accounts or catch data exist on it worth migrating.

### Deferred until production evidence supports them

Payments, advertising, native clients, social features, catch logging, live
cameras, notifications, passkeys, OAuth, a full regulations product, and the
full 851-species experience are not v1 launch requirements. Self-service data
export and account deletion (sprint 45) are the one carve-out from this list:
required at v1 launch, not deferred, because this is a public product
handling real accounts.

## Product decisions on record (2026-08-17)

These were made directly by the product owner (not inferred) and refine the
product contract above. Per the source-of-truth order, explicit decisions
recorded in this document are binding until superseded by a later decision
record; they do not reopen or contradict R0.

### Strategy and audience

- **Audience and scale.** Public, general-audience product intended to grow
  beyond a private circle. Plan for real public traffic, not a handful of
  known users.
- **Business model.** v1 is free, no payment processor or billing UI yet. The
  long-term intent is a ~$1/month subscription tier once usage justifies it;
  keep the account model tier-friendly without building billing now.
- **Growth.** No hard launch deadline. Growth starts organic (SEO, shareable
  forecast pages, word of mouth), but the product owner is willing to invest
  in paid acquisition or other growth spend later — keep that option live in
  the expansion-decision sprints.
- **Timeline.** No hard deadline; work the gates/sprints at a sustainable
  pace rather than compressing for speed.
- **Hosting budget.** No fixed ceiling stated, but the ~$1/month target price
  implies real cost-consciousness. Default to free/low-cost tiers where
  reasonable and surface costs as they come up.

### Legacy app

- The current self-hosted Flask app is retired outright, effective
  immediately. No real accounts or catch-log data exist on it worth
  migrating or communicating a sunset to.
- The merged `/v2` React/Vite prototype remains reference material for the R1
  reconciliation audit only, per the existing canonical contract.

### Trust, legal, and data sourcing

- Any regulations/legal-catch guidance must carry a clear "informational
  only — verify with official sources" disclaimer before shipping publicly.
  Hard requirement, not a nice-to-have.
- The legacy 851-species/regulations dataset is provisional. Long-term intent
  is to source regulations data from official state/NOAA feeds rather than
  trust the inherited hand-built dataset indefinitely; acceptable as a
  stopgap only with the disclaimer above.
- Self-service data export and account deletion (sprint 45) are required at
  v1 launch, not deferred.

### Product surface decisions

- **Branding.** Name, domain, and visual identity are all still open. Sprint
  27 needs an explicit branding decision as a prerequisite. No brainstorming
  has happened yet — deliberately deferred to a later pass.
- **Device/browser support.** Latest two versions of Chrome, Safari, Firefox,
  Edge; mobile and desktop; no legacy-browser support.
- **Offline/PWA depth.** Full app-shell navigation should work offline with
  graceful degradation (sprint 38), not just "show the last cached forecast."
- **Abuse hardening.** Public registration needs bot/CAPTCHA defense as a v1
  requirement (sprints 28/44), not just rate limiting and email verification.
- **Support channel.** A minimal in-app feedback widget is part of the
  launch-readiness bar (sprint 50), not a post-launch add-on.
- **Feature priority after v1.** None of the currently-deferred features
  (notifications, catch logging, OAuth/passkeys, full species DB, full
  regulations product, social, native apps, live cameras) are prioritized
  over nailing the core forecast experience first.
- **Beta recruiting (sprint 51).** Recruit the initial cohort through the
  product owner's personal network and local surf/pier fishing communities,
  not a fully public open beta.
- **Observability/analytics tooling (sprints 41-43).** No named vendor
  preference — choose pragmatic, free-tier-friendly tools rather than
  spending a sprint deciding between options.
- **Testing/CI rigor.** Keep the roadmap's definition-of-done as written in
  full (accessibility passes, Lighthouse budgets, load tests, security
  hardening) — not scaled back, since the audience is public, not private.

### Team and process

- Sole human reviewer/approver for the foreseeable future, with AI agents
  doing implementation work under the existing AI-review-plus-human-approval
  gate. Document the PR/roadmap process well enough that a future
  collaborator could onboard without extra chat context, since collaborators
  may be added later.

## Product decisions on record (2026-08-17, round 2)

- **Positioning.** "Surfline, but for fishing" — a polished conditions/
  forecast product purpose-built around "should I fish right now," not a
  general-purpose fishing social app.
- **Go/no-go presentation.** Lead the dashboard with a simple traffic-light
  style (color + short label); numeric score and narrative detail are
  supporting, expandable information, not the headline.
- **Voice.** Clean, utility-first, data-forward. Closer to a marine
  instrument than a lifestyle/social brand — not casual angler-buddy copy.
- **Naming.** "Surf & Pier Forecast" is a placeholder only. The product name
  itself, not just the visual identity, is open to a full rename during the
  branding decision ahead of sprint 27.
- **Paid-tier mechanism (informative, not yet built).** The most likely
  ~$1/month lever is saved-location count: free tier is capped at **1**
  saved (home) location; more locations is the paid unlock. Sprint 37
  (saved locations) should build the ownership model so a cap/limit field is
  natural to add later, without committing to billing now.
- **Species photos.** Defer together with the rest of the species-scoring
  feature — not worth decoupling for an early v1 win.
- **Freshness TTL.** Keep the legacy app's 4-hour cache window as the v1
  target for sprint 24 (snapshot caching); revisit only if real usage shows
  it's wrong.
- **Shareable forecast links.** Pull forward as a v1-required feature (not
  just "later in phase 4"), since public non-personal forecast pages
  (sprint 49) are direct organic-growth/SEO surface and growth is a stated
  priority.
- **Internationalization.** English-only content at v1, but build content/
  copy in an i18n-ready structure (externalized strings, no hard-coded
  English in logic) from the start, so Spanish-language support — relevant
  given Gulf Coast and other US coastal Hispanic angling communities — isn't
  a rewrite later. Actual Spanish translation stays out of v1 scope.
- **Data-quality validation.** No personal regional expertise to anchor
  testing on; rely on official upstream sources and general QA across all
  launched coasts equally rather than a single home-region deep check.
- **Alert channel (forward-looking).** Whenever notifications/alerts are
  eventually built, prioritize web push over email, fitting the installable
  mobile-first PWA product — this doesn't change notifications' deferred
  status for v1.

## Product decisions on record (2026-08-17, round 3)

- **What's actually broken today.** The product owner rates the current app
  as roughly equally broken across performance/reliability, UI/UX, and data
  quality — no single dimension is the standout problem. R1's reconciliation
  audit and the phase 1-3 sprints should treat this as a full rebuild, not a
  targeted patch of one weak layer.
- **Open source.** Not an active decision. Repo visibility (public vs.
  eventually private) stays as-is; revisit only if it becomes relevant later
  rather than deciding preemptively.
- **Success target.** Directional goal for 6-12 months post-launch: low
  thousands of active users — meaningful public traction, enough to
  seriously validate the ~$1/month tier as real revenue, not just a proof of
  concept. This is a directional target for calibrating sprints 57-59, not a
  committed metric.

## Canonical technical contract

| Area | Required product architecture |
|---|---|
| Repository | Monorepo with `apps/web`, `apps/api`, and generated shared OpenAPI schemas |
| Web | Next.js, mobile-first, deployed to Vercel |
| Browser path | Browser calls the Next.js backend-for-frontend only |
| Internal path | Next.js authenticates the user and signs internal FastAPI requests |
| API | FastAPI with versioned `/v1` endpoints, deployed on an always-on entry Render service |
| Authentication | Better Auth email/password, verification, reset, secure HTTP-only cookies, PostgreSQL sessions |
| Database | Fresh PostgreSQL on pooled Neon; no legacy account or catch-data migration |
| Background work | No Redis and no job queue in v1 |
| Forecast core | Port Python logic only after characterization tests capture defensible behavior |
| Availability | Bounded provider calls, independent source results, immutable snapshots, documented stale fallback |

### Required API surface

- `GET /v1/locations/search?q=`
- `POST /v1/locations/resolve`
- `GET /v1/forecasts/{location_id}`
- `POST /v1/forecasts/{location_id}/refresh`
- `GET /v1/me/preferences`
- `PATCH /v1/me/preferences`
- `GET /health/live`
- `GET /health/ready`

The forecast envelope contains location, generated time, freshness, source
status, confidence, conditions, tides, hourly outlook, recommendations, and
structured warnings.

## Current repository reality

| Subsystem | Canonical | Merged `/v2` reality | Required disposition |
|---|---|---|---|
| Frontend | Next.js BFF | React/Vite PWA calling the API | Audit reusable UI; establish Next.js path |
| Authentication | Better Auth cookie sessions | Custom JWT plus OAuth/passkeys/2FA | Replace core auth; defer non-v1 extras |
| Database | PostgreSQL/Neon | SQLite-oriented implementation | Design and migrate to fresh PostgreSQL |
| Deployment | Vercel + Render + Neon | Self-host/placeholder assumptions | Add canonical environments and runbooks |
| API | Versioned FastAPI | FastAPI prototype exists | Keep or adapt only after contract audit |
| Providers and scoring | Characterized Python core | Large legacy port exists | Keep candidates that pass fixture-based tests |
| End-to-end tests | Deterministic launch journey | Some tests depend on live upstreams | Replace live CI dependence with controlled fixtures |

Closed PRs [#309](https://github.com/ConnnnerDay/surf-pier-forecast/pull/309)
through [#313](https://github.com/ConnnnerDay/surf-pier-forecast/pull/313)
are unmerged reference material. Merged PRs
[#314](https://github.com/ConnnnerDay/surf-pier-forecast/pull/314) through
[#317](https://github.com/ConnnnerDay/surf-pier-forecast/pull/317) explain the
current `/v2` code, but do not override this contract.

## Recovery gates (must happen before new features)

| Gate | Outcome | Acceptance evidence | State |
|---|---|---|---|
| R0 | Durable canonical roadmap and cross-agent handoff | This document, `AGENTS.md`, Claude warning, master issue, merged PR | Complete — merged in PR #319 |
| R1 | Reconciliation audit | Every `/v2` route, module, schema, feature, and test mapped to keep/adapt/replace/defer with reasons and owning future sprint | Complete — merged in PR #322 |
| R2 | Truthful deterministic CI baseline | Exact current commands recorded; live-provider tests removed from required CI; failures classified as regression or known debt | Complete — merged in PR #323 |
| R3 | One canonical application path | Next.js/FastAPI/PostgreSQL skeleton is the named path; duplicate prototypes are clearly archived/reference-only; local smoke path is documented | Complete — merged in PR #324 |

No gate may be marked complete until its PR is merged to `main` and linked in
issue #318.

**All recovery gates (R0-R3) are complete.** Numbered product sprints
resume — see the live checkpoint below for the exact next action.

## Sprint ledger

Status meanings:

- **Not accepted**: no merged evidence satisfies the canonical outcome.
- **Candidate in `/v2`**: code may help, but R1 must map it and the numbered
  sprint still needs canonical acceptance evidence.
- **Deferred**: deliberately outside the v1 launch contract.
- **Complete**: only a merged PR with linked checks can earn this state.

None of the numbered sprints below is currently marked complete. That is
deliberately conservative: closed PR work and divergent merged code are inputs
to reconciliation, not proof that the agreed outcome passed.

### Phase 1 — Establish the rewrite

| Sprint | Outcome | Acceptance focus | Current state |
|---:|---|---|---|
| 1 | Repository baseline | Reproducible legacy audit, routes, sources, failures, reusable modules | **Complete** — `docs/baseline-audit.md` (this PR), recovered verbatim from closed PR #309 and reviewed for conflicts with later decisions; none found |
| 2 | Product definition | Journey, non-goals, metrics, vocabulary, attribution | **Complete** — `docs/product-definition.md` (this PR), recovered verbatim from closed PR #310; refined, not contradicted, by the later "Product decisions on record" sections above |
| 3 | Architecture decision | Boundaries, request path, records, hosting, data lifecycle | **Complete** — `docs/architecture.md` (this PR), recovered verbatim from closed PR #311 except one reconciliation note: its `pnpm`/`uv` tooling choice is superseded by the already-merged `npm`/`pip` tooling in `apps/web`/`apps/api` |
| 4 | Monorepo scaffold | Clean Next.js/FastAPI install, build, and smoke test | **Complete** — `apps/web`/`apps/api` skeletons (PR #324) plus their CI build/smoke-test evidence (PR #326) |
| 5 | Local developer workflow | One documented setup/run/check path from a fresh machine | **Complete** — `apps/setup.sh` and `apps/check.sh` (PR #327), verified end-to-end from a genuinely clean state (no `.venv`/`node_modules` present beforehand) |
| 6 | Quality gates | Frontend and Python lint, type, test, and intentionally failing proof | **Complete** — real lint/type/test checks for `apps/` (PR #326), plus the intentionally-failing proof (PR #329): a scratch branch that deliberately broke all five `apps-ci.yml` checks, pushed to observe a real red CI run. It was briefly and accidentally merged (see `docs/SPRINT_6_CI_PROOF.md`'s "Incident" section) and reverted in PR #330 — doesn't invalidate the proof itself, but is the direct motivation for sprint 7 |
| 7 | PR governance | Sprint/PR templates, ownership, dependency policy, AI review contract | **Complete** — `.github/pull_request_template.md` and `docs/PR_GOVERNANCE.md` (PR #331), consolidating ownership, one-PR-per-sprint policy, dependency policy, the AI review contract, and a branch-hygiene rule written directly from the sprint-6 incident |
| 8 | CI foundation | Checks, secret scan, dependency audit, builds | **Complete** — `apps/` build/lint/type/test checks (PR #326); this PR adds `.github/workflows/security.yml`: repo-wide gitleaks secret scan (with a documented `.gitleaksignore` for two verified false positives) and `pip-audit`/`npm audit` dependency checks for `apps/api`/`apps/web` (both currently clean) |
| 9 | Preview environments | Isolated web/API previews with URL and curl evidence | Not accepted |
| 10 | Production skeleton | Vercel, always-on Render, pooled Neon connectivity and environment separation | Not accepted |

### Phase 2 — Build a trustworthy forecast core

| Sprint | Outcome | Acceptance focus | Current state |
|---:|---|---|---|
| 11 | Canonical domain model | Typed models, schema snapshots, serialization round trips | **Complete** — `apps/api/app/domain/models.py` (fresh Pydantic models per `docs/architecture.md`'s ADR-003, not ported from `v2/backend/app/schemas/`, which are auth-coupled DTOs per `docs/R1_RECONCILIATION_AUDIT.md`); schema-snapshot + round-trip tests in `apps/api/tests/test_domain_models.py`, drift-detection verified locally before commit |
| 12 | HTTP client policy | Timeouts, bounded retries, user agent, size limit, structured errors | **Complete** — `apps/api/app/infra/http_client.py` (`BoundedHTTPClient` per `docs/architecture.md`'s ADR-003, provider-agnostic); `apps/api/tests/test_http_client.py` covers success, retries, retry exhaustion, no-retry-on-4xx, timeouts, connection errors, and oversized responses via `httpx.MockTransport` |
| 13 | NWS adapter | Typed weather, wind, alerts, and grid contract fixtures | **Partially complete** — `apps/api/app/providers/nws.py`: marine-zone wind/wave/direction parsing and fetch, point/state active-alerts parsing and fetch, **plus the gridpoint wind fallback** (`fetch_gridpoint_wind`/`parse_gridpoint_wind`, wired into `app/domain/assembly.py` as a last-resort wind source, fetched only when both primary wind sources fail), all behind `apps/api/tests/test_nws_provider.py`/`test_assembly.py` fixture tests. Current-weather observations (air temp, humidity, heat index, precipitation) remain deliberately deferred — nothing in the required `ForecastConditions` shape names them, so porting would be inventing product scope |
| 14 | NOAA CO-OPS adapter | Tide/water-temperature fixtures, missing values, DST | **Partially complete** — `apps/api/app/providers/noaa_coops.py`: water temperature and tide predictions, `zoneinfo`-based DST-safe timestamp parsing, missing-value/empty-row handling, **plus the CO-OPS wind fallback** (`fetch_coops_wind`, wired into `app/domain/assembly.py` as the second step of the last-resort wind chain, after marine-zone/buoy and before NWS gridpoint — the legacy priority order), all behind `apps/api/tests/test_noaa_coops_provider.py`/`test_assembly.py`. Currents, environmental metrics, and the tide-chart SVG helper remain deliberately deferred — nothing in the required `ForecastConditions` shape or product-definition's dashboard hierarchy names them |
| 15 | NDBC adapter | Buoy parsing, missing columns and markers | **Partially complete** — `apps/api/app/providers/ndbc.py`: wind/wave/pressure parsing from the fixed-width `realtime2` feed (`parse_realtime_text`) and fetch (`fetch_buoy_observation`), distinguishing missing columns from missing-value markers, behind `apps/api/tests/test_ndbc_provider.py`. `app/infra/http_client.py`'s `BoundedHTTPClient` gained `get_text` for this (NDBC is plain text, not JSON). Pressure-trend/fishing-impact narrative deliberately deferred to the scoring sprint (35) — see module docstring |
| 16 | Astronomy adapter | Pure deterministic coast/season/timezone tests | **Complete** — `apps/api/app/providers/astronomy.py`: sunrise/sunset, twilight, lunar details, and solunar periods, all pure math (no network). Typed timezone-aware `datetime`s instead of formatted strings; enums for moon phase/rating. `apps/api/tests/test_astronomy_provider.py` covers Atlantic/Pacific coasts, summer/winter seasons, a non-DST timezone, and polar-latitude clamping |
| 17 | Station catalog | Provenance, timestamps, idempotent refresh | **Complete** — `apps/api/app/providers/stations.py`: CO-OPS tide/water-temp and NDBC catalog fetch (degrades to `[]`, metadata not a reading), pure nearest-station distance ranking, and `StationCatalogCache` — an explicit, injectable-clock, idempotent TTL cache, tested without sleeping in `apps/api/tests/test_stations_provider.py` |
| 18 | Coastal coordinate validation | Inland/out-of-range rejection and all-coast boundaries | **Complete** (using only sprint 17's inputs — see the sprint's checkpoint entry for the curated-location fallback deferred to sprint 19) — `apps/api/app/providers/coastal_bounds.py`: `is_valid_coordinate`, `classify_coast_region` (bounding boxes for Atlantic/Gulf/Pacific/Alaska/Hawaii), and `gate_coastal_point` (the real inland-rejection mechanism, ported from `locations.py`'s `_DYN_GATE_MILES`), tested in `apps/api/tests/test_coastal_bounds.py` |
| 19 | Location resolution | Timezone, zone, tide/temp station and buoy golden tests | **Complete** — `apps/api/app/providers/locations.py`: curated 101-spot dataset (`app/data/coastal_locations.json`/`water_temps.json`, `ast.literal_eval`-extracted from the legacy source, a documented mechanical move), `find_nearest_locations`, `timezone_for_point`, `resolve_dynamic_location` (composes sprint 17's station catalogs), golden-tested in `apps/api/tests/test_locations_provider.py` against Montauk NY/Wrightsville Beach NC/Poipu HI |
| 20 | Observation normalization | Canonical units/times with raw provenance retained | **Complete** — `apps/api/app/domain/normalize.py`: wraps sprints 13-15's provider outputs into `Observation` (canonical `kt`/`ft`/`degF`/`mb` units, provider/station/observed_at retained); NWS forecast ranges become an `ObservationRange` (paired low/high) rather than a collapsed value; astronomy and categorical fields (wind direction) deliberately not wrapped; tested in `apps/api/tests/test_normalize.py` |
| 21 | Forecast assembly | Independent sources and every present/absent matrix | **Complete** — `apps/api/app/domain/assembly.py`: concurrently fans out to NWS/NOAA CO-OPS/NDBC + astronomy, assembles `ForecastEnvelope` and designs `conditions`; water-temperature fallback-to-monthly-average finally lands (deferred since sprint 14); `apps/api/tests/test_assembly.py` exercises all 8 present/absent combinations of the 3 fallible sources |
| 22 | Forecast scoring | Defensible stable score components with explanations | **Complete** — `apps/api/app/domain/scoring.py`: `score_conditions`, the 0-100 go/no-go index ported from `domain/forecast.py`, scores wind/wave/direction/water-temp/dawn-dusk/solunar factors with a sorted, plain-language summary; deliberately decoupled from sprint 21's `ForecastConditions` (takes a pre-reconciled `wind_range`/`wave_range`); tide, fishing-type/preference, and water-quality bonuses deliberately deferred (sprints 34/36, still-open product question); tested in `apps/api/tests/test_scoring.py` |
| 23 | Confidence model | Predictable degradation by availability, distance, age, fallback | **Complete** — `apps/api/app/domain/confidence.py`: `assess_confidence`, a 100-point four-factor score (source coverage, observation age, station distance, fallback use) with no legacy precedent, replacing sprint 21's interim liveness-only stub; each degrading factor reports an independent reason code; tested in `apps/api/tests/test_confidence.py` |
| 24 | Snapshot caching | Fresh/stale hit, miss, expiry, fallback, concurrency; target 4-hour freshness window, matching legacy cadence | **Complete** — `apps/api/app/infra/snapshot_cache.py`: `SnapshotCache[T]`, an injectable-clock, per-key, single-flight in-memory cache; `fresh_ttl_seconds` defaults to 4h matching legacy cadence, `stale_ttl_seconds` + fallback-on-fetch-failure are new (no legacy precedent); tested in `apps/api/tests/test_snapshot_cache.py` |
| 25 | Versioned API | Required endpoints, OpenAPI contract and breaking-change guard | **Complete** — `apps/api/app/api/v1/{locations,forecasts}.py`: 4 of 6 required endpoints (`GET`/`PATCH /v1/me/preferences` deliberately deferred — need Better Auth + Postgres, neither exists yet); `app/api/deps.py`'s `AppState` (lifespan-managed `BoundedHTTPClient` + station-catalog caches) backs both routers; `tests/openapi_snapshot.json` + `scripts/generate_openapi_snapshot.py` is the breaking-change guard, mirroring sprint 11's schema-snapshot pattern; verified against a real running `uvicorn` server, not just `TestClient`; tested in `apps/api/tests/test_locations_router.py`/`test_forecasts_router.py`/`test_openapi_snapshot.py` |
| 26 | Performance budget | Bounded parallel calls, no duplicates, warm p95 under 750 ms | **Complete** — `BoundedHTTPClient` now sets explicit `httpx.Limits` (bounded parallel calls); `tests/test_performance_budget.py` proves no-duplicates under real concurrent HTTP requests (`asyncio.gather` + `httpx.ASGITransport`) and measures warm p95 ≈ 2.8ms against the 750ms budget. Cold-path latency against live upstreams is untestable in this sandboxed CI environment — not part of this sprint's stated bar, but flagged as open evidence for whenever this runs with live network access |

### Phase 3 — Create the mobile product

| Sprint | Outcome | Acceptance focus | Current state |
|---:|---|---|---|
| 27 | Design system | Gallery at phone/desktop widths and accessible primitives; branding decided first (name included, not just visual identity — "Surf & Pier Forecast" is a placeholder); clean/utility-first tone, Surfline-for-fishing positioning | **Partially complete** — **product owner directed proceeding under the placeholder identity rather than blocking on a final branding decision.** `apps/web/app/globals.css` (Tailwind v4 `@theme` tokens, light/dark) and `app/components/ui/` (`Button`/`Card`/`Badge`/`Field`/`Container`) replace `v2/frontend`'s flagged `.button`/`.card`/`.field` global-CSS classes (`docs/R1_RECONCILIATION_AUDIT.md` §3.2) with real accessible primitives; `app/page.tsx` is the gallery page at phone/desktop widths. i18n-ready string externalization (bundled into this sprint by R1's disposition) and formal WCAG 2.2 AA verification (sprint 40's job) remain open |
| 28 | Authentication | Email/password lifecycle, session rotation, bot/CAPTCHA defense on registration, abuse tests | Divergent auth exists; replace/adapt |
| 29 | Account-required routing | Public exceptions and authorization/redirect tests | Candidate in `/v2` |
| 30 | Onboarding shell | Mobile recording from registration to dashboard | Candidate in `/v2` |
| 31 | Location search | Text, device, map, station preview, denial/ambiguity tests | Candidate in `/v2` |
| 32 | Dashboard hierarchy | Go/no-go as a simple traffic-light headline (score/narrative expandable, not primary); best window, conditions, confidence, freshness first | Candidate in `/v2` |
| 33 | Conditions experience | Full/partial/stale/unavailable source-attributed snapshots | Candidate in `/v2` |
| 34 | Tides and timing | Accessible charts, text alternatives, timezone/DST tests | Candidate in `/v2` |
| 35 | Fishing guidance | Limited supported suggestions; every recommendation explains why | Existing broad feature is out of scope; adapt |
| 36 | Preferences | Units, thresholds, style, default location persistence | Candidate in `/v2` |
| 37 | Saved locations | Ordered favorites, ownership, duplicates, deletion, empty state; ownership model built so a free-tier cap (target: 1 location) is a natural later addition, without building billing now | Candidate in `/v2` |
| 38 | PWA baseline | Installable shell with full offline navigation and graceful degradation, not just last-cached-forecast viewing; authenticated forecasts not cached forever | Candidate in `/v2` |
| 39 | Responsive polish | Layout shift, assets, tap targets, Lighthouse, screenshot budgets | Not accepted |
| 40 | Accessibility pass | WCAG 2.2 AA, axe plus keyboard/screen-reader evidence | Not accepted |

### Phase 4 — Make it operable and launch it

| Sprint | Outcome | Acceptance focus | Current state |
|---:|---|---|---|
| 41 | Structured observability | One request trace across web/API/sources with safe context | Not accepted |
| 42 | Error monitoring | Frontend/API releases, source maps and secret redaction | Not accepted |
| 43 | Privacy-safe analytics | Registration, resolution, forecast state, latency, return use | Not accepted |
| 44 | Security hardening | CSP, CSRF, signed internal API, brute force, headers, threat model | **Partially complete** — the signed-internal-API piece's verification primitive is built (`apps/api/app/infra/internal_signature.py` + `app/api/internal_auth.py`, ADR-004's HMAC-SHA-256 contract, ports nothing since there's no legacy precedent) but deliberately not wired onto the `/v1` routers yet — see the module docstrings. CSP, CSRF, brute-force defense, and security headers remain candidate pieces; not accepted |
| 45 | Privacy and deletion | **Required for v1 launch** (public product, real accounts): self-service export and account deletion/anonymization proof; legal pages | Candidate in `/v2`; not accepted |
| 46 | Database resilience | Migrations, constraints, indexes, pooling, backups, blank restore drill | Not accepted |
| 47 | Degraded-mode UX | Database/API/email/upstream chaos yields actionable UI | Not accepted |
| 48 | Release controls | Promotion, migration gate, smoke, rollback and staging drill | Not accepted |
| 49 | SEO and sharing | **Required for v1**, not just later phase-4 sequencing: public non-personal forecast pages (organic-growth surface); private dashboards | Not accepted |
| 50 | Launch readiness | Cross-device, load, security, a11y, restore, outage evidence | Not accepted |
| 51 | Limited beta | Small angler cohort recruited via personal network and local fishing communities (not a public open beta); severity/reproduction report | Not accepted |
| 52 | Public-launch runbook | Owners, freeze rules, alerts, go/no-go and rollback triggers | Not accepted |
| 53 | Production promotion | Validated release promoted through the release controls | Not accepted |
| 54 | Production smoke suite | Account, location, forecast and degraded-path smoke evidence | Not accepted |
| 55 | First-hour observation | Live error, latency, source and database health review | Not accepted |
| 56 | First-24-hour review | Health report and only launch-blocking remediation | Not accepted |
| 57 | Reliability baseline | Actual p50/p95, upstream failure and forecast completion recorded | Not accepted |
| 58 | Signup-funnel baseline | Registration and onboarding completion recorded; directional 6-12 month target is low thousands of active users | Not accepted |
| 59 | Return-usage baseline | Privacy-safe early return-use evidence recorded | Not accepted |
| 60 | Highest-impact reliability fix | One measured gap fixed with before/after evidence | Not accepted |
| 61 | Expansion option studies | Recommendations, alerts, catches, regulations, native apps compared; leading candidates per owner intent are a ~$1/month subscription tier and paid growth acquisition | Not accepted |
| 62 | Expansion decision record | Usage, value, risk, cost and architecture decision proposed | Not accepted |
| 63 | Expansion commitment | User-approved next investment recorded as a new roadmap | Not accepted |

Sprints 52-63 deliberately decompose the former broad public-launch,
post-launch-review, and expansion-decision work into mergeable, auditable
steps. They do not authorize any expansion feature by themselves.

## Definition of ready for every sprint

Before implementation begins, the issue must state:

- one outcome and explicit non-goals;
- dependencies and confirmation that their PRs are merged;
- exact acceptance criteria and test commands;
- API/schema/migration impact;
- security, privacy, accessibility, failure-mode, and observability impact;
- expected UI evidence where relevant.

## Definition of done and AI verification contract

Every sprint is exactly one small PR. Each PR includes:

- captured automated check results;
- mobile screenshots or recordings for visible changes;
- OpenAPI diff for interface changes;
- forward and rollback notes for database changes;
- an AI review for correctness, edge cases, scope growth, weak tests,
  secrets, and backward incompatibility;
- a second AI pass after fixes;
- resolution or evidence-backed rejection of every blocking finding;
- roughly no more than 400 changed implementation lines, excluding generated
  code, fixtures, and documented mechanical moves.

Do not begin a dependent sprint until the current PR is merged. A commit on a
local branch, a pushed branch, or an open PR is not done.

## Cross-agent handoff protocol

Before switching from Codex to Claude, Claude to Codex, or to a human:

1. Merge or explicitly abandon the current PR. Never leave its status implied.
2. Update the checkpoint below in the same PR when possible.
3. Update issue #318 with the last merged PR and check results.
4. Record blockers and decisions with evidence, not chat references.
5. Name one exact next action and its non-goals.

### Restart instructions for the next agent

1. Fetch and switch to the latest `main`.
2. Read `AGENTS.md`, this document, and issue #318.
3. Confirm that the checkpoint's last merged PR is actually in `main`.
4. Inspect related code and tests before proposing a change.
5. Create a branch for only the named gate/sprint.
6. Stop if a new product decision is required; record and ask rather than
   silently choosing a different architecture.

## Live checkpoint

- Last merged PR: #357 (sprint 44 partial, ADR-004 signed-internal-API
  verification primitive, `d713d86`, merged as `57dc794`). Note: that
  merge's `api-lint` check failed on a ruff patch-version bump
  (0.16.3 -> 0.16.4 changed the implicit default rule set) — fixed in a
  follow-up commit on PR #358 (sprint 27) rather than left as flagged
  debt; see that PR's history for the fix.
- **All recovery gates (R0-R3) are complete.** Phase 1 sprints complete:
  1-3 (#333), 4 (#326), 5 (#327), 6 (#329 + #330 revert), 7 (#331), 8
  (#332). Phase 1's only remaining items (9, 10) need external accounts —
  see below.
- **Phase 2** started with **sprint 11** (canonical domain
  model, #334): `apps/api/app/domain/models.py` — fresh Pydantic models
  (`Location`, `Observation`, `SourceStatus`, `Confidence`, `Warning`,
  `ForecastEnvelope`) per `docs/architecture.md`'s ADR-003, not ported
  from `v2/backend/app/schemas/` (those are auth-coupled DTOs per
  `docs/R1_RECONCILIATION_AUDIT.md`). `ForecastEnvelope.conditions`/
  `tides`/`hourly_outlook`/`recommendations` are deliberately left as
  opaque `dict[str, Any] | None` — designing those shapes belongs to the
  sprints that own them (21, 22, 34, 35), each behind characterization
  tests, not invented here without evidence. `apps/api/tests/
  test_domain_models.py` covers serialization round trips and JSON
  schema-snapshot drift detection (snapshots committed under
  `apps/api/tests/schema_snapshots/`); drift detection verified locally
  by deliberately adding a field, confirming the test failed, then
  reverting — not just assumed to work. `apps/api/scripts/
  generate_schema_snapshots.py` regenerates snapshots after a deliberate
  model change.
- This PR continues Phase 2 with **sprint 12** (HTTP client policy):
  `apps/api/app/infra/http_client.py` — `BoundedHTTPClient`, an async
  context manager over `httpx.AsyncClient` per `docs/architecture.md`'s
  ADR-003 ("bounded concurrency, explicit timeouts, limited retries, and
  response-size limits"). Explicit connect/read/write/pool timeouts;
  bounded exponential-backoff retries limited to transient failures
  (connection errors, timeouts, 429/502/503/504 — never 4xx, which won't
  succeed on retry); a response-size limit enforced by streaming and
  counting bytes rather than trusting `Content-Length`; a fixed
  identifying `User-Agent`; structured `ProviderError` subclasses
  (`ProviderConnectionError`, `ProviderTimeoutError`,
  `ProviderHTTPStatusError`, `ProviderResponseTooLargeError`) instead of
  leaking raw `httpx` exceptions. Has no knowledge of any specific
  provider's payload shape — every future provider adapter (NWS, NOAA
  CO-OPS, NDBC — sprints 13-16) is built on this. `apps/api/tests/
  test_http_client.py` covers success, retry-then-succeed on 503 and on
  timeout, retry exhaustion raising `ProviderHTTPStatusError`, no retry
  on 404, `ProviderTimeoutError`, `ProviderConnectionError`, and
  oversized-response rejection — all via `httpx.MockTransport`, no live
  network calls, per the R2 no-live-provider-dependence rule.
  `apps/api/pytest.ini` gained `asyncio_mode = auto` for the new
  `pytest-asyncio`-based async tests. All of `apps/api`'s checks (ruff,
  ruff format, mypy, pytest — 24 passed) pass clean.
- This PR continues Phase 2 with **sprint 13** (NWS provider adapter,
  partial — see the sprint-ledger row for the exact deferral):
  `apps/api/app/providers/nws.py` — marine-zone wind/wave/direction
  parsing (`parse_marine_zone_conditions`) and fetch
  (`fetch_marine_zone_conditions`, propagates `ProviderError` since
  marine conditions are decision-relevant), plus point and state
  active-alerts parsing (`_parse_alerts`, unifying the legacy module's
  two near-duplicate GeoJSON/JSON-LD parsers) and fetch
  (`fetch_point_alerts`/`fetch_state_alerts`, catch `ProviderError` and
  return `[]` since alerts are non-critical enrichment — matching the
  legacy resilience posture). Ported from `services/nws.py` behind
  `apps/api/tests/test_nws_provider.py`'s fixture-based characterization
  tests (abbreviated/spelled-out wind direction, "around Nft" phrasing,
  min/max across the first 3 periods, missing fields, both alert JSON
  shapes, truncation/limit behavior, and fetch-layer error handling via
  `httpx.MockTransport`) rather than a verbatim carry-over. Gridpoint
  wind fallback and current-weather observations (heat index, recent
  precipitation) are deliberately deferred — see the module docstring —
  to keep this PR reviewable; tracked as follow-up before sprint 21
  (forecast assembly) needs them. All of `apps/api`'s checks (ruff, ruff
  format, mypy, pytest — 38 passed) pass clean.
- This PR continues Phase 2 with **sprint 14** (NOAA CO-OPS adapter,
  partial — see the sprint-ledger row): `apps/api/app/providers/
  noaa_coops.py` — `fetch_water_temperature` and `fetch_tide_predictions`,
  both raising `NoaaDataUnavailableError` (a `ProviderError` subclass) on
  a valid response with no usable reading, since both are decision-relevant
  (unlike sprint 13's non-critical alerts). The legacy module's
  silent-failure/monthly-average-fallback (`get_water_temp`) is
  deliberately not ported — deciding when to fall back, and recording it
  on `Observation.is_fallback`, is forecast-assembly's job (sprint 21),
  not a single provider adapter's. CO-OPS timestamps are in
  station-local `lst_ldt` time; parsing uses `zoneinfo.ZoneInfo`, which
  (unlike `pytz`) resolves the correct UTC offset via a plain
  `.replace(tzinfo=...)`, verified in `apps/api/
  tests/test_noaa_coops_provider.py` by asserting the correct UTC offset
  on both sides of a DST transition (EDT vs. EST) and that parsing a
  timestamp during the fall-back fold doesn't raise. Also covers missing
  data rows/values (empty `data`/`predictions` lists, missing `v` or
  `t` fields) raising or being skipped rather than silently substituting
  a placeholder, unlike the legacy module's noon-hour fallback quirk.
  Wind/currents/environmental-metrics fetches and the `build_tide_
  chart_svg` rendering helper (not a provider concern) are deliberately
  deferred — see the module docstring. All of `apps/api`'s checks (ruff,
  ruff format, mypy, pytest — 49 passed) pass clean.
- This PR continues Phase 2 with **sprint 15** (NDBC adapter, partial —
  see the sprint-ledger row): `apps/api/app/providers/ndbc.py` —
  `parse_realtime_text`/`fetch_buoy_observation` for NDBC's fixed-width
  `realtime2` text feed (not JSON), so `app/infra/http_client.py`'s
  `BoundedHTTPClient` gained a `get_text` method alongside `get_json`
  (small, tested addition to sprint 12's infra, needed by this sprint).
  Wind speed/gust/direction, wave height, and pressure are each parsed
  independently, taking the first non-missing reading per field across
  the 10 most recent rows — a buoy missing a column (e.g. a wave-only
  buoy has no `WSPD`) leaves that field `None` rather than raising,
  while a provider-specific missing-value marker (`MM`, `99.0`, `999`,
  ...) in a present column is treated the same way; only a station with
  *no* usable reading for *any* tracked field raises
  `NdbcDataUnavailableError`. The legacy module's pressure-trend
  computation and fishing-impact narrative (`fetch_barometric_pressure`)
  are deliberately not ported — those are scoring/narrative concerns
  for sprint 35, not a provider adapter's. Ported from
  `services/ndbc.py` behind `apps/api/tests/test_ndbc_provider.py`'s
  fixture-based characterization tests (full row, all-missing raises,
  partial-missing leaves only those fields `None`, gust-falls-back-to-
  speed, missing column vs. missing value, first-usable-row-per-field,
  short-row skipping, too-few-lines raises). All of `apps/api`'s checks
  (ruff, ruff format, mypy, pytest — 62 passed) pass clean.
- This PR continues Phase 2 with **sprint 16** (astronomy adapter,
  **complete** — the first fully-complete Phase 2 provider sprint, since
  it's pure math with no fetch layer): `apps/api/app/providers/
  astronomy.py` — `compute_sun_times`, `compute_twilight_times`,
  `compute_lunar_details`, `compute_solunar_times`, ported from
  `services/astro.py`'s NOAA simplified solar-position algorithm and
  synodic-month lunar-phase approximation. Returns typed timezone-aware
  `datetime`s instead of the legacy module's pre-formatted 12-hour
  strings, and enums (`MoonPhaseName`, `SolunarRating`) instead of loose
  strings. Two behavior changes beyond typing, both documented in the
  module docstring: the duplicate sunrise/sunset formula
  (`_sun_times` vs. the parameterized `_sun_event_time`) is collapsed
  into one; and the legacy `_sun_times`'s "lat/lng `== 0` means unset,
  substitute a default location" sentinel is dropped as a latent bug
  ((0, 0) is a real point, not a safe "unset" marker) — callers now pass
  real coordinates, no silent fallback. The moonrise/moonset/solunar-
  window day-boundary approximation (an hour-of-day wrapped `mod 24`,
  stamped onto the same calendar date as the input, so a window that
  crosses midnight doesn't roll onto the next day) is carried over
  unchanged, since fixing it needs a materially different algorithm,
  out of scope here. `apps/api/app/infra/timezones.py` (new) extracts
  the `ZoneInfo`-with-fallback helper this adapter needs, shared with
  sprint 14's NOAA CO-OPS adapter (refactored to use it, dropping its
  own copy — two copies was fine, three wasn't). `apps/api/tests/
  test_astronomy_provider.py` covers Atlantic and Pacific coasts,
  summer vs. winter day length, a non-DST-observing timezone
  (`America/Phoenix`), polar-latitude `acos` clamping, and exact
  assertions at the lunar-phase reference instant (new moon → 0%
  illumination, half a synodic month later → full moon) — structural/
  exact assertions rather than pinned against an external ephemeris,
  since the module's formulas are explicitly approximations. All of
  `apps/api`'s checks (ruff, ruff format, mypy, pytest — 75 passed)
  pass clean. This completes the four-sprint provider-adapter run
  (13-16: NWS, NOAA CO-OPS, NDBC, astronomy) the sprint ledger named.
- This PR continues Phase 2 with **sprint 17** (station catalog,
  **complete**): `apps/api/app/providers/stations.py` —
  `fetch_coops_tide_catalog`/`fetch_coops_watertemp_catalog`/
  `fetch_ndbc_catalog` for the public NOAA CO-OPS and NDBC station
  catalogs (metadata used to point sprints 13-15's adapters at *any*
  US coastal coordinate, not just curated locations), degrading to
  `[]` on failure rather than raising — the opposite resilience
  posture from sprint 14's water-temperature/tide-*reading* fetches,
  since a catalog is routing metadata, not a decision-relevant value.
  `nearest_coops_station`/`nearest_ndbc_stations` are pure
  distance-ranking functions over an already-fetched catalog list,
  deliberately decoupled from the fetch/cache layer (the legacy
  module coupled them), so the haversine math is tested with zero
  network mocking. `StationCatalogCache` replaces the legacy module's
  implicit module-level `dict` + `threading.Lock` with an explicit,
  `asyncio.Lock`-based, injectable-clock idempotent TTL cache — a
  positive TTL for a successful fetch, a short negative TTL for a
  degraded one — satisfying the sprint's "provenance, timestamps,
  idempotent refresh" requirement and letting `apps/api/tests/
  test_stations_provider.py` characterize "still fresh, no refetch"
  vs. "expired, refetch and replace" vs. "failed fetch, short
  negative TTL" deterministically instead of sleeping in tests. All
  of `apps/api`'s checks (ruff, ruff format, mypy, pytest — 90
  passed) pass clean.
- This PR continues Phase 2 with **sprint 18** (coastal coordinate
  validation, **complete** using only sprint 17's inputs):
  `apps/api/app/providers/coastal_bounds.py` — `is_valid_coordinate`
  (lat/lng range check) and `classify_coast_region` (a coarse bounding
  box per supported coast — Atlantic, Gulf, Pacific, Alaska, Hawaii —
  both pure and offline, rejecting obviously-wrong input before any
  station lookup is worth doing), plus `gate_coastal_point`, the
  actual inland-rejection mechanism ported from `locations.py`'s
  `_resolve_dynamic_location`/`dynamic_location_for_point`
  (`_DYN_GATE_MILES = 60.0`): a point counts as coastal only if it's
  within `max_miles` of a real CO-OPS or NDBC station in sprint 17's
  catalogs — a bounding box alone can't do inland rejection, since the
  Atlantic-region box necessarily contains the Appalachians. The
  legacy gate's additional curated-location fallback is deliberately
  not ported: it needs the curated-locations dataset, sprint 19's job
  (location resolution), not this one's — so this sprint's gate is a
  strict subset of the legacy gate's inputs, which can only make it
  stricter, never more permissive. `apps/api/tests/
  test_coastal_bounds.py` covers boundary/out-of-range coordinates,
  each of the five supported coast regions by a known city, landlocked
  and far-outside-US rejection, and `gate_coastal_point` against
  synthetic station catalogs (nearer-of-both-catalogs selection,
  custom `max_miles`, empty-catalog handling). All of `apps/api`'s
  checks (ruff, ruff format, mypy, pytest — 105 passed) pass clean.
- This PR continues Phase 2 with **sprint 19** (location resolution,
  **complete**): `apps/api/app/providers/locations.py` — the curated
  101-spot location dataset and dynamic (any-coordinate) location
  resolution, ported from `locations.py`. `app/data/coastal_locations.json`
  and `app/data/water_temps.json` are a **documented mechanical move**:
  generated by `ast.literal_eval`-parsing the legacy module's
  `COASTAL_LOCATIONS`/`_WATER_TEMPS` list/dict literals directly rather
  than hand-transcribed, guaranteeing byte-for-byte fidelity across
  ~1,500 lines of coordinates and station IDs that hand-transcription
  could not credibly promise — the same "documented mechanical move"
  exception to the 400-line guideline sprints 1-3's evidence-acceptance
  PR used. `find_nearest_locations`, `timezone_for_point`,
  `monthly_water_temps_for_region`, `format_dynamic_id`/
  `parse_dynamic_id` (reusing sprint 18's `is_valid_coordinate` instead
  of duplicating the range check), and `resolve_dynamic_location`
  (composing sprint 17's CO-OPS/NDBC catalogs with the curated dataset,
  the direct port of the legacy `_resolve_dynamic_location`) are pure
  functions over already-loaded data, golden-tested in `apps/api/tests/
  test_locations_provider.py` against exact field values for Montauk
  NY, Wrightsville Beach NC (the same fixture coordinates used
  throughout sprints 13-18's tests), and Poipu HI, plus dynamic-point
  resolution near Wrightsville Beach (regional-field inheritance, NWS
  zone inherited within 75mi) and from Kodiak AK (no curated Alaska
  location exists, so nothing inherited, exercising the all-defaults
  path). All of `apps/api`'s checks (ruff, ruff format, mypy, pytest —
  127 passed) pass clean.
- This PR continues Phase 2 with **sprint 20** (observation
  normalization, **complete**): `apps/api/app/domain/normalize.py` —
  wraps sprints 13-15's provider-specific typed outputs (NWS, NOAA
  CO-OPS, NDBC) into `app.domain.models.Observation` (sprint 11's
  ADR-003 canonical vocabulary), a domain-layer concern rather than a
  provider one. Canonical units (`kt` wind, `ft` height, `degF` temp,
  `mb` pressure) were already what every provider emitted since
  sprints 13-15, so this sprint's actual job was consistent labeling
  and typed wrapping, not unit conversion. Deliberately not wrapped:
  astronomy (sprint 16) — computed, not measured, no provenance/
  freshness story to attribute; categorical fields like wind direction
  — no meaningful unit for a `float` value; NWS's marine-zone
  wind/wave data, which is a forecast *range* — collapsed to one
  `ObservationRange` of paired low/high `Observation`s rather than one
  lossily-averaged value, since picking a single representative number
  is an interpretation decision for forecast assembly (sprint 21), not
  this sprint. Every function leaves `is_fallback`/`fallback_reason` at
  their defaults — these are live readings, and deciding when to
  substitute (and mark) a fallback value is forecast-assembly's job,
  as repeatedly deferred there since sprint 14. `apps/api/tests/
  test_normalize.py` covers all three providers' happy paths, `None`
  propagation (a buoy field or NWS range component with no parseable
  source data yields no `Observation`, not an invented one), and that
  the `Observation`/`ObservationRange` provenance fields (provider,
  station/zone, observed_at) are populated correctly. All of
  `apps/api`'s checks (ruff, ruff format, mypy, pytest — 136 passed)
  pass clean.
- This PR continues Phase 2 with **sprint 21** (forecast assembly,
  **complete**): `apps/api/app/domain/assembly.py`'s
  `assemble_forecast` concurrently fans out to NWS marine-zone
  conditions, NOAA CO-OPS water temperature, and NDBC buoy readings
  (each independently fallible, caught individually — one upstream
  failure never blanks the forecast, per the product contract's
  Reliability bullet), plus astronomy (no failure mode given a
  resolved location). Designs `ForecastEnvelope.conditions` — sprint
  11 named sprints 21/22/34/35 as the owners of `conditions`/`tides`/
  `hourly_outlook`/`recommendations`; `tides` is deliberately untouched
  here (a distinct time-series concern, sprint 34's job), and
  `hourly_outlook`/`recommendations` stay opaque too. Each provider's
  normalized output is its own field on `ForecastConditions`, not
  merged into one reconciled value — that reconciliation is forecast
  *scoring*'s job (sprint 22). Water temperature finally gets the
  fallback-to-monthly-average substitution repeatedly deferred to
  "forecast assembly" since sprint 14: when the live CO-OPS fetch
  fails, sprint 19's `monthly_water_temps_for_region` supplies the
  value, labeled via `Observation.is_fallback=True` and
  `fallback_reason` — never presented as a live reading, satisfying
  the product contract's Integrity bullet ("missing observations are
  never turned into invented measurements"). `ForecastState`/
  `Confidence` are intentionally basic (FRESH/PARTIAL from wind-wave
  availability; HIGH/MEDIUM/LOW confidence from source liveness) —
  `ForecastState.STALE` isn't produced (no cache yet, sprint 24's job)
  and the fuller distance/age/fallback confidence-degradation policy is
  sprint 23's job; this sprint only needed something defensible enough
  to react to its own matrix. `apps/api/tests/test_assembly.py`
  exercises the full 2**3 = 8 present/absent matrix across the three
  fallible sources (the sprint's named acceptance criterion), plus
  astronomy-always-present, location-field mapping, and the
  no-stations-assigned edge case. All of `apps/api`'s checks (ruff,
  ruff format, mypy, pytest — 148 passed) pass clean.
- This PR continues Phase 2 with **sprint 22** (forecast scoring,
  **complete**): `apps/api/app/domain/scoring.py`'s `score_conditions`
  — the 0-100 go/no-go index and plain-language explanation, ported
  from the legacy `domain/forecast.py:score_conditions` plus the
  wind-orientation/onshore-offshore-direction mapping from
  `domain/species.py` (`wind_orientation_for_region`/
  `onshore_offshore_dirs`, carried over verbatim per that module's own
  "single authoritative function, do not reimplement elsewhere"
  warning). Deliberately decoupled from sprint 21's
  `ForecastConditions`: it takes an already-reconciled `wind_range`/
  `wave_range` pair (plus optional wind direction, water temperature,
  sun times, and solunar data) as explicit parameters rather than
  reading sprint 21's still-separate NWS/NDBC fields directly —
  picking a source when both report is a future wiring step this
  sprint doesn't own. Scores wind speed, wave height, wind direction
  (onshore/offshore by coastline orientation), water-temperature
  comfort band (with a small bonus for a live, non-fallback reading),
  dawn/dusk light window, and solunar rating/illumination — all
  thresholds, point values, and verdict tiers unchanged from the
  legacy function. Deliberately not ported, each named in the module
  docstring with its blocking dependency: tide state/range/turn-
  proximity bonuses (need tide predictions, sprint 34's job); fishing-
  type-specific modifiers and angler comfort thresholds (need user
  preferences data, sprint 36's job); water-quality/HAB signals from
  `services/datagov.py` (R1's still-open product question — enrichment
  vs. scope creep — re-flagged rather than silently ported or
  dropped). `apps/api/tests/test_scoring.py` characterizes every
  wind/wave threshold band, wind-direction onshore/offshore bonus
  (including Hawaii's deliberate no-op), all four coastline
  orientations via `wind_orientation_for_region`, water-temperature
  comfort bands and the live-vs-fallback bonus, the dawn/dusk window,
  solunar rating/illumination bonuses, verdict-tier boundaries,
  factor-sorted summary generation, and the `wind_range is None or
  wave_range is None` → `Unknown`-verdict early return. All of
  `apps/api`'s checks (ruff, ruff format, mypy, pytest — 203 passed)
  pass clean.
- This PR continues Phase 2 with **sprint 23** (confidence model,
  **complete**): `apps/api/app/domain/confidence.py`'s
  `assess_confidence` has no legacy precedent — the legacy Flask app
  never implemented a confidence concept at all, so this is this
  recovery's own design against `docs/product-definition.md`'s
  Confidence section ("derived from source coverage, observation age,
  station distance, and fallback use"), replacing sprint 21's interim
  HIGH/MEDIUM/LOW-from-liveness stub. Starts at 100 points and applies
  an independent, documented penalty per degrading factor: an
  unavailable/degraded `SourceStatus` per source; an aging/stale
  observation per two age thresholds; a fallback-flagged observation
  (penalized independently of *why* its source is unavailable, per the
  product-definition text treating fallback use as its own axis); and
  a far/very-far station distance (the "far" bound deliberately sits
  inside sprint 18's 60-mile `gate_coastal_point` cutoff — a station
  accepted as "coastal enough to use" can still be too far to fully
  trust). Each penalty reports a plain reason code into
  `Confidence.reasons`, giving the product contract's "always show the
  reasons for reduced confidence" requirement something concrete to
  render. Deliberately decoupled from `app.domain.assembly`, matching
  sprint 22's scoring module's pattern: takes already-computed source
  statuses, observations, and station distances as explicit parameters
  rather than reaching into `ForecastEnvelope`/`ForecastConditions`
  itself — wiring this into `assemble_forecast` in place of its
  interim stub, alongside sprint 22's scoring wiring, remains an
  unassigned follow-up. `apps/api/tests/test_confidence.py` covers
  each factor independently, boundary ages/distances, mutual
  exclusivity within each two-tier band, multiple factors combining on
  one observation and across the whole assessment, and score clamping
  at zero under compounded penalties. All of `apps/api`'s checks
  (ruff, ruff format, mypy, pytest — 226 passed) pass clean.
- This PR continues Phase 2 with **sprint 24** (snapshot caching,
  **complete**): `apps/api/app/infra/snapshot_cache.py`'s
  `SnapshotCache[T]` generalizes sprint 17's `StationCatalogCache[T]`
  (single global value, one TTL) to multiple keys and a two-tier
  freshness policy: `fresh_ttl_seconds` defaults to 4 hours, matching
  the legacy `forecast_cache` cadence the sprint ledger names;
  `stale_ttl_seconds` and the fallback-on-fetch-failure behavior below
  it have no legacy precedent — legacy's cache only ever had "hit" or
  "miss," never a policy for what to serve when a refresh itself
  fails. Built from `docs/product-definition.md`'s Stale-state
  definition ("a previously valid snapshot outside its freshness
  window... still useful as clearly aged fallback information") and
  the product contract's Reliability bullet, extended from "one
  upstream source failing" (sprints 21-23) to "the whole refresh
  failing." A cache entry younger than `fresh_ttl_seconds` is a fresh
  hit (`fetch` never called); between the two TTLs it's a stale hit
  that attempts a refresh; at or past `stale_ttl_seconds` it's evicted
  and behaves exactly like a miss, including that a subsequent fetch
  failure has nothing left to fall back to and propagates; and if a
  refresh fetch raises while a still-eligible entry exists, that entry
  is returned labeled `is_fallback=True` instead of propagating.
  `get_or_refresh` is single-flight per key via a per-key
  `asyncio.Lock` — concurrent callers for the same key share one
  fetch, callers for different keys never block each other. This
  sprint deliberately doesn't replicate legacy's SQLite storage layer
  (no Postgres connection yet, per this README's own "does not yet
  have... a Postgres connection" line) or its background-daemon
  refresh — see the module docstring — and wiring this cache around
  `assemble_forecast`, keyed by location id and producing
  `ForecastState.STALE` on a fallback hit, remains a follow-up
  alongside sprints 22/23's still-unwired scoring/confidence. `apps/api/
  tests/test_snapshot_cache.py` covers fresh hit, stale hit, miss,
  expiry (both successful-refresh and no-fallback-available cases),
  fallback-on-fetch-failure, and single-flight concurrency (same-key
  serialization and different-key non-blocking) independently. All of
  `apps/api`'s checks (ruff, ruff format, mypy, pytest — 241 passed)
  pass clean.
- **CI-health fix (PRs #347/#348, not a numbered sprint): legacy `lint`
  job resolved.** Fixed the ~38 ruff errors, 65 unformatted files, and
  23 mypy errors `docs/R2_CI_BASELINE.md` documented, plus a separate
  workflow bug where mypy crashed on a duplicate `tests` module
  colliding with `apps/api/tests` before it could check anything (fixed
  by excluding `apps/` from the root job's ruff/mypy invocations, since
  `apps/` already has its own dedicated CI). #347's fix passed every
  local check yet the `lint` job still failed post-merge with 580
  errors — root cause: neither the legacy repo-root nor `apps/api` had
  a `pyproject.toml`/`ruff.toml`, so ruff's rule selection silently fell
  back to an undocumented, version-dependent implicit default that
  disagreed between environments even under the same pinned
  `ruff>=0.4,<1.0` constraint. #348 fixed this at the root: an explicit
  repo-root `pyproject.toml` pins `select = ["E4", "E7", "E9", "F"]`
  (the set the cleanup was actually written against), and a separate
  `apps/api/pyproject.toml` (no explicit `select`) stops `apps/api` from
  inheriting that narrower selection while preserving the "clean under
  ruff's implicit default" bar it's held since sprint 12. Verified via
  the GitHub API that both `lint` job runs on #348's merge commit show
  `conclusion: success` — not just a local re-check.
- This PR continues Phase 2 with **sprint 25** (versioned API,
  **complete** except for the two auth-gated endpoints): `apps/api/app/
  api/v1/locations.py` (`GET /v1/locations/search`,
  `POST /v1/locations/resolve`) and `app/api/v1/forecasts.py`
  (`GET /v1/forecasts/{location_id}`,
  `POST /v1/forecasts/{location_id}/refresh`) — 4 of the canonical
  roadmap's 6 required `/v1` endpoints. `GET`/`PATCH /v1/me/preferences`
  are deliberately not attempted: they need Better Auth (sprint 28) and
  a Postgres-backed preferences store, neither of which exists yet — no
  amount of scoping cleverness makes those buildable now. No new domain
  logic: `search`/`resolve` are thin wrappers over sprint 19's location
  functions (plus two small new pure functions,
  `search_curated_locations`/`find_curated_location`), and
  `GET /v1/forecasts/{id}` returns exactly the `ForecastEnvelope`
  sprint 21's `assemble_forecast` already builds — sprints 22-24's
  scoring/confidence/caching refinements remain the unassigned wiring
  follow-up they already were. `refresh` is deliberately identical to
  `GET` today: `assemble_forecast` doesn't cache anything yet (sprint
  24's `SnapshotCache` isn't wired into it), so there's nothing for
  `refresh` to force bypassing until that wiring lands — documented in
  the module docstring rather than silently faked. `app/api/deps.py`'s
  `AppState` (one pooled `BoundedHTTPClient` plus the three sprint-17
  station-catalog caches, created once in `app/main.py`'s FastAPI
  `lifespan`) backs both routers via `Depends`, and its
  `resolve_location_id` helper is shared by both so a location resolved
  explicitly and one resolved implicitly by a forecast lookup always
  agree for the same id. The breaking-change guard
  (`tests/openapi_snapshot.json` + `scripts/generate_openapi_snapshot.py`)
  mirrors sprint 11's domain-model schema-snapshot pattern exactly.
  Verified against a real running `uvicorn` server, not just
  `TestClient`: `/health/live`, `/v1/locations/search`, and
  `/openapi.json` all responded correctly and the live schema matched
  the committed snapshot byte-for-byte. `apps/api/tests/
  test_locations_router.py`/`test_forecasts_router.py` cover search
  matches/no-match/empty-query, resolve-by-id (found and 404),
  resolve-by-point (coastal success and non-coastal 422), malformed
  request bodies, and out-of-range coordinates; forecasts router tests
  cover curated and dynamic location ids, unknown-id 404, non-coastal
  422, and refresh returning the same shape as get. All of `apps/api`'s
  checks (ruff, ruff format, mypy, pytest — 258 passed) pass clean.
- This PR continues Phase 2 with **scoring wiring** (not a numbered
  sprint — the first slice of the "wire sprints 22/23/24 into
  `assemble_forecast`" follow-up sprint 25 named): `app/domain/
  assembly.py` now computes a real `ForecastConditions.score` on every
  forecast via sprint 22's `score_conditions`, closing the gap where
  `GET /v1/forecasts/{id}` returned raw per-source readings but no
  actual go/no-go score. The source-reconciliation policy this
  required — which of NWS's marine-zone range or NDBC's buoy reading
  to score — is now assembly's, documented in its module docstring:
  the marine-zone range is preferred (a genuine forecast low/high
  spread) over the buoy's single live value (used only as a fallback,
  turned into a degenerate zero-width range); wind direction prefers
  NWS's parsed value over the buoy's. Confidence-model and caching
  wiring (sprints 23/24) remain explicitly deferred — `assess_confidence`
  needs real station-distance data `ResolvedLocation` doesn't carry
  yet, named as the concrete blocker rather than left vague. This
  wiring's manual real-server smoke test surfaced a genuine pre-existing
  gap unrelated to scoring: `app/infra/http_client.py`'s
  `BoundedHTTPClient` only caught `httpx.ConnectError`, not the
  broader `httpx.TransportError` family (this sandbox's outbound proxy
  raised `httpx.ProxyError` instead), so a proxy/read/write/pool-timeout
  failure would have escaped as an unhandled exception rather than
  degrading one source gracefully per the product contract's
  Reliability bullet — fixed and characterized in
  `apps/api/tests/test_http_client.py`. New/updated tests in
  `apps/api/tests/test_assembly.py` cover score presence matching the
  FRESH/PARTIAL split, the marine-zone-over-buoy range preference, the
  buoy fallback becoming a zero-width range, and NWS-over-buoy
  direction preference. All of `apps/api`'s checks (ruff, ruff format,
  mypy, pytest — 262 passed) pass clean.
- This PR continues with **confidence wiring** (not a numbered sprint —
  the second slice of sprint 25's named wiring follow-up, closing the
  blocker scoring-wiring's PR named explicitly): `ForecastEnvelope.
  confidence` now comes from sprint 23's `assess_confidence` instead of
  assembly's old liveness-only stub, given per-source status, one
  `AgedObservation` for water temperature (age + fallback), and — for
  dynamic points — a `StationDistance` from a new `ResolvedLocation.
  anchor_miles` field. That field is the plumbing the blocker named:
  `resolve_dynamic_location` (sprint 19) already computed an
  anchor-miles value but discarded it into a tuple `app.api.deps.
  resolve_location_id` (sprint 25) never used; it's now embedded on the
  model itself, `None` for curated locations (they *are* the named
  station — no distance factor) and for points with no anchor found at
  all (an aggregate `float("inf")` in the raw tuple isn't a meaningful
  JSON value). Documented as a deliberate simplification, not a full
  per-source distance breakdown: `resolve_dynamic_location` only ever
  returns the single nearest anchor's distance, not one per fallible
  source. `POST /v1/locations/resolve`'s response schema picked up the
  new field — `tests/openapi_snapshot.json` regenerated, reviewed as a
  one-field additive diff, exactly the deliberate-regeneration flow the
  breaking-change guard exists for. `apps/api/tests/test_assembly.py`'s
  present/absent matrix test now reproduces assembly's exact
  `assess_confidence` inputs to verify the wiring (not re-deriving the
  point arithmetic — `test_confidence.py` already exhaustively covers
  that), plus a new dedicated test confirms a dynamic location's
  `anchor_miles` actually degrades confidence via a
  `distant_station:location:anchor` reason.  Sprint 24's `SnapshotCache`
  remains the one still-unwired piece — it needs a keying/
  `ForecastState.STALE` design decision, not just missing input data,
  see the module docstring. All of `apps/api`'s checks (ruff, ruff
  format, mypy, pytest — 263 passed) pass clean.
- This PR closes the wiring follow-up with **caching wiring** (not a
  numbered sprint — the third and last of the three pieces sprint 25
  named): `app/domain/forecast_cache.py` wraps sprint 21's
  `assemble_forecast` in sprint 24's `SnapshotCache`, keyed by location
  id, *around* assembly rather than inside it — `assemble_forecast`
  stays a pure "assemble one envelope right now" function; caching,
  freshness, and `ForecastState.STALE` labeling are entirely
  `forecast_cache`'s concern. `GET /v1/forecasts/{id}`
  (`get_or_assemble_forecast`) serves a fresh cached envelope when one
  exists; `POST /v1/forecasts/{id}/refresh`
  (`refresh_and_assemble_forecast`) bypasses the freshness check
  entirely and forces a live assemble, repopulating the cache — the
  distinguishing behavior sprint 25's router docstring promised it once
  this landed, finally delivered. `SnapshotCache` gained a
  `force_refresh` method for exactly that, refactored to share
  `get_or_refresh`'s fallback/single-flight logic via a new private
  `_fetch_and_store` helper — externally-observable behavior unchanged,
  confirmed by the existing 15 `SnapshotCache` tests passing unmodified.
  A fallback hit (the wrapped assemble raising) is relabeled
  `ForecastState.STALE`, documented as a **practically dormant path**:
  `assemble_forecast` never raises by design, so normally nothing
  reaches the cache's fallback branch. Both cache-wrapper functions
  accept an injectable `assemble` callable (default: the real one)
  specifically so `apps/api/tests/test_forecast_cache.py` can
  substitute a deliberately failing stand-in and actually exercise that
  dormant path — `assemble_forecast` itself can't be made to raise, so
  a substitutable seam is what makes the path testable at all, the same
  "test the seam directly" approach sprint 24's own tests already take.
  Router-level tests in `test_forecasts_router.py` confirm the wiring
  end-to-end with a call-counting mock transport: a second `GET` never
  re-hits the marine-zone upstream, and `refresh` does even immediately
  after a `GET`. `AppState`/`app.main`'s lifespan gained the cache
  instance; a real running `uvicorn` server was smoke-tested to confirm
  the lifespan wiring boots cleanly. `POST /v1/locations/resolve` and
  `ForecastEnvelope`'s response schemas are unaffected — no OpenAPI
  snapshot regeneration needed, confirmed by the existing snapshot test
  passing unmodified. All of `apps/api`'s checks (ruff, ruff format,
  mypy, pytest — 279 passed) pass clean.
- This PR continues Phase 2 with **sprint 26** (performance budget —
  bounded parallel calls, no duplicates, warm p95 under 750 ms).
  `BoundedHTTPClient` (`app/infra/http_client.py`) now sets `httpx.Limits`
  explicitly (`max_connections`/`max_keepalive_connections`, defaults
  20/10) — despite ADR-003 naming "bounded concurrency" as part of the
  design from the start, the client had silently relied on httpx's own
  unexamined defaults until now, an implementation gap discovered while
  scoping this sprint. **Only takes effect when httpx builds its own
  transport** — a caller-supplied `transport` (every existing test in
  this codebase, via `httpx.MockTransport`) bypasses `limits` entirely;
  `test_http_client.py` characterizes it by constructing a
  no-mock-transport client and inspecting the real transport's
  connection pool's `_max_connections`/`_max_keepalive_connections`, not
  by exercising it through a mock. New `tests/test_performance_budget.py`
  proves the other two acceptance bars: "no duplicates" under genuine
  concurrent HTTP requests (not just the sequential case
  `test_forecasts_router.py` already covered) via `httpx.AsyncClient` +
  `httpx.ASGITransport` driving the real FastAPI app with real
  `asyncio.gather` concurrency — Starlette's synchronous `TestClient`
  can't produce genuine overlapping requests — three concurrent `GET`s
  for the same location produce exactly one upstream marine-zone call,
  proving sprint 24's single-flight caching holds at the HTTP layer
  too, not just the domain layer the caching-wiring PR already tested;
  and "warm p95 under 750ms," measured with real wall-clock timing (no
  fake clock) over 20 already-cached requests, also asserting zero new
  upstream calls occurred (a "warm" request that silently hit the
  network wouldn't be a valid warm-path measurement) — observed p95 ≈
  2.8ms, roughly 270x headroom under budget. This measures the warm
  path's *local* processing latency only: a real end-to-end cold-path
  p95 against live upstreams can't be measured in this sandboxed CI
  environment (`docs/R2_CI_BASELINE.md`'s no-live-provider-dependence
  rule) and isn't part of this sprint's stated acceptance bar, but is
  flagged as open evidence for whenever this runs somewhere with real
  network access. Smoke-tested against a real running `uvicorn` server
  to confirm the new `httpx.Limits` wiring boots cleanly. All of
  `apps/api`'s checks (ruff, ruff format, mypy, pytest — 283 passed)
  pass clean.
- This PR picks up **sprint 13's own deferred gridpoint-wind
  fallback**, now that Phase 2 is otherwise closed out and it's the one
  concrete backend pick needing no new product/infra decisions.
  `apps/api/app/providers/nws.py` gains `fetch_gridpoint_wind`/
  `parse_gridpoint_wind` (`GridpointWindForecast` — wind-only, no wave
  data, since it's a land-point forecast), ported from the legacy
  `_try_nws_gridpoint`, degrading to `None` on failure like
  `fetch_point_alerts` rather than raising like
  `fetch_marine_zone_conditions` — it's a fallback, not a
  decision-relevant primary source. `app/domain/assembly.py` wires it
  in as a *last-resort* wind source: fetched only when neither the
  marine-zone forecast nor the NDBC buoy provide wind, as one extra
  sequential call (deliberately outside the initial `asyncio.gather`)
  rather than on every request — sprint 26's "bounded parallel calls,
  no duplicates" performance-budget discipline applied to the very next
  sprint after landing it. A successful fetch adds a `SourceStatus`
  (`nws:gridpoint_wind`) and a `fallback:gridpoint_wind` warning, feeds
  wind range/direction into scoring (direction only if not already set
  from marine-zone/buoy), and rescues `ForecastState` (FRESH instead of
  PARTIAL). **Documented explicitly, not left as a surprising gap**:
  because the gridpoint forecast never has wave data, it cannot by
  itself rescue `score` — `score_conditions` still needs both wind and
  wave (sprint 22's unchanged contract), so `score` stays
  `None`/`UNKNOWN` whenever wave is unavailable from every source,
  gridpoint included. The legacy module's current-weather observations
  (air temp, humidity, heat index, precipitation) remain deliberately
  deferred: nothing in the required `ForecastConditions` shape names
  them, so porting now would be inventing product scope, not closing a
  named gap. `tests/test_nws_provider.py` gains 8 tests for the new
  parser/fetch functions; `tests/test_assembly.py` gains coverage for
  the present/absent matrix (now explicitly excluding gridpoint via a
  `gridpoint_ok=False` client, preserving its original three-source
  scope, with a fourth conditional `SourceStatus` folded into the
  confidence reproduction for the two now-affected combos), the rescue
  case, the state-vs-score distinction, and proof gridpoint is never
  fetched when marine-zone wind is already available (a mock transport
  that raises on any unexpected `/points/` request, not absence of
  assertions). Confirmed no OpenAPI drift (`ForecastConditions` stays
  opaque inside `ForecastEnvelope.conditions`) and smoke-tested against
  a real running `uvicorn` server. All of `apps/api`'s checks (ruff,
  ruff format, mypy, pytest — 294 passed) pass clean.
- This PR picks up **sprint 14's own deferred CO-OPS wind fallback**,
  right after sprint 13's gridpoint fallback — the two combine into a
  genuine two-step last-resort chain matching the legacy priority
  order exactly. `apps/api/app/providers/noaa_coops.py` gains
  `fetch_coops_wind` (`CoopsWindReading`), ported from the legacy
  `_try_coops_wind` — the latest wind reading from the same CO-OPS
  station already used for water temperature (if that succeeds, this
  is very likely to as well). `app/domain/assembly.py`'s fallback
  chain now tries CO-OPS wind first, then NWS gridpoint wind only if
  that also comes up empty, matching legacy `domain/forecast.py:
  get_marine_conditions`'s exact source order (marine-zone, buoy,
  CO-OPS wind, gridpoint) — stopping as soon as one succeeds, so the
  typical case never pays for either. Same resilience posture as
  gridpoint wind (degrades to `None`, doesn't raise — non-critical
  fallback, not a decision-relevant primary source) and the same
  score-vs-state distinction documented for gridpoint: can rescue
  `ForecastState` to FRESH, can't rescue `score` (neither fallback has
  wave data). A real gap surfaced and fixed along the way: the fallback
  was initially unconditional, meaning a location with no
  `water_temp_station` configured would still fire a pointless network
  call with an empty station id — now guarded exactly like
  `_fetch_water_temp`'s existing empty-station check. Currents
  (`fetch_currents_predictions`/`fetch_currents_observation`),
  environmental metrics (air temp, humidity, visibility, pressure,
  salinity, conductivity), and the tide-chart SVG helper remain
  deliberately deferred: nothing in the required `ForecastConditions`
  shape or `docs/product-definition.md`'s dashboard-hierarchy list
  names tidal currents or these metrics, and SVG rendering isn't a
  provider-adapter concern at all. `tests/test_noaa_coops_provider.py`
  gains 5 tests for the new fetch function (success, no-gust-uses-
  speed, missing-speed/empty-data/provider-error all degrading to
  `None`). `tests/test_assembly.py` gains a `coops_wind_ok` client
  parameter (matching `gridpoint_ok`'s pattern, both defaulting to
  `False` in the present/absent matrix test to preserve its original
  three-source scope, with both fallback `SourceStatus`es folded into
  the confidence reproduction for the two affected combos), a dedicated
  CO-OPS-wind rescue test proving gridpoint is never reached once
  CO-OPS wind already succeeded, and the existing gridpoint tests
  updated to isolate gridpoint's own behavior with
  `coops_wind_ok=False`. Confirmed no OpenAPI drift and smoke-tested
  against a real running `uvicorn` server. All of `apps/api`'s checks
  (ruff, ruff format, mypy, pytest — 300 passed) pass clean.
- **Sprint 44, partial (signed-internal-API verification primitive)**:
  with Phase 2's own sprint-13/14/15 deferred scope essentially exhausted,
  this PR picks up a different, fully-specified piece already decided in
  `docs/architecture.md`'s ADR-004 rather than inventing new scope —
  `app/infra/internal_signature.py` implements the HMAC-SHA-256
  request-signing contract for the Next.js BFF → FastAPI boundary
  (canonical method/path+query/body-digest/user-id/issued-at/expires-at/
  request-id/key-id, constant-time comparison, clock-skew window,
  expiration, and replay detection via an injectable-clock `ReplayGuard`
  that prunes entries at their own `expires_at`, same pattern as
  `SnapshotCache`). `app/api/internal_auth.py` wraps it as a
  constructor-injectable FastAPI dependency (`InternalAuthDependency`)
  that fails closed on missing configuration. Deliberately **not** wired
  onto the `/v1` routers yet: `apps/web` is still the sprint-13 skeleton
  with no signer to pair with it, and a mandatory signature check on
  routes nothing can currently sign would make `apps/api` uncallable by
  its only real client — this is a "build the primitive correctly, wire
  it in later" split, the same pattern sprints 22/23/24 already used
  before their own wiring follow-ups landed. `tests/
  test_internal_signature.py` and `tests/test_internal_auth.py` cover
  both layers: valid signature, tampered body/method/path, expired,
  clock-skew, validity-window-too-long, unknown key id, previous-key
  rotation, replay detection and replay-guard pruning, missing/malformed
  headers, and fail-closed with no configured keys. All of `apps/api`'s
  checks (ruff, ruff format, mypy, pytest — 321 passed) pass clean, no
  OpenAPI drift (nothing wired onto any route yet).
- **Sprint 27, partial (design-system foundation, `apps/web`)**: the
  product owner directed proceeding with Phase 3 frontend work under the
  placeholder identity rather than waiting on a final branding decision
  (name/visual identity still not decided — "Surf & Pier Forecast"
  remains explicitly provisional throughout). `apps/web/app/globals.css`
  adds Tailwind v4 via `@theme`-declared design tokens (colors, radius,
  font) for light and dark, starting from `v2/frontend/src/index.css`'s
  teal/coral palette -- `docs/R1_RECONCILIATION_AUDIT.md` §3.2 flagged
  that app's `.button`/`.card`/`.field` *global CSS classes* as Replace
  ("not a real design system"), not its color choices, so the palette
  carries forward while the component layer is rebuilt as real React
  primitives. `app/components/ui/` gained `Button` (renders a real
  Next.js `<Link>` when `href` is given, a native `<button>` otherwise;
  visible focus ring), `Card`, `Badge` (a status pill for sprint 32's
  go/no-go traffic-light headline -- color reinforces the verdict, the
  verdict itself is always the visible text label, never color alone),
  `Field` (label/hint/error wired together via `aria-describedby`/
  `aria-invalid`, closing the `.field`-class accessibility gap §3.6
  flagged), and `Container` (mobile-first responsive width). `app/page.tsx`
  is now a gallery page showcasing all five at phone and desktop widths,
  screenshotted in both color schemes via a headless Chromium smoke test
  (no console/page errors besides the pre-existing, unrelated favicon
  404 every un-favicon'd Next.js app skeleton has). `app/not-found.tsx`
  is the trivial 404 page R1's §3.1 route table names as a straight
  `Keep`. Deliberately not attempted here: i18n-ready string
  externalization (bundled into sprint 27 by R1's §3.7 disposition) and
  a formal WCAG 2.2 AA audit (axe + keyboard/screen-reader evidence,
  explicitly sprint 40's job per §3.6) -- this PR aims for
  accessible-by-construction markup (semantic landmarks, labeled/
  described form controls, visible focus states, text-not-color status
  labels), not a certified audit. `npm run lint` (oxlint) and
  `npm run build` both pass clean; TypeScript strict-mode compiles with
  no errors.
- **Incident (sprint 6, resolved earlier)**: a scratch branch explicitly
  titled `DO NOT MERGE` was merged into `main` under the repo owner's own
  account, landing deliberately-broken code; reverted within ~10 minutes
  in PR #330. Full account in `docs/SPRINT_6_CI_PROOF.md`. Sprint 7
  turned this into a documented branch-hygiene rule.
- **Milestone: Phase 2 (backend) is done.** Sprints 12 through 26 are
  all **Complete** except 13/14/15's remaining deliberately-deferred
  sub-scopes: sprint 13's gridpoint wind fallback and sprint 14's
  CO-OPS wind fallback are both done now (combined into one two-step
  last-resort chain, this PR). What's left of each: sprint 13's
  current-weather observations (air temp/humidity/heat-index/
  precipitation) and sprint 14's currents/environmental-metrics/
  tide-chart SVG remain deferred — no `ForecastConditions` field names
  any of them. NDBC (sprint 15: pressure-trend + fishing-impact
  narrative, both explicitly named as sprint 35's job, not a provider-
  adapter concern) is the one sub-scope not yet touched at all. Phase 3
  (sprints 27+) is entirely `apps/web` frontend/product work and needs
  decisions this session can't make unilaterally: sprint 27 (design
  system) explicitly requires a **branding decision** ("Surf & Pier
  Forecast" is a named placeholder, not a starting-point) before any UI
  work is defensible; sprint 28 (authentication) needs Better Auth +
  Postgres infrastructure decisions neither exist yet. **Update: the
  product owner has directed continuing on regardless** — including
  Phase 3 frontend/visual work — rather than pausing on the branding
  decision; "Surf & Pier Forecast" is used as a working placeholder
  identity throughout, explicitly not treated as final, so Phase 3 work
  can proceed without blocking on a name/visual-identity decision that
  hasn't been made yet. Sprint 15's NDBC pressure-trend/fishing-impact
  work is still explicitly sprint 35's (fishing guidance) per that
  module's own docstring, so it isn't a clean standalone backend pick.
  Sprint 44's signed-internal-API verification primitive (this PR) is a
  fully-specified, non-branding-dependent piece of backend work picked
  up in the meantime. Separately, sprint 9 (preview
  environments) and sprint 10 (production skeleton) need real
  Vercel/Render/Neon accounts this session has no credentials for —
  **flag to the product owner** rather than attempting them blind; they
  can be done whenever those credentials are available, in parallel
  with more Phase 2 sprints.
- Known blocker: sprints 9/10 need Vercel/Render/Neon account access not
  available to this session — needs the product owner to either provision
  and share access, or do that part directly.
- **Resolved**: the legacy `lint` CI job (PRs #347/#348) — no longer
  known baseline debt. `docs/R2_CI_BASELINE.md`'s legacy (repo-root)
  `ruff check`/`ruff format --check`/`mypy` findings (~600 errors, ~65
  unformatted files, 23 mypy errors) are fixed, and the underlying cause
  of the workflow's own CI-vs-local drift — no `pyproject.toml`/`ruff.toml`
  anywhere in the repo, so ruff's rule selection fell back to an
  undocumented, version-dependent implicit default — is fixed too via an
  explicit `select` pin at the repo root (`E4`/`E7`/`E9`/`F`) and a
  separate `apps/api/pyproject.toml` insulating `apps/api` from that
  narrower selection. Both `lint` job runs on PR #348's merge commit show
  `conclusion: success`, confirmed via the GitHub API, not just a local
  check. R1's open product question is still
  unresolved: whether `services/{datagov,hdx_fao,arcgis_live_feeds,
  bathymetry}.py` (not named in the canonical contract's required
  providers) are future enrichment or scope creep — route to the product
  owner before Phase 2 sprints port providers wholesale. This session
  still could not verify GitHub branch-protection/required-status-check
  configuration via available tooling, and separately could not delete a
  git branch via any available tool — a repo admin should handle both,
  including configuring `apps-ci.yml`/`security.yml`'s checks as required
  and deleting `claude/sprint6-ci-failure-proof` (still on the remote,
  merged-and-reverted, safe to delete).
- Unmerged work considered complete: none.
- Product decisions on record 2026-08-17 (see that section above) refine the
  product contract — public general-audience scope, ~$1/month subscription
  intent deferred to sprints 61-63, legacy Flask app retired outright,
  regulations disclaimer + official-source data sourcing requirement,
  export/deletion pulled forward as a v1 requirement, device/browser and
  offline/PWA targets, CAPTCHA-on-registration requirement, in-app feedback
  as a launch requirement, and full (not scaled-back) CI rigor. These refine,
  not reopen, R0's contract and do not change the current gate.

This checkpoint must be updated through a merged PR whenever the active gate
or sprint changes.
