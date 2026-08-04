---
title: "PR Stack Artifact Boundary Clean Closeout"
date: "2026-06-24"
category: "workflow"
module: "pr-closeout"
status: "current"
tags: ["pr-stack", "ci", "artifact-boundary", "clean-checkout"]
source_refs:
  - ".codex/skills/xic-pr-stack-repair/SKILL.md"
  - "docs/agent/execution-gates.md"
  - "docs/agent/planning-workflows.md"
  - "docs/superpowers/README.md"
---

# PR Stack Artifact Boundary Clean Closeout

## When To Read

Read this when a split or stacked XIC PR series has confusing diffs, repeated CI
fix commits, clean-checkout failures, stale PR bases, or tests that only pass
with ignored local artifacts.

## Problem

The PR closeout failed because branch boundaries and artifact ownership were not
settled before CI repair. Several PRs carried stale stack commits, global ledger
updates, externalized-artifact cleanup, and retained-fixture assumptions in the
wrong order. That made red CI look like ordinary test failures, when the real
problem was that some PRs were not self-contained from a clean checkout.

## Tempting Wrong Path

Do not keep adding CI-fix commits to a stale branch. Do not assume a later
cleanup PR will make an earlier PR pass. Do not cherry-pick broad output or
handoff cleanup into every PR. Do not treat missing GitHub Actions checks as
proof that force-push is required before checking token, event, filter,
approval, and branch-state causes.

## Working Pattern

Use `xic-pr-stack-repair` before PR closeout when the failure smells like a
stacked-PR boundary problem. Pull XIC readiness or artifact rules from their
canonical owners only when relevant:

1. Map every related PR with `xic-pr-stack-repair`; do not repair from memory.
2. Decide which PR owns product behavior, artifact hygiene, fixture retention,
   and final rollup docs.
3. Rebuild from the current intended base only when stale stack commits are in
   the branch; include only in-scope logical commits.
4. Make each PR clean-checkout safe: default CI must not require ignored
   `output/`, `.worktrees/`, or `local_validation_artifacts/` bytes.
5. Keep GitHub Actions recovery token/event-aware; use `force-with-lease` only
   for authorized rewritten PR branches after checking the expected remote head.

## Evidence

- Commands actually run:
  PR-local focused checks, productization state/authority checks, validation
  artifact retention, diagnostics index, hook fixture smoke, ruff, mypy, and
  full pytest were used to prove the final rebuilt row-completion PR.
- Artifact paths:
  retained validation evidence stayed under committed summaries/manifests while
  bulky local outputs remained ignored.
- Reviewers / CI:
  The stack incident retrospective records why PR88-PR92 repair had to stop
  patching red checks locally and re-establish artifact boundaries before
  continuing.

## Limits

This note does not replace the global code-review or PR-closeout workflows. It
only handles the precondition that each PR in a split stack has a coherent owner
surface and clean-checkout CI contract.

## Next Time

1. Classify repeated red CI in a PR stack as a possible boundary failure before
   changing code.
2. Run the stack map and artifact-boundary checklist from
   `xic-pr-stack-repair`.
3. Only after the branch is self-contained, run closeout checks and write the
   PR body.
