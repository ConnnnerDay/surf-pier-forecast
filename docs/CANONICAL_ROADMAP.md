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
| R0 | Durable canonical roadmap and cross-agent handoff | This document, `AGENTS.md`, Claude warning, master issue, merged PR | Complete when PR #319 merges |
| R1 | Reconciliation audit | Every `/v2` route, module, schema, feature, and test mapped to keep/adapt/replace/defer with reasons and owning future sprint | Complete when this PR merges |
| R2 | Truthful deterministic CI baseline | Exact current commands recorded; live-provider tests removed from required CI; failures classified as regression or known debt | Next |
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
| 24 | Snapshot caching | Fresh/stale hit, miss, expiry, fallback, concurrency; target 4-hour freshness window, matching legacy cadence | Candidate in `/v2` |
| 25 | Versioned API | Required endpoints, OpenAPI contract and breaking-change guard | Candidate in `/v2` |
| 26 | Performance budget | Bounded parallel calls, no duplicates, warm p95 under 750 ms | Not accepted |

### Phase 3 — Create the mobile product

| Sprint | Outcome | Acceptance focus | Current state |
|---:|---|---|---|
| 27 | Design system | Gallery at phone/desktop widths and accessible primitives; branding decided first (name included, not just visual identity — "Surf & Pier Forecast" is a placeholder); clean/utility-first tone, Surfline-for-fishing positioning | Candidate UI; not accepted |
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
| 44 | Security hardening | CSP, CSRF, signed internal API, brute force, headers, threat model | Candidate pieces; not accepted |
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

- Last merged recovery PR: #319 (R0 — canonical roadmap and handoff,
  `f10ae73`), plus decision-record PR #321 layering in the three rounds of
  product-owner decisions above.
- This PR delivers R1: [`docs/R1_RECONCILIATION_AUDIT.md`](R1_RECONCILIATION_AUDIT.md)
  — a file-level and endpoint-level inventory of every merged `/v2` route,
  module, schema, feature, and test, each labeled keep/adapt/replace/defer
  with rationale, risk, and owning future sprint. No code changed; no
  architecture migrated.
- Current gate once this PR merges: **R2 — truthful deterministic CI
  baseline.**
- Exact next action: record the exact current CI commands, remove
  live-provider-dependent tests from required checks (starting with
  `v2/frontend/e2e/forecast.spec.ts`, the one concrete offender the R1 audit
  found — everything else in both test suites already mocks network calls),
  and classify every remaining CI failure as regression or known debt. Do
  not implement product features or migrate architecture in the R2 PR.
- Known blocker: none.
- Known baseline carried into R2: the legacy CI workflow (`test.yml`) fails
  on current `main` with pre-existing Ruff/format findings; R1 did not hide
  or reformat them. R1 also surfaced an open product question for R2/Phase 2
  to route to the product owner rather than decide unilaterally: whether
  `services/{datagov,hdx_fao,arcgis_live_feeds,bathymetry}.py` (not named in
  the canonical contract's required providers) are future enrichment or
  scope creep to drop.
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
