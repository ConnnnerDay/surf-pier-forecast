# R3 — one canonical application path

Status: **complete.** This satisfies gate R3's acceptance evidence from
[`docs/CANONICAL_ROADMAP.md`](CANONICAL_ROADMAP.md) and
[master issue #318](https://github.com/ConnnnerDay/surf-pier-forecast/issues/318):
a Next.js/FastAPI skeleton is established as the named canonical path,
`/v2` and the legacy Flask app are clearly marked archived/reference-only,
and the local smoke path is documented below.

This is the last recovery gate. Once this PR merges, numbered product
sprints resume — starting with the next unaccepted sprint in the ledger
(sprint 4, repository baseline / monorepo scaffold, is the natural next
action; see the roadmap's live checkpoint for the exact call).

## 1. What "canonical path" means as of this PR

| Path | Role | State |
|---|---|---|
| [`apps/web`](../apps/web) | Next.js backend-for-frontend, mobile-first, deployed to Vercel | **New.** Skeleton only — one placeholder page, no auth, no API calls. Proves the path boots. |
| [`apps/api`](../apps/api) | FastAPI service, versioned `/v1`, deployed to Render | **New.** Skeleton only — `GET /health/live` and `GET /health/ready` per the required API surface. No `/v1` routes, no database, no ported domain logic yet. |
| `/v2` (`v2/backend`, `v2/frontend`) | Merged prototype (React/Vite + JWT/OAuth/passkeys + SQLite) | **Archived, reference-only** (banner added at [`v2/README.md`](../v2/README.md)). Classified item-by-item in [`docs/R1_RECONCILIATION_AUDIT.md`](R1_RECONCILIATION_AUDIT.md); not a second live application. |
| Repo root (`app.py`, `domain/`, `services/`, `storage/`, `web/`, `templates/`, …) | Legacy self-hosted Flask app | **Archived, reference-only** (banner added at [`README.md`](../README.md)). Retired outright per the product decisions on record; kept only so Phase 2 sprints can port characterized logic into `apps/api`. |

Neither `/v2` nor the legacy Flask app is deleted or physically moved in
this PR — moving ~19,000 lines of code with active reconciliation
classifications attached (R1) would itself be a large, risky diff with no
functional benefit, and the roadmap's source-of-truth order already treats
both as evidence, not authority. "Archived" here means: clearly labeled at
the point every future reader will look first (each tree's own README),
and excluded from `apps/`, the directory the canonical technical contract
names. Physically relocating or deleting them, if ever wanted, is a
mechanical follow-up sprint's decision, not R3's.

## 2. Why the skeletons are this small

The canonical technical contract calls for a monorepo with `apps/web`,
`apps/api`, and generated shared OpenAPI schemas, FastAPI with versioned
`/v1` endpoints, Better Auth, and pooled Postgres/Neon. Building all of
that now would mean: picking an auth implementation, standing up a
database, and porting forecast logic — each of which is its own numbered
sprint (28, 10, 11-22 respectively) with its own acceptance criteria,
characterization-test requirement, and review. R3's job is narrower:
prove *this* is the path, not build the product on it. Sprint 4
(repository baseline) and the Phase 1-2 sprints after it are where the
skeleton grows real CI, environments, auth, a database, and ported logic.

## 3. Local smoke path

Two independent apps, no shared dependency yet (that arrives with the
signed internal request path in a later sprint).

### `apps/api`

```bash
cd apps/api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

```bash
curl http://localhost:8000/health/live   # {"status":"ok"}
curl http://localhost:8000/health/ready  # {"status":"ok"}
```

Verified in this session: installs cleanly, boots, both endpoints return
`{"status": "ok"}`, and `/docs` (FastAPI's interactive docs) returns
`200`.

### `apps/web`

```bash
cd apps/web
npm install
npm run dev
```

Open http://localhost:3000 — renders a page with the heading "Surf & Pier
Forecast" and a short description.

```bash
npm run build
```

should complete without errors as the production-build smoke test.

Verified in this session: `npm install` reports 0 vulnerabilities (Next.js
was pinned to `^16.3.1` specifically because `^15.5.0` pulled in a
transitively-vulnerable `postcss`/`sharp` — see the commit that changed
this), `npm run build` completes and prerenders `/` and `/_not-found` as
static content, and `npm run dev` serves a `200` with the expected heading
in the rendered HTML.

## 4. What R3 does not decide

This PR does not: implement authentication, connect a database, port any
forecast/species/regulations logic, wire `apps/web` to call `apps/api`, set
up CI for the new `apps/` tree, or provision Vercel/Render/Neon
environments. Those are sprints 4 and onward, resuming after this PR
merges per the roadmap's "Resume numbered product sprints only after
R1-R3 are merged" rule.
