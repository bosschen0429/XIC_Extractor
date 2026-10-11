# Peak Anchor, Cross-Sample Group, and Hypothesis Boundary

This page defines the durable boundary between per-sample peak anchors, initial
grouping scopes, cross-sample group hypotheses, peak hypotheses,
ObservationRevisit, cell decisions, and product projections. Read this page
when a change touches Discovery peak-anchor grouping, alignment owner
construction, Backfill activation, or any matrix identity sidecar.

## Contract

`InitialGroupingScope` is an internal handoff value from the grouping bootstrap
to regrouping and Backfill planning.

- The scope contains only the m/z and RT bounds of the initial evidence plus
  the exact initial groups.
- The scope has no standalone identity, serialization contract, product
  authority, or public Family meaning.
- Its structure can be replaced when the grouping decision and its consumers
  change.
- A change must preserve physical observations, sample/acquisition
  provenance, explicit uncertainty, and confirmed identity constraints.
- No particular clustering strategy or complete pair graph is an invariant.
- A change to public/legacy `family` vocabulary or to a persisted schema
  requires explicit consumer updates.

### Owners

- `peak_anchor_id` (discovery `feature_family_id`) groups candidates that share
  the same MS1 chromatographic peak within a single sample. `peak_anchor_id` is a
  per-sample trace-identity label. `peak_anchor_id` is not a chemical identity
  claim.
- `CrossSamplePeakGroupHypothesis` owns cross-sample group identity:
  membership, feature inference evidence, identity constraints, review-only
  records, and group delivery metadata. `group_hypothesis_id` is the identity
  key.
- CrossSample grouping also owns the mutually exclusive group disposition and
  every membership/split/merge lineage edge.
- The CrossSample grouping workflow returns one immutable
  `CrossSampleGroupingResult(processing_owners, partition_transition,
  drift_lookup)`.
- The same `CrossSampleGroupingResult` is used before bounded Backfill and
  through finalization. A revisit does not reopen grouping.
- `CrossSamplePartitionTransition` records the complete group partition before
  and after an accepted membership change.
- `PeakHypothesis` owns candidate chromatographic identity: the physical peak
  candidate, selected integration, typed evidence, and selected-hypothesis
  decision semantics.
- Backfill can return one independently traceable observation from a revisit.
  That observation cannot imply group membership or writer authority.

### Decision order

1. Workflow projection owns Target/Untarget row identity/binding and the
   explicit Target/Group cell subject.
2. `ProductionCellDecision` owns the selected value or the typed no-write
   result.
3. The same projection then closes inclusion, counted detection, Review
   Queue/archive routing, and output shape.
4. ProductWriter renders only an authorized finalized result.

Legacy score, confidence, family role, and review tokens can rank, annotate,
or preserve compatibility behavior. These legacy signals must not become
standalone product truth.

## Roles

| Concept | Owns | Must not own |
| --- | --- | --- |
| Discovery `peak_anchor_id` / output `feature_family_id` | Per-sample MS1 peak identity and review grouping | Cross-sample identity, Backfill promotion, selected-peak truth |
| Alignment `public_family_id` (display label) | Stable public row label for output compatibility | Canonical identity proof - use `group_hypothesis_id` instead |
| Internal `InitialGroupingScope` | m/z/RT evidence bounds and exact initial groups for the bootstrap-to-regroup handoff | Standalone identity, persistence, chemical identity, public row identity, same-peak truth, or writer authority |
| `CrossSampleGroupingResult` | One immutable processing-owner-bound grouping result, accepted complete-partition transition, and prepared drift lookup | Regrouping after Backfill, selected values, or writer authority |
| `CrossSamplePeakGroupHypothesis` | Cross-sample owner/group identity, membership, disposition, and lineage via `group_hypothesis_id` | Per-peak integration truth, Backfill, or ProductWriter authority |
| `PeakHypothesis` | Candidate peak identity, evidence context, selected hypothesis semantics | Cross-sample grouping policy or final matrix writing |
| ObservationRevisit | Retained typed receipt for a bounded local revisit result or no-result | Membership, selected value, group origin, or recursive support |
| ProductionCellDecision | Selected value or typed no-write for an explicit Target/Group subject | Row identity, RAW access, grouping, or rendering |
| Product projection / ProductWriter | Pre-cell row binding/subject construction; post-cell inclusion, counted detection, product state, routing, delivery shape, and authorized rendering | Low-level evidence extraction, grouping, Backfill, or value recomputation |

## Migration Rule

The codebase is in a compatibility transition. Discovery outputs still expose
`feature_family_id` for per-sample peak anchors. Alignment outputs still
expose `public_family_id` or legacy `feature_family_id` as cross-sample group
display labels. These legacy labels form an adapter state with these rules:

1. A legacy ID that is a public output contract must be preserved.
   The persisted matrix identity field `source_feature_family_ids` keeps its
   current name and source-lineage meaning. `source_feature_family_ids` is not
   a grouping-scope identity.
2. A successor ID must be added alongside the legacy ID when identity semantics
   become product-relevant.
3. Successor alignment identity decisions use `group_hypothesis_id` after a
   workflow has promoted `group_hypothesis_id` as the decision key. Before that
   promotion, public matrix/projection maps can still key by legacy row labels.
   Those maps must describe that compatibility state.
4. Multiple source peak anchors or cross-sample groups must not collapse into
   one product row. Only an explicit split/consolidation contract and its
   tests together can approve a collapse.
5. Review-only group context must stay out of ProductWriter authority. The
   exception is context that is converted into typed same-peak or
   group-hypothesis evidence.
6. Projection-owned `UntargetRowKey` must be introduced separately from canonical
   group identity. `group_hypothesis_id` itself must not become the public
   Matrix row key. Row identity must not derive from m/z/RT/display order.

## Design Red Lines

- Do not say "the score decides whether the peak is trustworthy." Name the
  evidence facts, selected hypothesis, and workflow projection that own the
  decision.
- Do not use Discovery peak-anchor membership as cross-sample identity proof.
  Cross-sample identity belongs to `CrossSamplePeakGroupHypothesis` and
  `group_hypothesis_id`.
- Do not use cross-sample group membership as same-peak proof. Same-peak support
  needs a typed anchor, selected mode, or PeakHypothesis-level reason.
- Do not treat a Family as a same-peak or chemical-identity claim. Two
  resolved peaks in one sample can belong to different cross-sample groups in
  the same Family. Either group can be absent in another sample.
- Do not treat an accepted ObservationRevisit as implicit membership or
  selected value. CrossSample owns membership. ProductionCellDecision owns the
  selected value.
- Do not let `alignment_review.tsv` display tokens promote rows. Positive
  product support needs provenance-valid sidecars or explicit authority
  records.
- Do not treat `family_projection` rows as canonical identity proof. A
  `family_projection` row stays an unresolved projection row until one of
  these events happens:
  - explicit PeakHypothesis assignments replace the row;
  - an approved product contract declares the row out of scope.
- A helper that can emit or include `family_projection` rows must exclude them
  by default. Any inclusion path needs an explicit diagnostic or compatibility
  opt-in. Any inclusion path must keep canonical identity readiness
  fail-closed.

## Codebase Smells To Audit

These patterns are not automatically bugs. Each pattern requires close review
before refactor or promotion:

- A peak-anchor assignment function writes `evidence_score`, `evidence_tier`,
  confidence, or product-like status.
- A cross-sample alignment path keys product behavior only by
  `feature_family_id` or `public_family_id` when `group_hypothesis_id` or
  `peak_hypothesis_id` is available.
- A diagnostic sidecar exposes supportive labels without product-authority
  provenance.
- A new helper extracts shared code by moving discovery/alignment policy into
  `peak_detection/` instead of staying trace-level and role-neutral.

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
- [Targeted candidate selection](targeted-selection.md#candidate-selection)
- [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md)
- [Architecture contract](../architecture-contract.md)
