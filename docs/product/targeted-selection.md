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
CrossSample, Backfill, or selected-value implementation.

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

### Selected-hypothesis grouping

Targeted groups its already selected hypotheses through the existing CrossSample
owner. CID-NL support, observation-only
selections, and MS1-only targets retain their sample-local identities and values.
Grouping does not reselect peaks or add a cohort RT review policy.

### Physical evaluation

The Targeted selection module consumes an acquired TraceGroup, selected MS2
evidence, and the shared prepared signal and segment universe. It completes the
physical answer and synchronizes the final peak, hypothesis, and selection
decision before returning them. RAW acquisition owns trace construction and
MS2 reads; it consumes the completed selection without withdrawing or rebuilding
those facts itself.

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

- ISTD selection retains the complete configured RT window. A DDA NL anchor
  remains a selection reference; a displaced or absent anchor does not exclude
  an otherwise qualified ISTD MS1 component inside that window.
- Among non-strict ISTD candidates in the same eligibility class, local chemical
  support precedes the number of review reasons. Rolling-RT disagreement remains
  reported review evidence; exclusion, ambiguity, and hard-quality classes keep
  their precedence.
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

- Targeted NL matching excludes scans whose recorded vendor charge is not 1;
  unavailable charge remains eligible. `NO_MS2` means no method-eligible
  precursor scan, not necessarily no acquired MS2 scan. If only rejected-charge
  scans are present, `valid_ms2_scan_count` still records them but
  `matched_scan_count` is zero. The existing Targeted product projection owns
  counted detection; `count_no_ms2_as_detected` applies only to legacy outputs
  without that projection and cannot override it.
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

### Current-sample paired RT reference

For an analyte with a credible paired ISTD in this sample, the RT reference is
the analyte's configured window midpoint plus the observed ISTD shift from its
own configured midpoint. NL and no-NL analytes use this same answer. The
configured difference is retained, including nonzero isotope-labelled offsets;
pairing does not force identical apex times.

Missing or untrusted paired ISTD observations do not become anchors. The
fallback remains the configured analyte midpoint plus the available sample
drift. Neither fallback nor RT agreement establishes chemical identity. Humans
still provide target identities, pairings and method-appropriate search windows;
these cannot be assumed portable across batches or methods.

The reference is computed directly in memory for the current sample. External
RT-prior/pair calibration files are not analysis inputs. The retained rolling
ISTD context assesses within-batch ISTD consistency; it is not a replacement for
a missing current-sample paired observation or an independent identity oracle.

### Rescue scope

- The bounded headless `5-hmdC + 5-medC` contract is the only MS1
  shape-identity rescue scope with accepted `detected_flagged` expected-diff
  evidence.
- That contract grants no authority to broader targets, application wiring, or default
  broad NL_FAIL rescue. These extensions require a new target-family evidence
  gate.
- Current lane readiness lives in the status index.

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
| Current-sample paired RT reference | Method offset transferred by a credible observed ISTD; no additional writer authority |
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

## Retired candidate audit outputs

`emit_peak_candidates` and its candidate, boundary, region shadow, selected-envelope,
and target-pair RT auto-reselection TSV outputs are removed. Remove this key from
existing settings CSVs; there is no compatibility alias. Candidate-sidecar
verification and the dedicated shadow/pressure review commands are unavailable.
Use [XIC Inspector](xic-inspector.md) for visual review of its supported retained
evidence sources. Inspector does not apply candidate switches or generate RT
reselection comparison tables. Formal peak selection and numerical output remain
owner-controlled.
