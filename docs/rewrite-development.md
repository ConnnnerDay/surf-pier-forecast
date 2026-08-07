# Rewrite development

The new product is isolated under `apps/` and `packages/`. The root-level Flask
application remains a legacy behavior reference and is not imported by either
rewrite application.

## Prerequisites

- Node 24.16.0
- pnpm 11.16.0
- Python 3.12.13
- uv 0.12.1 or a compatible later 0.12 patch
- Docker with Compose support when running PostgreSQL locally

The repository pins Node and Python in `.node-version` and `.python-version`.
The root `package.json` pins pnpm.

## Check the environment

```powershell
pnpm run doctor
```

Node, pnpm, and uv are required. Docker is reported separately because it is
needed only when this machine will host the local PostgreSQL container.

## One-command setup

To install dependencies and create missing local environment files without
overwriting existing values:

```powershell
pnpm setup
```

To also start and wait for PostgreSQL:

```powershell
pnpm setup:postgres
```

The setup command copies `apps/web/.env.example` to `apps/web/.env.local` and
`apps/api/.env.example` to `apps/api/.env` only when the destinations do not
exist. Values in an existing local environment are never replaced.

## Manual install

```powershell
pnpm install --frozen-lockfile
uv --directory apps/api sync --frozen
```

Omit `--frozen-lockfile` and `--frozen` only when intentionally updating and
reviewing dependency lockfiles.

## Local PostgreSQL

`compose.yaml` runs pinned PostgreSQL 17.6 on `127.0.0.1:5432` using credentials
that are intentionally local-only. Override the host port when 5432 is already
occupied:

```powershell
$env:POSTGRES_PORT=55432
docker compose up -d --wait postgres
```

If the port changes, update both local application environment files. Inspect
or stop the service with:

```powershell
docker compose ps
docker compose down
```

`docker compose down` preserves the named database volume. Removing the volume
deletes local data and is intentionally not part of a workspace command.

The stable seed hook is:

```powershell
pnpm seed
```

The scaffold has no application tables or records yet, so the hook currently
performs an explicit idempotent no-op. The database-model sprint will extend the
same command; setup instructions will not change.

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
