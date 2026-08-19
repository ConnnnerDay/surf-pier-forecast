# PR governance

Sprint 7's deliverable: sprint/PR templates, ownership, dependency policy,
and the AI-review contract, consolidated in one place so a future human
collaborator (or agent) can onboard without extra chat context, per the
product decisions on record in
[`docs/CANONICAL_ROADMAP.md`](CANONICAL_ROADMAP.md) ("Document the
PR/roadmap process well enough that a future collaborator could onboard
without extra chat context, since collaborators may be added later").

## Ownership

Sole human reviewer/approver for the foreseeable future
(`ConnnnerDay`) — recorded in `docs/CANONICAL_ROADMAP.md`'s product
decisions. AI agents (Claude, Codex, or others) do implementation work
under an AI-review-plus-human-approval gate: an agent proposes a PR, a
second AI pass reviews it (see "AI review contract" below), and the human
approves and merges. No other collaborators are onboarded yet; when one
is, this document is the onboarding reference.

## One PR per sprint or gate

`AGENTS.md` and `docs/CANONICAL_ROADMAP.md` already establish this as a
hard rule: one small PR per numbered sprint or recovery gate, merged
before the next dependent one starts. `.github/pull_request_template.md`
carries this forward as a checklist item on every PR.

Roughly no more than ~400 changed *implementation* lines per PR
(excluding generated code, fixtures, and documented mechanical moves) —
see `docs/CANONICAL_ROADMAP.md`'s "Definition of done."

## Dependency policy

- New dependencies need a reason stated in the PR description — what it's
  for, why the alternative (writing it yourself, or an existing
  dependency) isn't preferable.
- Prefer version ranges that stay current without silent breakage:
  `apps/web`'s `package.json` and `apps/api`'s `requirements*.txt` use
  semver-compatible ranges (`^x.y.z` / `>=x,<y`), not exact pins, *except*
  where `docs/R2_CI_BASELINE.md` already documented that an unpinned range
  caused non-deterministic CI results (the legacy `ruff`/`mypy` case) — in
  that situation, pin exactly and say why in the PR.
- Run `npm audit` / check for known-vulnerable resolved versions before
  merging a new frontend dependency — see the sprint-4 PR (#326) for the
  precedent (pinned `next` to `^16.3.1` specifically to avoid a
  transitively-vulnerable `postcss`/`sharp` under `^15.5.0`).
- Security scanning and automated dependency audits in CI are sprint 8's
  job (partially delivered in #326; secret scanning and dependency audit
  itself still open) — this section is the policy, sprint 8 is the
  automation.

## AI review contract

Every AI-authored PR states, in its own description:

- what changed and why (not just what — the roadmap/sprint/gate this
  satisfies);
- the exact test commands run and their results (a screenshot of "tests
  pass" is not a substitute for the actual command output or CI run
  link);
- what was deliberately left out of scope, and why;
- for anything touching CI, security, or auth: an explicit statement of
  what was and wasn't verified.

A second AI pass (or the human reviewer standing in for one) checks for:
correctness, edge cases, scope growth beyond the stated sprint/gate, weak
or absent tests, secrets, and backward incompatibility — see
`docs/CANONICAL_ROADMAP.md`'s "Definition of done and AI verification
contract" for the full list this consolidates.

## Branch hygiene

**Incident that motivated this section**: sprint 6's intentionally-
failing-CI proof pushed a scratch branch
(`claude/sprint6-ci-failure-proof`) to the shared remote, containing
deliberately broken code, with a commit message and intended PR title of
`SCRATCH: ... DO NOT MERGE`. It was never meant to become a real PR. It
was opened as PR #328 and merged into `main` anyway — full account in
`docs/SPRINT_6_CI_PROOF.md`'s "Incident" section, including the correction
that no repo automation caused this; the most likely cause is GitHub's
"Compare & pull request" banner being clicked through without reading the
title.

Rule going forward: **a branch containing deliberately broken code, or
any other code not meant to be merged, does not get pushed to the shared
remote at all** unless there is no other way to capture the evidence
needed (e.g., a real CI run). If it must be pushed:

1. Push it, capture the evidence (e.g., the CI run URL/id), and then
   **immediately check whether GitHub created a PR from it** — don't
   assume it stayed unopened.
2. If a PR exists, close it explicitly (not just leave it open) before
   moving on to other work.
3. Only after confirming no open PR exists (or after closing one) is the
   task actually finished — this is the same "an open branch, pushed
   commit, or unmerged PR is not completed work" standard `AGENTS.md`
   already applies to real work, extended to scratch work too.

Delete the branch once its evidence is captured and documented, if branch
deletion is available (this session's tooling could not delete
`claude/sprint6-ci-failure-proof` — see the incident doc; a repo admin
can).
