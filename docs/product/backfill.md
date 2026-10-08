# Backfill

Backfill（回填）revisits a missing cell after Discovery and Alignment have
established an owner-backed Target or cross-sample group.
A revisit can produce a local observation. Backfill does not create a group,
decide membership, or count a detection. Backfill does not grant Matrix writer
authority on its own. The current Untargeted CID-NL path uses the bounded
conservative decision in `xic_extractor.alignment.conservative_backfill`.

## Contract

### Untargeted route

1. Normal Untargeted GUI runs, Alignment CLI runs, and preset runs enter the
   retained CID-NL workflow through `applications.untargeted.application.run_untargeted_application`.
2. The runtime schedules bounded per-sample Backfill work in
   `untargeted_execution.partition.workers`.
3. Production composition in `untargeted_execution.partition_composition` maps the
   resulting evidence through the conservative Backfill decision.
4. The Untargeted route does not execute the retained `ObservationRevisit`
   transaction.

### Group support gate

The active decision uses one fixed group and the direct SUPPORT closure of
that group.

| Quantity | Rule |
| --- | --- |
| `N` | Counts non-excluded study, QC, and pooled-QC samples. Blank, calibrator, system-suitability, solvent, and unknown roles do not vote. |
| `K` | `K = max(2, N//20)`. |
| `S` | Counts distinct eligible samples with direct SUPPORT for that exact group. Each sample counts once. |

- If `S < K`, the conservative revisit does not start for that group.
- The workset contains every eligible sample without direct SUPPORT for the
  group, including samples with a HOLD or REJECT assessment. The frozen
  group and the local RT/mass request bound the workset.
- A Backfill candidate does not add a direct SUPPORT vote.

### Query RT versus group RT

The frozen group center remains the group's identity/evidence coordinate.
The request center is sample-specific: take up to twelve same-cohort
injection neighbors, convert their measured RTs to common coordinates when
supported, fit a robust local linear trend over injection order so steep drift
is followed rather than lagged, then apply the target sample's frozen batch warp. Both XIC bounds
and preferred-peak selection use this query RT. Insufficient calibration and
missing-context fallbacks have explicit bases; neither means measured zero
shift. See the [RT mechanism table](alignment.md#rt-mechanisms-and-their-consumers).

Completed-empty evidence binds both the original group center and actual query
RT. It validates the acquired query, not containment of the group center in a
shifted window. Supported Backfill admission uses the same frozen coordinates
and owner-to-band trend rule as final grouping; it does not refit the warp.

### Cell outcome

1. An eligible missing Untargeted cell can yield
   `ms1_only_backfill_observation` when the cell has exactly one
   group-bounded MS1 peak and no same-sample CID-NL evidence.
2. `ProductionCellDecision` can write the area of that observation as
   `backfilled` / `accepted_backfill`.
3. A backfilled sample does not become a detected member of the group.
4. The cell gets no Backfill value in each of these cases:
   - no peak;
   - multiple peaks;
   - competing evidence;
   - insufficient group support;
   - an ineligible sample;
   - a member of a Matrix row already holds the peak;
   - the Backfill of another group also found the peak.
5. A member of an archive row writes no value and does not hold the peak.
6. If the windows of two rows resolve the same peak, the cell stays unresolved
   in both rows. The peak does not supply one value twice.
7. Each window traces the peak again with its own bounds. Therefore one peak
   means one apex scan in one sample within the preferred m/z tolerance, as in
   Discovery. One peak does not mean one peak-observation content ID.
8. When Backfill independently assesses a cell with an initial missing-MS1
   `REJECT`, the Backfill assessment retains the initial `REJECT`. The Backfill
   assessment must earn SUPPORT through the normal relation owner before the
   cell can hold a numeric value.

### Observation revisit contract

1. Final cell composition still accepts the typed `ObservationRevisitReceipt`
   input for a revisited member.
2. No installed workflow calls the former execute/close operation. No
   code creates the receipt, because the receipt mint token in
   `alignment/backfill/observation_revisit.py` is never passed to the receipt
   constructor. The receipt type alone does not prove that a revisit transaction
   ran.

## Surfaces

- `alignment_backfill_cell_evidence.tsv` is a compact validation output for
  accepted Backfill cells. Schema `alignment_backfill_cell_query_evidence` adds
  `backfill_query_rt_min` and `backfill_query_rt_max` from the executed assessment;
  `family_center_rt` remains a distinct group-level display value. Unavailable
  query bounds are empty, never copied from that display value. Readers require
  the query-bound columns; old compact TSV exports must be regenerated.
- Validation-minimal output selection emits that TSV. The ordinary production
  level writes the Matrix.
- The TSV can be empty when the run has no eligible accepted Backfill cell. A
  population check must use a fixture known to exercise this path.

## Boundaries

- `ProductionCellDecision` owns the selected numeric output and the
  write/no-write decision.
- CrossSample owns membership.
- The [Quant Matrix](quant-matrix.md), the
  [productization status index](../superpowers/validation/productization_status_index.tsv),
  and the [authority manifest](../superpowers/productization/status/productization_authority_manifest.json)
  own publication scope.
- Diagnostic traces, a well-formed coverage epoch, and candidate records do not
  grant any of the authorities listed above.
