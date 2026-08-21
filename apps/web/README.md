# apps/web

The canonical Next.js backend-for-frontend named by
[`docs/CANONICAL_ROADMAP.md`](../../docs/CANONICAL_ROADMAP.md)'s technical
contract: mobile-first, deployed to Vercel, the only thing the browser
talks to. The browser must never call `apps/api` (FastAPI) directly — this
app authenticates the user and signs the internal request instead.

## Status

Sprint 39 ("Responsive polish"), the "Lighthouse" acceptance sub-item:
ran a real [Lighthouse](https://developer.chrome.com/docs/lighthouse/)
audit (mobile, performance/accessibility/best-practices/SEO) against a
real production build (`next build && next start`, not `next dev`) for
`/`, `/locations`, and `/forecast/wrightsville-beach-nc` — the first
time this repo has run Lighthouse rather than just `axe-core`, which
only covers the accessibility category. Accessibility, best-practices,
and SEO all scored a clean 100 on every page; performance scored
96-98. One genuine finding, fixed: no `favicon`/`icon` file existed
anywhere in `apps/web`, so every page load made a real browser request
`GET /favicon.ico` that 404'd — a real console error, not a cosmetic
gap (Lighthouse's `errors-in-console` audit failed outright, 0/1, on
the pages tested). `app/icon.tsx` generates a real 32×32 PNG at build
time via `next/og`'s `ImageResponse` (a statically-optimized route, no
`public/` image asset needed) — a small teal/coral wave glyph using
`app/globals.css`'s own design-system palette, not an arbitrary color
choice, so this doesn't imply a branding decision beyond the one
already on record (sprint 27's row). Confirmed fixed: `best-practices`
went from 96 to 100 on the forecast page after adding it, and
`curl`/`file` confirmed a real `image/png` response. The remaining
performance-category audits (`total-blocking-time`,
`max-potential-fid`, `unused-javascript` — the last one flagging ~55
KiB of unused bytes inside Next.js/React's own framework chunks, not
identifiable app code) are recorded as a real baseline, not chased:
this sandbox's shared, unaccelerated CPU makes absolute timing numbers
noisy run-to-run (the same forecast page scored both 94 and 98 across
two runs), the same caveat sprint 26's performance-budget test already
documented for cold-path latency — further bundle-splitting work would
be premature before Phase 3 itself is complete. Screenshot-budget and
tap-target sub-items are not attempted here.

Sprint 44's remaining "security hardening" piece, CSP and security
headers: `next.config.ts`'s `headers()` attaches `Content-Security-Policy`,
`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
`Permissions-Policy`, `X-Permitted-Cross-Domain-Policies`, and
`Strict-Transport-Security` to every response, plus `poweredByHeader:
false` to drop `X-Powered-By: Next.js`. Adapted from the legacy Flask
app's `_set_security_headers` (`app.py`), not ported verbatim: apps/web
has no third-party scripts, external fonts, or non-`self` image origins
today, so the CSP is deliberately stricter — self-only on every
directive, nothing external to allow-list. `'unsafe-inline'` on
`script-src`/`style-src` is still required without a nonce (Next.js
inlines its hydration payload); nonce-based CSP would force *every*
page to render dynamically (per `node_modules/next/dist/docs/01-app/
02-guides/content-security-policy.md`'s "Without Nonces" section — this
repo's Next version renamed the nonce-generating file from
`middleware.ts` to `proxy.ts`, one of the breaking changes
`apps/web/AGENTS.md` warns to check docs for before assuming
training-data APIs still apply), a real cost not worth paying yet for
pages with no user data. `Permissions-Policy` locks geolocation/camera
fully closed, unlike the legacy app's `self`-scoped allowance for its
device-geolocation/catch-log-photo features — neither exists in
apps/web yet; sprint 31's still-open device-geolocation sub-item is the
right point to loosen it, not before. Verified against a real
production build (`next build && next start`, not just `next dev`):
`curl -D -` confirmed every header present and correctly formed on a
static page (`/`), a dynamic page (`/forecast/{id}`), and a Route
Handler (`/api/locations/search`); a headless-Chromium pass exercised
the full search → select → navigate interactive flow end-to-end and
confirmed **zero actual CSP violations** in the console (the initial
run's "404 Failed to load resource" console noise turned out to be
Next.js's own `Link` prefetch (`?_rsc=...`) 404ing and aborting when a
real navigation supersedes it — normal Next.js behavior, reproduced
identically without this PR's changes, not a CSP-caused regression); a
fresh `axe-core` spot-check across home/locations/forecast/404 found
zero violations. `npm run build`'s route table is unchanged (`headers()`
doesn't force static pages into dynamic rendering) and `npm run lint`
is clean.

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

Sprint 33 ("full/partial/stale/unavailable source-attributed
snapshots"): `ForecastCard` now renders `forecast.state` as a Badge
(fresh/stale/partial/unavailable, apps/api's own vocabulary, not
reinterpreted) instead of plain text, and a new `SourceStatusList`
shows every individual source apps/api fanned out to (marine zone,
water temperature, buoy, tides, and the wind-fallback chain when it
fires) with a human-readable label, an ok/degraded/unavailable badge,
and the raw provider error as its own line for non-ok sources — real
per-source attribution, not just the aggregate state/warnings already
shown. Verified against a real running server (every source correctly
`unavailable` in this sandbox, exactly as designed) and re-audited with
`axe-core` (zero violations). Caught one more real bug in the process:
detail/warning message text (which can contain long provider URLs)
overflowed the viewport at phone width instead of wrapping —
`break-words` on those three text nodes fixed it, confirmed by
re-screenshotting at 390px.

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

Sprint 34's remaining "timing" scope, frontend half: `ForecastCard`
now also renders an `HourlyOutlookTable` when `forecast.hourly_outlook`
is present — `apps/api`'s new `app.domain.timing.build_hourly_outlook`
24-hour fish-activity estimate (backend), shown the same way as
`TideTable`: a plain `<table>` with the current hour highlighted, an
`Activity` `Badge` (prime/high → go, med → marginal, low → neutral, so
a quiet overnight hour never reads as a failure state), and a `Why`
column showing apps/api's own plain-language reasons (`"Dawn · Minor
solunar"`, `"Major solunar"`, ...) verbatim rather than re-derived
here. Unlike `tides`, `hourly_outlook` never degrades to `null`
(astronomy always resolves), so — unlike `TideTable` — this was
verified against the real, live `/forecast/wrightsville-beach-nc` page
rather than mock data: real dawn/dusk/solunar reasoning rendered
correctly (hour 6 tagged `Prime · Peak`, `Dawn · Minor solunar`), and a
fresh `axe-core` spot-check across desktop/phone × light/dark (4
combinations) found zero violations and no horizontal overflow at
390px.

Sprint 32 (partial — hierarchy restructuring on the existing
single-location forecast page, not the multi-location dashboard):
`ForecastCard` is reordered to match the sprint's acceptance bar —
"go/no-go as a simple traffic-light headline (score/narrative
expandable, not primary); best window, conditions, confidence,
freshness first." The verdict `Badge` is now enlarged and first, a new
`deriveBestWindow` pure function derives a "best window" callout
straight from `forecast.hourly_outlook` (the longest contiguous run of
the day's best activity tag — no new fetch, sprint 34's own module
docstring flagged this as derivable), and the numeric score plus its
narrative move into a native `<details>`/`<summary>` — present, but
demoted, not the first thing a reader sees. Confidence/state/freshness
stay in the summary strip right below (a wind/wave/water-temperature
"conditions" mini-panel, the sprint's other named element, followed in
a later PR — see below). Since this sandbox's blocked upstream calls
mean the verdict is always `Unknown` (empty score/summary) on the live
path, the enlarged traffic-light badge and the expandable
score-details interaction were verified against a temporary mock
preview page (a `Good`/82 verdict with a real best-window block;
screenshotted collapsed and — via a real Playwright click on the
`<summary>` — expanded, in both color schemes, then deleted before
committing) rather than the live path, which only exercises the
`Unknown`/no-summary case; a fresh `axe-core` spot-check on both the
live page and the mock preview found zero violations and no
horizontal overflow at 390px.

Sprint 32, continued (the "conditions" mini-panel deferred above): a
new `ConditionsSummary` renders right after the "best window" callout,
matching the acceptance bar's literal ordering ("best window,
conditions, confidence, freshness"). It shows a single "Wind 10–15 kt
SW · Waves 2–3 ft · Water 76°F" line from `apps/api`'s new
`ForecastConditions.wind_range_kt`/`wave_range_ft`/`wind_direction`
fields (added in the same change, `app/domain/assembly.py`) — the
exact already-reconciled numbers `score_conditions` was computed from
— rather than re-deriving that NWS-marine-zone-over-NDBC-buoy
source-preference policy from the raw per-source fields on the
frontend a second time, which would risk drifting from
`app.domain.assembly`'s own `_reconcile_range`. Water temperature
(always present, unlike wind/wave) is labeled `(monthly avg)` when
`is_fallback` is set, same honesty rule as everywhere else this app
shows a fallback value. Since this sandbox always has both ranges
`null` on the live path, the populated case was verified the same way
as the traffic-light headline above: a temporary mock preview page
(one card with a full wind/wave/water-temp reading, one with the
water-temperature-fallback case) screenshotted and `axe-core`-checked
in both color schemes, zero violations, no overflow, then deleted
before committing; the live page was separately re-verified to still
correctly show only the water-temperature line when wind/wave are
`null`.

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
