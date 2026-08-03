# Shared Processing Kernel

This page defines the minimum workflow-neutral processing contract for Issue
#122. It is an architecture decision for the CID-NL / NL116 tracer, not an
implementation plan and not permission to create a new `core/` package.

Status: `diagnostic_only`. This contract grants no selected-value,
counted-detection, Matrix, Backfill-write, workbook, activation, or export
authority.

## Problem And Success

The current runtime has reusable shared facts but two partially independent
scientific paths. Targeted sample-local hypotheses and cross-sample support do
not produce the same group/cell contracts as Untargeted Alignment and
Backfill. This allows equivalent evidence to acquire different meanings by
entry point and makes a dual-policy tracer impossible to verify through one
scientific seam.

Leaving this unresolved means each new evidence provider or missing-cell rule
can require two implementations, two state vocabularies, and two independent
authority audits.

This contract succeeds when:

1. a no-RAW CID-NL / NL116 fixture produces one set of sample-local
   hypotheses, group membership decisions, and cell observations consumed by
   both projections;
2. Targeted hard-exact/expected-diff output remains unchanged and Untargeted
   Matrix authority remains unchanged; and
3. Targeted and Untargeted adapters contain constraints and projection policy,
   but no duplicate peak-integration, group-membership, or Backfill-observation
   implementation.

## Product Model

XIC has one scientific lifecycle:

`Discovery → PeakHypothesis → Alignment → evidence-based Backfill → workflow projection`

- Untargeted is the general method and the primary architectural path.
- Targeted narrows the candidate universe with known target, m/z, RT, role,
  pairing, and assay evidence. It does not skip peak membership or cross-sample
  evidence merely because the expected identity is configured.
- Targeted and Untargeted keep separate application policy, GUI flow, output
  schemas, and writer authority.

The kernel owns scientific observations and hypotheses. It does not own the
decision to count a Targeted result or write an Untargeted Matrix cell.

## Claims And Evidence

| Claim | Evidence | Confidence | Revisit when |
| --- | --- | --- | --- |
| A real shared trace seam already exists | `peak_detection.traces.TraceGroup` has Targeted and Untargeted adapters | High | Either workflow requires incompatible trace invariants |
| The current hypothesis builder is not workflow-neutral | `build_peak_hypotheses` hard-codes Targeted analysis mode and Targeted evidence conversion | High | A current Untargeted caller is found that produces equivalent hypotheses |
| `AlignedCell` is too broad to be the kernel cell interface | The dataclass combines observation, gap-fill, identity, evidence-sidecar, consolidation, and projection fields | High | A smaller existing cell type is found that already owns these invariants |
| One observation/group seam can support both projections | Existing shared evidence, integration, and group-hypothesis types cover much of the path, but no dual-projection tracer exists yet | Medium | The tracer proves workflow semantics are incompatible at the proposed seam |

The final claim is a design hypothesis. Failure of the bounded tracer must
narrow or redraw this contract rather than force parity through extra flags.

## Minimum Kernel Interface

The first implementation should deepen existing modules and types instead of
adding a parallel framework.

| Kernel concept | Minimum responsibility | Current starting point |
| --- | --- | --- |
| `TraceGroup` | One sample-local trace context and its source/provenance; carries workflow context without deciding product behavior | `peak_detection.traces` already supports Targeted and Untargeted adapters |
| `PeakHypothesis` | Candidate chromatographic identity, selected integration, typed evidence, and audit | `peak_detection.hypotheses`; current builder is still Targeted-shaped |
| `PeakHypothesisSelectionDecision` | Deterministic sample-local hypothesis selection/externalization | `peak_detection.selection_decision` |
| `CrossSamplePeakGroupHypothesis` | Cross-sample membership and canonical group-hypothesis identity | `alignment.cross_sample_peak_groups` |
| `CellObservationRef` | Sample × existing-group closure over orthogonal technical, method-result, identity, human-review, and authority axes | Bounded CID-NL M1 owner exists under `alignment.cell_observations`; production callers remain unmigrated |
| `BackfillRequest` / `BackfillResult` | Revisit a missing group cell using explicit group/hypothesis context; return observation facts and audit, never a writer decision | Current request plan and owner-Backfill result are Untargeted-specific starting points |

`PeakHypothesis` kernel identity is the hypothesis ID, trace-group reference,
integration, evidence, and audit. `target_label`, analyte/ISTD role, pairing, and
known-RT policy are Targeted context. Existing fields may remain during
compatibility migration, but they must not become requirements for Untargeted
hypotheses.

`AlignedCell` is a compatibility/product assembly model, not the future kernel
cell type. It currently combines observation, gap-fill provenance, group
identity, evidence sidecars, product-selection blockers, consolidation state,
and Matrix projection. The successor seam must not copy that full interface.

### Preconditions, Postconditions, And Invariants

- A `TraceGroup` contains traces from one sample and records the context used
  to query them. Workflow constraints may annotate the request but cannot
  become observed evidence.
- A `PeakHypothesis` references exactly one `TraceGroup`. Its integration
  bounds must lie within that trace context; its evidence and audit must not
  contain counted-detection or Matrix-write decisions.
- Hypothesis comparison uses one kernel implementation. Workflow adapters may
  supply explicit constraint/tie-break inputs, but every input and its effect
  must be recorded in the selection audit. Adapters must not independently
  rerun selection or mutate the selected integration afterward.
- A `CrossSamplePeakGroupHypothesis` contains explicit sample-local hypothesis
  membership. A configured Targeted identity may propose/constrain the group;
  it cannot create positive membership without a selected hypothesis or a
  provenance-valid Backfill observation.
- A `CellObservationRef` represents one required sample × already-existing
  group cell and binds both the group label and its complete canonical
  hypothesis membership. Technical availability and CID-NL method result are
  separate axes. SUPPORT requires one owner-backed physical observation and
  hypothesis; HOLD may retain one observation without fabricating support;
  REJECT and technical states need no hypothesis. A human disposition must
  cite the exact current method-assessment artifact. None of these axes grants
  a deliverable numeric value.
- A `BackfillRequest` requires an established group/target context and an
  eligible non-observed sample cell. A `BackfillResult` returns one
  `CellObservation` plus audit provenance and never writes a product surface.

## Orthogonal Assessment And Cell Axes

Keep each claim on its owner axis instead of multiplying or collapsing
user-visible intermediate labels.

| Axis | M1 values | Meaning |
| --- | --- | --- |
| technical/evidence availability | `evaluated`, `unsupported`, `failed`, `not_evaluated` | Whether a supported acquisition/search completed |
| CID-NL method result | `SUPPORT`, `HOLD`, `REJECT`, or `None` | What this completed method attempt claims; non-evaluated technical states require `None` |
| identity/model result | existing/provisional `group_hypothesis_id`, canonical `group_owner_ids`, exact CID-NL method subject, optional Targeted compatibility IDs, and the SUPPORT assessment closure that formed the group | Which exact method-subject-bound, already-established group membership is being evaluated; no cross-run stability claim |
| human review | no disposition or an owner-resolved human review ref citing the current assessment artifact | Human decision provenance over this exact assessment; machine logic cannot author final `unassessable` |
| product authority | always `none` in M1 | Matrix, count, fill, write, export, and activation remain outside this contract |

Internal ambiguity and set-level-only scan relations produce HOLD, not a public
fifth method result. Different scans may retain individual and set-level
relations in one assessment; at least one strict unique relation still makes
the method result SUPPORT. A completed search with qualifying CID-NL evidence
but no usable physical MS1 peak produces method-specific REJECT; it does not
assert chemical absence. Non-qualifying or missing positive evidence alone
proves neither a completed search nor a negative result. Technical failure,
HOLD, and internal ambiguity must never serialize as REJECT, zero, absence, or
human `unassessable`.

The cell is created only after another sample's SUPPORT has established an
existing/provisional group. The cell consumes the exact owner-validated group
context, including target identity and every SUPPORT assessment/hypothesis that
formed the group; equal-looking or cross-target groups fail closed. HOLD and
REJECT assessments may persist without a
cell when no group exists. Backfill eligibility and post-Backfill product
projection remain #162 responsibilities.

## Lifecycle Operations

### Discovery

Discovery always exists. Untargeted searches a broad configured evidence
space. Targeted supplies constraints that narrow the same candidate-generation
operation. Configured target identity is a prior, not proof that a
sample-local peak exists.

### Alignment

Alignment always establishes or evaluates cross-sample correspondence.
Untargeted must discover group identity. Targeted may use a configured target
identity as the initial group constraint, but it must still record whether each
sample-local hypothesis supports, conflicts with, ambiguously matches, or
cannot assess that group.

A Targeted Alignment operation can therefore be computationally trivial in a
clean case; it is not semantically absent.

### Backfill

Backfill is conditional. It may run only when:

1. Discovery has produced at least one sample-local hypothesis for the target
   or group;
2. Alignment has established the relevant cross-sample group context; and
3. a sample cell remains missing or otherwise eligible for explicit revisit.

Backfill cannot create the first Discovery hit or a new group identity. It
returns a `CellObservation` plus evidence/audit provenance. Targeted or
Untargeted projection separately decides whether the result may count or write.

### Workflow Projection

Projection is outside the kernel.

- Targeted consumes selected hypotheses/cell observations plus Targeted priors
  and produces `TargetedProductProjection` and its CSV/workbook delivery.
- Untargeted consumes group hypotheses/cell observations plus Matrix authority
  and produces the feature-by-sample analytical handoff.

One projection must not be used as the other projection's oracle.

## Policy Boundary

| Concern | Shared kernel | Targeted application | Untargeted application |
| --- | --- | --- | --- |
| Trace, candidate interval, integration, shape and scan evidence | Owns facts | Supplies constrained queries | Supplies broad queries |
| Candidate universe | Executes configured provider request | Known target/m/z/RT/role/pairing constraints | Broad Discovery-provider configuration |
| Peak selection | Owns one hypothesis-comparison implementation and audit | Supplies explicit known-RT/role/pairing constraint inputs; role-aware count policy remains after selection | Supplies explicit provider/group constraint inputs; Matrix policy remains after selection |
| Cross-sample identity | Owns `CrossSamplePeakGroupHypothesis` mechanics | Configured identity constrains membership | Group identity is inferred from evidence |
| Backfill observation | Owns request/result facts and provenance | Chooses Targeted eligibility under Targeted policy | Chooses group-cell eligibility under Untargeted policy |
| Counted detection | Does not own | `TargetedProductProjection` | Does not inherit Targeted count policy |
| Matrix presence/value | Does not own | Targeted delivery only | Untargeted projection and ProductWriter authority |
| Output schema/writer | Does not own | Targeted CSV/workbook | Untargeted Matrix/review/handoff |

Shared evidence may produce support, review, conflict, exclusion, or ambiguity
semantics for selection. Those semantics are not themselves counted-detection
or Matrix-write authority.

## Module And Seam Decisions

- Deepen `peak_detection` as the sample-local trace → hypothesis → selection
  module. Remove Targeted assumptions from its external interface
  incrementally; keep Targeted evidence conversion in an adapter.
- Deepen `alignment` as the cross-sample group and cell-observation module.
  Reuse its group-hypothesis mechanics, but do not promote `AlignedCell` into
  the kernel interface.
- Keep `extraction` as Targeted application orchestration and policy. It adapts
  Targeted method constraints into kernel requests and consumes kernel results.
- Keep `discovery` as evidence-provider candidate generation. Provider-specific
  seed facts enter hypotheses through adapters.
- Keep Targeted and Untargeted writers as separate projection modules.
- Do not add a generic `utils/`, an all-purpose `core/`, or storage-first
  models. A new seam requires at least the Targeted and Untargeted adapters.

## Alternatives And Trade-Offs

| Option | Decision | Reason |
| --- | --- | --- |
| Keep separate Targeted and Untargeted scientific paths and normalize only their outputs | Rejected | Preserves duplicate evidence/state meaning and cannot prove one scientific decision fed both projections |
| Create a new `processing_core/` package and migrate both workflows into it first | Rejected | Adds a hypothetical framework before the shared interface is proven and duplicates existing `peak_detection` / `alignment` owners |
| Share only dataclasses while leaving behavior in both callers | Rejected | Produces a shallow module; policy and failure semantics would still diverge |
| Deepen existing `peak_detection` and `alignment` seams through one bounded dual-projection tracer | Selected | Smallest path that can prove shared behavior while preserving current public outputs and rollback |

The selected option improves locality and auditability but temporarily adds
adapters alongside legacy behavior. That duplication is acceptable only during
the bounded tracer and must have an explicit retirement or kill decision.

## Current Gaps

The first no-write scientific-reference seam is executable:
`peak_detection.cid_nl_evidence` owns one direct measured CID-NL observation,
and the Targeted `MS2ProductEvidence` plus Untargeted direct `DiscoverySeed`
adapters both validate and reference that same immutable artifact. The focused
fixture proves exact content, owner/sample/acquisition-source/request/attempt
binding, request-pinned Targeted target/scan binding, finite direct Discovery
scan-precursor binding, exact-target precursor tolerance, owner-level numeric
canonicalization of present `IntegrationResult` values without changing null
semantics, integration-bounds scan-to-peak matching, shared upstream closure,
review supersession, zero acquisition-source reopen, and zero product write.
Inferred `product_plus_neutral_loss` Discovery evidence remains
outside this first slice and fails closed.

For the lab's current DDA/ddMS2 Untargeted method, `peak_detection` also owns a
bounded `DdaCidNlPeakSupportRef`: one exact DDA scan and strict direct CID-NL
artifact must resolve to exactly one owner-backed physical MS1 observation in
the exact supplied candidate universe. Reorder is neutral; substitution,
mutation, missing, or ambiguous matches fail closed. This is not a DIA model,
candidate generator, or selection decision.

The same owner also exposes a bounded `CidNlChromatographicSupportRef`. It
retains every exact strict scan relation but canonicalizes scan order and fixes
`chromatographic_support_event_count=1` for one physical peak. Duplicate scans
and mixed peak, candidate-universe, policy, sample, request, or attempt inputs
fail closed. This prevents repeated DDA triggers from becoming repeated
hypothesis support without creating another `PeakHypothesis` type.

This is not the complete kernel tracer. The remaining gaps are:

1. Legacy `build_peak_hypotheses` still hard-codes
   `analysis_mode="targeted"` and converts evidence through
   `common_evidence_from_targeted_candidate`. A separate composition function
   now deepens the same `PeakHypothesis` model for one workflow-neutral DDA
   CID-NL case, but no production Targeted or Untargeted caller consumes it.
2. `EvidenceVector` contains many Targeted/MS2-specific fields. Shared facts
   exist, but the whole current dataclass is not yet a minimal kernel
   interface.
3. The bounded M6 Untargeted CID-NL orchestration now constructs the shared
   sample-local `PeakHypothesis` path, but it is an opt-in no-RAW
   `production_candidate`; broad Untargeted runtime adoption remains open.
4. A bounded CID-NL M1 adapter now produces an existing provisional
   `CrossSamplePeakGroupHypothesis` and minimal `CellObservationRef`, then fans
   the same refs to two no-write diagnostic views. Targeted and Untargeted
   Targeted production callers have not adopted that interface. M6 consumes it
   only for the bounded Untargeted golden path through the existing Backfill and
   Matrix-version owners.
5. `AlignedCell` mixes observation facts, debug/evidence sidecars, gap-fill
   provenance, identity decisions, and Matrix/product projection.
6. Targeted CLI baseline/support/final orchestration is not shared by GUI or
   direct API. Issue #134 owns that entry-point parity defect; fixing it must
   not create a second permanent kernel.
7. `peak_detection.observations` owns a bounded canonical physical
   MS1-observation reference over an exact raw/selection trace plus a
   signal-validated `IntegrationResult`; a typed but independently assembled
   result is rejected when its apex, heights, area, baseline, or morphology
   contradict the supplied signals.
   The DDA CID-NL owner composes exact scan refs, aggregates repeated in-peak
   scans into one event, and the existing `PeakHypothesis` now consumes that
   typed event through a diagnostic owner-backed branch. The bounded M1
   assessment/group/cell spine now closes this CID-NL reference path without
   Backfill, selection, or product authority. Pt/Delta Mass multi-observation
   roles remain undefined.
8. Legacy `feature_family_id` / `public_family_id` remain compatibility labels.
   `group_hypothesis_id` is the successor cross-sample identity.

These are migration facts, not permission for a repo-wide rewrite.

## CID-NL / NL116 Tracer Contract

The bounded tracer is sufficient only when one set of kernel decisions can be
projected both ways:

1. The same physical MS1 observation is referenced, rather than re-minted, when
   Targeted and Untargeted methods interpret it.
2. Provider generation provenance is explicit: CID-NL may constrain the MS1
   query/candidate universe before peak evaluation, while other methods may
   compose several MS1 observations into one hypothesis.
3. Targeted constraints narrow the candidate/group context without changing
   the underlying physical observation facts.
4. Cross-sample membership and missing-cell state are represented by shared
   group/cell contracts.
5. Backfill returns the same typed observation/provenance contract regardless
   of projection.
6. Targeted output remains protected by its hard-exact/expected-diff contract.
7. Untargeted Matrix authority remains separate and fail-closed.
8. A no-RAW fixture proves both projections consume the same kernel decisions
   without requiring identical output schemas or product policy.

The tracer does not need to migrate every Discovery provider, Alignment
diagnostic, or Backfill authority path.

The scientific-reference fixture, identity-decomposition characterization,
bounded `PeakObservationRef`, DDA CID-NL individual/set relations, complete
method assessment, chromatographic event, owner-backed `PeakHypothesis`,
provisional group, and minimal `CellObservationRef` now satisfy the bounded
direct-evidence → physical-observation → method-assessment → relation →
hypothesis → group → cell chain. The same `peak_detection` owner composes the
existing hypothesis model; lineage and provider-local `DetectedFeature` do not
mint another one.
The Package 1 no-RAW runtime fixture now also reaches that physical-observation
owner from capability-bound batched `XICRequest` values in both Targeted and
Alignment process consumers. The batch receipt retains acquisition coordinates
and call counts while owner-supplied integration facts are revalidated against
the acquired trace; it does not perform hypothesis selection or projection.
Role-bearing multi-observation Pt/Delta Mass composition remains required before
that separate method can claim the same seam, but Pt is deferred and does not
block the current CID-NL tracer. M1 executes only a no-RAW reference fixture;
runtime candidate-universe completeness, production caller adoption, DDA
co-isolation, and valley-time calibration remain unknown or deferred. DIA is
explicitly outside the current lab method. Targeted output and Untargeted
Matrix authority remain unchanged.

## Failure, Validation, And Rollback

Highest-risk failure modes:

| Failure | Detection | Response |
| --- | --- | --- |
| Shared comparison changes the selected Targeted peak or delivered bytes | Targeted hard-exact golden and expected-diff gate | Stop; keep the legacy Targeted path authoritative and narrow/redraw the seam |
| Shared group/cell semantics alter Untargeted Matrix identity or values | Existing Matrix/identity characterization plus zero-unapproved-diff assertion | Stop before projection; legacy Alignment/Matrix remains authoritative |
| Workflow adapter reintroduces hidden scientific selection | Dependency/static scan plus interface-level tracer tests | Move behavior behind the kernel interface or reject the adapter |
| A HOLD, REJECT, or technical-state cell becomes zero or a numeric delivery value | Orthogonal-axis/value invariant tests across both projections | Fail closed; no artifact publication |
| Backfill creates a first hit or group identity | Backfill precondition and provenance tests | Reject the request and retain the original missing/unattempted state |
| technical availability and method result collapse into one token | Assessment/cell truth-table tests across evaluated, unsupported, failed, HOLD, and REJECT paths | Fail the tracer; preserve both axes and block projection |

Implementation must begin as an opt-in, no-write dual run. The legacy path
remains the behavior oracle until the tracer gates pass. A failed tracer is
deleted or narrowed; it must not be rescued by adding workflow-specific flags
to the kernel interface.

This specification does not own production alerts or persistence. #128 owns
attempt/runtime observability, and #123 owns retained evidence/lineage. The
tracer must still emit deterministic audit records sufficient to identify the
input constraint, selected hypothesis, group decision, cell state, and
projection used in a failed comparison.

## Related Decisions

- #123 owns processing identity, delivery-cell lineage, and evidence retention.
- #124 owns acquisition-format and reusable-artifact capability evidence.
- #125 owns the independent blinded oracle and tracer acceptance packet.
- #128 owns runtime session, cancellation, retry, cleanup, and cache lifecycle.
- #134 owns the current Targeted CLI/GUI/direct-API orchestration parity defect.

This contract constrains those tickets but does not resolve them by implication.

## Acceptance Mapping

- One shared lifecycle and its workflow-neutral types are named above.
- Targeted is constrained orchestration/policy over that lifecycle.
- Untargeted remains the general method and primary architectural path.
- Targeted priors and both output projections remain outside the kernel.
- Current implementation gaps are explicit and are not presented as target
  architecture.
- The CID-NL / NL116 tracer has a dual-projection, no-authority acceptance
  contract, an executable no-RAW M1 reference spine, and one opt-in M6
  source-to-`QuantMatrixVersion` fixture gated by the package-owned immutable
  `CheckedProductionAcceptanceManifest` snapshot. File-backed activation cannot
  validate one manifest read and consume another.
- No output, selected-value, counting, Matrix, Backfill-write, activation, or
  export authority is granted.
