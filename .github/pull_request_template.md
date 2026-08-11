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
