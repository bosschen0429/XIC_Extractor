# CID-NL Oracle Owner Decisions

Doc placement: repo_support_doc
Doc kind: note
Doc lifecycle: active
Repo owner: docs/product/README.md
Doc exit rule: Retire after these values are superseded by a new owner-signed
policy seal or absorbed into the formal Issue #125 execution packet.

Validation status: `diagnostic_only`

This record makes two explicit repository-owner decisions durable for the
Issue #125 no-RAW acceptance contract. It does not grant product authority.

## Decision CIDNL-OWNER-20260725-01

- Owner identity: Chao-hu-Lab repository owner.
- Gate: `untargeted_non_inferiority`.
- Value: Targeted correct cells minus Untargeted correct cells must be at most
  `2` over the complete `85RAW × 6 NL116 ISTD = 510` paired control census.
- Reason: these fixed-spike internal standards should be easy to recognise, so
  Targeted and Untargeted should have little practical difference.

## Decision CIDNL-OWNER-20260725-02

- Owner identity: Chao-hu-Lab repository owner.
- Gate: `automated_coverage`.
- Value: machine hold/abstention may cover at most `10%` of primary cases;
  equivalently automated coverage must be at least `0.90`.
- Reason: routine operation should remain machine-decided while preserving
  fail-closed hold behavior. Human `unassessable` is a separate completed
  observation label, not machine abstention.

## Authority Boundary

These values freeze validation policy only. They grant no Matrix, Backfill,
counted-detection, selected-area, workbook, GUI, activation, or export
authority.
