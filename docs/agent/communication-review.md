# Communication And Review

## Status And Verdicts

Lead with the result. Separate observed facts, inference, unknowns, and the next
decision. Report only checks actually run and link the Issue, PR, command, or
artifact that carries detailed evidence.

## Pull Requests

Use the repository PR template and describe final behavior, not temporary commit
history. The body is the durable squash-merge review surface: lead with outcome,
transfer the Issue scope, guide the load-bearing review, and record checks on the
validated head. Hosted checks on the final remote SHA are the merge authority.

Review comments are evidence, not scope authority. Fix a comment when it proves
an Issue/public-contract violation or a reachable loss introduced by the diff.
Otherwise route or reject it with the relevant locator.

## Issue Closure Ownership

The Issue owns current task intent and closure. Put durable scope or acceptance
changes there; keep PR evidence on the PR. Private or cross-session handoffs use
[`obsidian-handoff-contract.md`](obsidian-handoff-contract.md) and point back to
the canonical Issue rather than copying it.
