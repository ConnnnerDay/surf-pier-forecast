# Agent handoff: read this first

This repository is being repaired after several dependent sprint PRs were
closed without merging and a later `/v2` implementation was merged with
different architectural choices.

Before changing code, every agent (Codex, Claude, or otherwise) must:

1. Read [`docs/CANONICAL_ROADMAP.md`](docs/CANONICAL_ROADMAP.md) completely.
2. Read the current checkpoint in [master issue #318](https://github.com/ConnnnerDay/surf-pier-forecast/issues/318).
3. Work only on the recovery gate or sprint named as **Next action**.
4. Start from the latest `main`; an unmerged branch or PR is not completed work.
5. Use one small PR for one gate or sprint, and merge it before beginning a
   dependent PR.
6. Before handing off, update both the roadmap checkpoint and issue #318 with
   the last merged PR, checks, blockers, decisions, and exact next action.

Do not treat `docs/V2_PLAN.md`, old chat history, closed PRs, or the current
shape of `/v2` as authority over the canonical roadmap. They are evidence and
reference material only.

## Current stop sign

Do not add product features until recovery gates R0-R3 in the canonical
roadmap are complete. The next PR after this handoff is R1: the merged-code
reconciliation audit.
