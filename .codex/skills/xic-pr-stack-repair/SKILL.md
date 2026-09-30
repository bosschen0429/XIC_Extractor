---
name: xic-pr-stack-repair
description: Stack repair before splitting, retargeting, rebuilding, or merging stacked/superseded XIC PRs when stale bases, repeated ledgers, externalized artifacts, clean-checkout gaps, or missing GitHub checks are plausible.
---

# XIC PR Stack Repair

Determine whether confusing diffs or failing CI come from a stack boundary.
Ordinary code failures and healthy PR delivery do not need a stack inventory.

Inspect the affected base/head and the files or artifacts involved in the
failure. Expand to adjacent PRs only to resolve a dependency or competing owner.
Use `references/repair-checklist.md` for rebuild, artifact, or missing-check
mechanics relevant to that problem.

## Repair The Boundary

- Put a prerequisite in the PR that needs it or an already-merged dependency.
- Rebuild stale branches from their intended base with only in-scope commits.
- Keep default CI usable from a clean checkout; ignored local bytes belong to
  explicit opt-in validation.
- Give shared ledgers, active handoffs, and retained artifacts one current owner.
  Preserve live machine contracts while repairing their consumers.
- If checks are absent after a ref change, identify the event, token, filter, or
  approval cause before another publish attempt.

A discovered stack problem changes the repair target, not the task's completion
condition. Resolve technical facts and continue within existing authorization.
Ask only for missing intent or operator-only authority that changes the result.

## Verify And Deliver

Run checks affected by the repaired boundary, then follow
`docs/agent/execution-gates.md#pr-verification`. Use `pr-closeout` when the
durable review surface itself needs repair; an already sufficient PR body does
not require another closeout workflow.

Report the corrected dependency or ownership, actual checks, and remaining
limits. Trigger cases live in `evals/trigger-cases.json` and
`evals/semantic_config.json`; update them before broadening the description.
