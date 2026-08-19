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
6. At the time this proof was written, the branch had not been opened as
   a PR and `main` was confirmed unaffected. **This did not hold — see
   "Incident" below.**

## Incident: the scratch branch was merged to `main`

Shortly after this proof was captured, GitHub auto-opened PR #328 from
the pushed `claude/sprint6-ci-failure-proof` branch (visible in this
session's own investigation as a `pull_request`-event CI run despite no
PR existing moments earlier), and the repo owner merged it —
`merged_by: ConnnnerDay`, at `2026-08-19T15:00:49Z` — despite the PR
title reading `SCRATCH: ... DO NOT MERGE` and its body explaining why.
This landed the deliberately-broken code on `main` as commit `e2d7165`,
and `apps-ci.yml`'s push run against `main` (`32267479346`) correctly
went red.

**Fix**: reverted the merge on `main` via `git revert -m 1 e2d7165`
(commit `d53ab0a`), restoring all four files to their pre-incident
content exactly. Verified locally post-revert: `apps/setup.sh` then
`apps/check.sh` both succeed, all checks pass. See the PR that carries
this revert for the CI confirmation on `main` itself.

**Why this doesn't invalidate the proof above**: the proof's evidence —
that a real, freshly-triggered CI run genuinely failed on genuinely
broken code, for the specific reasons intended — still stands and is
unchanged. What changed is only the scratch branch's disposition: it was
merged (by a human, not by this session, and against its own explicit
instruction) and has now been reverted, rather than sitting unmerged
forever as originally planned. Sprint 6's outcome — proof that CI blocks
bad code — is still satisfied by the same run (`32267417356`).

**Process gap this surfaces**: pushing a `claude/**`-prefixed branch is
apparently enough to trigger automatic PR creation in this repo, which a
human can then merge without necessarily reading the "DO NOT MERGE"
warning. Future intentionally-failing-CI proofs (or any throwaway
branch) should avoid the `claude/**` prefix pattern, or use a fully
separate mechanism (e.g., a local-only CI dry run, or a fork) that
cannot auto-open a mergeable PR against `main` at all. Worth a decision
in sprint 7 (PR governance).

## Disposition of the scratch branch

`claude/sprint6-ci-failure-proof` remains pushed to the remote, now
merged-and-reverted rather than unmerged. This session attempted
`git push origin --delete claude/sprint6-ci-failure-proof` before the
incident and got an HTTP 403 from the git proxy; no GitHub API tool
available to this session can delete a branch either. A repo admin can
delete it at any time.

## Conclusion

`apps-ci.yml`'s five checks (`api-test`, and `api-lint`'s three
sub-checks, `web-build`, `web-lint`) each genuinely block on the specific
condition they exist to catch. This closes the one open part of sprint
6's outcome; the lint/type/test checks themselves were already delivered
in PR #326. The incident above — the proof branch briefly landing on and
then being reverted from `main` — is a real but now-resolved process
gap, not a defect in the checks themselves.
