# Communication And Review

## Status And Verdicts

Lead with the result. Separate observed facts, inference, unknowns, and the next
decision. Report only checks actually run and link the Issue, PR, command, or
artifact that carries detailed evidence.

## Pull Requests

Use the repository PR template to describe final behavior and bounded scope,
with Summary, Motivation, Testing, and Risks/Rollback. State breaking changes
explicitly when present and include relevant Issue linkage. The body is the
durable squash-merge review surface; link canonical Issue scope and record actual
checks for the proposed head.

Established-owner local work uses this compact body. Add review guidance and
expanded scope, source, or authority details only for the applicable cases in
[`planning-workflows.md#task-shaping`](planning-workflows.md#task-shaping). Bind
artifact evidence or required PR-gate results to exact producing/validated refs
when their identity matters, and make compatibility with the current head clear.
Hosted checks on the final remote SHA remain the merge authority.

Review comments are evidence, not scope authority. Fix a comment when it proves
an Issue/public-contract violation or a reachable loss introduced by the diff.
Otherwise route or reject it with the relevant locator.

## Issue Closure Ownership

The Issue owns current task intent and closure. Put durable scope or acceptance
changes there; keep PR evidence on the PR. Private or cross-session handoffs use
[`obsidian-handoff-contract.md`](obsidian-handoff-contract.md) and point back to
the canonical Issue rather than copying it.
