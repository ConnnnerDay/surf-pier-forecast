# v2 Plan: Mobile-First Fishing Forecast App

Status: **phases 1-4 complete** — scaffold, the v1 forecast-engine port,
full backend auth (email/password + Google/Apple OAuth + passkeys +
2FA + login-alert emails), a 4-hour TTL forecast cache, a full profile
API, matching frontend screens, and Playwright e2e coverage wired into
CI. See [`/v2/README.md`](../v2/README.md) for the current feature list
and what's still open (regulations lookup, multi-location switching UI,
in-app feedback). See §7.

**Repo note:** §4 below still says "new, separate repo" — that was the
plan, but this session's GitHub access couldn't create a new repository,
so v2 lives at `/v2` in this repo instead. Revisit moving it later if it
still matters.

## 1. Product summary

v2 is a **public product** (any angler can sign up), scoped to **US
coastal/saltwater fishing** — same domain as v1, not expanding to
freshwater/lakes/inland. All US coastlines (East, Gulf, West) are
**equal priority** — no regional favoritism like v1's East-Coast-centric
defaults.

It's meant to eventually **monetize** (model TBD — no monetization hooks
built into the first version). Priority is **doing it right over shipping
fast** — no fixed deadline. It replaces v1's self-hosted Flask website
with a **mobile-first, installable PWA**. No app store distribution for
now. No "surf & pier" branding — a name gets picked later; starting from a
placeholder.

Design direction: **SaltStrong-like** — clean, visual, spot/structure-aware
mobile UX, designed from zero (no existing brand assets). The actual
map/structure visual-cue feature itself is **post-MVP**, not part of the
initial build.

**Launch strategy:** private beta first (simple allowlist — you manually
admit specific users), not open public signup. Public launch, real
monetization, and legal-hardening happen after the beta proves out.

## 2. What stays from v1 (port forward, don't rewrite)

- `domain/forecast.py` — `score_conditions()`, `classify_conditions()`,
  `build_activity_timeline()`
- `domain/species.py` — 851-species DB, scoring, rig/bait recommendations
- `services/nws.py`, `services/noaa.py`, `services/ndbc.py`, `services/astro.py`,
  `services/stations.py` — external data integrations
- `regulations.py` + `storage/reg_scraper.py` — 2,700+ regulation entries
- `locations.py` — dynamic any-point resolution logic (station lookup,
  timezone-for-point, geocoding) carries over; the **curated spot list
  itself gets expanded** — more spots, rebalanced across all US coasts
  equally instead of v1's East Coast-heavy defaults

These are framework-agnostic Python and drop into the new backend with
minimal changes.

**Not carried over:** v1's accounts, catch logs, and other user data. v2 is
a **clean slate** — new schema, new database, existing v1 users re-register.

## 3. Fishing domain decisions

| Area | Decision |
|---|---|
| Fishing styles | **All of v1's** — surf, pier, kayak, inshore, offshore/nearshore. No narrowing. |
| Skill level | **Both**, same as v1 — plain-language headline score for casual users, full data available underneath for experienced anglers |
| Species DB | **Keep the 851-species DB as-is** — no trimming |
| Forecast priority | **Tide movement and wind/wave height** are what matter most to prioritize/surface prominently; solunar, water temp, and pressure stay in the model but aren't the headline factors |
| Regulations | Lookup (species/state size & bag limits) **plus a legal-catch calculator** — user enters species + length, app says if it's legal to keep right now, in their state/water |
| Regulations coverage | **Continental US coastal states only** for MVP — no Alaska/Hawaii/territories yet |
| Bait/rig style | **Both live bait and artificial lures**, same as v1 — no lean either direction |
| Safety info (small craft advisories, rip currents) | **Present but secondary**, same as v1 — included in conditions/score, not headlined |
| Best-time-to-fish | Dashboard gets a **headline "best window today" callout** pulled from the existing hourly activity-timeline model, with the full timeline available below/on tap |
| Personalization | Beyond v1's max-wind/max-surf thresholds and offshore/inshore profile: add **gear/tackle limitations**, **mobility/accessibility factors**, **experience-level filtering**, and a **simple target-species favorites list** that biases rankings toward species the user actually wants |

**Noted for later (not MVP, but worth designing toward):**
- **Crowdsourced real-time bite reports** ("what's biting near me" from other users) — post-MVP, ties into the community/visual-cues direction, needs moderation/spam handling
- **Camera-based fish species ID** (photo → species + legal-to-keep check) — interesting, explicitly post-MVP, needs an image-classification component
- **Species migration/run alerts** (e.g. "striped bass run starting near you") as a direction for the eventual alerts feature, beyond v1's simple condition-threshold alerts
- Catch log (post-MVP) should capture **species, length/weight, photo, GPS location of the catch, and kept-vs-released** when it's built — GPS location of individual catches is more sensitive than location-level data and needs privacy handling when implemented

## 4. Architecture decisions

| Area | Decision |
|---|---|
| Repo | **New, separate repo** — v1 (`surf-pier-forecast`) stays untouched/archived |
| Backend language | **Python / FastAPI** — preserves the species/forecast/regulations logic above without hand-porting to JS |
| Frontend | **React** (Vite) |
| Distribution | **PWA only** for now — installable via browser, no app store |
| Offline behavior | Service worker **caches the last-loaded forecast** so it's viewable with no/spotty signal (e.g. at the water) |
| Auth | Email+password, Google/Apple OAuth, and passkeys — all carried forward from v1, issued as **JWT access+refresh tokens** (not session cookies) |
| Database | SQLite to start; revisit Postgres only if/when running multiple API workers |
| Hosting | Simple/cheap self-hosted (single server), same model as v1 |
| Backups | **Automated regular (e.g. nightly) off-server backups** from day one — real user accounts exist even in beta |
| Multi-location | Users can save **~5 locations**, not unlimited, not just one |
| Shared forecast links | **Public, no login required** — same as v1's `/f/<location_id>` |
| Rollout | **Big-bang cutover** once MVP is ready — no long side-by-side v1/v2 period |
| Beta access | **Private beta via simple allowlist** you manage manually before any public/open signup |
| Monetization | Not built into MVP; model undecided (freemium/ads/one-time) — deliberately deferred |
| Legal | Basic Privacy Policy + ToS drafted as part of the build (not a lawyer substitute), gating public (not beta) launch |
| Error monitoring | **Self-hosted Sentry** (accepted added ops cost, consistent with self-hosting everything else) |
| Usage analytics | **Self-hosted PostHog** (full-featured product analytics — funnels/retention, not just pageviews) |
| CI/CD | GitHub Actions runs tests/lint on every change; **auto-deploys to the server on merge to main** |
| Team process | Lightweight for now (linting/formatting + README) — formalize PR templates/contribution docs only once a real second contributor shows up (none lined up yet) |
| Beta feedback | **In-app feedback form** — low-friction way for beta testers to report issues/ideas |

## 5. UX & operational decisions

| Area | Decision |
|---|---|
| Units | Support **both imperial and metric**, user-toggleable (not imperial-only like v1) |
| Theme | **Light + dark mode**, following system setting by default with a manual override |
| Onboarding | **Guided walkthrough** after signup (a few screens covering the score, timeline, species list) before landing on the dashboard |
| Visual identity | **Fully new** — clean break from v1's look, not an evolution of it |
| Design ownership | **Claude proposes a starting logo/icon/color palette** as part of the build so there's something functional for the beta; refine or replace later |
| Session length | **Long-lived (weeks)** with silent refresh-token renewal — casual-use app, minimize re-login friction |
| Data rights | **Self-service export + delete** in account settings from the start |
| Device/browser support | **Modern mobile browsers only** (recent iOS Safari, Android Chrome) as the real target; desktop gets a reasonable but not heavily-optimized experience |
| Data freshness transparency | Go beyond v1's silent stale-cache fallback — **timestamp every data point's freshness in the UI** so users always know how current what they're seeing is, especially when an upstream source (NOAA/NWS/NDBC) is degraded |
| Testing rigor | **Thorough** — unit tests for domain logic (ported from v1) + integration tests for the API + end-to-end tests (Playwright) for critical flows (signup, viewing a forecast) |
| Budget | Keep hosting/services **minimal (~$0-25/mo)** — free tiers and cheap server, consistent with self-hosting everything |
| Age policy | **13+** minimum to create an account |
| Account security | **Stricter than v1**: optional 2FA (TOTP authenticator), tighter rate limiting/lockouts on failed logins, and login-alert emails on sign-in from a new device/location. (v1's existing password-complexity rule is kept, not raised further.) |
| Future UGC moderation | For the eventual crowdsourced bite-reports feature: **community-driven** (flag/upvote, auto-hide past a threshold) rather than manual review |
| Help content | A **simple FAQ page** alongside the in-app feedback form |
| Share previews | **Rich Open Graph preview cards** (image showing score/conditions) when a shared forecast link is posted elsewhere |
| Curated spot count | Target **100+ curated locations** at launch — denser out-of-the-box coverage across all US coasts, on top of the existing dynamic any-point resolution |
| Beta discovery | A **public landing page with a "request beta access" form** — you manually approve requests onto the allowlist. Gives the private beta a shareable front door without opening signup |
| Affiliate links (future monetization) | Worth considering later — tackle/gear affiliate links tied to existing rig/bait recommendations. Not built now. |
| Partnerships (bait shops, marinas, tournaments, charters) | Noted as an interesting future direction, not a current priority |

## 6. MVP scope

**In scope for launch:**
- Accounts (signup/login via email+password, Google/Apple, passkeys, optional 2FA), gated by the beta allowlist, 13+ age gate, login-alert emails
- Guided onboarding walkthrough for new users
- Forecast dashboard — conditions with freshness timestamps, go/no-go score, headline best-time-window callout, ranked species, rig/bait recs (both live bait & artificial)
- Personal profile: max-wind/max-surf thresholds, gear/tackle limitations, mobility/accessibility factors, experience-level filtering, target-species favorites list, unit toggle (imperial/metric), light/dark theme toggle
- Regulations lookup + legal-catch calculator (continental US coastal states)
- Save ~5 locations, switch between them; expanded curated-spot list (100+) across all US coasts equally
- Installable PWA shell with offline caching of the last forecast
- Public shared forecast links with rich Open Graph preview cards
- Self-service data export + account deletion
- In-app feedback form + a simple FAQ page
- Public landing page with a "request beta access" form
- Self-hosted error monitoring (Sentry) + usage analytics (PostHog)
- Automated DB backups
- Thorough test coverage (unit + integration + Playwright e2e for critical flows)

**Explicitly deferred (not MVP):**
- Catch log (species, length/weight, photo, GPS, kept/released — see §3)
- Alerts/notifications (condition-threshold, plus species migration/run alerts as a future direction)
- Crowdsourced real-time bite reports (community flag/upvote moderation, when built)
- Camera-based fish species ID
- SaltStrong-style visual map/structure cues
- Native iOS/Android apps
- Monetization/payments, including affiliate/tackle-shop links
- Partnership/integration angles (bait shops, marinas, tournaments, charters)
- Public (non-beta) open signup and the associated legal hardening
- Formal team/contribution process
- Any migration of v1 data
- Alaska/Hawaii/territories regulations coverage

## 7. Phased build plan

1. ✅ **Repo scaffold** (done — see `/v2`, built inside this repo rather than
   a separate one) — FastAPI backend skeleton + React (Vite) frontend
   skeleton, GitHub Actions CI (`.github/workflows/v2-ci.yml`, lint + type
   check + test + build), JWT auth (email/password only so far — signup,
   login, refresh, `/me`), beta-allowlist gate, age gate, saved-locations
   CRUD (capped at 5), installable PWA shell with offline API caching,
   theme + units contexts, guided onboarding, a public beta-request
   landing page. **Not yet done from this step:** auto-deploy on merge
   (placeholder job only, no server to deploy to yet), Sentry/PostHog.
2. ✅ **Forecast engine port** (done) — `domain/forecast.py`,
   `domain/species.py`, `domain/catch_insights.py`, `locations.py`,
   `regulations.py`, `utils.py`, and their `services/`/`storage/`
   dependencies copied verbatim into `v2/backend` (same top-level layout
   as v1, so imports needed zero changes); a new minimal
   `storage/sqlite.py` shim replaces v1's full user/profile DB layer,
   providing just the two small read-through caches
   (`species_image_cache`, `reg_scrape_cache`) those modules still touch
   directly. `GET /forecast/{location_id}` calls
   `locations.py:build_dynamic_location()` → `domain/forecast.py:generate_forecast()`
   for real, now fronted by a 4-hour TTL cache (`ForecastCache` table,
   matching v1's cache window — see item 3) — verified with a live browser
   walkthrough producing a real scored forecast with ranked species and
   bait/rig recs. Ported code is excluded from v2's own ruff/mypy config
   (carries v1's lint debt, not this project's to fix as a side effect of
   the port — see `v2/backend/pyproject.toml`). Also stripped a
   `source_file` field (an absolute server filesystem path) from
   regulation data before it reaches the API response, since v1 never
   filtered that either. **Known follow-up:** no background refresh —
   v1's stale-serve-then-refresh-in-a-thread behavior isn't replicated,
   a cache miss/expiry is still a synchronous live fetch on the request
   that hits it.
3. ✅ **Backend core** (done) — Google/Apple OAuth (JWKS-verified
   `id_token`, with a DOB-collection step for first-time signups since
   neither provider reliably hands back a birthdate and the 13+ age gate
   still has to apply); passkeys/WebAuthn (registration + discoverable/
   usernameless login, multiple credentials per user, `webauthn` package);
   2FA enrollment (`/auth/2fa/enroll` + `/confirm` + `/disable`, TOTP
   verification already existed from the scaffold); login-alert emails
   (backgrounded, new-device detection via a per-user device-label history,
   ported v1's `services/email.py` SMTP utility — safe no-op until
   `SMTP_HOST`/`SMTP_FROM` are set); profile API (`GET`/`PATCH /profile`,
   full CRUD on every personalization field, wired into the forecast
   route's profile-to-dict adapter). All net-new engineering, not a port —
   v1 never actually had OAuth/WebAuthn routes despite CLAUDE.md
   describing them.
4. ✅ **Frontend core** (done) — signup/login flow (allowlist-gated, age
   gate, guided onboarding walkthrough), mobile-first dashboard with a
   real forecast view (score, best-time window, ranked species),
   installable PWA shell with offline caching, unit/theme toggles, a full
   profile screen (thresholds, styles, gear/accessibility, target
   species, units/theme, 2FA and passkey management), Google/Apple
   sign-in buttons + OAuth callback/complete-signup pages, passkey
   register/login UI (`navigator.credentials`). **Not yet done:** in-app
   feedback form, FAQ page.
5. ✅ **Playwright e2e coverage** (done) — `v2/frontend/e2e/`, wired into
   `.github/workflows/v2-ci.yml` as a dedicated `e2e` job (sets up both
   Python and Node, seeds a fixed beta-allowlist fixture set via
   `backend/scripts/seed_e2e.py`, boots both servers, runs the suite).
   Covers signup (allowlist rejection + happy path + onboarding),
   login (success, wrong password, logout), and adding a location through
   to a real rendered forecast. All 6 tests pass reliably against a real
   backend + real (if network-degraded in this sandbox) forecast engine.
6. **Feature parity for MVP (remaining)** — legal-catch calculator,
   multi-location switching UI (API supports it, UI shows one at a time),
   OG preview cards for shared links, expanded curated-spot list (100+)
   across all coasts, self-service export/delete, starter logo/icon/palette
   (placeholder logo done, see `v2/frontend/public/pwa-*.png`), public
   landing page with beta-request form (done)
7. **Private beta** — admit allowlisted users, collect feedback, fix issues
8. **Cutover** — v1 retired/redirects once v2 clears the beta and is ready
   for public signup (legal pages finalized at that point)
9. **Post-launch backlog** — catch log, alerts, visual map cues, native
   app packaging, monetization, formal contribution process — ordered
   once there are real users and (if a collaborator joins) more hands

## 8. Still open (small, resolve as we go)

- Product name / domain
- Exact monetization model
- Who ends up on the private beta allowlist

---

*This is the working spec. Next step: OAuth/passkeys, 2FA enrollment, and
the profile API (§7.3) — see `/v2/README.md` for local dev setup.*
