# CID-NL Oracle Independent Spec Review

Doc placement: repo_support_doc
Doc kind: report
Doc lifecycle: implemented
Repo owner: docs/product/discovery.md
Doc exit rule: Retain with the Issue #125 acceptance contract; supersede only
with a later fixed-snapshot review of a changed contract.

Validation status: `diagnostic_only`
Review date: 2026-07-25
Review mode: independent fixed-diff review; no RAW execution
Final verdict: `APPROVE`

## Scope

The independent reviewer examined the Issue #125 no-RAW statistical design,
proposal/sampling/reference/review/lineage schemas, gate and verdict contract,
NARROW preregistration, synthetic suite, calibration retention, executable
validators, and product-authority boundary.

## Adjudication History

The initial review returned `REQUEST_CHANGES`. Its substantive findings were
closed through repeated fixed-snapshot adjudication:

- verdict reduction now binds the exact canonical 25-gate table and fails
  closed on missing or substituted gates;
- NARROW requires a pre-reveal sealed scope row present in the hashed manifest,
  exact membership bytes, membership-bundle seal, and scope-specific gate
  results;
- proposal, reference, review, adjudication, statistical-design, lineage, and
  owner-decision records use canonical row or content hashes;
- sampling rows bind to their preregistered estimand, frame, design, dependency
  groups, and compatible weighting rule;
- independent-reference source, dependency, enumerator, and construction
  evidence is content-bound; full recovery requires a complete sealed frame
  whose manifest bytes, strata, source manifests, and protocol hashes verify;
- hidden duplicates derive from the sealed sampling rows; review, taxonomy,
  and adjudication links require unique immutable records and valid
  predecessors;
- synthetic metric denominators are derived from scenario state rather than
  author-supplied answer flags;
- owner policy values bind to a durable owner decision record;
- lifecycle metadata and `diagnostic_only` authority boundaries are explicit.

The final bounded review reported:

- Findings: none.
- Residual risks within the no-RAW contract scope: none.
- Verdict: `APPROVE`.

## Boundary

Approval means the no-RAW acceptance-contract design and its executable
integrity checks are internally coherent. It does not mean a tracer exists or
that formal evaluation ran. It grants no Matrix, Backfill, counted-detection,
selected-area, workbook, GUI, activation, export, or product authority.
