# R1 — merged `/v2` reconciliation audit

Status: **complete, documentation-only.** This satisfies gate R1's acceptance
evidence from [`docs/CANONICAL_ROADMAP.md`](CANONICAL_ROADMAP.md) and
[master issue #318](https://github.com/ConnnnerDay/surf-pier-forecast/issues/318):
a file-level and endpoint-level inventory of merged `/v2`, each item labeled
keep/adapt/replace/defer, with rationale, risks, test evidence, and the
future numbered sprint that owns the action.

No code changed in this PR. No architecture is migrated here. This is input
to R2 (CI baseline) and R3 (one canonical app path), and to the numbered
sprints once R1-R3 are merged.

## How to read the dispositions

- **Keep** — matches the canonical contract already; carry forward as-is.
- **Adapt** — the concept/business logic is sound and should survive, but the
  implementation must change (framework, auth model, database, or API shape)
  before it's canonical.
- **Replace** — the mechanism itself conflicts with a canonical decision
  (custom JWT, direct browser→FastAPI calls, SQLite-primary, no `/v1`
  prefix, WebAuthn/OAuth infra) and gets rebuilt, not ported.
- **Defer** — functionally fine but out of v1 scope per the product
  contract's deferred list (OAuth, passkeys, 2FA-by-implication, full
  regulations product, notifications, catch logging). Code may be revisited
  verbatim later; not touched for v1.

Source hierarchy used throughout: `docs/CANONICAL_ROADMAP.md` §"Canonical
technical contract", §"Required API surface", §"Product contract", and the
sprint ledger.

---

## 1. Top-line finding

`v2/backend/{domain,services,storage,locations.py,regulations.py,utils.py}`
are **byte-for-byte identical** to the legacy Flask app's equivalents
(verified via `diff -q` across all 17 files plus 3 JSON data snapshots) —
about 17,000 of `v2/backend`'s ~18,900 LOC. `v2/backend/README.md` and
`pyproject.toml` both confirm this was intentional ("copied verbatim,"
excluded from v2's own lint/type config). They're imported via
`sys.path`-relative imports (`from domain.forecast import generate_forecast`)
rather than through any versioned boundary, and there are now **two
divergent copies of the same forecast/species/regulations engine** living in
one repository. Reconciling this duplication — not just relocating it — is
the single largest piece of work R1 surfaces, and it belongs to the Phase 2
sprints (11-26), which already require characterization tests before any of
this logic is ported into the canonical FastAPI service.

Both inventory passes found **no leaked secrets or credentials** — only
expected dev-default placeholders (`jwt_secret: str = "dev-secret-change-me"`
in `v2/backend/app/core/config.py:14`, mirrored in `.env.example`) and one
test-fixture string (`test-google-secret` in `tests/test_oauth.py`). Neither
is a real credential.

---

## 2. Backend (`v2/backend/`)

### 2.1 API routes

All routes mount with **no `/v1` prefix** (`/auth/...`, `/forecast/...`,
not `/v1/...`) — itself a Replace item, owned by sprint 25 (versioned API).

| Route(s) | File | Disposition | Rationale | Risk if kept as-is | Owning sprint |
|---|---|---|---|---|---|
| `POST /auth/signup`, `/login`, `/refresh`, `GET /auth/me` | `app/api/routes/auth.py` | **Replace** | Custom JWT (HS256, `pyjwt`) + bcrypt, not Better Auth cookie sessions | Long-lived JWTs in this shape don't map to Better Auth's session model; migrating later is a full rewrite anyway | 28 |
| `POST /auth/2fa/enroll`, `/confirm`, `/disable` | `app/api/routes/auth.py` | **Defer** | TOTP 2FA isn't in the v1 auth description (email/password only) and isn't named in the deferred list either — treat as out of scope pending an explicit decision | None if simply not carried forward | 61-63 (revisit with account-security hardening) |
| `GET/POST /oauth/{google,apple}/*`, `/oauth/complete-signup` | `app/api/routes/oauth.py` | **Defer** | OAuth explicitly on the deferred list | None — working code exists as reference if OAuth is revisited | 61-63 |
| `POST/GET/DELETE /auth/passkey/*` | `app/api/routes/passkey.py` | **Defer** | Passkeys explicitly on the deferred list | None | 61-63 |
| `POST /beta-requests` | `app/api/routes/beta.py` | **Defer / reconsider** | Product decision: beta cohort is recruited via personal network + local communities, not a public "request access" landing form; this public-capture model may not be needed | Low — small, isolated, easy to add back if a public front door is wanted later | 51 |
| `GET/POST/PATCH/DELETE /locations` | `app/api/routes/locations.py` | **Adapt** | CRUD concept (label, lat/lng, default, 5-cap) is correct product shape; needs to move behind BFF auth and onto Postgres ownership model with a cap field per the round-2 decision | Cap-at-5 and default-switch logic worth preserving as a spec even though implementation is rewritten | 37 |
| `GET/PATCH /profile` | `app/api/routes/profile.py` | **Adapt** | Maps directly to required `GET/PATCH /v1/me/preferences`; field set (thresholds, styles, gear, accessibility, target species, units/theme) matches round-1/round-2 decisions | Field list is a good starting spec; must be re-validated against Postgres/Better Auth user id | 36 |
| `GET /forecast/{location_id}` | `app/api/routes/forecast.py` | **Adapt** | Maps to required `GET/POST /v1/forecasts/{location_id}[/refresh]`; 4h TTL cache matches the round-2 freshness decision | Current implementation does a **synchronous live fetch on cache miss** (no background refresh — legacy's stale-serve-then-refresh-in-thread wasn't replicated); must be re-solved under sprint 24, not assumed fixed | 21-26 |
| `GET /regulations/species`, `/lookup`, `POST /regulations/legal-catch` | `app/api/routes/regulations.py` | **Defer** | Full regulations product is on the deferred list; when it does ship it must carry the "informational only — verify with official sources" disclaimer (hard requirement) and move toward official state/NOAA feeds instead of the scraped dataset | Existing `app/core/catch_calculator.py` size-verdict logic (conservative "unknown" on ambiguous text) is worth keeping as reference for whenever regulations is prioritized | 61-63 |
| `GET /account/export`, `DELETE /account` | `app/api/routes/account.py` | **Adapt** | Required at v1 launch (sprint 45, not deferred); export/delete field scope (excludes password hash, TOTP secret, refresh tokens, passkey keys) is close to right, needs Postgres/Better Auth remapping | Password-gated delete logic needs re-checking once passwords are Better Auth-managed, not locally hashed | 45 |
| `GET /health` | `app/api/routes/health.py` | **Keep** | Trivial, framework-agnostic; canonical contract wants `GET /health/live` and `GET /health/ready` specifically | Needs splitting into live/ready, not a straight keep | 10 |

### 2.2 Auth core (`app/core/security.py`, `auth_helpers.py`, `app/api/deps.py`)

**Replace.** JWT issuance/verification, bcrypt hashing, and the
`HTTPBearer`-based `get_current_user()` dependency are the mechanism Better
Auth replaces outright. `app/core/catch_calculator.py` is the one file in
`app/core/` that's pure business logic with no auth coupling — **Defer**
alongside the regulations feature it supports, not because it's bad code.

### 2.3 Data layer

| Item | File | Disposition | Rationale | Owning sprint |
|---|---|---|---|---|
| `Settings.database_url` default | `app/core/config.py` | **Replace** | Defaults to `sqlite:///./data/app.db`; canonical requires Postgres/Neon as primary, not an afterthought | 10 |
| SQLite branch in engine creation | `app/db/session.py` | **Replace** | `connect_args={"check_same_thread": False}` special-case shows SQLite was the design target; drop once Postgres is primary | 10 |
| `User`, `RefreshToken` | `app/models/user.py` | **Replace** | Auth-stack models tied to custom JWT; Better Auth manages its own user/session tables | 28 |
| `PasskeyCredential`, `WebAuthnChallenge` | `app/models/passkey.py` | **Defer** | Ties to deferred passkey feature | 61-63 |
| `BetaAllowlistEntry` | `app/models/user.py` | **Adapt** | Allowlist concept survives even for a personally-recruited beta; simple table | 51 |
| `BetaRequest` | `app/models/beta_request.py` | **Defer** | Tied to the public beta-request form being reconsidered | 51 |
| `SavedLocation` | `app/models/location.py` | **Adapt** | Correct entity shape; `user_id` FK needs remapping to Better Auth's user id | 37 |
| `Profile` | `app/models/profile.py` | **Adapt** | JSON columns are Postgres-compatible; should become `JSONB` under Neon | 36 |
| `ForecastCache` | `app/models/forecast_cache.py` | **Adapt** | Concept (location_id-keyed, TTL) matches sprint 24's target | 24 |
| Alembic migrations `ecef7818bd1f_*`, `29a4918be459_*` | `alembic/versions/` | **Adapt** | Generic SQLAlchemy types port to Postgres cleanly; still needs a fresh Neon-targeted migration history per "no legacy migration" decision, not a reused history | 10 |
| Alembic migration `e7c724d11d95_add_passkey_tables` | `alembic/versions/` | **Defer** | Drops with the passkey feature | 61-63 |
| `storage/sqlite.py` (v2's 50-LOC shim) | `v2/backend/storage/sqlite.py` | **Replace** | Raw `sqlite3`, a second unmanaged DB file (`data/legacy_cache.db`) parallel to the SQLAlchemy-managed `app.db`, used only by the ported `reg_scraper.py`/`species_images.py`. Bypasses migrations entirely | 14/17 (or dropped if regulations/species-images stay deferred) |
| `scripts/seed_e2e.py` | `v2/backend/scripts/` | **Replace** | Raw `sqlite3` against `data/app.db`; test-fixture-only, needs a Postgres-aware rewrite if e2e is kept in this shape | 9 |

Note: legacy's full `storage/sqlite.py` (1,392 LOC user/profile/forecast/
catch-log data layer) was **not** carried into v2 — the v2 authors already
replaced it with SQLAlchemy models. That's a real precedent of doing the
right kind of rewrite, just not yet applied to the domain/services layer.

### 2.4 Ported domain/services/storage business logic

| Item | Disposition | Rationale | Owning sprint |
|---|---|---|---|
| `domain/forecast.py`, `domain/species.py` | **Adapt** | Correct business logic in the abstract (scoring, species ranking, rig/bait recs), but must go through characterization tests per the canonical contract ("port Python logic only after characterization tests capture defensible behavior") before it's trusted as the real `/v1` implementation — not assumed correct because it "already worked" in Flask | 11, 21, 22 |
| `domain/catch_insights.py` | **Defer** | Ported but no v2 route calls it — catch logging itself is deferred | 61-63 |
| `services/{ndbc,noaa,nws,astro,stations}.py` | **Adapt** | Core forecast providers named explicitly in the canonical sprint ledger (13-17); need typed contracts, bounded retries, and fixture-based tests, not a verbatim carry-over | 12-17 |
| `services/{datagov,hdx_fao,arcgis_live_feeds,bathymetry}.py` | **Defer / re-scope** | Not named anywhere in the canonical contract's required providers or sprint ledger; likely legacy scope creep beyond the documented data flow. R1 flags this for an explicit decision, not a silent drop | Needs a decision record before R1 closes the question either way — flag for product-owner input, tentatively 61-63 |
| `services/email.py` | **Adapt** | SMTP utility is auth-agnostic infrastructure; no-op until configured, fine to keep pattern | 28 (login-alert emails are 2FA-adjacent, so bundle with that decision) |
| `services/http_client.py` | **Adapt** | Shared HTTP wrapper; canonical sprint 12 wants "timeouts, bounded retries, user agent, size limit, structured errors" as an explicit policy — audit this file against that bar rather than assuming it qualifies | 12 |
| `storage/reg_scraper.py` | **Defer** | Regulations feature deferred; scraper approach also conflicts with the "source from official state/NOAA feeds" long-term intent | 61-63 |
| `storage/species_loader.py`, `species_images.py` + JSON snapshots | **Defer** | Species scoring/photos explicitly deferred together per round-2 decision | 61-63 |
| `locations.py` (root) | **Adapt** | Curated-spot + dynamic-point resolution is core product logic (sprint 19); needs the same characterization-test treatment, plus the round-2 decision to rebalance coverage across all coasts equally (not East-Coast-heavy) | 19 |
| `regulations.py` (root) | **Defer** | Bundled with the regulations feature deferral | 61-63 |
| `utils.py` (root) | **Adapt** | Trivial, framework-agnostic helpers; low risk either way | 11 |

**Missing from v2 (present in legacy, not ported):** `services/
forecast_refresh.py`, `services/notifications.py`, `services/push.py`.
Consistent with notifications being deferred — no action needed, noted for
completeness only.

### 2.5 Backend tests

**Keep the pattern.** Every route test in `v2/backend/tests/` patches the
network-touching call site (`generate_forecast`, `lookup_regulation`,
`httpx.post`, `_verify_id_token`, `webauthn.verify_*_response`,
`_send_login_alert`) — none hit live upstreams. This already matches R2's
"remove live-provider dependence from required CI" bar *for the backend*.
The gap: **no test coverage exists for the ported `domain/`, `services/`,
`storage/` layer itself** — only for FastAPI routes that call into it with
everything mocked. Characterization tests for that layer are new work owned
by sprints 11-22, not something R1 can mark as already covered.

---

## 3. Frontend (`v2/frontend/`)

### 3.1 Routes/pages

| Route | Component | Disposition | Rationale | Owning sprint |
|---|---|---|---|---|
| `/` | `Landing.tsx` | **Adapt** | Marketing shell reusable once beta-request form is reconsidered (§2.1) | 27 |
| `/login`, `/signup` | `Login.tsx`, `Signup.tsx` | **Replace** | Rebuilt against Better Auth; UX shape (email/password + age gate) is reusable reference, OAuth/passkey/TOTP UI is not | 28 |
| `/oauth/:provider/callback`, `/oauth/complete-signup` | `OAuthCallback.tsx`, `CompleteOAuthSignup.tsx` | **Defer** | OAuth deferred | 61-63 |
| `/onboarding` | `Onboarding.tsx` | **Adapt** | Static walkthrough carousel concept matches sprint 30 | 30 |
| `/dashboard` | `Dashboard.tsx` | **Adapt** | Location-switcher UX (chips, 5-cap, default star) and forecast embed are correct product shape; component internals rebuilt under sprint 32-33's go/no-go-first hierarchy | 32, 33, 37 |
| `/profile` | `Profile.tsx` | **Adapt** | Field set matches sprint 36; strip `PasskeySettings`/`TwoFactorSettings` sub-components | 36 |
| `/regulations` | `Regulations.tsx` | **Defer** | Regulations feature deferred | 61-63 |
| `*` (404) | `NotFound.tsx` | **Keep** | Trivial | 27 |

`ProtectedRoute.tsx` (client-side `AuthContext`-gated redirect) is
**Replace** — canonical auth gating happens at the Next.js BFF/middleware
level, not client-only, per sprint 29's "authorization/redirect tests."

### 3.2 Components

| Component | Disposition | Rationale |
|---|---|---|
| `ForecastView.tsx` | **Adapt** | Core product surface; presentation logic reusable, styling (`style={}` inline, no design tokens) is not |
| `Header.tsx` | **Adapt** | Nav shell structure reusable |
| `.button`/`.card`/`.field` global CSS classes | **Replace** | Not a real design system (no `<Button>`/`<Card>` components); sprint 27 needs an actual accessible-primitive gallery |
| `OAuthButtons.tsx`, `PasskeySettings.tsx`, `TwoFactorSettings.tsx` | **Defer** | Tied to deferred auth methods |
| `AccountDangerZone.tsx` | **Adapt** | Export/delete UX required at v1 (sprint 45); the hard `window.location.href` redirect workaround is a JWT-model artifact to drop, not the export/delete flow itself |

### 3.3 API client / BFF conflict

**Replace — this is R1's central frontend finding.** `src/api/client.ts`
calls FastAPI directly from the browser (`VITE_API_BASE`, default
`http://localhost:8000`); there is no Next.js layer, no server-side proxy,
no signed internal request path. This is the exact "React/Vite PWA calling
the API" pattern `docs/CANONICAL_ROADMAP.md`'s repository-reality table
already names as requiring a BFF. Every `src/api/*.ts` module (`forecast`,
`locations`, `oauth`, `passkey`, `profile`, `regulations`, `account`) is a
thin typed wrapper around this pattern — the TypeScript interfaces mirroring
backend schemas are salvageable as a starting point for shared OpenAPI-
generated types; the fetch mechanism is not. Owning sprint: 3 (architecture
decision, already recorded) through 10 (production skeleton) establish the
replacement; sprint 25 (versioned API) is where the contract gets locked.

### 3.4 Auth implementation

**Replace, in full.** JWT stored in `localStorage` (`tokenStorage` in
`src/api/client.ts:11-29`) is exactly the pattern Better Auth's httpOnly
secure-cookie sessions are meant to prevent (XSS token exposure). Bearer-
token attachment, silent 401-refresh-retry, `AuthContext`'s client-managed
session state — none of it maps to server-rendered/cookie-based sessions.
Owning sprint: 28.

### 3.5 PWA/offline

**Adapt the requirement, replace the implementation.** `vite-plugin-pwa`
config exists (manifest, icons, a `NetworkFirst` runtime-cache rule for
`/api/*`) but only implements "cache the last-loaded forecast," not the
round-2-adjacent sprint 38 requirement of full app-shell offline navigation
with graceful degradation. Also: the cache rule's URL pattern (`/api/*`)
doesn't match this app's actual request paths (no `/api` prefix is ever
used), so as written it likely never fires — noted as a latent bug in the
reference code, not something to fix here since the whole PWA layer is
being rebuilt under Next.js tooling anyway. Owning sprint: 38.

### 3.6 Accessibility

**Replace.** No `jsx-a11y`/oxlint a11y plugin enabled, only 6 total aria/
role/alt attribute usages across all `.tsx` files, several icon-only
controls (emoji theme toggle) with no verified contrast, heavy inline-style
usage instead of a token-driven system. This is a real gap against the
"not scaled back" WCAG 2.2 AA bar the product owner set explicitly in
round-3 decisions. Owning sprint: 40 (with primitives sourced from 27).

### 3.7 i18n

**Adapt the requirement, replace the implementation.** All copy is
hardcoded English directly in JSX; no externalized strings file, no
`react-i18next`/`next-intl` dependency. The round-2 decision requires
i18n-*ready* structure at v1 (not translation itself) — this is a from-
scratch requirement for the Next.js rebuild, not a portable asset. Owning
sprint: 27 (bundle with design-system/content-architecture work).

### 3.8 E2E tests

| Finding | Disposition | Rationale | Owning sprint |
|---|---|---|---|
| Playwright suite pattern (`auth`, `locations`, `regulations`, `account` specs) | **Adapt** | Full-stack, stateful, serial-execution pattern against a real seeded backend is a reasonable e2e shape to keep | 6, 9 |
| `forecast.spec.ts` | **Replace (for CI), reference (for design)** | Explicitly hits **live NOAA/NWS/NDBC/astronomy** upstreams through the real forecast pipeline, with a 60s timeout and documented sandbox flakiness. This is precisely the "live-provider tests removed from required CI" item R2 exists to fix — flagging here, fixing under R2/R3, not in this PR | R2 (blocker classification), then 21 (fixture-based provider tests) |
| Unit tests (`src/App.test.tsx`) | **Adapt** | Only covers routing/redirect; no a11y assertions — expand under sprint 40 | 40 |

---

## 4. CI

| Workflow | Disposition | Rationale | Owning gate/sprint |
|---|---|---|---|
| `.github/workflows/test.yml` (legacy Flask CI) | **Defer / retire with the app it tests** | Product decision: "the current self-hosted Flask app is retired outright, effective immediately." Its CI stays as historical scaffolding until R3 names the canonical path and archives the legacy app; not touched in R1 | R3 |
| `.github/workflows/v2-ci.yml` — `backend`/`frontend` jobs | **Adapt** | Lint/type/test/build job shape is a reasonable pattern for the canonical monorepo's CI (sprint 8); job *targets* (paths, package managers) change once `apps/web`/`apps/api` exist | 8 |
| `.github/workflows/v2-ci.yml` — `e2e` job | **Replace (blocker)** | Runs `forecast.spec.ts` against live upstream network calls as a **required** check — directly the condition R2 exists to correct ("live-provider tests removed from required CI; failures classified as regression or known debt") | R2 |
| `.github/workflows/v2-ci.yml` — `deploy` job | **Replace** | Explicit placeholder, no real target; canonical target is Vercel + Render, not whatever self-hosted target v2 implied | 10, 48 |

Known baseline already recorded in issue #318: the legacy CI workflow fails
on current `main` with pre-existing Ruff/format findings. R1 does not touch
or hide these; R2 owns classifying them.

---

## 5. Cross-cutting risks surfaced by this audit

1. **Duplicated business-logic source of truth.** Legacy `domain/services/
   storage` and `v2/backend/domain/services/storage` are identical today but
   will drift the moment either is edited independently. Phase 2 sprints
   must treat this as one porting decision, not two codebases to keep in
   sync.
2. **Unscoped services with no canonical mandate.** `datagov.py`,
   `hdx_fao.py`, `arcgis_live_feeds.py`, `bathymetry.py` (2,399 LOC
   combined) aren't named anywhere in the canonical contract's required
   providers (NWS, CO-OPS, NDBC, astronomy). This needs an explicit
   product-owner call — keep as future enrichment or drop as scope creep —
   before Phase 2 sprints port providers wholesale.
3. **No characterization tests exist yet** for the actual forecast/species/
   regulations logic, only for FastAPI routes with that logic mocked out.
   The canonical contract's "port Python logic only after characterization
   tests capture defensible behavior" is a hard gate sprint 11 must satisfy
   before any of §2.4's "Adapt" items are trusted.
4. **Synchronous cache-miss fetch, no background refresh.** v2's forecast
   route never replicated legacy's stale-serve-then-refresh-in-background
   behavior. Sprint 24 needs to solve this explicitly, not assume the
   ported cache table is sufficient.
5. **CI's one required-check live-network dependency** (`forecast.spec.ts`)
   is the concrete, single item R2 needs to resolve first — everything else
   in both test suites already avoids live upstreams.

---

## 6. What R1 does not decide

Per `AGENTS.md`, this PR is documentation-only. It does not: migrate any
code, stand up Next.js/FastAPI/Postgres, remove live-provider tests from
CI, or archive the legacy Flask app or `/v2` prototype. Those are R2, R3,
and later sprints' jobs, using this inventory as their input.
