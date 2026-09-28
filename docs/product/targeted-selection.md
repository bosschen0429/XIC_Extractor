# Targeted Selection

Targeted CID-NL uses the same quadrature precursor/product m/z window as
Discovery when it evaluates a configured neutral loss. `nl_ppm_max` and the
CID-NL support policy tolerance are per-ion ppm inputs to that window. The
reported observed-loss error in ppm remains a descriptive measurement.

Targeted selection is the Targeted workflow projection for known targets.
Targeted selection does four things:

1. Targeted selection constrains the shared scientific lifecycle with an exact
   target-list snapshot.
2. Targeted selection binds finalized groups to fixed target rows.
3. Targeted selection applies Targeted inclusion/counting and typed
   reason/routing policy.
4. Targeted selection closes references to the existing Review Queue and the
   delivery-shape decisions.

Existing CSV/workbook/review surfaces render human-facing reasons from those
typed decisions. Targeted selection does not require a second capture,
CrossSample, ModeTrack, Backfill, or selected-value implementation.

## Contract

### Reference input diagnostics

`tools.diagnostics.check_reference_consistency` checks target CSV references
on demand, using the existing configuration reader and RAW acquisition owner.
The JSON manifest explicitly names each method/group, target CSV, and optional
RAW files. The reference RT is the target search-window midpoint.
Multiple groups use leave-one-out monotonic interpolation of other shared
targets, including ISTDs. For each directed group pair, anchors with the
largest supported own leave-one-out residual above the policy threshold are
iteratively excluded while at least three anchors remain. Excluded targets
still receive predictions and warnings; exclusion does not declare a reference
wrong. Unsupported endpoints are not extrapolated.
Single-group input skips mapping and checks only RAW peak-cluster positions.

The diagnostic emits `reference_consistency.json` and `reference_review.tsv`.
The JSON records screened anchors, pairwise predictions, and the median/p90
absolute residual distribution over group/target median predictions. Mapping
residuals above the policy threshold receive informational warnings. High
warnings require RAW multi-cluster ordinal disagreement, a reference outside
every detected cluster's half-prominence extent and beyond the apex-match
tolerance, or a mapping prediction matching a different RAW cluster. A matching
single peak cannot produce a high warning.
Ordinals compare only equal cluster counts, with one vote per group and a
majority of all peer groups; incomparable groups abstain. Gaussian smoothing,
minimum prominence, and minimum half-prominence width reject narrow impulses
before assigning ordinals. These are diagnostic positions, not selected peaks.
Missing RAW evidence remains unavailable. CLI help owns policy defaults and
acquisition bounds; the JSON preserves the effective policy and input provenance.
These outputs have `diagnostic_only` evidence status and no selected-peak,
reference-editing, quantitation, or ProductWriter authority.

### Within-group selected-peak review

Targeted groups the already selected owner hypotheses through CrossSample without
ModeTrack. CID-NL support, observation-only selections, and MS1-only targets keep
their sample-local identities and measurements; grouping does not reselect peaks.

For each target in one method request, the Targeted post-cell owner compares all
observed selected apex RTs within each sample metadata group, independently of
CrossSample's RT partition. A range greater than 0.3 minutes routes every selected
cell in that group to `targeted_group_rt_inconsistent` review. This repeatability
tolerance is the one used to identify the within-method cohort disagreements;
it is a review threshold, not a peak-identity or automatic correction rule.
Empty/single-observation groups and groups within tolerance receive no such flag.
Missing observations are not assigned an RT. Different method requests or sample
groups are never pooled.

The public `Projection Review Reasons` and review state render this owner result.
Clean detections become flagged; existing stricter states remain. Selected peaks,
bounds, areas, and counted-detection decisions are unchanged, including genuine
retention shifts. A whole group selecting the same wrong peak is not detectable
by this comparison; reference diagnostics remain a separate review surface.

### Physical evaluation

- Acquisition enumerates the shared physical segment universe for the
  configured signal profile.
- Targeted scoring and identity evidence choose a component. The physical
  evaluation of that component supplies the boundary and counts·seconds area.
- Profile-bound capture retains that evaluation through finalization.
  Profile-bound capture does not run a second boundary resolver or
  integration.
- RT, bounds, and width are expressed in minutes.
- When identity selection chooses no component, the physical question retains
  the acquired context and full segment universe with no admissible selection;
  capture preserves the unavailable answer instead of selecting a broad tail.
- If the selection window has no qualified physical candidate but contains a
  clipped segment's apex, acquisition expands its context once by the configured
  target-window width on each side, with a zero lower RT limit. Selection bounds
  and reference RT stay unchanged. An existing qualified in-window candidate
  keeps its original acquisition and quantitation.

### Target identity and search scope

- For no-NL inputs, the configured RT-window center participates in component
  identity while the complete acquisition context remains available to the
  shared physical owner. The supplied group reference is encoded in that
  center; missing reference rows remain excluded by the input owner.
- A credible paired ISTD transfers observed drift while preserving the
  configured analyte/ISTD RT offset. Missing or untrusted pairs fall back to the
  analyte reference and existing sample drift. No-NL selection does not use
  learned RT-library priors.
- Among strongly supported no-NL physical candidates without other identity
  blockers, selection first prefers an interval containing the reference, then
  the closest apex. Shape concerns remain evidence for the existing product
  policy; identity selection does not alter counted detection or physical
  bounds and area. Equal references for different target rows are permitted.
- Product Targeted output is a workflow-owned projection from evidence and
  selection decisions.
- Shared evidence providers inform Targeted product state. Shared evidence
  providers do not choose Targeted product state.
- `TargetKey` is the stable logical row identity.
- `TargetDefinition` is the exact immutable scientific revision.
- `TargetListSnapshot` is the exact ordered request input.
- A same-label definition change preserves the key. The same change alters the
  definition identity and the snapshot identity.
- Duplicate labels fail closed.
- `TargetSearchScope` narrows the allowed target universe.
  `TargetSearchScope` can still ask the shared Discovery/capture method to
  inspect broad evidence relationships such as dR, R, or MeR.
- Broad candidate discovery is not Untargeted-only.

### Row binding

- `TargetRowBinding` retains the qualified finalized groups that each fixed
  Target row needs.
- If zero groups qualify, the row stays with a typed state.
- Groups that cover distinct samples bind the same `TargetKey` row.
- Multiple groups that compete for one sample remain unresolved with stable
  Review Queue references.
- `TargetRowBinding` does not rescan RAW, rank by area, change integration, or
  choose an arbitrary winner.
- A Target row can bind a unique established or provisional group.
- An owner-backed direct local observation can produce a Target cell value.
  This rule holds even when the group is provisional or the observation is not
  a group member.
- A sample without its own owner-backed direct local observation cannot get
  a Target cell value from singleton or RT-prior evidence of other samples.
  Such a sample requires its own direct local evidence. The Target cell
  decision takes no revisit receipt input.

### Cell decision order

1. Targeted selection owns `TargetRowBinding` and constructs
   `TargetCellSubject`.
2. `ProductionCellDecision` owns the selected numeric value or the typed
   no-write result.
3. The same Targeted owner then closes inclusion, counted-detection policy,
   typed reason facts, and Review Queue routing. The Targeted owner must not
   recompute the value.

### Numeric output and evidence

- Finalized CSV/workbook RT, area, and bounds render the cell owner's numeric
  answer even when `Counted Detection` is false.
- Count, confidence, and review remain separate evidence/policy fields.
- A typed no-write clears the retained peak before rendering.
- Missing NL alone is not another writer veto.
- Accepted MS1-only cells in Untargeted output follow the same distinction.
- The main workbook table shows measurements, integration boundaries,
  `NL Evidence`, and a plain-language `Reason` for user interpretation.
- Internal product/count/review codes remain available in advanced columns.

### Detection and review policy

- Region, boundary, and selected-envelope logic are candidate and
  model-selection surfaces.
- Before region, boundary, or selected-envelope logic changes any selected
  peak, area, confidence, reason, product state, counted detection, workbook
  value, or matrix value, the change requires expected-diff review.
- Selected-hypothesis public behavior must preserve compatibility
  projections unless an explicit schema/versioning plan changes the
  downstream contracts.
- A positive, coherent analyte peak can sit inside the configured search scope
  with candidate-associated MS2/NL support. Such a peak keeps
  RT-prior/window-position conflicts as review, independent of the sample
  applicability category.
- RT disagreement alone does not make that peak ND.
- Sample exclusions, paired-anchor conflicts, hard quality blockers, and
  unresolved quantitative boundaries still apply to that peak.

### Rescue and RT-pair scope

- The bounded headless `5-hmdC + 5-medC` contract is the only MS1
  shape-identity rescue scope with accepted `detected_flagged` expected-diff
  evidence.
- That contract grants no authority to broader targets, GUI wiring, or default
  broad NL_FAIL rescue. These extensions require a new target-family evidence
  gate.
- Current lane readiness lives in the status index.
- Target-pair RT auto-reselection and calibration artifacts are
  activation-gated selection aids. These aids are not default authority unless
  the status index, authority manifest, promotion contract, and focused tests
  say so.

### Expected-diff inputs

- Targeted expected-diff approvals must reference runtime product
  `PeakHypothesis` candidate IDs.
- Overlay-only or audit-table candidate IDs can explain support. These IDs are
  not valid product-switch inputs.
- Product action or verdict tables are review/reason surfaces. These tables
  must not hide the evidence chain or grant writer authority.

## Retained Validation Anchors

- The 2026-06-04 `BenignfatBC1055_DNA / 8-oxodG` closeout is a row-specific
  expected-diff activation. The closeout is not a general target-pair RT
  auto-reselection rule.
- The closeout changed one Targeted product row. The change happened after the
  approved successor existed in runtime product hypotheses and had role-aware
  RT, paired-area-ratio, MS1, and trace support.
- The visible product decision surface for that slice is `Product State`,
  `Counted Detection`, `Review State`, and projection-backed `Reason`. Legacy
  `Confidence`, score, and cap evidence remain technical audit material while
  this projection contract is active.
- The observed 8RAW count change for that slice was `8-oxodG: 3/8 -> 4/8`,
  with all other target labels unchanged. That count change is a validation
  anchor for the activation gate. That count change is not broad target-pair
  policy.

## Surfaces

| Surface | Role |
| --- | --- |
| Targeted CSV/workbook fields | Public output for target decisions |
| RT / Area / PeakStart / PeakEnd | Retained cell-owner measurements |
| `NL Evidence` / `Reason` | Human-readable observations and review context |
| `Product State` / `Counted Detection` / `Review State` | Advanced product-policy fields; do not veto a retained numeric value |
| Confidence | Advanced review context rendered from typed projection decisions |
| Region and selected-envelope candidates | Boundary and model-selection evidence |
| Target-pair RT calibration artifacts | Activation-gated selection support |
| Expected-diff packets | Required gate for output-changing behavior |

## Boundaries

- **Consumes:** owner-issued `TargetSearchScope`.
- **Owns:** fixed `TargetKey` row projection, pre-cell
  `TargetRowBinding`/`TargetCellSubject`, post-cell inclusion,
  typed confidence/reason/product-state/counting decisions, existing Review Queue
  routing, MS1 rescue policy scope, and target-pair RT rules.
- **Selected value:** `ProductionCellDecision` owns the selected numeric value.
  Targeted projection renders that decision and its retained physical
  evidence. Targeted projection does not rerun peak selection or integration.
- **Does not own:** Backfill/Discovery writer scope, the current selected peak
  of a specific target, full review-action apply behavior, or maturity tier
  decisions.
- Targeted authority does not extend to Untargeted pipelines. Untargeted
  authority does not extend to Targeted pipelines.

## Verification

- Focused tests for selected peak, area, confidence, reason, product state, and counted detection.
- Expected-diff packet for workbook/CSV or matrix-impacting changes.
- Schema/versioning review when output fields or compatibility projection change.
- RAW-tier evidence label matching the product claim.
- Structured productization-owner update when maturity, lane, or authority
  changes: status index for lane state, authority manifest for writer scope,
  and the promotion contract for readiness evidence.
- The bounded CID-NL `targeted_output` delivery claims and current test
  dispositions are indexed in the
  [public-output claim catalog](../superpowers/validation/cid_nl_public_output_claim_catalog.tsv).
  The catalog points to these owners. The catalog does not grant
  selected-value, counted-detection, ProductWriter, or Matrix authority.

## Pitfalls

- Treating diagnostic boundary proposals as final selected boundaries.
- Promoting target-pair RT support without explicit activation.
- Letting selected-hypothesis refactors silently change public reason or counted-detection behavior.
- Reusing Backfill or Discovery authority for Targeted selection.
- Treating configured Target identity as proof of a local observation or group
  membership.
- Creating a second Target-only capture, trajectory, Backfill, or cell-decision
  implementation because the output rows differ.

## See Also

- [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md) | [Evidence spine](evidence-spine.md)
- [Quantitation context](quantitation-context.md) | [Review roundtrip](review-roundtrip.md)
- [Peak model selection](peak-model-selection.md)
- [Decision policy](decision-policy.md)
