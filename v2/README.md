# v2

> **ARCHIVED — reference only, not the active application path.** This
> prototype (React/Vite + custom JWT/OAuth/passkey auth + SQLite) predates
> the canonical architecture decision recorded in
> [`docs/CANONICAL_ROADMAP.md`](../docs/CANONICAL_ROADMAP.md). As of
> recovery gate R3, the canonical path is [`apps/web`](../apps/web)
> (Next.js) and [`apps/api`](../apps/api) (FastAPI + Better Auth +
> PostgreSQL). Every route, module, and feature here has already been
> classified keep/adapt/replace/defer against that contract in
> [`docs/R1_RECONCILIATION_AUDIT.md`](../docs/R1_RECONCILIATION_AUDIT.md) —
> read that before reusing anything from this tree. Do not add features
> here or deploy it; it is evidence for porting into `apps/api`/`apps/web`,
> not a second live application.

The mobile-first rewrite described in [`/docs/V2_PLAN.md`](../docs/V2_PLAN.md)
(also archived — see the note at the top of that file).

> **Note on location:** the plan originally called for a new, separate
> GitHub repo. In practice, this session's GitHub integration couldn't
> create one (no permission to create repos, only to work within repos
> already granted), so v2 is being built here under `/v2` in the
> `surf-pier-forecast` repo instead. Move it to its own repo later if
> that still matters once real GitHub access allows it — nothing here
> depends on the repo layout.

- [`backend/`](backend/) — FastAPI + SQLAlchemy JSON API
- [`frontend/`](frontend/) — React + Vite mobile-first PWA

## Status

Phases 1-7 of `docs/V2_PLAN.md`'s build plan are done: scaffold, the v1
forecast-engine port, full backend auth (email/password, Google/Apple
OAuth, passkeys/WebAuthn, TOTP 2FA, login-alert emails, a 4-hour TTL
forecast cache, a full profile API), the matching frontend screens,
Playwright e2e coverage (signup, login, add-a-location-to-forecast,
regulations lookup, multi-location switching, account export/delete)
wired into CI, a regulations lookup + legal-catch calculator,
multi-location switching (rename/set-default/remove), and self-service
data export + account deletion. Lint, type-check, unit tests, e2e
tests, and a production build all pass for both halves — see each
directory's README.

**Not done yet:** in-app feedback form, FAQ page, OG preview cards for
shared links, an expanded curated-spot list (100+), and background
forecast-cache refresh (a cache miss is still a synchronous live
fetch). See `docs/V2_PLAN.md` §7 "Phased build plan" for what's next.

## Local dev

Run both halves at once from two terminals:

```bash
# terminal 1
cd v2/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# terminal 2
cd v2/frontend
npm install
npm run dev
```

Frontend: http://localhost:5173 · Backend docs: http://localhost:8000/docs
