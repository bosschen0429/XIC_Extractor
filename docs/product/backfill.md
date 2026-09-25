# Backfill

Backfill（回填）revisits a missing cell after Discovery and Alignment have
established an owner-backed Target or cross-sample group. A revisit can produce
a local observation; it does not create a group, decide membership, count a
detection, or independently grant Matrix writer authority. The current
Untargeted CID-NL path uses the bounded conservative decision in
`xic_extractor.alignment.conservative_backfill`.

## Current Untargeted route

Normal Untargeted GUI, Alignment CLI, and preset runs enter the retained CID-NL
workflow through `alignment.pipeline.run_alignment`. The runtime schedules
bounded per-sample Backfill work in
`extraction.untargeted_real_data_worker`; production composition in
`extraction.untargeted_production_batch` maps the resulting evidence through
the conservative Backfill decision. This route does not execute the retained
`ObservationRevisit` transaction.

The active decision is based on a fixed group and its direct SUPPORT closure:

- `N` counts non-excluded study, QC, and pooled-QC samples. Blank,
  calibrator, system-suitability, solvent, and unknown roles do not vote.
- `K = max(2, N//20)`. `S` counts distinct eligible samples with direct
  SUPPORT for that exact group, once per sample. A group with `S < K` does
  not start the conservative revisit.
- The workset contains eligible missing sample/group positions, bounded by
  the frozen group and local RT/mass request. Backfill candidates do not add
  direct SUPPORT votes.

For an Untargeted eligible missing cell, one group-bounded MS1 peak with no
same-sample CID-NL evidence can yield
`ms1_only_backfill_observation`. `ProductionCellDecision` may write its area
as `backfilled` / `accepted_backfill`; the sample does not become a detected
group member. No peak, multiple peaks, competing evidence, insufficient group
support, or an ineligible sample leaves the cell without that value. Neither
does a peak that a Matrix row's member holds or another group's Backfill also
found; an archive row's member writes no value and does not hold the peak. When
two rows' windows resolve the same peak, the cell stays unresolved in both
rather than taking one value twice. Each window traces the peak again with its own bounds, so one peak
is one apex scan in one sample within the preferred m/z tolerance, as in
Discovery, not one peak observation content ID. An
independent Backfill assessment of an initial missing-MS1 `REJECT` retains
the initial assessment and must earn SUPPORT through the normal relation
owner before it can produce a numeric cell.

`alignment_backfill_cell_evidence.tsv` is a compact validation output for
accepted Backfill cells. It is emitted by validation-minimal output selection;
the ordinary production level writes the Matrix. Empty evidence is possible
when the run has no eligible accepted Backfill cell, so population checks
require a fixture known to exercise this path.

## Other current consumers

Targeted diagnostic processing calls
`alignment.targeted_hypothesis_backfill`, which uses
`alignment.backfill_peak_fact.build_backfill_result` as an XIC search
capability. This does not add an Untargeted eligibility decision. Shared
coverage and request-plan types remain available to this path and to current
alignment metadata.

Final cell composition still accepts the typed `ObservationRevisitReceipt`
input for a revisited member. No installed workflow calls the former
execute/close operation. The receipt type alone does not establish that a
revisit transaction ran.

## Authority boundary

`ProductionCellDecision` owns selected numeric output and write/no-write.
CrossSample owns membership. The [Quant Matrix](quant-matrix.md), the
[productization status index](../superpowers/validation/productization_status_index.tsv),
and the [authority manifest](../superpowers/schemas/productization_authority_manifest.json)
own publication scope. Diagnostic traces, a well-formed coverage epoch, and
candidate records do not grant that authority.
