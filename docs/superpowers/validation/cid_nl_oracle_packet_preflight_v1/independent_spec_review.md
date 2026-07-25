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
Current verdict: `APPROVE`

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

The pre-hosted-review bounded snapshot reported:

- Findings: none.
- Residual risks within the no-RAW contract scope: none.
- Verdict: `APPROVE`.

That verdict is historical rather than authority for every later PR snapshot.
Hosted PR review subsequently reopened the contract after finding five
fail-open paths in verdict disposition, schema enforcement, and cross-phase
identity. The required same-class census then found related gaps in sealed
scope completeness, review/adjudication cardinality, reference denominator
counts, manifest primary keys, schema versions/enums, and statistical binding.

The implementation and regression suite now address those finding classes.
The final fixed-diff review used two independent reviewers. One reviewer found
no remaining issue. The other drove a final same-class closure over manifest
byte-to-row binding, seal cardinality and identity invariants, review and
adjudication custody, reference-frame denominators, cross-stratum invariants,
and standalone sampling-manifest method scope. Both reviewers then returned:

- Findings: none.
- Residual risks within the no-RAW contract scope: none.
- Verdict: `APPROVE`.

The final local verification for this snapshot reported:

- 235 related CID-NL and artifact-retention tests passed;
- ruff passed for `xic_extractor` and `tests`;
- mypy passed for 445 source files;
- strict artifact retention passed with 281 retained files, 52 externalized
  files, and zero `shrink_later` entries;
- `git diff --check` passed.

## Boundary

Approval means the no-RAW acceptance-contract design and its executable
integrity checks are internally coherent. It does not mean a tracer exists or
that formal evaluation ran. It grants no Matrix, Backfill, counted-detection,
selected-area, workbook, GUI, activation, export, or product authority.
