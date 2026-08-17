# apps/web

The canonical Next.js backend-for-frontend named by
[`docs/CANONICAL_ROADMAP.md`](../../docs/CANONICAL_ROADMAP.md)'s technical
contract: mobile-first, deployed to Vercel, the only thing the browser
talks to. The browser must never call `apps/api` (FastAPI) directly — this
app authenticates the user and signs the internal request instead.

## Status: R3 skeleton

This app currently proves the canonical path boots — a single placeholder
page, no routing, no auth, no calls to `apps/api`. Real screens land in the
Phase 3 sprints listed in the roadmap's sprint ledger (27 onward), reusing
UX patterns catalogued as "Adapt" in
[`docs/R1_RECONCILIATION_AUDIT.md`](../../docs/R1_RECONCILIATION_AUDIT.md)
§3 rather than the `v2/frontend` implementation verbatim.

## Local dev

```bash
cd apps/web
npm install
npm run dev
```

Open http://localhost:3000 — should render the placeholder page.

```bash
npm run build
```

should complete without errors as the smoke test for a production build.
