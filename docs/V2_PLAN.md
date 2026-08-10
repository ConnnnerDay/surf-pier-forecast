# v2 Plan: Mobile-First Fishing Forecast App

Status: **locked — full spec from stakeholder Q&A, ready to scaffold.**

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

## 5. MVP scope

**In scope for launch:**
- Accounts (signup/login via email+password, Google/Apple, passkeys), gated by the beta allowlist
- Forecast dashboard — conditions, go/no-go score, headline best-time-window callout, ranked species, rig/bait recs (both live bait & artificial)
- Personal profile: max-wind/max-surf thresholds, gear/tackle limitations, mobility/accessibility factors, experience-level filtering, target-species favorites list
- Regulations lookup + legal-catch calculator (continental US coastal states)
- Save ~5 locations, switch between them; expanded curated-spot list across all US coasts equally
- Installable PWA shell with offline caching of the last forecast
- In-app feedback form
- Self-hosted error monitoring (Sentry) + usage analytics (PostHog)
- Automated DB backups

**Explicitly deferred (not MVP):**
- Catch log (species, length/weight, photo, GPS, kept/released — see §3)
- Alerts/notifications (condition-threshold, plus species migration/run alerts as a future direction)
- Crowdsourced real-time bite reports
- Camera-based fish species ID
- SaltStrong-style visual map/structure cues
- Native iOS/Android apps
- Monetization/payments
- Public (non-beta) open signup and the associated legal hardening
- Formal team/contribution process
- Any migration of v1 data
- Alaska/Hawaii/territories regulations coverage

## 6. Phased build plan

1. **New repo scaffold** — FastAPI backend skeleton + React (Vite) frontend
   skeleton, GitHub Actions CI (lint/test) + auto-deploy on merge, dev
   environment, self-hosted Sentry + PostHog wired in early so they cover
   the whole build
2. **Backend core** — port domain/services modules unchanged; JWT auth
   (email/password + OAuth + passkeys) with a beta allowlist gate; new
   schema (users, locations, profiles) via SQLAlchemy + Alembic; automated
   backup job
3. **Frontend core** — signup/login flow (allowlist-gated), mobile-first
   dashboard for one location, installable PWA shell with offline caching,
   in-app feedback form
4. **Feature parity for MVP** — species/rig recs, regulations lookup,
   multi-location (~5) with switching, public shared links, expanded
   curated-spot list across all coasts
5. **Private beta** — admit allowlisted users, collect feedback, fix issues
6. **Cutover** — v1 retired/redirects once v2 clears the beta and is ready
   for public signup (legal pages finalized at that point)
7. **Post-launch backlog** — catch log, alerts, visual map cues, native
   app packaging, monetization, formal contribution process — ordered
   once there are real users and (if a collaborator joins) more hands

## 7. Still open (small, resolve as we go)

- Product name / domain / logo & color palette
- Exact monetization model
- Who ends up on the private beta allowlist

---

*This is the working spec. Next step: scaffold the new repo (§6.1) when
you're ready to start building.*
