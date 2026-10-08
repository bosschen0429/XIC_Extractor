<!-- This repository squash-merges. Commit messages do not survive the merge, so
this body is the only surviving narrative and is the unit of review. Write it for
three readers: the reviewer now, yourself in three months, and the evidence chain.
Delete any section that does not apply — empty headings are noise, not ceremony. -->

## Summary

<!-- Lead with the user-visible before/after and what deliberately stays unchanged.
Use the smallest useful view: a behavior table, diff sketch, or short flow. Plain
text is enough for a simple change. Put source-PR/SHA inventories and construction
history after the outcome, only when needed to review the combined behavior. -->

## Motivation

<!-- Name the problem and its observable cost. -->

## Testing

<!-- Start with the decisive evidence: the previous wrong result and the observed
result after the change, with their producing refs. Then record commands/checks
actually run, results, and the proposed head they cover. Passing test counts alone
do not explain the behavior or establish an acceptable scientific trade-off.
Link existing evidence rather than reconstructing its receipt. When an artifact
or required PR gate depends on source identity, include exact producing/validated
refs and their compatibility with the current head. Hosted checks on the final
remote SHA remain the merge authority.

Name material limits of the evidence. -->

## Risks/Rollback

<!-- Name affected consumers and material risks. State whether rollback restores
code alone or also needs data migration/reprocessing, and give the applicable
rollback or stop condition. Describe breaking changes explicitly when present. -->

Issue linkage: <!-- Closing keyword + #N, Refs #N, or No issue with the clear request. -->

<!-- Expanded details: include only the applicable cases from
docs/agent/planning-workflows.md#task-shaping. Small established-owner changes
can finish with the compact body above; omit inapplicable details.

- A material owner, consumer, public-contract, or persisted/schema change:
  link canonical scope/acceptance, describe the accepted delta, affected entries,
  preserved behavior, and any migration/retirement census needed by the decision.
- A real delivery edge: link blockers and the consumed/produced handoff.
- Science/readiness or high-cost validation: link the source-qualified owner
  receipt, method-appropriate oracle/verdict, Evidence label, and remaining gaps.
- ProductWriter or matrix/workbook authority change: link the explicit contract
  and expected diff; record the affected real-interface validation.
- A diff requiring review guidance: give the load-bearing path and questions.
-->
