# Communication And Review

## Status And Verdicts

Lead with the result. Separate observed facts, inference, unknowns, and the next
decision. Report only checks actually run and link the Issue, PR, command, or
artifact that carries detailed evidence.

For refactoring, explain what becomes easier to understand or change and which
behavior stays the same. Keep technical ownership and dependency evidence with
[Change Locality](../architecture-contract.md#change-locality); a shorter diff
or passing tests alone does not prove a simpler design.

## Pull Requests

The PR is a human review surface, not an agent completion log. Write in the
user's language for someone judging intent and behavior without reading every
line of the diff. Use familiar product terms; explain necessary internal terms
at first use rather than making the reader decode owner/contract vocabulary.

The `pr` skill owns the body structure: Summary, Evidence, and Merge Danger; the
[PR template](../../.github/pull_request_template.md) mirrors it. Before publishing, check that
the reader can explain what changes, why it matters, what supports the claim,
and what happens if it is wrong. Use the smallest useful before/after view;
plain prose is enough, and code or file trees are not mandatory.

Keep the outcome, material uncertainty, breaking changes, and rollback cost in
the main text. Put commands, exact producing/validated refs, and detailed
technical evidence in links or a collapsed details block, with enough context
to show which claim and proposed head they support. Describe what tests actually
confirmed, not just their count; distinguish manual inspection from execution.
Do not fabricate before/after runs or scientific readiness. Hosted checks on
the final remote SHA remain merge authority.

Link the existing Issue rather than repeating its scope. The task-shaping
contract determines what must be investigated, not extra headings that every
PR must contain. Expand technical detail only when it changes the reviewer's
decision; keep construction history and internal checklists out of the body.

## Review Focus

Review the uncertainty left by the diff and its existing evidence. A local fix
with a decisive regression oracle does not need a separate adversarial round.
A further pass earns its cost by answering a concrete uncovered question:
whether an expected value is independently justified, whether a scientific
trade-off is accepted, or whether a changed owner/persistence/publication
handoff preserves the consumer's result. Follow the affected production path
and use a counterexample capable of changing that judgment. A different reviewer
repeating the same assumptions is not independent evidence.

Review comments are evidence, not scope authority. Fix a comment when it proves
an Issue/public-contract violation or a reachable loss introduced by the diff.
Otherwise route or reject it with the relevant locator.

## Issue Closure Ownership

The Issue owns current task intent and closure. Put durable scope or acceptance
changes there; keep PR evidence on the PR. Private or cross-session handoffs use
[`obsidian-handoff-contract.md`](obsidian-handoff-contract.md) and point back to
the canonical Issue rather than copying it.
