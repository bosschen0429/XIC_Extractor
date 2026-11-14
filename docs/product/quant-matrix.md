# Quant Matrix

The quant matrix delivers finalized feature × sample numeric values for
downstream analysis. A published area, counted detection and chemical identity
are different facts; a blank cell is not an inferred zero. For how to read and
curate the result, use the [user guide](../user/untargeted.md#整理矩陣).
This page owns Matrix content, cell-decision and publication rules.

The type name `Matrix` is reserved for this finalized canonical shape. Owner
observations,
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
- [Signal processing](signal-processing.md#boundary-and-area-owners)
  owns the area policy.
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
  The Alignment TSV writers render those decisions directly; a cell `status`
  is `detected`, `backfilled`, or the cell's no-write reason.
- Inclusion and counting consume those completed answers, not raw cells.

An owner whose apex is not strictly inside its left/right bounds (the selected
integration's when present) has `quantification_eligible` set to false. That
flag only keeps the owner out of the RT-calibration anchors. Cell decision and
Backfill do not read it: a group member's cell takes its value from its peak
integration, and Backfill counts direct support by sample.

### Matrix publication

- Normal Matrix publication consumes the live Alignment cell decisions and
  projection owners, without a separate promotion or closeout chain.
- Only the workflow writer publishes values; see [Writer Authority](decision-policy.md#writer-authority).
  A past validation packet cannot authorize current values.
- Changes to selected values require the owning product contract and
  method-appropriate evidence.

The default `production` Matrix artifact is `alignment_matrix.tsv`. Alignment's
method-neutral per-cell evidence is `alignment_explanation.sqlite`, published
after the Matrix; see [retained evidence](workbench.md#retained-evidence-and-diagnostics).
Identity, review, provenance, Backfill evidence, HTML and workbook artifacts
remain explicit machine, validation, diagnostic or compatibility outputs.

All renderers (TSV, XLSX, HTML) format one finalized decision set from the live
cell-decision owner. A renderer cannot rebuild competing values, statuses,
counts or row inclusion from raw cells. The output files share one atomic
temp-write, replace and rollback transaction. Diagnostic sidecars can preserve
raw evidence but never supply competing production decisions.

### Row identity and display

Targeted retains a typed empty fixed row when no group qualifies. Qualified
groups with disjoint sample membership bind the same fixed `TargetKey` row;
only groups competing for the same sample keep that row unresolved. Untargeted
standard Matrix includes established groups only.
Provisional groups belong in a compact machine archive/review surface;
unresolved and rejected groups remain AuditTrail facts.

A Matrix row's m/z is its group's centre: the mean of the members' m/z,
decided once when the group is built. Backfill searches
that same centre, and Matrix, Review and the explanation report write it. A
member's m/z is its peak's own mass (`peak_mz`): the apex MS1 reading for an
inferred precursor, whose product + loss can sit 15–20 ppm from the MS1 ion,
else the XIC centre. Review XICs replay each
observation's saved extraction mass and ppm tolerance, not its MS2 evidence key.

`feature_id` is a report-scoped group label, not canonical group identity;
the Matrix itself carries no ID column. A change to public keys, filenames,
ordering, columns or schema versions is a public-contract change, with the
existing expected-diff and downstream checks. Review tokens and diagnostic
risk flags do not independently authorize a value or row.

## Surfaces

| Surface | Role |
| --- | --- |
| `alignment_matrix.tsv` / workbook `Matrix` | Primary product-facing numeric matrix |
| `ProductionCellDecision` | Published value/no-write decision retaining the selected integration and workflow-specific subject |

Alignment additionally exposes `alignment_review.tsv` for group review and
bound Matrix row references,
`alignment_backfill_cell_evidence.tsv` for Backfill evidence, conditional
`alignment_run_metadata.json`, and versioned `alignment-results` metadata.
These are public schema surfaces, not independent scientific authority.
The standard fast public validation profile remains `validation-minimal`;
it also writes the compact `alignment_explanation.sqlite` so a run can be reviewed
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

- `tests/test_alignment_tsv_writer.py`
- `tests/test_alignment_cell_decision.py`

## Pitfalls

- Treating candidate Backfill rows as accepted matrix values.
- Treating a Backfill observation as the selected value without a closed
  `ProductionCellDecision`.
- Passing a pre-final cell collection directly to a writer.
- Adding diagnostic evidence columns to the primary matrix.
- Claiming production readiness from contract fixtures or 8RAW smoke alone.

## See Also

- [Backfill](backfill.md) | [Alignment](alignment.md) | [Decision Policy](decision-policy.md)
- [Targeted candidate selection](targeted-selection.md#candidate-selection)
- [Workbench and review](workbench.md)
