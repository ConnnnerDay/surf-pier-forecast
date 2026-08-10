# v2 Plan: Mobile-First Fishing Forecast App

Status: **draft — for review, no code changes yet.**

## 1. What's changing

v1 is a self-hosted Flask website with session-cookie auth, server-rendered
Jinja2 pages, and optional accounts. v2 pivots to a **mobile-first,
installable web app (PWA)** where creating an account is the front door:
sign up, save your location(s), and get the fishing forecast for your area
on your phone. No more "surf & pier" framing — this should read as a
general fishing-forecast product for any US coastal (and eventually
inland/lake) location.

Backend is being rewritten alongside the frontend, not just re-skinned.

## 2. What stays (don't reinvent this)

The domain logic in v1 is the hard-won, tested part of this project and
should be **ported forward as-is**, not rewritten:

- `domain/forecast.py` — `score_conditions()`, `classify_conditions()`,
  `build_activity_timeline()`
- `domain/species.py` — 851-species DB, scoring, rig/bait recommendations
- `domain/catch_insights.py` — catch-log pattern analysis
- `services/nws.py`, `services/noaa.py`, `services/ndbc.py`, `services/astro.py`,
  `services/stations.py` — external data integrations
- `regulations.py` + `storage/reg_scraper.py` — 2,700+ regulation entries
- `locations.py` — curated locations + dynamic any-point resolution

These are framework-agnostic Python and should drop into a new backend
service with minimal changes (mostly import path updates).

## 3. Target architecture

### Backend: FastAPI (rewrite from Flask)

- **FastAPI** + **Pydantic** models for the JSON API — async-friendly, fits
  a mobile client better than session-cookie Flask, gives us OpenAPI docs
  for free (replaces hand-maintained `/api/openapi.json`)
- **SQLAlchemy + Alembic** replacing the current hand-written `storage/sqlite.py`
  CRUD + `migrate_sqlite.py` — proper migrations instead of one-shot schema setup
- **Auth: JWT access + refresh tokens**, not Flask session cookies — required
  for a mobile client that isn't a browser. Keep passkeys/WebAuthn and
  Google/Apple OAuth from v1; add device/session listing so users can see
  and revoke logged-in devices
- DB: SQLite is fine for dev/self-host; evaluate Postgres for production
  once the app is not single-process (needed if we run more than one API
  worker/process, since SQLite's writer-locking doesn't scale across
  processes the way the current single-process Flask deployment tolerates)
- Push notifications: carry over `services/push.py` (VAPID web push) —
  this already works for browser-based PWAs, which covers the mobile-web
  target directly

### Frontend: Mobile-first PWA

- Rebuild `templates/` + `static/` as a dedicated mobile-first frontend
  (open decision — see §5) instead of the current desktop-first Jinja2
  pages with responsive CSS bolted on
- Installable (manifest + service worker already exist in v1 and are
  reusable groundwork) — "Add to Home Screen" is the initial distribution
  path, no app store needed for launch
- Signup/login is the entry point — no anonymous dashboard access like v1
  currently allows before the setup wizard

### Non-goals for the first v2 milestone

- Native iOS/Android apps (App Store/Play Store) — revisit once the PWA
  and API are validated with real users
- Multi-region/international expansion — stay US-coastal for parity with v1
- Payments/monetization — not in scope until the core product is live

## 4. Data model changes

- Accounts become **required**, not opt-in — every forecast view is tied
  to a signed-in user and their saved location(s)
- A user can save **multiple locations** (v1 supports one default + shared
  links; v2 should support a small list, e.g. home spots, with quick switching)
- Carry forward: `profiles` (fishing preferences), `catch_log` (with
  condition snapshot), `push_subscriptions`, `notification_log`, WebAuthn
  credentials
- Shareable links (`/f/<location_id>`) — keep the concept, but decide
  whether unauthenticated viewers can see a shared forecast or must sign up
  first (open decision, §5)

## 5. Open decisions (need your input before/while building)

1. **Frontend stack** — plain server-rendered mobile-first templates (fastest,
   closest to v1) vs. a JS framework (React/Svelte + Vite) for a more
   app-like feel (more work, better long-term UX for a mobile product)
2. **Shared forecast links** — public (no login) or gated behind signup in v2?
3. **Hosting/infra** — staying on the current self-hosted single-VM model,
   or moving to something that supports multiple API workers (relevant to
   the SQLite-vs-Postgres call above)?
4. **Timeline/rollout** — big-bang cutover once v2 is ready, or run v1 and
   v2 side by side for a while (e.g. v2 behind a beta flag/subdomain)?
5. **Existing users** — do accounts/catch logs from v1 need to migrate into
   v2's new schema, or is v2 a clean slate?

## 6. Phased rollout

1. **Backend skeleton** — new FastAPI service, JWT auth, port domain/services
   modules untouched, stand up account signup/login + "save a location, get
   a forecast" as the one core API flow
2. **Frontend skeleton** — mobile-first signup/login + single-location
   dashboard, installable PWA shell
3. **Feature parity** — species rankings, rig recommendations, regulations,
   catch log, notifications, multi-location support
4. **Cutover** — v1 retired/redirects to v2 once parity + the open decisions
   above are resolved
5. **Post-launch** — revisit native app packaging once the PWA has real usage

---

*This doc is the starting point for review — nothing here is final. Once
the open decisions in §5 are answered, this becomes the working spec for
implementation.*
