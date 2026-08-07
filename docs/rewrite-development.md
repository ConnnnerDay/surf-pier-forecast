# Rewrite development

The new product is isolated under `apps/` and `packages/`. The root-level Flask
application remains a legacy behavior reference and is not imported by either
rewrite application.

## Prerequisites

- Node 24.16.0
- pnpm 11.16.0
- Python 3.12.13
- uv 0.12.1 or a compatible later 0.12 patch

The repository pins Node and Python in `.node-version` and `.python-version`.
The root `package.json` pins pnpm.

## Install

```powershell
pnpm install --frozen-lockfile
uv --directory apps/api sync --frozen
```

Omit `--frozen-lockfile` and `--frozen` only when intentionally updating and
reviewing dependency lockfiles.

## Run

Use separate terminals:

```powershell
pnpm dev:web
pnpm dev:api
```

The scaffold exposes the Next.js page on port 3000 and FastAPI health endpoints
on port 8000:

- `GET /health/live` confirms that the API process can serve requests.
- `GET /health/ready` is the future dependency-readiness contract. In this
  scaffold it has no external dependencies and therefore matches liveness.

## Verify

```powershell
pnpm check:web
pnpm check:api
pnpm check
```

The checks cover lint, static types, tests, and the production frontend build.
They apply only to the rewrite. Existing legacy checks remain unchanged until a
later CI sprint separates the two codebases explicitly.
