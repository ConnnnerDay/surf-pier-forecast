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

- Audience: recreational US surf and pier anglers.
- Experience: polished, marine-utility, mobile-first installable web product.
- Coverage: any valid US coastal coordinate, including Atlantic, Gulf,
  Pacific, Alaska, and Hawaii.
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
  starts or disposable production data.

### Deferred until production evidence supports them

Payments, advertising, native clients, social features, catch logging, live
cameras, notifications, passkeys, OAuth, a full regulations product, and the
full 851-species experience are not v1 launch requirements.

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
| R0 | Durable canonical roadmap and cross-agent handoff | This document, `AGENTS.md`, Claude warning, master issue, merged PR | Complete when PR #319 merges |
| R1 | Reconciliation audit | Every `/v2` route, module, schema, feature, and test mapped to keep/adapt/replace/defer with reasons and owning future sprint | Next |
| R2 | Truthful deterministic CI baseline | Exact current commands recorded; live-provider tests removed from required CI; failures classified as regression or known debt | Blocked by R1 |
| R3 | One canonical application path | Next.js/FastAPI/PostgreSQL skeleton is the named path; duplicate prototypes are clearly archived/reference-only; local smoke path is documented | Blocked by R2 |

No gate may be marked complete until its PR is merged to `main` and linked in
issue #318.

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
| 1 | Repository baseline | Reproducible legacy audit, routes, sources, failures, reusable modules | Closed PR #309; not accepted |
| 2 | Product definition | Journey, non-goals, metrics, vocabulary, attribution | Closed PR #310; recovered here, not accepted |
| 3 | Architecture decision | Boundaries, request path, records, hosting, data lifecycle | Closed PR #311; recovered here, not accepted |
| 4 | Monorepo scaffold | Clean Next.js/FastAPI install, build, and smoke test | Divergent scaffold exists; not accepted |
| 5 | Local developer workflow | One documented setup/run/check path from a fresh machine | Closed PR #313; not accepted |
| 6 | Quality gates | Frontend and Python lint, type, test, and intentionally failing proof | Not accepted |
| 7 | PR governance | Sprint/PR templates, ownership, dependency policy, AI review contract | Not accepted |
| 8 | CI foundation | Checks, secret scan, dependency audit, builds | Candidate workflows; not accepted |
| 9 | Preview environments | Isolated web/API previews with URL and curl evidence | Not accepted |
| 10 | Production skeleton | Vercel, always-on Render, pooled Neon connectivity and environment separation | Not accepted |

### Phase 2 — Build a trustworthy forecast core

| Sprint | Outcome | Acceptance focus | Current state |
|---:|---|---|---|
| 11 | Canonical domain model | Typed models, schema snapshots, serialization round trips | Candidate in `/v2` |
| 12 | HTTP client policy | Timeouts, bounded retries, user agent, size limit, structured errors | Candidate in `/v2` |
| 13 | NWS adapter | Typed weather, wind, alerts, and grid contract fixtures | Candidate in `/v2` |
| 14 | NOAA CO-OPS adapter | Tide/water-temperature fixtures, missing values, DST | Candidate in `/v2` |
| 15 | NDBC adapter | Buoy parsing, missing columns and markers | Candidate in `/v2` |
| 16 | Astronomy adapter | Pure deterministic coast/season/timezone tests | Candidate in `/v2` |
| 17 | Station catalog | Provenance, timestamps, idempotent refresh | Candidate in `/v2` |
| 18 | Coastal coordinate validation | Inland/out-of-range rejection and all-coast boundaries | Candidate in `/v2` |
| 19 | Location resolution | Timezone, zone, tide/temp station and buoy golden tests | Candidate in `/v2` |
| 20 | Observation normalization | Canonical units/times with raw provenance retained | Candidate in `/v2` |
| 21 | Forecast assembly | Independent sources and every present/absent matrix | Candidate in `/v2` |
| 22 | Forecast scoring | Defensible stable score components with explanations | Candidate in `/v2` |
| 23 | Confidence model | Predictable degradation by availability, distance, age, fallback | Not accepted |
| 24 | Snapshot caching | Fresh/stale hit, miss, expiry, fallback, concurrency | Candidate in `/v2` |
| 25 | Versioned API | Required endpoints, OpenAPI contract and breaking-change guard | Candidate in `/v2` |
| 26 | Performance budget | Bounded parallel calls, no duplicates, warm p95 under 750 ms | Not accepted |

### Phase 3 — Create the mobile product

| Sprint | Outcome | Acceptance focus | Current state |
|---:|---|---|---|
| 27 | Design system | Gallery at phone/desktop widths and accessible primitives | Candidate UI; not accepted |
| 28 | Authentication | Email/password lifecycle, session rotation, abuse tests | Divergent auth exists; replace/adapt |
| 29 | Account-required routing | Public exceptions and authorization/redirect tests | Candidate in `/v2` |
| 30 | Onboarding shell | Mobile recording from registration to dashboard | Candidate in `/v2` |
| 31 | Location search | Text, device, map, station preview, denial/ambiguity tests | Candidate in `/v2` |
| 32 | Dashboard hierarchy | Go/no-go, best window, conditions, confidence, freshness first | Candidate in `/v2` |
| 33 | Conditions experience | Full/partial/stale/unavailable source-attributed snapshots | Candidate in `/v2` |
| 34 | Tides and timing | Accessible charts, text alternatives, timezone/DST tests | Candidate in `/v2` |
| 35 | Fishing guidance | Limited supported suggestions; every recommendation explains why | Existing broad feature is out of scope; adapt |
| 36 | Preferences | Units, thresholds, style, default location persistence | Candidate in `/v2` |
| 37 | Saved locations | Ordered favorites, ownership, duplicates, deletion, empty state | Candidate in `/v2` |
| 38 | PWA baseline | Installable offline shell; authenticated forecasts not cached forever | Candidate in `/v2` |
| 39 | Responsive polish | Layout shift, assets, tap targets, Lighthouse, screenshot budgets | Not accepted |
| 40 | Accessibility pass | WCAG 2.2 AA, axe plus keyboard/screen-reader evidence | Not accepted |

### Phase 4 — Make it operable and launch it

| Sprint | Outcome | Acceptance focus | Current state |
|---:|---|---|---|
| 41 | Structured observability | One request trace across web/API/sources with safe context | Not accepted |
| 42 | Error monitoring | Frontend/API releases, source maps and secret redaction | Not accepted |
| 43 | Privacy-safe analytics | Registration, resolution, forecast state, latency, return use | Not accepted |
| 44 | Security hardening | CSP, CSRF, signed internal API, brute force, headers, threat model | Candidate pieces; not accepted |
| 45 | Privacy and deletion | Legal pages, export, deletion/anonymization proof | Candidate in `/v2`; not accepted |
| 46 | Database resilience | Migrations, constraints, indexes, pooling, backups, blank restore drill | Not accepted |
| 47 | Degraded-mode UX | Database/API/email/upstream chaos yields actionable UI | Not accepted |
| 48 | Release controls | Promotion, migration gate, smoke, rollback and staging drill | Not accepted |
| 49 | SEO and sharing | Public non-personal forecast pages; private dashboards | Not accepted |
| 50 | Launch readiness | Cross-device, load, security, a11y, restore, outage evidence | Not accepted |
| 51 | Limited beta | Small angler cohort and severity/reproduction report | Not accepted |
| 52 | Public-launch runbook | Owners, freeze rules, alerts, go/no-go and rollback triggers | Not accepted |
| 53 | Production promotion | Validated release promoted through the release controls | Not accepted |
| 54 | Production smoke suite | Account, location, forecast and degraded-path smoke evidence | Not accepted |
| 55 | First-hour observation | Live error, latency, source and database health review | Not accepted |
| 56 | First-24-hour review | Health report and only launch-blocking remediation | Not accepted |
| 57 | Reliability baseline | Actual p50/p95, upstream failure and forecast completion recorded | Not accepted |
| 58 | Signup-funnel baseline | Registration and onboarding completion recorded | Not accepted |
| 59 | Return-usage baseline | Privacy-safe early return-use evidence recorded | Not accepted |
| 60 | Highest-impact reliability fix | One measured gap fixed with before/after evidence | Not accepted |
| 61 | Expansion option studies | Recommendations, alerts, catches, regulations, native apps compared | Not accepted |
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

- Roadmap PR: [#319](https://github.com/ConnnnerDay/surf-pier-forecast/pull/319);
  it becomes the last merged roadmap PR when this file reaches `main`.
- Current gate after PR #319 merges: R1 — merged-code reconciliation audit.
- Exact next action: produce the R1 reconciliation audit; do not
  implement features or migrate architecture in that PR.
- R1 required output: a file-level and endpoint-level inventory of merged
  `/v2`, each item labeled keep/adapt/replace/defer, with rationale, tests,
  risks, and the future sprint that owns the action.
- Known blocker: none.
- Unmerged work considered complete: none.

This checkpoint must be updated through a merged PR whenever the active gate
or sprint changes.
