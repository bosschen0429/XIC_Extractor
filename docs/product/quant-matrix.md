# Quant Matrix

The quant matrix is the final product-facing numeric grid. The type name
`Matrix` is reserved for this finalized canonical shape. Owner observations,
Backfill results, consolidation snapshots, and provisional cell collections
use different typed names. These pre-final collections cannot cross the
delivery seam as if they were already final.

## Contract

### Matrix content

- The primary matrix exposes numeric product values and a stable user-facing
  row/sample structure. The primary matrix does not expose diagnostic evidence
  columns.
- Identity, row-basis, source, evidence, and review details belong in
  sidecars unless an explicit public schema says otherwise.
- LC-MS/MS evidence rules and morphology-aware area policy supersede the
  historical AsLS primary-matrix policy.

### Cell decision

- Direct observations and Backfill observations are different evidence
  states.
- Backfill supplies an observation under an established Target or group.
- The owning projection first constructs the explicit Target/Group cell
  subject.
- `ProductionCellDecision` then independently selects the final numeric value
  or a typed no-write result. Only after that selection can the same
  projection include the cell.

### Acceptance authority

- `ProductionAcceptanceManifest` is the authority boundary for accepted
  Backfill values.
- Candidate sidecars, review packets, galleries, lockbox labels, and
  diagnostics do not grant authority by themselves.
- File-backed activation reads each manifest byte snapshot once.
- The `production_acceptance` owner validates that snapshot and mints an
  immutable `CheckedProductionAcceptanceManifest`. Activation writers consume
  those exact checked rows. Activation writers do not reopen the path.
- The lower-level `QuantMatrixVersion` row builder is a pure renderer. The row
  builder does not grant authority to caller-authored mappings.
- Forbidden authority-source classification applies to the normalized manifest
  spelling and to its resolved in-repo target. A symlink alias cannot relabel a
  shadow, report, gallery, or candidate artifact as an accepted Matrix source.
- Quant matrix, identity, manifest, and expected-diff source-summary digests
  bind the same byte snapshots that were parsed and consumed. These digests are
  not hashes of a later reread of the input paths.

### Activation

- `QuantMatrixVersion` and default activation artifacts are activation
  surfaces. These artifacts are not proof that broad Backfill is
  production-ready.
- An approved activation packet can write a versioned matrix bundle without
  re-reading the full `alignment_cells.tsv` ledger. This shortcut requires the
  productization status index and authority manifest to define the exact
  accepted scope.
- Matrix-only activation still requires these explicit provenance and replay
  artifacts:
  - the acceptance manifest or expected-diff packet;
  - `QuantMatrixVersion`;
  - cell provenance;
  - the row/source summary;
  - the value-delta record;
  - the application summary.
- Row-completion confidence and downstream-impact benchmarks are readiness
  evidence. These benchmarks do not change default matrix authority without a
  separate activation decision.

### Renderer input

- Shared-peak explanation and PeakHypothesis selection sidecars are
  diagnostic evidence only. These sidecars do not expose an independent
  activation writer or a matrix reconstruction command.
- Finalized production owners and the accepted `QuantMatrixVersion` path own
  matrix output.
- Within one activated Alignment run, the owner-minted
  `ProductionDecisionSet` is the single live semantic input for TSV, XLSX, and
  HTML projections.
- Renderers can format the `ProductionDecisionSet`. Renderers cannot rebuild
  competing values, statuses, counts, or row inclusion from raw cells.
- This in-process handoff does not define a durable decision carrier by
  itself. This handoff does not replace the separate file-backed activation
  contracts in Acceptance authority and Activation.
- `ProductionDecisionSet` is the current compatibility surface. The successor
  of `ProductionDecisionSet` is one deep `ProductionCellDecision` owner over
  explicit Target and Group subjects. Discriminated immutable finalized results
  will carry that successor owner.
- The structural migration must preserve current values, states, counts, row
  inclusion, and renderer bytes before any behavior promotion.

### No-RAW golden path

- The Untargeted CID-NL no-RAW golden-path projection prepares one
  deterministic raw-area row and an owner-mapped Backfill diff.
- The golden-path projection cannot self-authorize. Its prepared Backfill row
  must pass the file-level `ProductionAcceptanceManifest` owner. The row must
  then enter `QuantMatrixVersion` through its immutable checked snapshot. Only
  then can that cell be rendered.
- This fixture is opt-in `production_candidate` evidence only. The fixture
  does not register a current/default lane or change writer scope.

## Retained Validation Anchors

Archived validation notes stay in repo only when they still anchor exact product
or oracle references. Archived validation notes are support packets. Archived
validation notes are not matrix authority by themselves.

- PR70 matrix-handoff validation remains the scoped oracle for the
  `AlignedCell.matrix_area` handoff behavior: 8RAW and 85RAW foreground runs
  produced byte-identical `alignment_matrix.tsv`, `alignment_review.tsv`, and
  `alignment_cells.tsv` against accepted P8b outputs.
- That PR70 claim is limited to the alignment matrix handoff behavior. The
  PR70 claim does not promote baseline policy, resolver defaults, broader
  cleanup work, or any new matrix writer scope.
- Future branches must use stable `local_validation_artifacts/` discovery
  batch indexes for this validation shape. Future branches must not use the
  ignored `output/` paths of another worktree.
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
| `ProductionDecisionSet` | One live Alignment-owned decision set consumed by all activated renderers |
| `ProductionCellDecision` | Successor sole owner of selected value or typed no-write for a Target/Group cell subject |
| `QuantMatrixVersion` | Versioned activation bundle |
| Productization status index / authority manifest | Current state and writer scope |

## Boundaries

- **Owns:** final primary matrix schema, projection-owned row/cell identity,
  cell provenance, activation bundle format, and rendering of finalized cell
  decisions.
- **Does not own:** which Backfill cells are currently accepted, per-row
  scientific confidence, maturity tier decisions, or upstream
  alignment/detection logic.
- A candidate Backfill row is not an accepted matrix value until the authority
  manifest accepts that row.

## Verification

- Expected-diff packet for changed values or schema.
- Focused output tests for matrix, provenance, row summary, and source summary.
- Productization state and authority checker pass.
- Downstream-impact smoke when consumers depend on the matrix.
- Heldout/manual/oracle evidence when making production-readiness claims.

## Pitfalls

- Treating candidate Backfill rows as accepted matrix values.
- Treating a Backfill observation as the selected value without a closed
  `ProductionCellDecision`.
- Passing the pre-final owner Matrix or loose `AlignedCell` collection directly
  to a writer.
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
- [Status index](../superpowers/validation/productization_status_index.tsv) | [Authority manifest](../superpowers/schemas/productization_authority_manifest.json)
- [Peak model selection](peak-model-selection.md)
- [Run provenance](run-provenance.md)
