# Sprint 6 — intentionally-failing CI proof

Sprint 6's outcome ("quality gates") requires not just that lint/type/test
checks exist for `apps/`, but evidence that they actually catch bad code.
This document is that evidence.

## Method

1. Branched from `main` at `fed28ab` (after PR #327 merged) onto a scratch
   branch, `claude/sprint6-ci-failure-proof`, matching `apps-ci.yml`'s
   `claude/**` push trigger.
2. Deliberately broke every check in `.github/workflows/apps-ci.yml`:

   | Check | Break | File |
   |---|---|---|
   | `api-lint` (ruff check) | Added an unused `import os` | `apps/api/app/main.py` |
   | `api-lint` (mypy) | Assigned a `str` to an `int`-annotated variable | `apps/api/app/main.py` |
   | `api-test` (pytest) | Asserted the health endpoint returns a value it doesn't | `apps/api/tests/test_health.py` |
   | `web-lint` (oxlint) | Added a `debugger` statement, with `no-debugger` elevated to `"error"` in `.oxlintrc.json` so it actually fails the job instead of only warning | `apps/web/app/page.tsx`, `.oxlintrc.json` |
   | `web-build` (tsc via `next build`) | Referenced an undefined variable | `apps/web/app/page.tsx` |

3. Verified all five failures locally first (`ruff check`, `mypy .`,
   `pytest -q`, `npm run lint`, `npm run build` — all non-zero exit)
   before spending a CI run on it.
4. Pushed the branch. `apps-ci.yml` ran automatically (push trigger, no PR
   opened).
5. Confirmed via the GitHub Actions API that the run failed, and that
   **every one of the four jobs failed for the specific reason
   intended** (not some incidental setup failure):

   - Run: `32267417356` (commit `ba2ea44`), conclusion: **failure**.
   - `api-test`: job failure — the "Run tests" step itself failed
     (pytest assertion), not an earlier setup step.
   - `api-lint`: job failure — the "Fail if any static-analysis check
     failed" aggregator step failed, correctly detecting the mypy
     failure via `steps.mypy.outcome` (each check's own step still
     showed `continue-on-error`'s masked `conclusion: success`, exactly
     as designed in PR #326 — the *outcome*-based check is what caught
     it).
   - `web-build`: job failure — the "Type check + build" step itself
     failed (`error TS2304: Cannot find name 'thisVariableDoesNotExist'`).
   - `web-lint`: job failure — the "Lint" step itself failed
     (`no-debugger` error).
6. The branch was never opened as a PR and was never merged. `main` was
   not touched by any of this — confirmed via `git log origin/main`
   before and after, both at `fed28ab`.

## Disposition of the scratch branch

`claude/sprint6-ci-failure-proof` remains pushed to the remote. This
session attempted `git push origin --delete claude/sprint6-ci-failure-proof`
to clean it up and got an HTTP 403 from the git proxy; no GitHub API tool
available to this session can delete a branch either. The branch is safe
to leave as-is: it holds exactly one commit, titled
`SCRATCH: sprint 6 intentionally-failing CI proof - DO NOT MERGE`, whose
message explains it in full; no PR was ever opened from it; and it will
never be merged. A repo admin (who has branch-delete permission this
session doesn't) can delete it at any time — it is pure evidence, not
live work.

## Conclusion

`apps-ci.yml`'s five checks (`api-test`, and `api-lint`'s three
sub-checks, `web-build`, `web-lint`) each genuinely block on the specific
condition they exist to catch. This closes the one open part of sprint
6's outcome; the lint/type/test checks themselves were already delivered
in PR #326.
