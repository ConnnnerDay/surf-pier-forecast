# apps

The canonical application path — see
[`docs/CANONICAL_ROADMAP.md`](../docs/CANONICAL_ROADMAP.md)'s technical
contract, [`docs/R3_CANONICAL_PATH.md`](../docs/R3_CANONICAL_PATH.md), and
each app's own README for what each one is and its current status.

- [`web/`](web/) — Next.js backend-for-frontend
- [`api/`](api/) — FastAPI service

## Fresh-machine setup

Requirements: Python 3.11+, Node 22+.

```bash
apps/setup.sh
```

Creates `apps/api/.venv` and installs its dependencies (including
ruff/mypy/pytest/httpx), then runs `npm install` in `apps/web`.

## Run

Two independent apps, run in separate terminals — there's no shared dev
server yet (that arrives with the signed `apps/web` → `apps/api` internal
request path in a later sprint):

```bash
# terminal 1
cd apps/api
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000

# terminal 2
cd apps/web
npm run dev
```

`apps/web`: http://localhost:3000 · `apps/api` docs: http://localhost:8000/docs

## Check

```bash
apps/check.sh
```

Runs the same checks as `.github/workflows/apps-ci.yml` for both apps —
`ruff check`, `ruff format --check`, `mypy`, and `pytest` for `apps/api`;
`lint` and `build` for `apps/web` — and reports every failure rather than
stopping at the first one. Exits non-zero if anything failed.
