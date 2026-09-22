<!-- This repository squash-merges. Commit messages do not survive the merge, so
this body is the only surviving narrative and is the unit of review. Write it for
three readers: the reviewer now, yourself in three months, and the evidence chain.
Delete any section that does not apply — empty headings are noise, not ceremony. -->

## Summary

<!-- State the outcome, before/after behavior, and bounded scope. -->

## Motivation

<!-- Name the problem and its observable cost. -->

## Non-goals

<!-- What this deliberately does not do, one line each. This is what stops a
reviewer asking "why not also X" and stops a future reader reading a decision as
an omission. Deferred work belongs here with the reason it was deferred. -->

## Public surface impact

<!-- CLI/config/API, TSV/CSV/schema, workbook/report, run metadata, persisted
identifiers, domain-selected values, counted detections, cross-repo handoff.
Name what moved and what is unchanged. Write "None" when nothing moved — that
line is itself the check, not a formality. -->

## Testing

<!-- Record commands/checks actually run, results, and the proposed head they cover.
Link existing evidence rather than reconstructing its receipt. When an artifact
or required PR gate depends on source identity, include exact producing/validated
refs and their compatibility with the current head. Hosted checks on the final
remote SHA remain the merge authority.

State what was NOT verified and why. A gap named here is evidence; a gap found in
review is a defect. -->

## Why not X

<!-- Routes considered and rejected, one line each, with the measurement or
constraint that falsified them. A reverted experiment belongs here with its
numbers. Usually the section that pays off latest and largest. -->

## Risks/Rollback

<!-- State material risks and the applicable rollback or stop condition.
Describe breaking changes explicitly when present. -->

## Reviewer notes

<!-- Assumptions, known limits, and where you want eyes. For a large or
test-heavy diff, give a review map: the load-bearing paths in reading order, and
for each one what would make it wrong. -->

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
