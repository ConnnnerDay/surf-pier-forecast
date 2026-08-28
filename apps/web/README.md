# apps/web

The canonical Next.js backend-for-frontend named by
[`docs/CANONICAL_ROADMAP.md`](../../docs/CANONICAL_ROADMAP.md)'s technical
contract: mobile-first, deployed to Vercel, the only thing the browser
talks to. The browser must never call `apps/api` (FastAPI) directly — this
app authenticates the user and signs the internal request instead.

## Status

Sprint 40 ("Accessibility pass"), ledger acceptance "WCAG 2.2 AA, axe plus
keyboard/screen-reader evidence." Scope actually run against real servers,
not just static markup: (1) a full `axe-core` sweep (`wcag2a`/`wcag2aa`/
`wcag22aa` tags) across every real page in 2 viewports (desktop/phone) x
2 color schemes -- 20 combinations, 0 violations; (2) the same sweep
against `LocationSearch`'s interactive states specifically (dropdown open
with results, dropdown open with zero matches), since the earlier static
sweep only ever loaded pages at rest; (3) a scripted keyboard-only
walkthrough (Tab to the combobox, type a query, Arrow through results,
Escape, re-open, Enter to select, Tab away) checking focus order, visible
focus rings, and no keyboard trap; (4) Playwright's
`page.accessibility.snapshot()` as an **automated proxy for
screen-reader-consumable structure** -- confirming real names/roles reach
the platform accessibility tree the way a screen reader would read them,
explicitly *not* a claim of testing with actual screen-reader software,
which this environment cannot run.

Step (2) caught a real bug: `LocationSearch`'s `<input role="combobox">`
pointed `aria-controls` at the listbox's id unconditionally, but the
listbox `<div>` was only ever rendered when there were results to show
-- axe's `aria-valid-attr-value` flagged the dangling reference whenever
the dropdown was open with zero matches. The first fix (only setting
`aria-controls` when the listbox was rendered) traded that violation for
`aria-required-attr` instead, since ARIA's combobox role requires
`aria-controls` to be present unconditionally. The actual fix: the
listbox `<div id role="listbox">` is now always rendered (so
`aria-controls` always resolves), with visibility toggled via the native
`hidden` attribute -- the convention the ARIA APG combobox pattern itself
uses, and one that also excludes the empty/idle listbox from axe's checks
so an option-less `role="listbox"` never gets flagged by
`aria-required-children`. The "no matches" state renders a single
non-selectable `role="option"` (`aria-disabled`) row inside the listbox
rather than a sibling `<p>`, for the same required-owned-elements reason.

Step (1)'s sweep also caught a real WCAG 1.4.10 (Reflow) bug axe-core
can't detect automatically: `ForecastErrorCard`'s troubleshooting
paragraph named `INTERNAL_SIGNING_KEY_SECRET` in an inline `<code>` --
one long unbreakable token -- which pushed real horizontal overflow past
a 390px mobile viewport. Fixed with the same `break-words` class already
used one line above it in that component.

Step (3)'s walkthrough caught a second real bug, unrelated to ARIA
attributes: selecting a result sets `query` to the result's full name to
fill the field, and since that name still meets `MIN_QUERY_LENGTH`, it
re-triggered the same debounced search effect and reopened the dropdown
~300ms after a keyboard selection (`aria-expanded` flipping back to
`true` on its own, with no user action). Fixed with a ref flag that skips
exactly one search-effect run when the query change came from
`selectResult` rather than an edit.

Sprint 49 ("SEO and sharing"), the "public non-personal forecast pages
(organic-growth surface)" half of the acceptance bar: `app/forecast/
[locationId]/page.tsx` gains a real `generateMetadata` -- per-location
title (`"{label} Fishing Forecast"`), description, canonical URL, and
OpenGraph tags, replacing the root layout's generic default on the one
page type worth indexing individually. The description is deliberately
static copy about the location, never live score/warning text — a
degraded-conditions sentence (a real "could not connect to..." warning)
has no business being cached into a search result or link-preview
card. The actual `internalApiFetch` call was wrapped in React's
`cache()`, intended to make `generateMetadata` and the page body share
one real network round trip per request instead of two (`fetch`'s own
automatic per-request memoization can't help here regardless — ADR-004
signs every request with a fresh `requestId`/timestamp, so two calls
for the same location never look like the same `fetch(...)` call to
that mechanism). **Correction, found once sprint 41's request tracing
existed to check it**: `cache()` does not actually dedupe this in
apps/web's Next.js/Turbopack setup — a real two-server trace (grepping
one `request_id` across both services' logs) showed two distinct
signed calls reaching apps/api per page view, not one. See
`app/forecast/[locationId]/page.tsx`'s `getForecast` docstring for the
full account; `apps/api`'s own sprint-24 `SnapshotCache` keeps the
real-world cost of the second call low (single-digit milliseconds)
regardless, so this is a known, measured inefficiency, not a
correctness problem. A 404 (unknown `location_id`) calls `notFound()`
from `generateMetadata` too, matching the page body, so a bad link
gets Next's real not-found metadata instead of a fabricated title.

`app/layout.tsx` gains `metadataBase` (env-driven via
`NEXT_PUBLIC_SITE_URL`, defaulting to `http://localhost:3000` so
`next build`/`next dev` never need it set), a title template
(`"%s — Surf & Pier Forecast"`), and `openGraph`/`twitter` defaults.
`app/opengraph-image.tsx` generates a real 1200×630 PNG link-preview
card at build time via `next/og`'s `ImageResponse` (same technique as
`app/icon.tsx`'s favicon — no `public/` asset), using the design
system's own teal/coral palette, so this implies no branding decision
beyond the one already on record (sprint 27's row); a per-location
card is a follow-up, not attempted here. `app/robots.ts` allows every
page (nothing is private yet — sprint 28's job) and deliberately omits
a `Sitemap` directive: a real sitemap needs an endpoint that can
enumerate every curated location, and `apps/api`'s 101-spot dataset
isn't exposed that way today (only via `/v1/locations/search`'s
query-based lookup) — inventing a partial sitemap from whatever
happens to be searchable would misrepresent the site's real page count
more than omitting it entirely; that's this sprint's remaining open
piece, needing a small `apps/api` addition first.

Verified against two real running servers: `curl` confirmed the real
per-location `<title>`/description/canonical/`og:*` tags, the 404
page's fallback to the generic title, `robots.txt`'s real output, and
the OG image's real `image/png` response (`file` confirmed 1200×630).
A fresh `axe-core` spot-check on the forecast and home pages found
zero violations. `npm run build`'s route table gained `/robots.txt`
and `/opengraph-image` as new static routes; `/forecast/[locationId]`
is still `ƒ Dynamic`. `npm run lint`/`npm run build` both pass clean.

Sprint 34's last remaining open piece, the tide visual chart:
`ForecastCard` gains `TideChart`, rendered alongside (not instead of)
the already-complete `TideTable`. This is a **point chart with
straight lines between real predictions, not an interpolated curve**:
NOAA CO-OPS's `hilo` predictions product
(`app.providers.noaa_coops.fetch_tide_predictions`) gives real
predicted heights only at each high/low extremum, not a continuous
hourly series, so drawing a smooth cosine-shaped curve between them
would be *inventing* the in-between shape rather than showing real
predicted values — the same Integrity discipline this recovery already
enforces server-side (e.g. `is_fallback` labeling) applied to a
frontend chart choice. A small point count (typically 4-6 across the
2-day fetch window) means every point gets its own direct height
label, unlike the 24-bar hourly chart's sparser labeling — `dataviz`'s
"label selectively" guidance explicitly allows labeling every point
when there are only a handful. Single-hue on `--color-primary` (a
magnitude series, not identity — same reasoning as
`HourlyOutlookChart`), `aria-hidden="true"`, no `tabindex` inside it.
The x-axis is real elapsed time between the first and last prediction
(not evenly-spaced-by-index), since predictions can span more than one
day unevenly. Caught and fixed one real rendering bug in the process:
the first/last point's centered height label was clipped by the
`viewBox` edge with zero horizontal padding — added
`_TIDE_CHART_SIDE_PADDING` and confirmed both edge labels fully
visible in a re-screenshot. Since `tides` is always `null` on this
sandbox's live path (blocked upstream calls), verified against a
temporary mock preview page (six realistic alternating high/low
predictions; screenshotted in both color schemes at desktop and phone
widths, then deleted before committing) plus a fresh `axe-core`
spot-check — zero violations, no overflow. This closes sprint 34's
acceptance bar entirely.

Sprint 34's earlier "accessible charts" sub-item: `ForecastCard` gains
`HourlyOutlookChart`, a hand-rolled SVG bar chart rendered
alongside (not instead of) the already-complete `HourlyOutlookTable`.
Per the `dataviz` skill's own form heuristic, one activity level per
hour is a **magnitude** series, not a categorical identity, so it gets
a single-hue sequential encoding — `--color-primary` at variable
opacity, bar height *and* opacity both tracking `level` — rather than
four discrete colors for `ActivityTag`'s low/med/high/prime tiers.
`--color-go-text`/`--color-marginal-text` (used elsewhere, but only
*paired* with their own light-tint badge background) were considered
and rejected for a bar fill: per `app/globals.css`'s own comment on
`--color-danger-text`, those tokens are theme-invariant and would risk
the exact dark-mode contrast bug the sprint-27 `axe-core` pass already
found and fixed once for `--color-nogo-text` — `--color-primary`/
`--color-accent` are already theme-aware, sidestepping that risk
entirely instead of adding new tokens to re-solve it. The current hour
gets an accent-colored ring; the day's peak gets an accent dot. The
whole chart is `aria-hidden="true"` — it's a decorative duplicate of
data the table already carries as real, screen-reader-reachable text,
so re-announcing it would be noise, not a service; per that same rule,
each bar has a native SVG `<title>` (mouse-hover tooltip) but no
`tabindex`, since an `aria-hidden` subtree must never contain a
keyboard-focusable element (axe-core's `aria-hidden-focus` rule).
Since `hourly_outlook` never degrades to `None` (unlike `tides`), this
was verified against the real, live forecast page rather than mock
data — the chart's bar heights visibly match the adjacent table's
levels — plus a fresh `axe-core` spot-check across desktop/phone ×
light/dark (4 combinations) found zero violations and no horizontal
overflow. A visual chart for tides remains open, and is a separate,
smaller follow-up (a curve/point chart, not a bar chart, since the
`dataviz` skill's own form heuristic treats a small handful of
high/low events differently from a fine-grained hourly series).

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

### Other environment variables

`NEXT_PUBLIC_SITE_URL` (optional, default `http://localhost:3000`) —
the real deployed origin, once one exists (Vercel isn't provisioned
yet, `docs/CANONICAL_ROADMAP.md`'s Phase 1 blockers 9/10). Used as
`app/layout.tsx`'s `metadataBase`, so every relative canonical/
OpenGraph URL (sprint 49) resolves correctly. Never needs setting for
local dev or `next build`.

## Checks

Run these from `apps/web` — they mirror `.github/workflows/apps-ci.yml`:

```bash
npm run lint
npm run build
```

`npm run build` completing without errors is the production-build smoke
test.
