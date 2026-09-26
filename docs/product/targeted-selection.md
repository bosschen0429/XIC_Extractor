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

### Physical evaluation

- Acquisition enumerates the shared physical segment universe for the
  configured signal profile.
- Targeted scoring and identity evidence choose a component. The physical
  evaluation of that component supplies the boundary and counts·seconds area.
- Profile-bound capture retains that evaluation through finalization.
  Profile-bound capture does not run a second boundary resolver or
  integration.
- RT, bounds, and width are expressed in minutes.
- An NL-focused acquisition can clip a selected peak at an acquired trace edge
  within the configured target scope, while the apex stays inside the
  configured target RT window. In that case the acquisition widens its query
  once to the union of the current query RT range and the configured target RT
  window (`rt_min`..`rt_max`). The same peak and evidence owners then
  select from the observed context.
- A complete selected peak keeps its original query. A manual RT does not
  trigger this window restore and does not choose its result.

### Target identity and search scope

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
  Such a sample requires direct local evidence or an accepted independent
  `ObservationRevisit`. No production code currently mints an
  `ObservationRevisit` receipt.

### Cell decision order

1. Targeted selection owns `TargetRowBinding` and constructs
   `TargetCellSubject`.
2. `ProductionCellDecision` owns the selected numeric value or the typed
   no-write result.
3. The same Targeted owner then closes inclusion, counted-detection policy,
   typed reason facts, and Review Queue routing. The Targeted owner must not
   recompute the value.

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
| `Product State` / `Counted Detection` | User-facing product projection |
| Confidence and reason fields | Existing review surface rendered from typed projection decisions |
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
