# Quant Matrix

The quant matrix is the product-facing numeric matrix. It separates detected values from accepted Backfill quantification values and enforces explicit authority and provenance before any value enters the matrix.

## Contract

- The primary matrix exposes numeric product values and stable user-facing row/sample structure, not diagnostic evidence columns.
- Detected values and accepted Backfill values are different product states. Backfill values may enter the matrix only through explicit authority and provenance.
- `ProductionAcceptanceManifest` is the authority boundary for accepted Backfill values. Candidate sidecars, review packets, galleries, lockbox labels, and diagnostics do not grant authority by themselves.
- File-backed activation reads each manifest byte snapshot once. The
  `production_acceptance` owner validates that snapshot and mints an immutable
  `CheckedProductionAcceptanceManifest`; activation writers consume those exact
  checked rows instead of reopening the path. The lower-level
  `QuantMatrixVersion` row builder is a pure renderer and does not grant
  authority to caller-authored mappings.
- Quant matrix, identity, manifest, and expected-diff source-summary digests bind
  the same byte snapshots that were parsed and consumed. They are not hashes of
  a later reread of the input paths.
- `QuantMatrixVersion` and default activation artifacts are activation surfaces, not proof that broad Backfill is production-ready.
- Approved activation packets may write a versioned matrix bundle without re-reading the full `alignment_cells.tsv` ledger, provided the productization status index and authority manifest define the exact accepted scope.
- Matrix-only activation still requires explicit provenance and replay artifacts: acceptance manifest or expected-diff packet, `QuantMatrixVersion`, cell provenance, row/source summary, value-delta record, and application summary.
- Identity, row-basis, source, evidence, and review details belong in sidecars unless an explicit public schema says otherwise.
- Historical AsLS primary-matrix policy is superseded by LC-MS/MS evidence rules and morphology-aware area policy.
- Row-completion confidence and downstream-impact benchmarks are readiness evidence; they do not change default matrix authority without a separate activation decision.
- The M6 Untargeted CID-NL no-RAW golden path prepares one deterministic raw-area
  row and owner-mapped recovery diff, but it cannot self-authorize. Its prepared
  Backfill row must pass the file-level `ProductionAcceptanceManifest` owner and
  enter `QuantMatrixVersion` through its immutable checked snapshot before that
  cell may be rendered.
  This fixture is opt-in `production_candidate` evidence only; it does not
  register a current/default lane or change writer scope.

## Retained Validation Anchors

Archived validation notes stay in repo only when they still anchor exact product
or oracle references. They are support packets, not matrix authority by
themselves.

- PR70 matrix-handoff validation remains the scoped oracle for the
  `AlignedCell.matrix_area` handoff behavior: 8RAW and 85RAW foreground runs
  produced byte-identical `alignment_matrix.tsv`, `alignment_review.tsv`, and
  `alignment_cells.tsv` against accepted P8b outputs.
- That PR70 claim is limited to the alignment matrix handoff behavior. It does
  not promote baseline policy, resolver defaults, broader Phase2 cleanup, or any
  new matrix writer scope.
- Future branches should use stable `local_validation_artifacts/` discovery
  batch indexes for this validation shape rather than another worktree's
  ignored `output/` paths.
- If the validation note is ever compressed or moved, exact repo refs must first
  retarget to this owner or to a compact oracle artifact.

## Surfaces

| Surface | Role |
| --- | --- |
| `alignment_matrix.tsv` / workbook `Matrix` | Primary product-facing numeric matrix |
| `cell_provenance.tsv` | Cell-level provenance for accepted values |
| `row_summary.tsv` | Matrix row identity and summary sidecar |
| `expected_diff_summary` | Review gate for behavior-changing activation |
| `ProductionAcceptanceManifest` | Accepted Backfill authority record |
| `QuantMatrixVersion` | Versioned activation bundle |
| Productization status index / authority manifest | Current state and writer scope |

## Boundaries

- **Owns:** primary matrix schema, cell provenance, activation bundle format, authority boundary between detected and Backfill values.
- **Does not own:** which Backfill cells are currently accepted, per-row scientific confidence, maturity tier decisions, or upstream alignment/detection logic.
- Candidate Backfill rows are not accepted matrix values until the authority manifest says so.

## Verification

- Expected-diff packet for changed values or schema.
- Focused output tests for matrix, provenance, row summary, and source summary.
- Productization state and authority checker pass.
- Downstream-impact smoke when consumers depend on the matrix.
- Heldout/manual/oracle evidence when making production-readiness claims.

## Pitfalls

- Treating candidate Backfill rows as accepted matrix values.
- Treating review-only labels or diagnostic sidecars as writer authority.
- Adding diagnostic evidence columns to the primary matrix.
- Treating historical AsLS-vs-raw policy as current area authority.
- Reopening retired AsLS/linear-edge comparison gates instead of using current
  morphology-aware area and evidence rules.
- Claiming `production_ready` from contract fixtures or 8RAW smoke alone.
- Updating topic prose while leaving status index or authority manifest stale.

## See Also

- [Backfill](backfill.md) | [Alignment](alignment.md) | [Productization](productization.md)
- [Historical compatibility index](../superpowers/plans/2026-06-15-productization-control-plane.md)
- [Status index](../superpowers/validation/productization_status_index_v1.tsv) | [Authority manifest](../superpowers/schemas/productization_authority_manifest.v1.json)
- [Peak model selection](peak-model-selection.md)
- [Run provenance](run-provenance.md)
