# apps/web

The canonical Next.js backend-for-frontend named by
[`docs/CANONICAL_ROADMAP.md`](../../docs/CANONICAL_ROADMAP.md)'s technical
contract: mobile-first, deployed to Vercel, the only thing the browser
talks to. The browser must never call `apps/api` (FastAPI) directly — this
app authenticates the user and signs the internal request instead.

## Status

Still no auth — but `apps/web` can now call `apps/api` through ADR-004's
signed internal request path
([`docs/architecture.md`](../../docs/architecture.md)):
`lib/internal-signature.ts` is a TypeScript mirror of `apps/api/app/
infra/internal_signature.py`'s canonical-string + HMAC-SHA-256
primitive (same field order, same algorithm — that's the entire
contract), and `lib/internal-api-client.ts`'s `internalApiFetch` signs
and sends requests with it, failing closed (throws) if the signing-key
env vars aren't set rather than silently calling unsigned.
`app/forecast/[locationId]/page.tsx` is a Server Component proving this
end-to-end: it calls apps/api's real `GET /v1/forecasts/{id}` for
*any* recognized `location_id` and renders whatever comes back,
including a gracefully degraded response, using the sprint 27
primitives (`app/components/forecast-card.tsx`). A 404 from apps/api
(unknown location) becomes this page's own not-found state via
`notFound()`. It's marked `export const dynamic = 'force-dynamic'`
since forecasts are live, per-request data — without that, `next
build`'s static prerender pass would freeze whatever the fetch did at
build time (when apps/api isn't running) into the shell served to every
visitor. This is a signed-path proof, not the real dashboard (sprint
32) — its verdict-badge mapping is page-scoped presentation only.
`app/forecast/demo/page.tsx` (the earlier fixed-location proof, only
ever Wrightsville Beach) now just redirects to
`/forecast/wrightsville-beach-nc`, kept in case anything still links to
it rather than deleted outright.

Sprint 34's frontend half (the accessible-chart/text-alternative
rendering `apps/api`'s backend half — merged earlier — deliberately
left to `apps/web`): `app/components/forecast-card.tsx`'s
`ForecastCard` now renders a `TideTable` when `forecast.tides` is
present — a plain, properly-labeled `<table>` (`<caption>`,
`scope="col"` headers) rather than a visual chart, which is itself an
accessible representation, not a fallback for one. Times are formatted
in the *location's* timezone (`Intl.DateTimeFormat` with
`forecast.location.timezone`), not the viewer's browser timezone — a
tide time is only meaningful relative to the place it's for. Since
this sandbox's blocked upstream calls mean `tides` is always `null`
here (verified — the `noaa_coops:tides` source correctly degrades),
the table itself was visually verified against realistic mock data via
a temporary preview page (screenshotted in light/dark, then deleted
before committing) rather than the live path, which only proves the
`null` case.

Sprint 31 (partial — text search only): `app/components/
location-search.tsx` is a hand-rolled [WAI-ARIA
combobox](https://www.w3.org/WAI/ARIA/apg/patterns/combobox/) (no
dependency) — debounced, keyboard-navigable, distinct empty-results and
fetch-failure states — calling `app/api/locations/search/route.ts`, a
Route Handler that proxies apps/api's `GET /v1/locations/search`
through the signed internal path. This is the one place a Client
Component's `fetch` can land, since the browser must never call
apps/api directly (the signing secret is server-only). Demonstrated on
`app/locations/page.tsx`, where selecting a result links straight to
its `/forecast/{id}` page — search and forecast are separate
pages/concerns joined by a plain navigation link, not folded into one
page. Device geolocation, map search, and station-preview/ambiguity
states aren't attempted. Verified interactively via headless Chromium,
including the full search → select → view-forecast flow (typed a
query, saw one real matching result, arrow-keyed to it, pressed Enter,
clicked "View forecast," landed on a real rendered forecast page) — not
just curl, since this is the app's first genuinely interactive (Client
Component) UI.

Sprint 27 (design system) has a first pass: `app/globals.css` defines
light/dark design
tokens via Tailwind v4's `@theme` (colors, radius, font), starting from
`v2/frontend/src/index.css`'s teal/coral palette per
[`docs/R1_RECONCILIATION_AUDIT.md`](../../docs/R1_RECONCILIATION_AUDIT.md)
§3.2 ("Replace" applied to that app's `.button`/`.card`/`.field` global
CSS classes, not its color choices), plus semantic `go`/`marginal`/`nogo`
tokens for sprint 32's traffic-light dashboard headline. `app/components/ui/`
holds the accessible primitives that row's "gallery ... accessible
primitives" acceptance bar names: `Button` (real `<Link>` when `href` is
given, native `<button>` otherwise, visible focus ring), `Card`, `Badge`
(status pill — the verdict is always the visible text label, never color
alone), `Field` (label/hint/error wired together via `aria-describedby`/
`aria-invalid`, replacing §3.2's flagged `.field` class), and `Container`
(mobile-first responsive width). `app/page.tsx` is a gallery page
showcasing all of them at phone and desktop widths; `app/not-found.tsx`
is the trivial 404 R1's §3.1 disposition table names. **"Surf & Pier
Forecast" and this palette are a working placeholder identity, not a
final branding decision** — see `docs/CANONICAL_ROADMAP.md`'s sprint 27
row; the product owner has directed proceeding with Phase 3 work under
this placeholder rather than blocking on a name/visual-identity decision.
Full WCAG 2.2 AA verification (axe + keyboard/screen-reader evidence) and
i18n-ready string externalization remain sprint 40/27's respective
follow-up scope, not formally attempted here — but an `axe-core`
spot-check was run against every real page (`/`, `/locations`,
`/forecast/{id}`, the 404 page, both color schemes, plus the location
search dropdown open and the gallery's error `Field` in dark mode) and
found two genuine bugs, both fixed: (1) `--color-nogo-text` used
directly on the page background (Field's error message,
`ForecastErrorCard`) failed WCAG AA contrast in dark mode (2.6:1,
needs 4.5:1) — that token is correctly theme-invariant for `Badge`'s
own pill (its background is also theme-invariant, so the pairing was
never broken there), but bare status text needed its own
theme-adjusted token, `--color-danger-text`, added for exactly that
use; (2) `LocationSearch`'s dropdown used `<ul>`/`<li role="option">`,
which breaks ARIA's required-owned-elements relationship (overriding
`<li>`'s implicit "listitem" role to "option" makes the `<ul>` no
longer see real list items) — switched to plain `<div role="listbox">`/
`<div role="option">`, which carry no conflicting implicit role. Zero
violations across every page/state after both fixes. This is a
spot-check with today's real screens, not the formal sprint 40 audit
(no captured screen-reader-software evidence, no CI-wired regression
gate) — the general accessible-by-construction claim for sprint 27's
primitives already made above stands, now with automated verification
behind it rather than just design intent.

Real screens land in the remaining Phase 3 sprints (28
onward), reusing UX patterns catalogued as "Adapt" in
[`docs/R1_RECONCILIATION_AUDIT.md`](../../docs/R1_RECONCILIATION_AUDIT.md)
§3 rather than the `v2/frontend` implementation verbatim.

Lint is [`oxlint`](https://oxc.rs/), not `next lint` — Next.js 16 removed
the built-in `next lint` command, and `oxlint` is already the convention
used by `v2/frontend`.

## Local dev

```bash
cd apps/web
npm install
npm run dev
```

Open http://localhost:3000 — should render the design-system gallery page.

### Calling apps/api locally

`/locations` and `/forecast/{id}` need apps/api running with a
**matching** signing key (see `apps/api/README.md`'s "Local dev"
section) — any value works as long as both sides agree:

```bash
# terminal 1, from apps/api
INTERNAL_SIGNING_KEY_ID=dev-key INTERNAL_SIGNING_KEY_SECRET=dev-secret-please-change \
  uvicorn app.main:app --reload --port 8000

# terminal 2, from apps/web
INTERNAL_API_BASE_URL=http://localhost:8000 \
INTERNAL_SIGNING_KEY_ID=dev-key INTERNAL_SIGNING_KEY_SECRET=dev-secret-please-change \
  npm run dev
```

Open http://localhost:3000/locations, search for a curated location
(e.g. "wrightsville"), and select it to view its forecast — or go
straight to http://localhost:3000/forecast/wrightsville-beach-nc. In
this sandboxed environment apps/api's own upstream (NOAA/NWS/NDBC)
calls are blocked
([`docs/R2_CI_BASELINE.md`](../../docs/R2_CI_BASELINE.md)'s
no-live-provider-dependence rule), so expect a gracefully degraded
forecast (state `partial`, confidence `low`, real warning messages) —
that's the signed path and the degradation path both working, not a
bug. Against real network access the same page renders a live forecast.

## Checks

Run these from `apps/web` — they mirror `.github/workflows/apps-ci.yml`:

```bash
npm run lint
npm run build
```

`npm run build` completing without errors is the production-build smoke
test.
