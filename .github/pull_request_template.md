## Outcome

<!-- State what now works, whether it is safe to use/write/ship, and the next step. -->

## Problem and observable loss

<!-- Name the user, product, scientific, or engineering failure and its observable cost. -->

## What changed

<!-- Describe before/after behavior by owner or contract, not as a file list. -->

## Scope and contracts

- Lane:
<!-- Choose exactly one: shared, targeted, untargeted, workflow-only. -->
- Product output change:
<!-- Choose exactly one: yes, no. -->
- Public contracts at risk:
<!-- Choose one or more comma-separated values: CLI, config, TSV-CSV schema, workbook, run metadata, matrix identity, selected peak-area, counted detection, none. Use none alone. -->
- Execution base:
<!-- Enter the complete 40-character code-evidence commit used to freeze the issue scope. -->
- Semantic owner:
<!-- Name the one owner surface this PR changes. -->
- Finite production universe:
<!-- Name the reachable production entries/current direct consumers in scope and the census locator; exclude future lifecycle consumers. -->
- Scope delta from issue:
<!-- Enter None plus a rationale, or link the self-contained issue refreeze that authorizes the delta. -->

## Ticket-to-PR closure receipt

- Issue identity:
<!-- Enter the repository-qualified identity as owner/repo#N. -->
- Ticket delivery:
<!-- Choose exactly one: closes-entire-ticket, independent-atomic-unit. -->
- Bounded outcome:
<!-- Name the one outcome delivered by this head. -->
- Current consumer:
<!-- Name the consumer that can use this outcome now, without a later sibling. -->
- Independently decisive oracle:
<!-- Name the stable pass/fail oracle for this unit. -->
- Independently useful:
<!-- Enter yes only when the unit has durable owner value by itself. -->
- Independently reviewable:
<!-- Enter yes only when the unit can be reviewed without reconstructing a later PR. -->
- Independently testable:
<!-- Enter yes only when this head has a decisive test or real-interface oracle. -->
- Independently mergeable:
<!-- Enter yes only when no later sibling is required for safe merge. -->
- Independently reversible:
<!-- Enter yes only when this unit has its own rollback. -->
- Later sibling required:
<!-- Enter no. If yes, this is not an Atomic delivery unit. -->
- Split rationale:
<!-- For an independent unit, name its semantic owner/failure-domain seam; size, context, review time, or construction stage is invalid. Otherwise explain that this PR closes the ticket. -->
- Replaced surface disposition:
<!-- Enter Retire: <surface>; Retain: <surface>; Obligation: <current reason/consumer>; or Not applicable: <reason>. Activation/replacement cannot use Not applicable. -->
- Rollback:
<!-- State this unit's independent rollback. -->

## Review guide

<!-- Give an ordered path through the load-bearing diff and the questions a reviewer should answer. -->

## Verification

- Reviewed head:
<!-- Enter the complete 40-character commit SHA reviewed by this receipt. -->
- Validation evidence status:
<!-- Choose exactly one canonical Evidence label: diagnostic_only, shadow_ready, production_candidate, production_ready, inconclusive. -->

| Claim | Basis | Evidence and binding | Result | What it proves | What it does not prove |
| --- | --- | --- | --- | --- | --- |
<!-- Add one to three rows for a small PR. Repo evidence needs head=<SHA> plus command=<exact command> or locator=<repo path/public URL>. External evidence needs input, config, hash, and locator. -->

## Residual risk and rollback

- Residual risk:
<!-- Name up to three material risks. If none, explain why none remain in this scope. -->
- Rollback/stop:
<!-- State the rollback or stop condition. If none, explain why no rollback applies. -->

## Issue and delivery linkage

- Issue linkage:
<!-- Use a supported closing keyword plus #N for a completing PR, Refs #N for intermediate delivery, or No issue plus a rationale. -->
- Acceptance closed:
<!-- State which issue acceptance is closed by this head and what remains open, if anything. -->

## Authority Check

- [ ] This PR does not grant ProductWriter or matrix/workbook authority from diagnostics, sidecars, adjudication, review packets, or lockbox labels unless an explicit contract and expected-diff are included.
- [ ] Unrelated dirty files were not reverted or rewritten.
