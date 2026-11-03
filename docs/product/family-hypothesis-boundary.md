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

- Discovery `feature_id` groups candidates that share the same MS1
  chromatographic peak within a single sample. It is a per-sample
  trace-identity label, not a chemical identity claim.
- `CrossSamplePeakGroupHypothesis` owns cross-sample group identity:
  membership, feature inference evidence, identity constraints, review-only
  records, and group delivery metadata. In process the group object is the
  identity; written reports label each published group with `feature_id`.
- CrossSample grouping also owns the mutually exclusive group disposition and
  every membership/split/merge lineage edge.
- The CrossSample grouping workflow returns one immutable
  `CrossSampleGroupingResult(processing_owners, partition_transition,
  drift_lookup)`.
- Initial members remain fixed during bounded Backfill. CrossSample alone can
  admit unique missing-sample SUPPORT and return the successor result consumed
  by finalization; a revisit does not reopen ordinary grouping.
- `CrossSamplePartitionTransition` records the complete group partition before
  and after an accepted membership change.
- `PeakHypothesis` owns candidate chromatographic identity: the physical peak
  candidate, selected integration, typed evidence, and selected-hypothesis
  decision semantics.
- Backfill can return one independently traceable observation from a revisit.
  That observation cannot imply group membership.

### Group identity and disposition

- A group is identified by its identity-bearing members. Each member combines
  the sample key with an owner-backed `PeakHypothesis` and the physical
  observations it binds. Equivalent hypothesis wrappers over the same physical
  observation resolve to the same member subject. `group_hypothesis_id` joins
  the member subjects in sample order for in-process lookup and is not written.
- `feature_id` (`F000001`) numbers the published groups of one report in
  partition order and raw measured member order. It, row order, sample stem,
  m/z/RT display coordinates, and area are report/display facts, not identity
  bases.
- A group contains at most one identity-bearing member per canonical sample.
  Same-sample distinguishable peaks remain separate; unresolved ambiguity does
  not choose an arbitrary winner.
- Any accepted initial membership change or split/merge appears in the one
  complete before/after `CrossSamplePartitionTransition`, creates the resulting
  group and immutable lineage, and closes before bounded Backfill. A new
  processing request creates new groups; this contract does not infer
  cross-request continuity.
- `CrossSampleGroupDisposition` is exactly one of `established`, `provisional`,
  `unresolved`, or `rejected`. Established requires accepted identity-bearing
  members from at least two distinct canonical samples under the applicable
  inference route; a singleton is always provisional. Low support alone is not rejection.
  Disposition and output inclusion are separate.

Bounded CID-NL admission can add a missing sample's unique SUPPORT observation
without moving initial members. CrossSample records the successor identity and
lineage; Backfill itself does not mint membership. See
[membership admission](backfill.md#membership-admission).

### Decision order

1. Target projection constructs fixed-row bindings and Target cell subjects
   before cell decision. Untarget cell decision consumes completed Feature × Sample
   evidence before any output row/cell identity exists.
2. Cell decision selects the value or typed no-write result and integration.
3. Projection consumes those answers, assigns Untarget output identities and
   `ProductionCellDecision` wrappers, and closes inclusion, counts and routing.
4. ProductWriter renders only the finalized result.

Legacy score, confidence, family role, and review tokens can rank, annotate,
or preserve compatibility behavior. These legacy signals must not become
standalone product truth.

## Roles

| Concept | Owns | Must not own |
| --- | --- | --- |
| Discovery `feature_id` (per-sample peak anchor) | Per-sample MS1 peak identity and review grouping | Cross-sample identity, Backfill promotion, selected-peak truth |
| Alignment `feature_id` (`F000001`) | Report-scoped label joining one published group across Review, Cells, Status, Seed audit, and SQLite | Cross-report identity, same-peak proof, or a Matrix column |
| Internal `InitialGroupingScope` | m/z/RT evidence bounds and exact initial groups for the bootstrap-to-regroup handoff | Standalone identity, persistence, chemical identity, public row identity, or same-peak truth |
| `CrossSampleGroupingResult` | One immutable processing-owner-bound grouping result, accepted complete-partition transition, and prepared drift lookup | Regrouping after Backfill or selected values |
| `CrossSamplePeakGroupHypothesis` | Cross-sample owner/group identity, membership, disposition, and lineage through its member subjects | Per-peak integration truth or Backfill |
| `PeakHypothesis` | Candidate peak identity, evidence context, selected hypothesis semantics | Cross-sample grouping policy or final matrix writing |
| ObservationRevisit | Retained typed receipt for a bounded local revisit result or no-result | Membership, selected value, group origin, or recursive support |
| Cell decision / ProductionCellDecision | Selects value or typed no-write from a Target subject or completed group/sample evidence; packages the answer for output | RAW access, grouping, or rendering; output identity cannot be a prerequisite for Untarget value selection |
| Product projection / ProductWriter | Target fixed-row binding; Untarget row/cell identity after completed Feature × Sample answers; inclusion, counted detection, product state, routing, delivery shape, and authorized rendering | Low-level evidence extraction, grouping, Backfill, or value recomputation |

## Output Labels

Discovery outputs expose `feature_id` for per-sample peak anchors.
Alignment outputs expose one report-scoped `feature_id` per published group;
in-process group keys are not written. These labels follow these rules:

1. Discovery `feature_id` is a public output contract. It is not a
   grouping-scope identity or the Alignment `feature_id`.
2. A new written identifier needs a boundary that the object itself cannot
   cross and no sufficient natural key
   ([Change Locality](../architecture-contract.md#change-locality)).
3. Multiple source peak anchors or cross-sample groups must not collapse into
   one product row. Only an explicit split/consolidation contract and its
   tests together can approve a collapse.
4. Review-only group context must stay out of ProductWriter authority. The
   exception is context that is converted into typed same-peak or
   group-hypothesis evidence.
5. `feature_id` order derives from raw measured member facts, never from m/z/RT
   centers, scores, or display order.

## Design Red Lines

- Do not say "the score decides whether the peak is trustworthy." Name the
  evidence facts, selected hypothesis, and workflow projection that own the
  decision.
- Do not use Discovery peak-anchor membership as cross-sample identity proof.
  Cross-sample identity belongs to `CrossSamplePeakGroupHypothesis` and its
  member subjects.
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

## Codebase Smells To Audit

These patterns are not automatically bugs. Each pattern requires close review
before refactor or promotion:

- A peak-anchor assignment function writes `evidence_score`, `evidence_tier`,
  confidence, or product-like status.
- A cross-sample alignment path keys product behavior only by a display label
  (`feature_id`) when the group or hypothesis object is
  available.
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
