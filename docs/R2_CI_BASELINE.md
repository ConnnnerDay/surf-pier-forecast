# R2 — truthful deterministic CI baseline

Status: **complete.** This satisfies gate R2's acceptance evidence from
[`docs/CANONICAL_ROADMAP.md`](CANONICAL_ROADMAP.md) and
[master issue #318](https://github.com/ConnnnerDay/surf-pier-forecast/issues/318):
exact current CI commands recorded, the one live-provider dependency R1
found removed from the default/required run, and every current CI failure
classified as regression or known debt.

This PR changes CI configuration only (`.github/workflows/*.yml`,
`v2/frontend/package.json`'s scripts, and a one-line tag on one test title).
No application/product code changed.

## 1. What changed and why

### 1.1 Live-provider dependency removed from the default e2e run

R1 found exactly one test that depends on live upstream network calls:
`v2/frontend/e2e/forecast.spec.ts`, which drives the real
`domain/forecast.py:generate_forecast()` pipeline (NOAA/NWS/NDBC/astro) end
to end. Every other test in both the backend and frontend suites already
mocks the network boundary.

- The test is now tagged `@live-network` in its title.
- `package.json`'s `test:e2e` script (what `v2-ci.yml`'s required `e2e` job
  runs) now passes `--grep-invert @live-network`, so it never runs there.
  Verified locally: `npx playwright test --list --grep-invert @live-network`
  lists the other 8 tests and excludes this one;
  `--grep @live-network` lists only this one.
- A new `test:e2e:live` script (`playwright test --grep @live-network`) and
  a new `e2e-live-network` job in `v2-ci.yml` let it still be run
  on demand via the workflow's new `workflow_dispatch` trigger — not on
  every push/PR, and not part of `deploy`'s `needs:`. The test is preserved
  for manual verification, per R1's "Replace (for CI), reference (for
  design)" classification; it is not required to write NOAA/NWS/NDBC
  fixtures in this PR — that's Phase 2's job (owning sprint: 21).

### 1.2 A CI truthfulness bug found and fixed: pytest and mypy have never run

While recording the exact current commands, per-step evidence from the
actual GitHub Actions run on this branch's own docs-only R1 PR
(run `32042545841`, commit `0fc7d4c`, same code as `main`@`f4d1096`)
showed:

| Job | Step | Outcome |
|---|---|---|
| `test (3.10)` | `Lint with ruff` | `failure` |
| `test (3.10)` | `Run tests` | **`skipped`** |
| `test (3.11)`, `test (3.12)` | (all steps after install) | `cancelled` (matrix fail-fast, triggered by 3.10's failure) |
| `lint` | `Check formatting` | `failure` |
| `lint` | `Type check with mypy` | **`skipped`** |

`.github/workflows/test.yml`'s `test` job ran `ruff check` as a step
*before* `pytest`, in the same job, with no `continue-on-error`. Because
`ruff check` has always failed (see §2), GitHub Actions has never executed
the `Run tests` step — pytest has not actually run in this workflow's
recorded history. Symmetrically, the `lint` job ran `ruff format --check`
before `mypy`, so `mypy` has never actually run either. Both jobs'
red/failure status has always meant "an earlier static-analysis check
failed," never "the thing named in the job's title (tests / type
checking) failed" — which is precisely the kind of non-truthful signal R2
exists to fix, and it made it impossible to classify pytest's or mypy's own
findings, because neither had ever produced any.

Fix applied in `test.yml`:

- `test` job: removed the embedded `Lint with ruff` step. It now only
  installs dependencies and runs `pytest`, matrixed across 3.10/3.11/3.12
  as before. Its conclusion is now a truthful signal about the test suite.
- `lint` job: `ruff check`, `ruff format --check`, and `mypy` each get
  `continue-on-error: true` and a `steps.<id>.outcome` check, followed by a
  final step that fails the job if any of the three failed. All three now
  always run and report their own findings; the job's overall conclusion
  is still accurate.

This is the first time this workflow produced a real pytest result and a
real mypy result. **Update: this PR's own CI run confirmed both** (job
`32062644421`, commit `05106bd`): `pytest` passed cleanly on all three of
3.10/3.11/3.12 — no regressions found by finally letting it run — and
`mypy` failed with 23 errors in 6 files, matching this session's local
reproduction exactly. See the now-filled-in §2.1 table.

## 2. Exact current commands and their classification

### 2.1 `.github/workflows/test.yml` (legacy Flask app, applies to all of
the repo root except `v2/`)

Triggers: `push`/`pull_request` to `main`/`master`/`claude/**`, no path
filter (runs on every change, including docs-only ones like R1's PR).

| Command | Job (after this PR) | Result | Classification | Owning sprint |
|---|---|---|---|---|
| `pip install -r requirements-dev.txt` | `test` | N/A (setup) | — | — |
| `pytest tests/ -v --tb=short --cov=. --cov-report=term-missing` | `test` (×3.10/3.11/3.12) | **Passing.** Confirmed on this PR's own CI run (job `32062644421`, commit `05106bd`) — the first real execution of this command in the workflow's history (see §1.2). All three Python versions green, no regressions. | No action needed. | — |
| `ruff check . --exclude v2` | `lint` | **Failing.** Locally reproduced: 38 errors (ruff 0.15.8, unpinned — `requirements-dev.txt` pins `ruff>=0.4,<1.0`). CI's own last recorded run of this exact command (same commit, run `32042545841`) found **604 errors** — the discrepancy is version drift from the unpinned range, not a difference in the code (see §3). | Known debt, pre-existing (predates R0). Not fixed in this PR — reformatting/fixing ~600 lint findings is out of scope for a CI-plumbing PR. | 6 |
| `ruff format --check . --exclude v2` | `lint` | **Failing.** Confirmed on this PR's own CI run: 65 files would be reformatted, 32 already formatted. | Known debt, pre-existing. Not fixed here (`ruff format` without `--check` would touch ~65 files, over any reasonable "small PR" line budget and unrelated to CI plumbing). | 6 |
| `mypy --ignore-missing-imports --exclude '(migrations?|migrate|^v2/)' .` | `lint` | **Failing.** Confirmed on this PR's own CI run — the first real execution of this command in the workflow's history (see §1.2): 23 errors in 6 files (checked 88 source files), matching this session's local reproduction exactly. Findings: `Name "Dict" is not defined` (missing `typing` import) in `scripts/add_species.py`, `services/arcgis_live_feeds.py` (×4), `regulations.py`, `domain/species.py` (×2); `Unsupported right operand type for in` in `domain/species.py` (×9, lines 2427-2436); an `Incompatible types in assignment` each in `domain/species.py:2452`, `web/helpers.py:21`, `domain/forecast.py` (×3, lines 1128/2985/2987). | Known debt, pre-existing (predates R0) — real type errors, not flakes, but fixing them is application-code work out of scope for a CI-plumbing PR. | 6 |

### 2.2 `.github/workflows/v2-ci.yml` (`/v2` prototype)

Triggers: `push`/`pull_request` on paths `v2/**` or the workflow file
itself; `workflow_dispatch` added by this PR.

| Command | Job | Evidence | Classification |
|---|---|---|---|
| `ruff check .` / `ruff format --check .` / `mypy app` / `pytest -q` / `alembic upgrade head` (all in `v2/backend`) | `backend` | **Passing.** Confirmed on this PR's own CI run (job `32062644440`, commit `05106bd`), consistent with the last prior run on `main`@`d948a5e`. | No known failures; this PR doesn't touch `v2/backend`. |
| `npm run lint` / `npm run build` / `npm test` (in `v2/frontend`) | `frontend` | **Passing.** Confirmed on this PR's own CI run. | No known failures. |
| `npm run test:e2e` (now excludes `@live-network`) | `e2e` | **Passing.** Confirmed on this PR's own CI run — the first run with the live-network test actually excluded. | No known failures; behavior narrows (one fewer test) but doesn't newly fail anything. |
| `npm run test:e2e:live` (new) | `e2e-live-network` (new, `workflow_dispatch`-only) | Correctly `skipped` on this PR's push/pull_request-triggered runs (confirmed — it only fires on manual `workflow_dispatch`, not yet exercised). The same test previously ran inside `e2e` and passed there per R1's audit note ("live-verified... 8/9 e2e tests pass live... 9th is documented sandbox flakiness"). | Known, documented flaky-under-sandbox-network-conditions test; intentionally kept out of the required path per §1.1. |
| Deploy placeholder | `deploy` | Correctly `skipped` (this PR is not a push to `main`). | No real signal; owned by sprints 10/48. |

### 2.3 Branch protection / required status checks

This session could not query GitHub branch-protection or required-status-
check configuration directly — no tool in the available GitHub MCP surface
exposes it, and this environment has no `gh`/admin API access. Empirical
evidence from PR #322 (R1) is informative: that PR merged to `main` despite
`lint` and `test (3.10)` showing `failure` in its check-run list, which is
consistent with either no required-status-checks branch protection being
configured, or a manual owner override. Either way, "required CI" in this
document is treated as *the checks these workflows produce*, not a verified
GitHub branch-protection setting. **Action item for a repo admin** (not
performable by this session): configure branch protection on `main` to
require the `test`, `lint`, `backend`, `frontend`, and `e2e` checks, once
their current failures are worked down — turning them on today would block
all merges given §2.1's pre-existing failures.

## 3. Non-determinism gap noted, not fixed here

`requirements-dev.txt` pins `ruff>=0.4,<1.0` and `mypy>=1.8,<2.0` — open
ranges, not exact versions. That's why this session's local reproduction
(ruff 0.15.8) found 38 `ruff check` errors while the actual CI run of the
identical command against the identical commit found 604: newer ruff
releases enable additional lint rules by default. An unpinned linter
version means the "exact current commands" in §2.1 can silently produce a
different failure count on every future CI run without any code change —
which works against "deterministic" baseline. Recorded as known debt for
sprint 6 (quality gates) to pin exact versions (or use a lockfile) rather
than fixed in this CI-plumbing-only PR.

## 4. What R2 does not decide

This PR does not: fix any of the ~600 ruff findings or ~65 unformatted
files, resolve whatever mypy/pytest findings this PR's own CI run
surfaces for the first time, configure GitHub branch protection, write
NOAA/NWS/NDBC fixtures to properly test `forecast.spec.ts`'s scenario
deterministically, or migrate any architecture. Those are R3's and the
numbered sprints' (primarily 6 and 21) jobs, using this baseline as input.
