# Quant Matrix

The quant matrix delivers finalized feature × sample numeric values for
downstream analysis. A published area, counted detection and chemical identity
are different facts; a blank cell is not an inferred zero. This page owns
Matrix content, cell-decision and publication rules; for reading and curating
the result, use the [user guide](../user/untargeted.md#整理矩陣).

| Question | Rule | Detail |
| --- | --- | --- |
| What is in the primary Matrix? | Numeric values and a stable row/sample structure, no diagnostic columns. | [Matrix content](#matrix-content) |
| Who decides a cell? | The cell decision; writers render its value or no-write. | [Cell decision](#cell-decision) |
| Which rows are published? | Untargeted: established groups only. Targeted: one fixed row per Target. | [Row identity and display](#row-identity-and-display) |
| What is a row's m/z? | The mean of its members' m/z, fixed when the group is built. | [Row identity and display](#row-identity-and-display) |

The type name `Matrix` is reserved for this finalized shape. Owner
observations, Backfill results, consolidation snapshots and provisional cell
collections use other names and cannot cross the delivery seam as if final.

## Contract

### Matrix content

- The primary Matrix exposes numeric product values and a stable user-facing
  row/sample structure, not diagnostic evidence columns. Identity, row basis,
  source, evidence and review details belong in sidecars unless an explicit
  public schema says otherwise.
- [Signal processing](signal-processing.md#boundary-and-area-owners) owns the
  area policy.
- A finite positive observed censored physical area can supply the value; its
  censored source and `near_detection_limit` or truncation evidence are kept,
  and missing signal is not imputed. Numeric publication alone does not change
  counted-detection or physical selection policy.

### Cell decision

- Direct and Backfill observations are different evidence states; Backfill
  supplies an observation under an established Target or group.
- Target projection constructs its explicit subject before the cell decision.
  The Untarget cell decision consumes completed Feature × Sample evidence
  first; Matrix projection then assigns output identities and wraps the
  unchanged value/no-write payload and selected integration.
- The Alignment TSV writers render those decisions directly, with the cell
  [`status`](domain-glossary.md#evidence-terms). A `backfilled` peak can supply
  an area in an established row; it does not establish the row or count as
  direct CID-NL support.
- Inclusion and counting consume those completed answers, not raw cells.
- Owners without an integration, or with an apex on an integration boundary,
  do not train the RT calibration. Cell decisions take values from peak
  integrations, not the anchor-eligibility flag.

### Matrix publication

- Normal publication consumes the live Alignment cell decisions and projection
  owners, without a separate promotion or closeout chain. Only the workflow
  writer publishes values ([Writer Authority](decision-policy.md#writer-authority));
  a past validation packet cannot authorize current values. Changing selected
  values needs the owning product contract and method-appropriate evidence.
- The default `production` Matrix artifact is `alignment_matrix.tsv`; every
  level also writes the [RT anchor table](rt-calibration.md#rt-anchor-table).
  Alignment's method-neutral per-cell evidence is
  `alignment_explanation.sqlite`, published after the Matrix
  ([retained evidence](workbench.md#retained-evidence-and-diagnostics)).
  Identity, review, provenance, Backfill evidence, HTML and workbook artifacts
  are explicit machine, validation, diagnostic or compatibility outputs.
- All renderers (TSV, XLSX, HTML) format one finalized decision set from the
  live cell-decision owner and cannot rebuild competing values, statuses,
  counts or row inclusion from raw cells. The output files share one atomic
  temp-write, replace and rollback transaction. Diagnostic sidecars can keep
  raw evidence but never supply competing production decisions.

### Row identity and display

- Targeted keeps a typed empty fixed row when no group qualifies. Qualified
  groups with disjoint sample membership bind the same fixed Target row; only
  groups competing for one sample leave it unresolved.
- The Untargeted standard Matrix includes established groups only.
  Provisional groups belong in a compact machine archive/review surface;
  unresolved and rejected groups stay audit-trail facts.
- A row's m/z is its group's centre, the mean of the members' m/z, decided once
  when the group is built; Backfill searches that centre, the Matrix displays
  it and run metadata keeps full precision. A member's m/z is its peak's own
  `peak_mz` ([accurate-mass reading](discovery.md#accurate-mass-reading)),
  whose product + loss can sit up to 10 ppm from the MS1 ion. Review XICs
  replay each observation's saved extraction mass and ppm tolerance, not its
  MS2 evidence key.
- `feature_id` is a report-scoped group label, not canonical identity; the
  Matrix carries no ID column. A change to public keys, filenames, ordering,
  columns or schema versions is a public-contract change needing expected-diff
  and downstream checks. Diagnostic risk flags do not authorize a value or row.

## Surfaces

| Surface | Role |
| --- | --- |
| `alignment_matrix.tsv` / workbook `Matrix` | Primary product-facing numeric matrix |
| `ProductionCellDecision` | Published value/no-write decision retaining the selected integration and workflow-specific subject |

Workbench shows group review from the explanation report. The Matrix SHA-256,
row-ordered feature IDs and full-precision masses are stored together in run
metadata. Diagnostic outputs include `alignment_backfill_cell_evidence.tsv`,
`alignment_rt_anchors.tsv` (frozen calibration knots), conditional
`alignment_run_metadata.json`, and versioned `alignment-results` metadata;
these are public schema surfaces, not scientific authority. The fast public
validation profile is `validation-minimal`; it also writes the compact
`alignment_explanation.sqlite` so a run can be reviewed and scored without a
rerun, and does not establish production readiness.

## Boundaries

- **Owns:** the final primary Matrix schema, projection-owned row/cell
  identity, cell provenance, and rendering of finalized cell decisions.
- **Does not own:** which Backfill cells are accepted, per-row scientific
  confidence, maturity tiers, or upstream alignment/detection logic.

## Pitfalls

- Treating candidate Backfill rows as accepted Matrix values.
- Treating a Backfill observation as the selected value without a closed
  cell decision.
- Passing a pre-final cell collection directly to a writer.
- Adding diagnostic evidence columns to the primary Matrix.
- Claiming production readiness from contract fixtures or 8RAW smoke alone.

## Verification

`tests/test_alignment_tsv_writer.py`, `tests/test_alignment_cell_decision.py`.
See also [Backfill](backfill.md) · [Alignment](alignment.md) ·
[Decision Policy](decision-policy.md) ·
[Targeted candidate selection](targeted-selection.md#candidate-selection) ·
[Workbench and review](workbench.md)
