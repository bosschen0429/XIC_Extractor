# Pt Pattern Conflict Graph And Event Decision Contract

Doc placement: repo_support_doc
Doc kind: design_contract
Doc lifecycle: active
Repo owner: docs/product/discovery.md
Doc exit rule: Retire after the conflict-graph and decision-state design is
  rejected, or implemented and absorbed into canonical product docs.

Status: `proposed`, `diagnostic_only`

This document defines a pre-event conflict and review contract for Pt Pattern
Discovery. It does not change current code, thresholds, schemas, machine calls,
RAW execution, or product authority.

## Inherited Untargeted Workflow Contract

This document does not define a Pt-specific Discovery/Alignment/Backfill
lifecycle. Pt pattern inherits the shared Untargeted Product Flow in
`docs/product/discovery.md`, together with the identity and authority boundaries
in `docs/product/alignment.md`, `docs/product/backfill.md`, and
`docs/product/family-hypothesis-boundary.md`:

1. Pt Discovery may propose a sample-local hypothesis only from the complete
   base/+1/+2/+4 method evidence required by the Pt provider.
2. Alignment, not Pt Discovery, owns cross-sample group identity.
3. Backfill may revisit a missing sample cell only after Discovery has
   established the hypothesis and Alignment has established the relevant
   cross-sample group context. It cannot create the first Pt Discovery hit or a
   new Pt hypothesis, and it is not counted Discovery detection.
4. Workflow projection and ProductWriter authority remain separate from all
   graph, evidence, machine-disposition, and review states in this document.

An incomplete base/+1/+2 pattern with absent or unsupported +4 therefore cannot
be accepted as Pt Discovery. Whether an aligned missing cell can later be
recovered is a Backfill decision under the existing Backfill evidence and
authority contract; it is not a weaker Pt Discovery mode.
Accordingly, base/+1/+2 core-only same-scan support may be retained as
diagnostic evidence but cannot emit a Discovery preferred candidate.

## Problem And Decision

Current Pt rows are greedy matcher representatives: for each base anchor, the
v1 matcher serializes one compatible base/+1/+2/+4 assignment and prunes other
compatible choices. They are not complete hypothesis enumeration units. They
also lack explicit pairwise conflict, duplicate-control, joint-selection, and
non-counting semantics. Consequently, overlapping rows can look like independent
detections even when they reuse the same observed features, while un-emitted
alternatives remain invisible.

The immediate design unit is therefore a **versioned Pt hypothesis conflict
graph**, not a physical event wrapper:

- the successor contract requires vertices to be complete enumerated Pt
  assignments that may later map into canonical `PeakHypothesis` alternatives;
  this phase does not instantiate that enumeration or mapping;
- an edge means two hypotheses cannot both be selected under one explicit
  conflict rule;
- connected components are computational partitions only;
- a valid selected set may contain zero, one, or multiple pairwise-compatible
  hypotheses from the same connected component.

The long-term product unit remains a sample-local Pt envelope peak event. This
phase does not instantiate or count that event. A selected hypothesis may be
promoted to an event only after a later contract establishes physical-event
identity and the necessary evidence/authority gates. Compound identity remains
unknown without an independent standard, validated RT, library/MS2 evidence, or
another explicit identity anchor.

### Evidence for the problem

- 386 current Fullscan rows are overlapping greedy representatives, not
  detections or a complete alternative universe;
- 301/386 rows claim at least one feature claimed by another hypothesis;
- 336 feature IDs are claimed by multiple hypotheses, with one feature used by
  as many as seven rows;
- the 386-row shared-claim graph has 135 connected components: 50 multi-row
  components, of which 17 are non-cliques; the largest has 36 hypotheses but
  only 137 of 630 possible conflict edges;
- all 32 accepted-set conflict components are ambiguous and produce no current
  preferred hypothesis;
- `shifted_shape_review_only` covers 209/386 rows but has no matching
  current-rule adjudicated oracle.

These observations justify explicit conflict representation. They do not prove
the number of physical events, detector precision/recall, or compound identity.

### Current diagnostic scaffold status

The pairwise graph mechanics now have a package-owned diagnostic scaffold in
`xic_extractor/discovery/pt_pattern_conflict_graph.py`. Focused unit tests cover
fail-closed provenance, deterministic identity, shared-claim edges, singleton
partitions, and non-clique components. A 2026-07-15 no-RAW characterization of
the frozen Fullscan inputs used:

- `pt_pattern_candidates.csv` SHA256
  `9dbafd049110c47776ad645eb1d9b86931c21f66efadf8ca2d13b71e619b86ec`;
- `detected_features.csv` SHA256
  `d373b2667eadaaba50e9320c67804dfd2771a52fd108b6c29fb71d66a7c66943`;
- graph rule `pt_conflict_graph.v1`, producing graph instance
  `pt_graph_e558731d26bfaee25ef0b2aaff0e08e237ccf5a41e4d1916281f6adbd762b31c`.

It reproduced 386 representative-row vertices, 703 pairwise edges, 135
components, 50 multi-row components, 17 multi-row non-cliques, and maximum
component size 36. This closes graph mechanics over the serialized
representative universe only. It does not show that all compatible assignments
were enumerated, and the graph cannot recover alternatives already pruned by
the matcher. A provenance-bound no-RAW
diagnostic replay now emits versioned vertex, edge, component, legacy-comparison,
and summary sidecars under
`output/pt_pattern_productization_v1/two_raw_pt_only_scanaware_closeapex_20260703/conflict_graph_pairwise_v1_20260715/`.
Among the 32 legacy accepted-set conflict components, all 32 remain ambiguous
and none has a preferred candidate. Twenty-eight induced subgraphs are cliques;
four are non-cliques containing 52 pairwise-compatible hypothesis pairs. This
does not show that the legacy replay emitted a wrong winner. It shows that its
connected-component container cannot safely be promoted to a component-wide
`at_most_one` constraint. The current scaffold is not wired into the current
machine call and does not implement assignment enumeration, compatible-set
selection, event instantiation, or product projection.

## Alternatives

| Alternative | Decision |
| --- | --- |
| Keep row artifacts and add labels only | Reject: labels do not expose pairwise conflicts or prevent duplicate interpretation |
| Add a versioned conflict graph/sidecar over existing rows | **Choose as scaffold:** exposes conflicts among emitted representatives, but does not close complete assignment generation or selection |
| Treat each connected component as one event | Reject: transitive connectivity is not mutual exclusivity or physical identity |
| Add `PtEnvelopeEvent` now | Defer until a physical-event grouping invariant and validation oracle exist |

## Domain Units And Ownership

| Unit | Owns | Must not imply |
| --- | --- | --- |
| Normalized feature claim | One observed feature assigned to one Pt position, with source provenance | Compound identity or unique event membership |
| `PeakHypothesis` | One proposed base/+1/+2/+4 assignment, local RT interval, typed evidence, and provenance | That it is selected or counted |
| Conflict edge | One explicit pairwise incompatibility and its reason | That every vertex in the connected component is mutually exclusive |
| Conflict graph/component | Joint optimization and review scope | A physical event, one-winner rule, or event count |
| Evidence facts | Same-scan, mass accuracy, shape, intensity/S/N, boundary, censoring, and provenance | Independent votes or direct product authority |
| Future Pt event | A later validated sample-local physical envelope assertion | Named compound identity or product projection |

Canonical `PeakHypothesis` remains the product-spine owner. This phase must not
add a parallel Pt-specific event/hypothesis abstraction. `PtPatternCandidate`
is currently a greedy representative-row adapter, not a complete alternative
enumerator. Anchor-local segmentation is boundary evidence only: it
must not be named or treated as a `PeakHypothesis`. An explicit mapping from a
complete Pt assignment into canonical `PeakHypothesis` remains future work.

For that mapping, position roles are fixed and must not be collapsed:

- `base` / 0 is the canonical hypothesis identity anchor;
- `+1` / experimental 195 is the future quantitative channel;
- the anchor-local common interval is a co-elution comparison interval, not an
  integration boundary;
- a future Pt `IntegrationResult` must use the `+1` position's own selected
  peak segment, never the four-position common intersection and never a
  default sum of base/+1/+2/+4.

These are domain roles, not current authority. In this `diagnostic_only` phase,
no Pt `IntegrationResult`, selected area, or quantitative value is emitted.

## Identity And Graph Contract

### Normalized feature key

Every claim key is the tuple:

```text
(sample_stem,
 feature_source_kind,
 feature_source_artifact_sha256,
 source_schema_version,
 feature_id)
```

Empty claims, duplicate hypothesis IDs, unknown source versions, mixed-sample
rows, or missing source hashes make the successor graph `invalid_input`. They
must not be silently skipped or joined by bare `feature_id`.
The current adapter accepts only `pt_pattern_candidates.v1` representative rows and
`detected_features.v1` normalized claims; a new source version requires an
explicit adapter change and tests.

### Edge and selection invariants

1. A hypothesis assigns four distinct normalized claims to `base/+1/+2/+4`.
2. Sharing a normalized claim creates a pairwise hard-conflict edge.
3. Connected components partition graph computation; they do not define events.
4. Any selected set must be pairwise non-conflicting. It may contain multiple
   hypotheses from one non-clique component.
5. Component-wide `at_most_one` is allowed only for an explicitly typed subtype
   whose vertices form a clique or share a validated common exclusive anchor.
6. RT proximity, same-scan support, or a clear anchor-local boundary cannot by
   themselves add a physical-identity or one-winner constraint.
7. Known compound names remain review metadata. `known:<compound>` is not an
   observed identity claim.

### Deterministic identity

The graph records `graph_rule_version`, all source artifact hashes, and source
schema versions. Vertex identity is derived from normalized assignments and
provenance. Edge identity is derived from the sorted vertex pair and conflict
reason. Component-instance identity is a digest of graph-rule version, input
hashes, sorted membership, and sorted edges.

Any input, rule, membership, or edge change creates a new component instance.
Human review is valid only for the exact component-instance ID it reviewed;
otherwise it becomes `stale`.

## Separate State Dimensions

The successor sidecar keeps these fields separate.

| Dimension | Allowed states | Meaning |
| --- | --- | --- |
| Evaluation lifecycle | `not_evaluated`, `evaluated`, `invalid_input`, `stale` | Whether current input/provenance permits a decision |
| Structural solution | `unique_set`, `ambiguous_sets`, `none`, `unavailable` | Whether the graph supports one compatible set, multiple sets, no supported hypothesis, or no valid computation |
| Machine disposition | `accept`, `reject`, `inconclusive`, `not_evaluated`, `invalid` | Machine assessment; not a product projection |
| Review status | `not_required`, `required`, `pending`, `completed`, `stale` | Human workflow state |
| Human disposition | `accept`, `reject`, `unassessable`, or empty | Adjudicated result after completed review |

`review_required` is the derived routing condition
`review_status in {required, pending}`. It is not a human disposition and must
not emit `non_detected` or count as machine `reject` in successor summaries.
`unassessable` is available only after human review completes.

### Transition and provenance rules

- `not_evaluated` may become `evaluated` or `invalid_input`; invalid input has
  structural state `unavailable` and machine disposition `invalid`.
- An `ambiguous_sets` structural result or `inconclusive` machine disposition
  requires human review and cannot auto-promote.
- Human disposition may be written only with reviewer identity, timestamp,
  reviewed component-instance ID, and source hashes.
- Any source/rule/graph change marks the prior review `stale`; stale review is
  not reused or silently copied.
- A human may accept visible Pt-event presence while structural alternatives
  remain ambiguous. This does not select a hypothesis, create an event count,
  or establish compound identity.
- Existing `uncertain` records, including Case 08, remain unchanged until the
  reviewer explicitly closes them as `accept`, `reject`, or `unassessable`.
- Legacy `shifted_shape_review_only -> reject/non_detected` remains only in the
  frozen legacy artifact. A successor adapter maps it to `inconclusive` plus
  required review; it never rewrites history or promotes authority.

## Evidence Semantics

- Same-scan, feature RT, chromatographic shape, and intensity often derive from
  the same RAW signal. Record correlated typed facts, not additive votes.
- Evidence shared by every conflicting alternative has no selection power.
- Anchor-local segmentation owns boundary and local-peak facts; `clear` does not
  prove hypothesis identity.
- Intensity-envelope evidence belongs in `EvidenceVector` with S/N, censoring,
  relative abundance, and interference context, not a dataset-derived hard
  `+4/base` floor.
- Missing or insufficient evidence produces `inconclusive` and review routing,
  not proof of absence.

## Intended Flow And Ownership

```text
existing Pt candidate rows
  -> normalize feature claims and provenance
  -> build versioned pairwise conflict graph
  -> attach typed evidence without recomputing it
  -> characterize compatible hypothesis sets
  -> emit diagnostic structural/machine/review states
  -> optional provenance-bound human disposition
  -> audit only; no event instantiation or product projection
```

| Responsibility | Existing owner to deepen |
| --- | --- |
| Pattern enumeration and assignments | `xic_extractor/discovery/delta_mass_patterns.py` |
| Pairwise shared-claim graph mechanics | `xic_extractor/discovery/pt_pattern_same_scan_evidence.py` or a focused Discovery-domain successor |
| Anchor-local segmentation and boundary facts | `xic_extractor/discovery/pt_pattern_anchor_segmentation.py` and `xic_extractor/peak_detection/chrom_peak_segments.py`; this evidence is not a separate `PeakHypothesis` type |
| Typed evidence and later model selection | `xic_extractor/discovery/`; never a writer or diagnostic CLI |
| CSV/RAW orchestration and rendering | `tools/diagnostics/pt_pattern_*` |

## Validation And Falsification

### No-RAW graph gate

The first loop reads the frozen 386-row artifact and performs no winner or event
selection. Passing requires all three outcomes:

1. **Lossless integrity:** every input hypothesis appears in exactly one graph
   partition; every valid normalized claim is represented; malformed, missing,
   or duplicate claims fail the whole successor graph closed.
2. **Determinism:** the same input hashes, schema versions, and graph-rule version
   produce identical vertices, edges, component IDs, and semantic summaries.
   Run paths and operator report labels are presentation metadata and stay out
   of the semantic summary.
3. **Conflict correctness:** every shared normalized claim creates the expected
   pairwise edge; no non-clique component is treated as one-winner; any emitted
   compatible set contains zero shared claims.

For the current artifact, 386 rows, 135 components, 50 multi-row components,
17 multi-row non-cliques, and maximum component size 36 are characterization
expectations only, not durable product thresholds.

### Pre-registered failure meanings

| Observation | Meaning / action |
| --- | --- |
| Two hypotheses share a claim but land in different partitions | Under-merge; graph implementation invalid |
| Pairwise-compatible hypotheses are suppressed only because they are transitively connected | Over-merge; one-winner component model invalid |
| Same frozen inputs produce different graph/component IDs | Provenance contract invalid |
| Useful event-like signals still require known-list injection | Generator gap remains plausible, but cannot be closed without independent anchors |
| Human review cannot distinguish competing compatible sets | Selection remains ambiguous; do not invent an event winner |

Passing this gate yields only `conflict_graph_ready_for_packet_design`. It may
preserve the row/evidence spine and justify a bounded review surface. It cannot
validate event identity, compound identity, precision, recall, detection count,
or the candidate generator.

### Later event-packet loop

Only after separate RAW approval, sample graph partitions rather than machine
rows: multi-hypothesis conflicts, singleton/non-conflict hypotheses, and
low-assessability or negative controls. Show all relevant alternatives and ask
event presence separately from structural selection. Do not pre-label any case
`unassessable`; that label exists only after completed review. The current
machine-variable 3x3 next-nine manifest remains paused.

## Failure And Rollback

If the successor sidecar is missing, invalid, stale, unsupported, or hash-
mismatched:

- do not run successor selection;
- emit `unavailable/not_countable` with an audit reason;
- preserve current v1 candidate/evidence artifacts byte-for-byte;
- do not fall back to stale human review or legacy calls as successor truth;
- grant no Matrix, Backfill, counted-detection, selected-area, workbook,
  activation, or export authority.

## Non-goals

- No event wrapper, event count, or compound identity in this phase.
- No threshold tuning, ratio gate, or RAW replay without separate approval.
- No current public CSV/workbook migration in this document.
- No reinterpretation of 146 historical accepts as compounds or detections.
- No product projection or externalization authority.
