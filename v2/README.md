# v2

The mobile-first rewrite described in [`/docs/V2_PLAN.md`](../docs/V2_PLAN.md).

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

Phase 1 (scaffold) and phase 2's forecast-engine port are both done.
v1's forecast engine (`domain/forecast.py`, `domain/species.py`,
`locations.py`, `regulations.py`, and their `services/`/`storage/`
dependencies) is copied verbatim into `v2/backend` and wired to a real
`GET /forecast/{location_id}` endpoint — verified with a live browser
walkthrough (signup → onboarding → add a location → a real scored
forecast renders with ranked species and bait/rig recommendations).
Lint, type-check, tests, and a production build all pass for both
halves — see each directory's README.

**Not done yet:** OAuth/passkeys (email/password auth only so far), 2FA
enrollment, login-alert emails, the profile/personalization API and
screen (the model exists, no UI to edit it), forecast caching (every
request is a live multi-second NOAA/NWS/NDBC fetch — v1's 4-hour TTL
cache + background refresh isn't ported), and Playwright e2e tests. See
`docs/V2_PLAN.md` §7 "Phased build plan" for what's next.

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
