# v2 Plan: Mobile-First Fishing Forecast App

Status: **decided — locked in from stakeholder Q&A, ready to scope into build tasks.**

## 1. Product summary

v2 is a **public product** (any angler can sign up), scoped to **US
coastal/saltwater fishing** — same domain as v1, not expanding to
freshwater/lakes. It's meant to eventually **monetize** (model TBD — no
monetization hooks get built into the first version), and the priority is
**doing it right over shipping fast** — no fixed deadline.

It replaces v1's self-hosted Flask website with a **mobile-first,
installable PWA**. No app store distribution for now. No more "surf & pier"
branding — genuinely for any coastal angler. Naming stays a placeholder
until closer to launch.

Design direction: **SaltStrong-like** — clean, visual, spot/structure-aware
mobile UX. (The actual map/structure visual-cue feature is a **post-MVP**
addition, not part of the initial build — see §4.)

## 2. What stays from v1 (port forward, don't rewrite)

The domain logic is the hard-won, tested part of this project:

- `domain/forecast.py` — `score_conditions()`, `classify_conditions()`,
  `build_activity_timeline()`
- `domain/species.py` — 851-species DB, scoring, rig/bait recommendations
- `services/nws.py`, `services/noaa.py`, `services/ndbc.py`, `services/astro.py`,
  `services/stations.py` — external data integrations
- `regulations.py` + `storage/reg_scraper.py` — 2,700+ regulation entries
- `locations.py` — curated locations + dynamic any-point resolution

These are framework-agnostic Python and drop into the new backend with
minimal changes.

**Not carried over:** v1's accounts, catch logs, and other user data. v2 is
a **clean slate** — new schema, new database, existing v1 users re-register.

## 3. Architecture decisions

| Area | Decision |
|---|---|
| Repo | **New, separate repo** — v1 (`surf-pier-forecast`) stays untouched/archived |
| Backend language | **Stays Python** (FastAPI) — preserves the species/forecast/regulations logic above without a hand-port to JS |
| Frontend | **Modern JS framework** (React or Svelte — leaning React for ecosystem/tooling maturity for a solo-maintained PWA; open to Svelte if you'd rather) |
| Distribution | **PWA only** for now — installable via browser, no app store. Native apps are a post-launch consideration once there's traction |
| Auth | **Email+password, Google/Apple OAuth, and passkeys** — all three carried forward from v1, issued as JWT access+refresh tokens (not session cookies) so a non-browser mobile client works cleanly |
| Database | SQLite to start (matches "simple/cheap self-hosted"); revisit Postgres only if/when running multiple API workers, since SQLite's write-locking doesn't scale across processes |
| Hosting | **Simple/cheap self-hosted**, same model as v1 (single VM/server) |
| Multi-location | Users can save **a handful of locations** (~5), not unlimited, not just one |
| Shared forecast links | **Public, no login required** — same as v1's `/f/<location_id>` behavior |
| Rollout | **Big-bang cutover** — v1 retired once v2 hits MVP parity, no side-by-side beta period |
| Monetization | Not built into MVP. Model undecided (freemium/ads/one-time) — defer the decision, don't design revenue hooks yet |

## 4. MVP scope

**In scope for launch:**
- Accounts (signup/login via email+password, Google/Apple, passkeys)
- Forecast dashboard — conditions, go/no-go score, ranked species, rig/bait recs
- Regulations lookup
- Save up to ~5 locations, switch between them
- Installable PWA shell (manifest + service worker)

**Explicitly deferred (not MVP):**
- Catch log
- Alerts/notifications (email/web-push)
- SaltStrong-style visual map/structure cues
- Native iOS/Android apps
- Monetization/payments
- Any migration of v1 data

## 5. Phased build plan

1. **New repo scaffold** — FastAPI backend skeleton + React frontend
   skeleton (Vite), CI, dev environment
2. **Backend core** — port domain/services modules unchanged; JWT auth
   (email/password + OAuth + passkeys); new schema (users, locations,
   profiles) via SQLAlchemy + Alembic
3. **Frontend core** — signup/login flow, mobile-first dashboard for one
   location, installable PWA shell
4. **Feature parity for MVP** — species/rig recs, regulations lookup,
   multi-location (up to 5) with switching, public shared links
5. **Cutover** — v1 retired/redirects once MVP is live and stable
6. **Post-launch backlog** — catch log, alerts, visual map cues, native
   app packaging, monetization — in whatever order makes sense once there
   are real users

## 6. Still open (small, can resolve as we go)

- React vs. Svelte, final call
- New repo name/branding
- Exact monetization model (deliberately deferred, not blocking)

---

*Decisions above came from a stakeholder Q&A session and are the working
spec. Next step: scaffold the new repo (§5.1) once you're ready to start
building.*
