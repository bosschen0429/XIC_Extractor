# Targeted Selection

Targeted selection is the Targeted workflow projection for known targets. It
constrains the shared scientific lifecycle with an exact target-list snapshot,
binds finalized groups to fixed target rows, applies Targeted inclusion/counting
policy, and renders the existing Review Queue and delivery. It does not require
a second capture, CrossSample, ModeTrack, Backfill, or selected-value
implementation.

## Contract

- Product targeted output is a workflow-owned projection from evidence and selection decisions. Shared evidence providers inform but do not choose targeted product state.
- `TargetKey` is the stable logical row identity. `TargetDefinition` is the
  exact immutable scientific revision, and `TargetListSnapshot` is the exact
  ordered request input. A same-label definition change preserves the key but
  changes definition/snapshot identity; duplicate labels fail closed.
- `TargetSearchScope` narrows the allowed target universe but may still ask the
  shared Discovery/capture method to inspect broad evidence relationships such
  as dR, R, or MeR. Broad candidate discovery is not Untargeted-only.
- `TargetRowBinding` resolves zero, one, or many qualified finalized groups for
  each fixed Target row. Zero keeps the row with a typed state; one unique group
  binds; many remain unresolved with stable references in the existing Review
  Queue. It does not rescan RAW, rank by area, change integration, or choose an
  arbitrary winner.
- A Target row may bind a unique established or provisional group. An
  owner-backed direct local observation may produce a Target cell value even
  when the group is provisional or the observation is not a group member.
  Other samples cannot be filled from singleton or RT-prior evidence alone;
  they require direct local evidence or an accepted independent
  `ObservationRepair`.
- Targeted selection first owns `TargetRowBinding` and constructs
  `TargetCellSubject`. `ProductionCellDecision` then owns the selected numeric
  value or typed no-write result. The same Targeted owner afterward closes
  inclusion, counted-detection policy, reason rendering, and Review Queue
  routing; it must not recompute the value.
- Region, boundary, and selected-envelope logic are candidate and model-selection surfaces. They require expected-diff review before changing selected peak, area, confidence, reason, product state, counted detection, workbook, or matrix values.
- Selected-hypothesis public behavior must preserve compatibility projections unless an explicit schema/versioning plan changes downstream contracts.
- The bounded headless `5-hmdC + 5-medC` contract is the only MS1
  shape-identity rescue scope with accepted `detected_flagged` expected-diff
  evidence. It grants no authority to broader targets, GUI wiring, or default
  broad NL_FAIL rescue; those require a new target-family evidence gate.
  Consult the status index for current lane readiness.
- Target-pair RT auto-reselection and calibration artifacts are activation-gated selection aids, not default authority unless the status index, authority manifest, promotion contract, and focused tests say so.
- Targeted expected-diff approvals must reference runtime product
  `PeakHypothesis` candidate IDs. Overlay-only or audit-table candidate IDs can
  explain support, but they are not valid product-switch inputs.
- Product action or verdict tables are review/reason surfaces; they must not hide the evidence chain or grant writer authority.

## Retained Validation Anchors

- The 2026-06-04 `BenignfatBC1055_DNA / 8-oxodG` closeout is a row-specific
  expected-diff activation, not a general target-pair RT auto-reselection rule.
  It changed one targeted product row after the approved successor existed in
  runtime product hypotheses and had role-aware RT, paired-area-ratio, MS1, and
  trace support.
- The visible product decision surface for that slice is `Product State`,
  `Counted Detection`, `Review State`, and projection-backed `Reason`. Legacy
  `Confidence`, score, and cap evidence remain technical audit material while
  this projection contract is active.
- The observed 8RAW count change for that slice was `8-oxodG: 3/8 -> 4/8`,
  with all other target labels unchanged. Treat it as a validation anchor for
  the activation gate, not as broad target-pair policy.

## Surfaces

| Surface | Role |
| --- | --- |
| Targeted CSV/workbook fields | Public output for target decisions |
| `Product State` / `Counted Detection` | User-facing product projection |
| Confidence and reason strings | Reviewable decision explanation |
| Region and selected-envelope candidates | Boundary and model-selection evidence |
| Target-pair RT calibration artifacts | Activation-gated selection support |
| Expected-diff packets | Required gate for output-changing behavior |

## Boundaries

- **Owns:** `TargetSearchScope`, fixed `TargetKey` row projection, pre-cell
  `TargetRowBinding`/`TargetCellSubject`, post-cell inclusion,
  confidence/reason/product-state/counting policy, existing Review Queue
  routing, MS1 rescue policy scope, and target-pair RT rules.
- **Compatibility state:** current Targeted projection code still participates
  in selected peak/area assembly. Structural migration must preserve those
  outputs while moving sole selected-value authority to
  `ProductionCellDecision`; any actual value change is a separate promotion.
- **Does not own:** Backfill/Discovery writer scope, specific target's current selected peak, full review-action apply behavior, or maturity tier decisions.
- Targeted authority does not extend to untargeted pipelines, and untargeted authority does not extend back.

## Verification

- Focused tests for selected peak, area, confidence, reason, product state, and counted detection.
- Expected-diff packet for workbook/CSV or matrix-impacting changes.
- Schema/versioning review when output fields or compatibility projection change.
- RAW-tier evidence label matching the product claim.
- Structured productization-owner update when maturity, lane, or authority
  changes: status index for lane state, authority manifest for writer scope,
  and the promotion contract for readiness evidence.
- The bounded CID-NL `targeted_output_v1` delivery claims and current test
  dispositions are indexed in the
  [public-output claim catalog](../superpowers/validation/cid_nl_public_output_claim_catalog_v1.tsv).
  The catalog points to these owners; it does not grant selected-value,
  counted-detection, ProductWriter, or Matrix authority.

## Pitfalls

- Treating diagnostic boundary proposals as final selected boundaries.
- Promoting target-pair RT support without explicit activation.
- Letting selected-hypothesis refactors silently change public reason or counted-detection behavior.
- Reusing Backfill or Discovery authority for targeted selection.
- Treating configured Target identity as proof of a local observation or group
  membership.
- Creating a second Target-only capture, trajectory, repair, or cell-decision
  implementation because the output rows differ.

## See Also

- [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md) | [Evidence spine](evidence-spine.md)
- [Quantitation context](quantitation-context.md) | [Review roundtrip](review-roundtrip.md)
- [Peak model selection](peak-model-selection.md)
- [Decision policy](decision-policy.md)
