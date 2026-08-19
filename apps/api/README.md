# apps/api

The canonical FastAPI service named by
[`docs/CANONICAL_ROADMAP.md`](../../docs/CANONICAL_ROADMAP.md)'s technical
contract: versioned `/v1` endpoints, deployed on an always-on Render
service, signed and called only from `apps/web`'s backend-for-frontend —
never directly from a browser.

## Status

Boots, has real CI, and now has the canonical typed domain models
(`app/domain/models.py` — `Location`, `Observation`, `SourceStatus`,
`Confidence`, `Warning`, `ForecastEnvelope`; see the module docstring and
`docs/architecture.md`'s ADR-003). It does not yet have the `/v1` routes,
the ported forecast domain *logic*, or a Postgres connection. Those land
in the Phase 2 sprints listed in the roadmap's sprint ledger (12 onward),
each behind its own characterization tests, porting from the
reconciliation audit
([`docs/R1_RECONCILIATION_AUDIT.md`](../../docs/R1_RECONCILIATION_AUDIT.md))
rather than copying `v2/backend` or the legacy Flask app verbatim.

If you change a model in `app/domain/models.py`, its schema snapshot test
will fail — regenerate deliberately and review the diff:

```bash
python -m scripts.generate_schema_snapshots
```

## Local dev

```bash
cd apps/api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # includes runtime deps + ruff/mypy/pytest/httpx
uvicorn app.main:app --reload --port 8000
```

Smoke test:

```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
# both return {"status": "ok"}
```

Interactive docs: http://localhost:8000/docs

## Checks

Run these from `apps/api` with the dev venv active — they mirror
`.github/workflows/apps-ci.yml`:

```bash
ruff check .
ruff format --check .
mypy .
pytest -q
```
