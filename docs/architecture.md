> **Status: accepted as sprint 3 evidence** (see `docs/CANONICAL_ROADMAP.md`'s
> sprint ledger). Recovered verbatim from closed PR #311, which predates the
> canonical roadmap (R0) and was never merged. Content below is unchanged from
> that PR except this note. **One detail is superseded by merged code**: ADR-001
> specifies `pnpm` workspaces and `uv` for the Python environment. The
> already-merged `apps/web`/`apps/api` skeleton (R3, sprints 4-8 — see
> `docs/R3_CANONICAL_PATH.md` and `apps/README.md`) uses plain `npm` and
> `pip`/`venv` instead, with working, verified CI (`apps-ci.yml`,
> `security.yml`). Per the roadmap's source-of-truth order, merged code takes
> priority over a closed PR's recommendation — this document is not being used
> to justify migrating the working tooling to `pnpm`/`uv`. Everything else
> below (system context, service boundaries, auth, database ownership,
> caching, API contract, deployment topology, observability) was reviewed
> against what's since been merged and found consistent — no other conflicts.

# Sprint 3: Rewrite architecture decisions

Status: accepted for v1 implementation

This document fixes the principal architecture decisions for the rewrite. A
later sprint may refine an internal implementation, but changing a decision or
crossing a boundary defined here requires an architecture decision record and
an explicit product-impact review.

## System context

The product consists of three deployable or managed systems:

1. A Next.js web application deployed to Vercel.
2. A FastAPI forecast service deployed as an always-on Render web service.
3. A pooled PostgreSQL database hosted by Neon.

The browser communicates only with same-origin Next.js routes. Next.js owns
user sessions and acts as a backend-for-frontend (BFF). FastAPI owns location
resolution, upstream marine/weather integration, forecast assembly, scoring,
confidence, and forecast persistence. Both applications use PostgreSQL, but
each owns a separate database schema and database role.

```text
Browser
  |
  | HTTPS; secure session cookie
  v
Next.js web + BFF  ------------------->  PostgreSQL auth schema
  |
  | HTTPS; signed short-lived internal request
  v
FastAPI forecast service  ------------>  PostgreSQL forecast schema
  |
  +--> NWS
  +--> NOAA CO-OPS
  +--> NDBC
```

## ADR-001: Monorepo structure

### Decision

The rewrite lives beside the legacy application during extraction:

```text
apps/
  web/                 Next.js application and BFF
  api/                 FastAPI service
packages/
  api-client/          generated TypeScript client and types
  config/              shared JavaScript tooling configuration only
docs/                  product and architecture contracts
legacy application     unchanged until explicitly retired
```

The repository uses `pnpm` workspaces for JavaScript and `uv` for the Python
environment. Node and Python versions are pinned at the repository root. Root
commands wrap install, development, test, lint, type-check, schema generation,
and build operations.

### Consequences

- One commit can update an API contract and its generated consumer atomically.
- Legacy Python code is not placed on the new API import path accidentally.
- JavaScript dependencies are installed once.
- The API remains an independently deployable Python service.

## ADR-002: Next.js web application

### Decision

Use Next.js with the App Router and TypeScript in strict mode. Prefer React
Server Components for initial authenticated data loading. Use Client
Components only for interactive charts, map behavior, device geolocation, and
forms that require client state.

Next.js owns:

- public marketing, legal, status, authentication, and share pages;
- authenticated onboarding, dashboard, settings, and saved-location pages;
- Better Auth configuration, account flows, sessions, and CSRF boundary;
- BFF routes that validate a session and call FastAPI; and
- presentation-specific formatting and accessible visualization.

Next.js does not implement forecast rules, select stations, call NOAA/NWS/NDBC
directly, or write to forecast-owned tables.

### Consequences

- Authentication cookies never need to be understood by FastAPI.
- Forecast behavior has one server-side implementation.
- Browser bundles do not contain provider credentials or internal API secrets.

## ADR-003: FastAPI forecast service

### Decision

Use FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, and an async PostgreSQL driver.
Provider adapters use an async HTTP client with bounded concurrency, explicit
timeouts, limited retries, and response-size limits.

FastAPI owns:

- canonical location, observation, source, forecast, confidence, and warning
  models;
- coastal validation and location/station resolution;
- NWS, NOAA CO-OPS, NDBC, and astronomy adapters;
- normalization, forecast assembly, scoring, confidence, and guidance;
- forecast snapshots and provider/station catalogues; and
- `/v1` APIs plus live and ready health endpoints.

FastAPI does not render HTML, manage user passwords or sessions, send account
email, or accept browser cookies as authentication.

The legacy Flask application is never imported by the new service at runtime.
Reusable algorithms must first receive characterization tests, then be copied
or rewritten behind new typed interfaces.

## ADR-004: Browser-to-service boundary

### Decision

The browser calls only Next.js. Authenticated Next.js BFF routes call FastAPI.
FastAPI is network-addressable for hosting and health checks but rejects
application requests that lack a valid internal signature.

For each request, the BFF sends:

- HTTP method and canonical path;
- SHA-256 digest of the body;
- authenticated internal user identifier when required;
- issued-at time and short expiration;
- cryptographically random request identifier; and
- key identifier plus HMAC-SHA-256 signature over the canonical fields.

FastAPI validates the signature with constant-time comparison, enforces a
maximum clock-skew window, rejects expired requests, and records request IDs for
replay detection within the validity window. Signing keys are environment
secrets and support an active and previous key during rotation.

Health endpoints require no internal signature and expose no secrets or
detailed dependency errors. Public share data is served through a distinct,
rate-limited FastAPI route called only by the Next.js public share page.

### Rejected alternatives

- Direct browser-to-FastAPI traffic would duplicate session and CSRF policy.
- A permanent shared bearer token would be replayable and provide no user or
  request binding.
- Sending Better Auth cookies to FastAPI would couple forecast code to web
  session implementation.

## ADR-005: Authentication and user identity

### Decision

Use Better Auth in the Next.js application with email/password accounts,
verified email, password reset, secure HTTP-only cookies, session rotation, and
PostgreSQL persistence.

The authentication system creates an opaque stable user ID. The BFF passes only
that ID and the minimum authorization context required by FastAPI. FastAPI may
store preferences and saved forecast locations keyed by this opaque ID, but it
does not store email addresses, password data, verification tokens, or session
tokens.

Cookies use `Secure`, `HttpOnly`, and an appropriate `SameSite` policy in
production. State-changing web routes enforce origin and CSRF validation.
Login, registration, verification resend, password reset, and refresh actions
have separate rate limits.

OAuth, passkeys, guest accounts, and legacy-user migration are excluded.

## ADR-006: PostgreSQL ownership

### Decision

Use one Neon PostgreSQL project with separate `auth` and `forecast` schemas,
owned by separate least-privilege roles:

- the web role can access only Better Auth and web-owned account metadata;
- the API role can access only forecast locations, preferences, station
  catalogues, observations, forecast snapshots, and refresh locks; and
- migration roles are separate from runtime roles.

Neither runtime role can create schemas, roles, or extensions. Connections use
Neon's pooled endpoint with TLS required. Production, staging, preview, test,
and local environments never share databases or credentials.

User deletion is coordinated by the web application: it calls an authenticated
FastAPI deletion operation, confirms completion, then removes auth-owned data.
The operation is idempotent and auditable without retaining deleted personal
data.

### Rejected alternatives

- SQLite and local files do not survive server replacement safely.
- A shared unrestricted database role weakens ownership and breach containment.
- Separate database vendors increase cost and operational overhead for v1.

## ADR-007: Forecast caching and refresh

### Decision

PostgreSQL stores immutable forecast snapshots. Each snapshot records its
location, schema version, generation time, source states, evidence times,
freshness deadline, and response payload.

Request behavior:

1. Return the newest fresh compatible snapshot when available.
2. On a miss, acquire a per-location PostgreSQL advisory lock and generate a
   forecast within the cold-request deadline.
3. Concurrent callers wait briefly for the lock holder, then re-check the cache.
4. If refresh fails, return the newest compatible stale snapshot when policy
   permits, visibly marked stale with failure context safe for users.
5. If no usable snapshot exists, return the structured unavailable envelope.

Manual refresh is authenticated, rate-limited, and coalesced by the same lock.
Snapshot retention is bounded by age and count per location. Provider raw
payloads are not embedded in browser-facing forecast snapshots.

Redis, background workers, daemon threads, and distributed job queues are not
part of v1. A queue requires a later decision backed by measured need.

## ADR-008: API contracts and generated clients

### Decision

FastAPI's checked-in OpenAPI document is the source of truth for HTTP schemas.
CI generates the TypeScript API client and types under `packages/api-client`
and fails if generated output differs from the committed version.

The initial contract contains:

- `GET /health/live`
- `GET /health/ready`
- `GET /v1/locations/search?q=`
- `POST /v1/locations/resolve`
- `GET /v1/forecasts/{location_id}`
- `POST /v1/forecasts/{location_id}/refresh`
- `GET /v1/me/preferences`
- `PATCH /v1/me/preferences`

All application responses use typed JSON. Errors contain a stable code,
human-safe message, request ID, and optional field details. They never expose
tracebacks, provider response bodies, database errors, or secret configuration.

Breaking changes require a new API version. Additive optional fields may remain
within `/v1` when old generated clients continue to work.

## ADR-009: Deployment topology

### Decision

- Vercel deploys `apps/web` with preview, staging, and production environments.
- Render deploys `apps/api` as an always-on web service with separate staging
  and production services.
- Neon supplies isolated pooled databases or branches for each persistent
  environment; ephemeral previews use isolated non-production data.
- Vercel is the only intended caller of application FastAPI endpoints.
- Render terminates TLS and the application trusts forwarded headers only from
  the hosting boundary configured for production.

Deployments run migrations as an explicit gated step before traffic promotion.
Application startup never performs unbounded migrations. The prior release and
backward-compatible schema remain available for rollback.

Free Render instances are allowed only for disposable previews because idle
wake-up time conflicts with the reliable-daily-use launch gate.

## ADR-010: Observability and privacy boundary

### Decision

Both services emit structured logs containing request ID, release, route
template, status, latency, cache state, and privacy-safe provider timing.

Logs, metrics, error reports, and analytics must not contain:

- passwords, cookies, tokens, signatures, or authorization headers;
- email addresses or email content;
- precise user coordinates;
- raw provider bodies; or
- complete request bodies by default.

Resolved location IDs and rounded region-level coordinates may be recorded only
after the privacy sprint defines retention. Request IDs propagate from the BFF
to FastAPI and provider timing spans.

## Repository and ownership rules

- New product behavior belongs under `apps/` or `packages/`; legacy files change
  only in an explicitly scoped legacy-maintenance sprint.
- The generated API client is never edited by hand.
- Database access occurs through the owning application, not cross-schema joins.
- Provider adapters return canonical observations or typed provider errors; they
  do not choose UI language or fishing recommendations.
- Domain services do not import web frameworks.
- UI components do not infer missing measurements or recalculate forecast rules.
- Environment variables are parsed and validated at application startup.
- Secrets are never committed, copied into preview artifacts, or exposed through
  `NEXT_PUBLIC_*` variables.

## Architecture verification

Each implementation PR affecting these boundaries must answer:

- Which component owns the changed behavior and data?
- Does the browser still communicate only with Next.js?
- Does the change alter the OpenAPI contract or generated client?
- Does it require a database migration and remain rollback-compatible?
- Can an upstream or managed dependency failure remain bounded?
- What identifiers or coordinates reach logs, analytics, and error monitoring?
- Does the change introduce a capability excluded from v1?

## Sprint 3 exit criteria

- [x] Deployable components and ownership boundaries are explicit.
- [x] Browser, BFF, API, provider, and database data flow is defined.
- [x] Authentication and internal request trust boundaries are defined.
- [x] Database schema and runtime-role ownership are defined.
- [x] Cache hit, miss, concurrency, failure, and stale behavior are defined.
- [x] API source of truth and compatibility policy are defined.
- [x] Deployment, migration, rollback, observability, and privacy constraints are
  defined.
- [x] Rejected alternatives are recorded where future drift is likely.
- [x] No application code, schema, dependency, or deployment behavior changed.
