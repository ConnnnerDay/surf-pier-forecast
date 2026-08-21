# apps/web

The canonical Next.js backend-for-frontend named by
[`docs/CANONICAL_ROADMAP.md`](../../docs/CANONICAL_ROADMAP.md)'s technical
contract: mobile-first, deployed to Vercel, the only thing the browser
talks to. The browser must never call `apps/api` (FastAPI) directly — this
app authenticates the user and signs the internal request instead.

## Status

Still no routing, auth, or calls to `apps/api` — but sprint 27 (design
system) has a first pass: `app/globals.css` defines light/dark design
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

## Checks

Run these from `apps/web` — they mirror `.github/workflows/apps-ci.yml`:

```bash
npm run lint
npm run build
```

`npm run build` completing without errors is the production-build smoke
test.
