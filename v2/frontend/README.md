# v2 Frontend

Mobile-first React PWA for the v2 rewrite. See [`/docs/V2_PLAN.md`](../../docs/V2_PLAN.md)
for the full product/architecture spec this implements.

## Status

Phase 1 scaffold: routing, auth flow (signup/login wired to the backend),
theme (light/dark/system) and units contexts, a guided onboarding
walkthrough, a saved-locations dashboard (capped at 5, per the plan), and
an installable PWA shell with offline caching for API responses. The logo
and color palette are a placeholder starting point (see `docs/V2_PLAN.md`
§5 "Design ownership") — swap out `public/pwa-*.png`, `public/favicon.png`,
and the CSS custom properties in `src/index.css` when real branding lands.

The dashboard's `ForecastView` (`src/components/ForecastView.tsx`) calls
the real `/forecast/{id}` endpoint and renders the live score, best-time
window, and ranked species — `src/api/forecast.ts` types only the fields
currently rendered, not the full (much larger) response shape, since the
backend response isn't fully modeled on the frontend yet.

**Not yet built:** the profile/personalization screen (backend model
exists, no UI), regulations lookup + legal-catch calculator, multi-location
switching UI (backend supports up to 5, dashboard only shows one at a
time), and Playwright e2e coverage for the critical flows (only unit
tests exist so far).

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
npm test
npm run lint
npm run build
```
