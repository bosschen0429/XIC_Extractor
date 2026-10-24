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
- A finite positive observed censored physical area may supply the numeric
  value. Preserve its observed censored source and the retained
  `near_detection_limit` or truncation evidence; do not impute missing signal.
  Numeric publication alone does not change counted-detection or physical
  selection policy.

### Cell decision

- Direct observations and Backfill observations are different evidence
  states.
- Backfill supplies an observation under an established Target or group.
- Target projection constructs its explicit subject before cell decision.
- Untarget cell decision consumes completed Feature × Sample evidence first.
  Matrix projection then assigns output identities and wraps the unchanged
  value/no-write payload and selected integration in `ProductionCellDecision`.
- Inclusion and counting consume those completed answers, not raw cells.

Owner quantification is eligible only when the selected integration apex is
strictly inside its selected left/right bounds. An endpoint apex remains an
owner and remains visible in diagnostics, but its Matrix cell is
`unchecked` with no area or selected integration, and it does not count as a
detected owner for the Backfill sample-quorum gate. A Backfill observation may
replace that censored owner only when its scalar and selected-integration
boundaries are both interior and its primary Matrix area is valid; an
endpoint or incomplete observation is censored by the same rule. This is a
quantification-authority guard, not a new peak-selection or Backfill route.

### Matrix publication

- Normal Matrix publication consumes the live Alignment cell decisions and
  projection owners. It does not run a separate promotion, activation dry-run,
  or product-ready closeout chain.
- Consult the [authority manifest](../superpowers/productization/status/productization_authority_manifest.json)
  for current registered writer scopes. Historical validation packets cannot
  authorize current values.
- Changes to selected values still require the owning product contract and
  method-appropriate evidence; deleting a historical validation workflow does
  not change the scientific selection policy.

Writer projection checks sample completeness per finalized row without building
a second global row-by-sample key set. Cell and integration-audit TSV writers
consume formatted rows incrementally instead of retaining a second complete
population of cell dictionaries.

The default `production` Matrix artifact is `alignment_matrix.tsv`. Retained
CID-NL runs also emit `cid_nl_explanation.sqlite` after successful Matrix
publication, alongside their runtime marker; see
[execution explanations](xic-inspector.md#cid-nl-execution-explanations).
Identity, review, provenance, Backfill evidence, HTML, and workbook artifacts
remain explicit machine, validation, diagnostic, or compatibility outputs;
their schemas and value policies are unchanged. `production` and
`validation-minimal` publication consumes finalized rows in one pass, retaining
the identity census rather than the complete cell and decision populations.
Public row numbering and identity hashes span that complete ordered stream.
Workbook, HTML and additional whole-matrix consumers retain the materialized
projection API. Both routes use the same atomic publication and rollback owner.

TSV, XLSX and HTML share the existing multi-file temp-write, replace,
fingerprint and rollback transaction. The live handoff does not define persisted
owner rehydration, cross-version replay or run/attempt identity. Diagnostic
sidecars may preserve raw evidence, but do not supply competing production
decisions.

### Renderer input

- Shared-peak explanation and PeakHypothesis selection sidecars are
  diagnostic evidence only. These sidecars do not expose an independent
  activation writer or a matrix reconstruction command.
- Finalized production owners own Matrix output through
  `output/alignment/product_matrix.py`.
- Within one activated Alignment run, the owner-minted
  `ProductionDecisionSet` is the single live semantic input for TSV, XLSX, and
  HTML projections.
- Renderers can format the `ProductionDecisionSet`. Renderers cannot rebuild
  competing values, statuses, counts, or row inclusion from raw cells.
- This in-process handoff does not define a durable decision carrier by
  itself.
- The live cell-decision owner is `alignment/cell_decision/owner.py`. Target
  retains its fixed-row decision path. Untarget completes payload and selected
  integration from group/sample evidence before its single Matrix projection
  wraps them in `ProductionCellDecision` with the published row/cell identity.
- `ProductionDecisionSet` in `alignment/production_decisions.py` is the
  writer-input projection of those finalized decisions for the existing TSV,
  XLSX, and HTML writers. `ProductionDecisionSet` holds a separate
  compatibility cell type that is also named `ProductionCellDecision`.
- The legacy matrix-based `build_production_decisions` still builds a
  `ProductionDecisionSet` as the fallback in `output/alignment/product_matrix.py` and
  for diagnostics and provenance replay.
- This writer-input projection preserves selected values, statuses, counts,
  inclusion and renderer bytes; output compatibility is not a second
  scientific decision.

The writer maps retained provenance to its legacy candidate-ID field without
revalidating assessment/hypothesis closure.

### Row identity and display

Targeted retains a typed empty fixed row when no group qualifies. Qualified
groups with disjoint sample membership bind the same fixed `TargetKey` row;
only groups competing for the same sample keep that row unresolved. Untargeted
standard Matrix includes established groups only.
Provisional groups belong in a compact machine archive/review surface;
unresolved and rejected groups remain AuditTrail facts.

A Matrix row's m/z is the mean of its members' observed m/z
(`center_mz_basis = cross_sample_owner_arithmetic_mean`). When any direct
member's Discovery candidate carries an apex MS1 reading (`ms1_apex_mz`), the
row reports the mean of those readings instead
(`center_mz_basis = discovery_ms1_apex_mean`). An inferred precursor's
observed m/z is product + loss and can sit 15–20 ppm from the MS1 ion; the
reading changes only the reported value, not grouping, traces, Backfill or
cell values.

Legacy `feature_family_id` / `public_family_id` are public row labels, not
canonical group identity. Existing public Matrix, decision and identity TSVs
may key by those labels for compatibility. A change to public keys, filenames,
ordering, columns or schema versions is a public-contract change, with the
existing expected-diff and downstream checks. Review tokens and diagnostic
risk flags do not independently authorize a value or row.

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
| `ProductionDecisionSet` | One live Alignment-owned decision set consumed by all activated renderers |
| `ProductionCellDecision` | Published value/no-write decision retaining the selected integration and workflow-specific subject |
| Productization status index / authority manifest | Current state and writer scope |

Alignment additionally exposes `alignment_review.tsv` for review,
`alignment_matrix_identity.tsv` for identity/owner traceability,
`alignment_backfill_cell_evidence.tsv` for Backfill evidence, conditional
`alignment_run_metadata.json`, and versioned `alignment-results` metadata.
These are public schema surfaces, not independent scientific authority.
The standard fast public validation profile remains `validation-minimal`;
it also writes the compact `cid_nl_explanation.sqlite` so a run can be reviewed
and scored without a rerun. It does not establish production readiness by itself.

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
- Adding diagnostic evidence columns to the primary matrix.
- Treating historical AsLS-vs-raw policy as current area authority.
- Reopening retired AsLS/linear-edge comparison gates instead of using current
  morphology-aware area and evidence rules.
- Claiming `production_ready` from contract fixtures or 8RAW smoke alone.
- Updating topic prose while leaving status index or authority manifest stale.

## See Also

- [Backfill](backfill.md) | [Alignment](alignment.md) | [Productization](productization.md)
- [Historical compatibility index](../superpowers/plans/2026-06-15-productization-control-plane.md)
- [Status index](../superpowers/validation/productization_status_index.tsv) | [Authority manifest](../superpowers/productization/status/productization_authority_manifest.json)
- [Targeted candidate selection](targeted-selection.md#candidate-selection)
- [Run provenance](run-provenance.md)
