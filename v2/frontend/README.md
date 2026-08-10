# v2 Frontend

Mobile-first React PWA for the v2 rewrite. See [`/docs/V2_PLAN.md`](../../docs/V2_PLAN.md)
for the full product/architecture spec this implements.

## Status

Routing, auth (signup/login/logout, Google/Apple sign-in buttons + OAuth
callback/complete-signup pages, passkey register/login via
`navigator.credentials`), theme (light/dark/system) and units contexts, a
guided onboarding walkthrough, a saved-locations dashboard (capped at 5,
per the plan), and an installable PWA shell with offline caching for API
responses. A full profile screen (comfort thresholds, fishing
styles/gear/accessibility, target-species favorites, units/theme, 2FA and
passkey management). The logo and color palette are a placeholder starting
point (see `docs/V2_PLAN.md` §5 "Design ownership") — swap out
`public/pwa-*.png`, `public/favicon.png`, and the CSS custom properties in
`src/index.css` when real branding lands.

The dashboard's `ForecastView` (`src/components/ForecastView.tsx`) calls
the real `/forecast/{id}` endpoint and renders the live score, best-time
window, and ranked species — `src/api/forecast.ts` types only the fields
currently rendered, not the full (much larger) response shape, since the
backend response isn't fully modeled on the frontend yet.

A `/regulations` page (`src/pages/Regulations.tsx`) looks up a species +
state (species via `<datalist>` autocomplete backed by
`GET /regulations/species`, state limited to the continental US coastal
states the dataset covers) and shows status/size/bag-limit/season/notes,
plus an inline "check a catch" length input that calls
`POST /regulations/legal-catch` for a legal/too-small/too-large/
cannot-target/unknown verdict.

**Not yet built:** multi-location switching UI (backend supports up to 5,
dashboard only shows one at a time), in-app feedback form, FAQ page.

## Setup

```bash
cd v2/frontend
npm install
cp .env.example .env   # point VITE_API_BASE at your backend if not localhost:8000
npm run dev
```

Open **http://localhost:5173**.

## Test / lint / build

```bash
npm test          # Vitest unit tests
npm run lint
npm run build
npm run test:e2e  # Playwright — see below
```

## e2e tests

`e2e/` holds Playwright specs covering signup (allowlist rejection + happy
path + onboarding), login (success, wrong password, logout), adding a
location through to a real rendered forecast, and the regulations lookup +
legal-catch calculator flow. `playwright.config.ts` boots
both the frontend dev server and a real backend
(`e2e/start-backend.sh` resets the dev DB and seeds a fixed set of
beta-allowlist emails via `../backend/scripts/seed_e2e.py`) automatically —
just run:

```bash
npm run test:e2e
```

First time, install browsers: `npx playwright install --with-deps chromium`.
The forecast test hits the real live `generate_forecast()` pipeline
(NOAA/NWS/NDBC + astronomy), so it's slower than the others (~15-20s
typical) and has its own extended per-test timeout.
