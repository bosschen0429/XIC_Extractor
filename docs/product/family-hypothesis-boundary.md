# Peak Anchor, Cross-Sample Group, and Hypothesis Boundary

This page defines the durable boundary between per-sample peak anchors, wide
Family contexts, cross-sample group hypotheses, peak hypotheses, ModeTrack
proposals, ObservationRepair, cell decisions, and product projections. Use it when a change touches
discovery peak-anchor grouping, alignment owner construction, diagnostic
ModeTrack review, Backfill activation, or any matrix identity sidecar.

## Contract

Current `FamilyContextRef` and `ModeTrackAssignment` values are internal
diagnostic structures. Their useful trajectory capability is the migration
source for a proposal-only `alignment/mode_track/` interface; it must be
deepened rather than replaced. Neither current nor successor terms rename the
public/legacy `family` vocabulary or add public schema by implication.

- `peak_anchor_id` (discovery `feature_family_id`) groups candidates that share
  the same MS1 chromatographic peak within a single sample. It is a per-sample
  trace-identity label, not a chemical identity claim.
- `CrossSamplePeakGroupHypothesis` owns cross-sample group identity:
  membership, owner edge evidence, hard split gates, review-only records, and
  group delivery metadata. Use `group_hypothesis_id` for identity.
- CrossSample also owns the mutually exclusive group disposition and every
  membership/split/merge successor lineage edge. ModeTrack may propose a change
  but cannot apply it or mint the successor.
- Internal `FamilyContextRef` owns one wide m/z/RT observation universe and
  references zero or more existing `group_hypothesis_id` ModeTracks. It is
  owner-minted from canonical context, policy, lineage, and group IDs. Public or
  legacy family labels, `method_subject_id`, display labels, and ordinals are
  not substitutes for this identity.
- `PeakHypothesis` owns candidate chromatographic identity: the physical peak
  candidate, selected integration, typed evidence, and selected-hypothesis
  decision semantics.
- `ModeTrackAssignment` currently records for debug or batch review whether one
  sample-local `PeakHypothesis` is
  uniquely assigned to one existing ModeTrack, ambiguous across tracks,
  outside the model, absent, or technically unassessable. The
  identity-settlement/identity-trajectory boundary mints this internal,
  immutable diagnostic fact.
  The successor ModeTrack result remains proposal-only; it does not mint a
  second group owner or select a product value.
- Backfill may return one independently traceable owner-backed repaired
  observation. It cannot imply group membership or writer authority.
- Workflow projection first owns Target/Untarget row identity/binding and the
  explicit Target/Group cell subject. `ProductionCellDecision` then owns the
  selected value or typed no-write result. The same projection afterward closes
  inclusion, counted detection, Review Queue/archive routing, and output shape.
  ProductWriter renders only an authorized finalized result.
- Legacy score, confidence, family role, and review tokens may rank, annotate,
  or preserve compatibility behavior. They must not become standalone product
  truth.

## Roles

| Concept | Owns | Must not own |
| --- | --- | --- |
| Discovery `peak_anchor_id` / output `feature_family_id` | Per-sample MS1 peak identity and review grouping | Cross-sample identity, Backfill promotion, selected-peak truth |
| Alignment `public_family_id` (display label) | Stable public row label for output compatibility | Canonical identity proof - use `group_hypothesis_id` instead |
| Internal `FamilyContextRef` | Wide chromatographic observation context and its zero-or-more existing ModeTracks | Chemical identity, public row identity, same-peak truth, or writer authority |
| `CrossSamplePeakGroupHypothesis` | Cross-sample owner/group identity, membership, disposition, and lineage via `group_hypothesis_id` | Per-peak integration truth, ModeTrack fitting, repair, or ProductWriter authority |
| `PeakHypothesis` | Candidate peak identity, evidence context, selected hypothesis semantics | Cross-sample grouping policy or final matrix writing |
| Current/successor ModeTrack result | Typed sample/acquisition × existing ModeTrack correspondence, exclusions, assessability, and proposal lineage | Applying membership, minting groups, evidence/area transfer, Backfill/Matrix values, or chemical identity |
| ObservationRepair | Bounded local repaired observation or typed no-repair result | Membership, selected value, first hit, or recursive support |
| ProductionCellDecision | Selected value or typed no-write for an explicit Target/Group subject | Row identity, RAW access, grouping, or rendering |
| Product projection / ProductWriter | Pre-cell row binding/subject construction; post-cell inclusion, counted detection, product state, routing, delivery shape, and authorized rendering | Low-level evidence extraction, grouping, repair, or value recomputation |

## Migration Rule

The codebase is in a compatibility transition. Discovery outputs still expose
`feature_family_id` for per-sample peak anchors, and alignment outputs still
expose `public_family_id` or legacy `feature_family_id` as cross-sample group
display labels. Treat this as an adapter state:

1. Preserve legacy IDs when they are public output contracts.
2. Add successor IDs alongside legacy IDs when identity semantics become
   product-relevant.
3. Use `group_hypothesis_id` for successor alignment identity decisions when a
   workflow has promoted it as the decision key. Until then, public
   matrix/projection maps may still key by legacy row labels and must describe
   that compatibility state.
4. Do not collapse multiple source peak anchors or cross-sample groups into one
   product row unless an explicit split/consolidation contract and tests approve
   it.
5. Keep review-only group context out of ProductWriter authority unless it is
   converted into typed same-peak or group-hypothesis evidence.
6. Introduce projection-owned `UntargetRowKey` separately from canonical group
   identity. Do not make `group_hypothesis_id` itself the public Matrix row key
   or derive row identity from m/z/RT/display order.

## Design Red Lines

- Do not say "the score decides whether the peak is trustworthy." Say which
  evidence facts, selected hypothesis, and workflow projection own the decision.
- Do not use discovery peak-anchor membership as cross-sample identity proof.
  Cross-sample identity belongs to `CrossSamplePeakGroupHypothesis` and
  `group_hypothesis_id`.
- Do not use cross-sample group membership as same-peak proof. Same-peak support
  needs a typed anchor, selected mode, or PeakHypothesis-level reason.
- When a `PeakHypothesis` is already an exact owner of one ModeTrack in the
  current `FamilyContextRef`, preserve that identity-settlement identity as observed occupancy;
  do not reclassify the same owner from aggregate group-center geometry. This
  grants no SUPPORT vote, trajectory reinforcement, or product authority.
- Do not treat a Family as a same-peak or chemical-identity claim. Two resolved
  peaks in one sample may occupy different ModeTracks in the same Family, and
  either track may be absent in another sample.
- Do not train a ModeTrack from MS1-only, Backfill, inferred, deconvolved,
  manual, shared-set, or other non-training observations. Only an existing
  owner-proven direct-SUPPORT `PeakHypothesis` may seed or reinforce one track,
  and reinforcement follows unique assignment only. ModeTrack must retain and
  revalidate the exact processing request/attempt, physical peak observation,
  candidate, and direct-SUPPORT assessment identities. It must not retain or
  depend on `CellObservationRef`; matching only the `PeakHypothesis` is
  insufficient provenance.
- Do not let a consumer refit ModeTracks or reinterpret an ambiguous,
  out-of-model, absent, or technically unassessable state as unique. Shadow
  assignments carry no value or writer authority.
- Do not treat an accepted ObservationRepair as implicit membership or selected
  value. Those decisions remain with CrossSample and ProductionCellDecision.
- Do not let `alignment_review.tsv` display tokens promote rows. Positive
  product support needs provenance-valid sidecars or explicit authority records.
- Do not treat `family_projection` rows as canonical identity proof. They are
  unresolved projection rows until replaced by explicit PeakHypothesis
  assignments or an approved product contract says they are out of scope.
- Helpers that can emit or include `family_projection` rows must default to
  excluding them. Any inclusion path needs an explicit diagnostic or compatibility
  opt-in and must keep canonical identity readiness fail-closed.

## Codebase Smells To Audit

These patterns are not automatically bugs, but they require close review before
refactor or promotion:

- A peak-anchor assignment function writes `evidence_score`, `evidence_tier`,
  confidence, or product-like status.
- A cross-sample alignment path keys product behavior only by
  `feature_family_id` or `public_family_id` when `group_hypothesis_id` or
  `peak_hypothesis_id` is available.
- A diagnostic sidecar exposes supportive labels without product-authority
  provenance.
- A new helper extracts shared code by moving discovery/alignment policy into
  `peak_detection/` instead of keeping the helper trace-level and role-neutral.

## Verification

Before changing peak-anchor, owner, cross-sample group, group-hypothesis, or
PeakHypothesis behavior, require the relevant subset of:

- focused tests for row identity, sidecar columns, and deterministic duplicate
  policy;
- expected-diff approval if selected peak, selected area, counted detection,
  matrix values, or product row identity can change;
- artifact provenance checks for Backfill evidence used as product support;
- RAW-tier evidence label matching the claim.

## See Also

- [Evidence spine](evidence-spine.md)
- [Alignment](alignment.md)
- [Backfill](backfill.md)
- [Peak model selection](peak-model-selection.md)
- [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md)
- [Architecture contract](../architecture-contract.md)
