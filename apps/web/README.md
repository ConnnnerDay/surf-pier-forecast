# apps/web

The canonical Next.js backend-for-frontend named by
[`docs/CANONICAL_ROADMAP.md`](../../docs/CANONICAL_ROADMAP.md)'s technical
contract: mobile-first, deployed to Vercel, the only thing the browser
talks to. The browser must never call `apps/api` (FastAPI) directly — this
app authenticates the user and signs the internal request instead.

## Status: skeleton

This app currently proves the canonical path boots and has a real CI job —
a single placeholder page, no routing, no auth, no calls to `apps/api`.
Real screens land in the Phase 3 sprints listed in the roadmap's sprint
ledger (27 onward), reusing UX patterns catalogued as "Adapt" in
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

Open http://localhost:3000 — should render the placeholder page.

## Checks

Run these from `apps/web` — they mirror `.github/workflows/apps-ci.yml`:

```bash
npm run lint
npm run build
```

`npm run build` completing without errors is the production-build smoke
test.
