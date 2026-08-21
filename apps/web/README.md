# apps/web

The canonical Next.js backend-for-frontend named by
[`docs/CANONICAL_ROADMAP.md`](../../docs/CANONICAL_ROADMAP.md)'s technical
contract: mobile-first, deployed to Vercel, the only thing the browser
talks to. The browser must never call `apps/api` (FastAPI) directly — this
app authenticates the user and signs the internal request instead.

## Status

Still no routing or auth — but `apps/web` can now call `apps/api`
through ADR-004's signed internal request path
([`docs/architecture.md`](../../docs/architecture.md)):
`lib/internal-signature.ts` is a TypeScript mirror of `apps/api/app/
infra/internal_signature.py`'s canonical-string + HMAC-SHA-256
primitive (same field order, same algorithm — that's the entire
contract), and `lib/internal-api-client.ts`'s `internalApiFetch` signs
and sends requests with it, failing closed (throws) if the signing-key
env vars aren't set rather than silently calling unsigned.
`app/forecast/demo/page.tsx` is a Server Component proving this
end-to-end: it calls apps/api's real `GET /v1/forecasts/{id}` for a
fixed demo location (Wrightsville Beach, NC — no location search yet,
sprint 31) and renders whatever comes back, including a gracefully
degraded response, using the sprint 27 primitives below. It's marked
`export const dynamic = 'force-dynamic'` since forecasts are live,
per-request data — without that, `next build`'s static prerender pass
would freeze whatever the fetch did at build time (when apps/api isn't
running) into the shell served to every visitor. This is a signed-path
proof, not the real dashboard (sprint 32) — its verdict-badge mapping
is demo-page-scoped presentation only.

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
follow-up scope — this PR aims for accessible-by-construction markup, not
a formal audit. Real screens land in the remaining Phase 3 sprints (28
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

`/forecast/demo` needs apps/api running with a **matching** signing key
(see `apps/api/README.md`'s "Local dev" section) — any value works as
long as both sides agree:

```bash
# terminal 1, from apps/api
INTERNAL_SIGNING_KEY_ID=dev-key INTERNAL_SIGNING_KEY_SECRET=dev-secret-please-change \
  uvicorn app.main:app --reload --port 8000

# terminal 2, from apps/web
INTERNAL_API_BASE_URL=http://localhost:8000 \
INTERNAL_SIGNING_KEY_ID=dev-key INTERNAL_SIGNING_KEY_SECRET=dev-secret-please-change \
  npm run dev
```

Open http://localhost:3000/forecast/demo. In this sandboxed environment
apps/api's own upstream (NOAA/NWS/NDBC) calls are blocked
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
