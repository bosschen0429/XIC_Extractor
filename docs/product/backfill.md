# Backfill

Backfill is the bounded missing-cell module for an owner-backed Target or
existing cross-sample group. It makes one context-directed revisit and returns
an independently traceable `ObservationRepair` decision containing a
repaired/new observation or typed no-repair result. It does not select a
product value, create membership, or write an output.

The product-stage name is **Backfill（回填）**, following Discovery and Alignment,
and implementation identifiers use the same word. `ObservationRepair` names the
typed decision it returns. Persisted keys and phase values written before this
convergence still read `recovery` (for example `may_attempt_recovery`,
`recovery_candidate_count`, the `"recovery"` acquisition phase); they change
only with the public-surface vocabulary step in #431.

Backfill does not need to fill every cell. Existing direct support makes a
conservative Backfill decision `not_applicable`; insufficient group support or
uncertain local evidence can leave it on `hold`. A method assessment's `HOLD`
is a separate result and does not by itself establish whether a revisit was
needed, attempted, or accepted. Judge Backfill by the cell's eligibility,
recorded reason and observation decision, not by the number of filled cells
or HOLD results alone.

## Contract

- Backfill is an evidence-based revisit of an owner-backed Target or existing
  group cell after Discovery/capture has established the relevant context
  elsewhere. It may backfill the first usable observation for that cell, but it
  cannot originate the Target/group or bootstrap a group without prior
  owner-backed evidence. It is not blind imputation, recursive candidate
  generation, or counted detection.
- One request yields a typed `ObservationRepair` decision containing an
  owner-backed repaired observation or a typed no-repair outcome. It never
  returns a caller-authored area alias or a writer decision.
- Observation repair and group membership are independent. An accepted repaired
  observation does not silently join a group; CrossSample must make a separate
  membership decision. A Target-local value may also remain valid without
  becoming an Untargeted group member.
- Across the independent membership and cell-decision owners, the legal final
  outcomes include member+value, member+null,
  nonmember+Target-local-value, nonmember+MS1-only-Backfill-value, and
  nonmember+null. Backfill itself decides neither axis.
- MS1-only Backfill value: when an Untargeted group has at least
  `K = max(2, N//20)` direct supports among activation-eligible samples, a
  missing eligible sample's group-bounded revisit that finds exactly one MS1
  peak and no CID-NL evidence gets the final cell state
  `ms1_only_backfill_observation`. `ProductionCellDecision` writes that peak's
  area to the Matrix as `rescued` / `accepted_rescue`; it never counts as a
  detection or joins the group. No peak, several peaks, CID-NL evidence without
  a unique relation, a competing lane, support below `K`, or an ineligible
  (blank or excluded) sample leaves the cell without a value.
- Backfill runs once after group membership is fixed. Repaired observations
  enter the cell-decision path without reopening grouping or ModeTrack.
- Alignment and ModeTrack supply cross-sample context and trajectory evidence;
  Backfill owns the local missing-cell revisit. A trend or RT prior alone cannot
  manufacture a local observation or fill another sample from singleton
  evidence.
- The Targeted or Untargeted projection first constructs the explicit cell
  subject. `ProductionCellDecision`, not Backfill, then selects the final numeric
  value or typed no-write result; the same projection afterward applies its row
  inclusion and delivery policy.
- Any new behavior that lets repaired evidence affect publication requires an
  explicit public authority contract, expected-diff evidence, and focused
  output tests.
- Broad auto-write from the full candidate universe has no registered writer
  authority. The candidate/audit universe is not the writer pool; consult the
  status index for current lane readiness.
- Lifecycle vocabulary (missing call, candidate, evidence-chain packet, review
  item, approval decision, export policy, quant-matrix version) describes
  separate states and is not writer authority by itself.
- `shadow_gate_ready` classifies Backfill readiness; it is not matrix authority.
- Positive support must be provenance-valid. Display tokens in
  `alignment_review.tsv` do not promote a row without the approved source
  sidecar or authority chain.
- Family-level context can guide review, but repaired evidence must be tied to a
  same-peak, group-hypothesis, or PeakHypothesis-level owner record before it
  can enter `ProductionCellDecision`.
- Reusable offline overlay trace evidence caches are opt-in diagnostic replay
  helpers, not Backfill authority. A warm hit must match per-sample
  `path_stat` RAW identity: resolved path, size, mtime, device, and inode.
  Missing or mismatched identity fails closed to normal RAW extraction; a same
  path string alone is never sufficient cache identity.

## Population-derived Repair Authorization

`authorize_observation_repair` is the Backfill owner operation for the active
CID-NL tracer. It validates one complete owner-issued
`CellObservationRef` population over the declared existing-group × canonical-
sample coverage before reducing eligibility. A cell with
`technical_state="evaluated"` and `method_result="HOLD"` is eligible unless its
coverage position explicitly records `retained_local_owner` and the cell
already carries a physical peak observation; the sample may be a predecessor
member or a nonmember. That owner-closed exception keeps an observation-only
HOLD in the ModeTrack candidate population without reopening Backfill.
`selected_for_output` is a producer fact and never changes eligibility,
candidate identity, or cardinality.

Retained CID-NL production also transfers two closed conservative outcomes into
typed no-request coverage. A frozen group below the existing direct-SUPPORT
threshold retains the policy owner's exact decision and yields `not_searched`.
An already completed backfill with no retained observation requires a sealed
runtime projection, the exact backfill assessment and acquisition/request
binding, and the conservative owner's empty-universe decision. It yields
`unavailable`: the retained receipt does not establish a negative physical
peak-detection result. Both preserve the original HOLD and blank quantity and
prevent another repair epoch. Missing receipts, arbitrary HOLD, and incomplete
or positive backfill evidence cannot use these exceptions.

The coverage owner revalidates the complete group/sample/cell closure and binds
the proof into its planning epoch. ObservationRepair and the final cell owner
consume that same proof. Proof-bearing epochs use the canonical payload;
epochs without a proof retain their existing bytes and identity. Source cell
artifacts and runtime result artifacts retain their existing formats. The
runtime owner issues the completed-backfill projection; coverage never imports
the extraction runtime or treats nearby trace coordinates as exact requests.

Retained CID-NL production passes its closed coverage directly to CrossSample
and final cell composition. It does not request another ObservationRepair
authorization after runtime backfill has closed. The coverage still supplies
the exact group/sample associations and `not_searched`, `unavailable`, or
`unresolved` evidence; removing the extra authorization does not discard those
facts. Workflow paths that request an actual repair retain the authorization
and execution operations below.

The retained CID-NL runtime also permits one independent backfill for an initial
evaluated `REJECT` whose exact reason is
`qualifying_nl_evidence_without_any_ms1_candidate_peak`. This does not change the
original assessment or reopen all rejected Discovery inputs. An existing frozen
group, direct-SUPPORT threshold, compatible sample role and bounded local request
remain prerequisites. The new assessment combines fresh MS1 with original,
lineage-validated qualifying MS2 inside that window and must earn method SUPPORT
through the normal relation owner. Its receipt can produce a numeric final cell
with no source Discovery candidate; explanation lineage retains the initial
REJECT and exclusions. No group, insufficient support, blank samples, technical
failure or a completed empty backfill grant another repair epoch.

The result is a closed authorization union: no eligible cells produce
`not_run`, one produces `repair(candidate)`, and more than one produces a
typed `multiple_eligible` block. Missing, duplicate, extra, crossed, stale, or
noncanonical population facts are contract failures rather than `not_run`, and
the blocked variant never selects a winner or calls repair. The candidate is
bound to the exact request/attempt, existing group, canonical sample, prior
search cell artifact, logical request, coverage position, authorization, and
repair epoch `1`; it carries no membership or successor authority.

`execute_observation_repair` is the owner-owned execution seam. It derives the
authorization from the complete current population, invokes its injected
bounded repair operation exactly once only for `repair`, and returns a sealed
`ObservationRepairTerminalResult`. `not_run` and `multiple_eligible` are
terminal blocks with no execution and no receipt. A candidate may also be
closed through `close_observation_repair`, but that boundary requires the
current complete coverage and population and revalidates the exact
authorization/candidate, so an old authorization cannot be replayed after the
population changes.

The terminal receipt carries the repaired observation and its provenance for
current consumers. A candidate-scoped no-repair receipt instead carries a
sealed `ObservationRepairNoRepairTerminal` that binds the exact owner work unit
and physical answer; two distinct no-repair executions therefore cannot collapse
to the same receipt identity. Candidate receipts use the canonical receipt
namespace; existing member receipts retain their exact shape and identity.
The candidate no-repair receipt still exposes `evidence is None` to current
consumers and carries no synthetic repaired observation. Membership, value,
Matrix, activation, and writer authority remain `none`; the candidate remains
membership-neutral. Existing member repair subjects and receipt consumers
retain their current contract.

## Current Direction

The successor Backfill interface is one deep module with a small typed seam:

| Input | Output | Explicitly outside the interface |
| --- | --- | --- |
| Exact request/attempt, canonical sample, typed Target or existing-group repair context, owner-bound search receipt, eligible local evidence, and optional finalized group/trajectory context | Immutable repaired observation plus provenance, or typed no-repair decision | First-hit Discovery, group minting, implicit membership, ModeTrack fitting, row identity, selected value, reason strings, Matrix inclusion, and artifact rendering |

The work remains method-aware. SDO/LEK evidence is assessed through HCD;
MixSTDs may use CID or HCD; every Targeted method uses the evidence provider
named by that method. Architecture consolidation does not silently change the
current Alignment Backfill or Instrument QC scientific strategies. Any such
change is a separate promotion lane with method-appropriate evidence.

## Current Untargeted Owner Route

This section describes current compatibility behavior, not the frozen successor
interface. Normal Untargeted GUI `full`, GUI `align_only`, generic Alignment
CLI, and built-in preset runs enter the existing Owner Backfill transaction
through `run_alignment`. Candidate observation and product write authorization are
separate: a physical peak found inside the broad owner-authorized RT context is
not same-peak or cell-identity proof by itself.

The current promotion path reuses owner-validated geometry, identity,
provenance, and replay truth. Missing, conflicting, or unrehydratable owner
evidence remains unavailable or audit-only; a persisted legacy evidence label
cannot promote it. Current write scope still comes from the status index and
authority manifest, not from this route description.

The former Standard Peak preset, machine pipeline, productization transaction,
authority bundle, source-Matrix republisher, and separate writer scope are
retired. Retained Standard Peak shape/overlay helpers and packets are explicit
offline diagnostics, falsifiers, calibration context, or historical
self-consistency evidence only. They cannot mint Backfill `SUPPORT`, promote a
cell, or publish Matrix values. Normal preset/config/CLI entry points reject the
retired route and publication fields before expensive work.

## Conservative Eligibility Kernel

Issue #162 adds a bounded `shadow_ready / no-write` policy owner over the
canonical `CellObservationRef` reference spine. Its design and activation base is
`e30a04f8ed30a02c7424b49b24a42c844937a201`. It does not replace legacy
Backfill, activate a runtime path, or grant Matrix, CSV, workbook, config,
selected-value, counted-detection, or writer authority.

The frozen rules are:

- `N` counts non-excluded `study_sample`, `qc`, and `pooled_qc` samples. Blank,
  MixSTD/calibrator, system-suitability, solvent, and unknown roles are outside
  the activation population. Study and QC samples each contribute one equal
  vote.
- `K = max(2, floor(0.05 × N))`.
- `S` counts distinct activation-eligible direct SUPPORT samples in the exact
  existing group context, at most once per sample × group. Repeated scans,
  Backfill candidates, shared-set, inferred, deconvolved, shadow, and manual
  evidence cannot add votes.
- A group and its cells remain representable when `S < K`; the group simply
  cannot start a conservative backfill attempt.
- The declared run population and the canonical cell set must close exactly
  before the group is treated as frozen. Backfill cannot create a first hit,
  group, target universe, identity, landmark, or recursive support vote.
- Every group context binds the processing attempt that formed its direct
  SUPPORT closure. A later cell may reuse that closure only in the same attempt
  or through the current attempt's owner-validated retry-predecessor chain;
  same-request sibling attempts fail closed.
- The complete direct SUPPORT `PeakObservationRef` trace set is retained as
  exact references. It is not collapsed into a median template.
- Backfill candidates enter only through an immutable owner receipt that binds
  the exact request/attempt, sample, method subject and optional Targeted
  compatibility identity derived from the frozen group
  facts, source-cell artifacts, and one owner-minted CID-NL method assessment.
  That assessment—not a naked `PeakObservationRef` argument—owns the complete
  method-subject-bound candidate universe and its peak artifacts. Mapping consumes the
  receipt itself, so an empty completed universe remains distinguishable from
  a missing receipt, and callers cannot independently pair a method subject
  with a physical peak, combine receipts for one sample × method subject, or
  omit individual members.
- Receipt-backed backfill evidence is mapped jointly within the same exact
  method subject by equal cardinality, finite group RT, unique elution order, and the
  existing candidate query RT/ppm envelope. `1↔N`, `N↔1`, tied order, or
  out-of-envelope evidence stays HOLD and nonnumeric. Drift, split,
  deconvolution, and multi-component resolution remain later work.
- A method-level `REJECT` is terminal for that assessed evidence and is not itself
  Backfill authority. The retained-runtime missing-MS1 exception described above
  authorizes a separate evidence acquisition and assessment; it never rewrites
  the rejection. Technical/no-evidence and HOLD cells may remain nonnumeric pending
  an owner-bound backfill universe; neither state creates a vote by itself.
- Targeted and Untargeted adapters unwrap their CellObservationRef reference projections and
  call the same policy owner over the same exact `CellObservationRef` objects;
  neither adapter owns a second workflow-neutral cell model.

This slice, including the request/candidate, attempt-lineage, and backfill
receipt closure added while resolving #156 gaps 5/8/9, leaves every current
productization maturity tier and active lane unchanged. It changes admission
and replay identity only: no selected area, counted detection, output schema,
Matrix value, or writer authority changes. All pre-existing registered
Backfill writer authorities remain owned by the status index and authority
manifest; those machine owners and the historical productization control plane
therefore require no update for Issue #162.

## Current Evidence Compression

The broad Backfill auto-write decision is retained as provenance, not as a
current status owner. It must not be shortened or moved until the status index
and authority manifest point at a replacement compact artifact with updated
hashes.

Stable claims already absorbed here:

- The candidate/audit universe is not a writer pool.
- Current Backfill product authority is only the registered `write_ready` scope
  named by the status index and authority manifest.
- All remaining candidates stay outside writer authority, including
  trace-matched candidates with no approved evidence class or passing oracle
  and candidates missing trace/overlay evidence.
- Profile-only separation, quality blockers, round-trip reintegration, and
  ISTD behavior cannot become broad ProductWriter predicates.
- Broad Backfill can reopen only through a new independent truth source for
  peak choice, family identity, or area truth, plus an expected-diff contract.

The seed-aware review packet remains a shadow-gate support surface. Its current
signal is useful for review triage, but it does not change matrix authority or
write scope.

## Exact Backfill request reuse

The owner-closed Backfill path may reuse one extracted trace only through the
typed request partition minted by `owner_backfill_request_plan`. Its canonical
identity binds the canonical sample stem, the complete rehydratable
`AcquisitionSourceIdentity`, exact m/z and RT bounds, ppm tolerance, extraction
backend, window strategy and span factor, validation phase, and trace-result
semantics. The partition retains every hypothesis-bound logical request with a
stable logical index and requires complete, exactly-once membership in one
canonical class. Display IDs, rounded coordinates, persisted projections, and
proxy counts are never accepted as equivalence proof.

The extraction owner executes one representative per canonical class and
returns an owner-bound typed trace result. It fans that trace back to every
logical member in deterministic request order; each hypothesis still performs
its own peak evaluation and keeps its original result and candidate-audit
semantics for the consuming owner. Primary, hybrid prefilter, and
RAW validation phases use distinct identities and cannot reuse across phases or
incompatible acquisition/backend/window/result semantics.

Exact reuse is available only when the Discovery owner closure supplies one
owner-minted acquisition identity per sample and the opened source exposes the
same identity. Missing, ambiguous, or mismatched owner semantics fail closed:
the runtime emits no exact-equivalence observation. Any legacy in-call
`XICRequest` equality behavior is compatibility behavior only and is not
semantic evidence. Timing diagnostics may report observed logical-request and
canonical-class counts when the exact owner partition exists; they do not claim
elapsed-time, RAW-call, chromatogram-call, point-count, or monetary savings.
This path grants no new Backfill eligibility, Matrix, ProductWriter,
activation, streaming, artifact, or backfill authority.

## Surfaces

| Surface | Role |
| --- | --- |
| ObservationRepair owner seam | Bounded local repair facts and provenance; no value or writer authority |
| `ProductionCellDecision` handoff | Consumes repair facts only through an explicit projection-owned Target/Group cell subject and independently decides write/no-write |
| Legacy `ProductWriter` authority path | Current compatibility path for already approved matrix changes; must not be absorbed into Backfill's successor interface |
| Productization status index | Machine-checkable Backfill maturity and lane state |
| Authority manifest | Fail-closed writer scope and accepted authority records |
| Mechanical adjudication schema/index | Structured review evidence for candidate promotion |
| Backfill validation packets | Evidence availability, replay, and expected-diff artifacts |
| `alignment_backfill_cell_evidence.tsv` | Compact alignment-side evidence sidecar |

## Boundaries

- **Owns:** bounded repair eligibility, exact local repair work, owner-backed
  repaired observations, no-repair outcomes, provenance, and transaction
  closure.
- **Does not own:** group membership, ModeTrack fitting, selected value,
  counted detection, Matrix/row inclusion, Review Queue policy, writer
  authority, current maturity tier or active lane, or sample-level private
  investigation details.
- The candidate/audit universe feeds review and classification, not automatic
  matrix writing.

### Stable Categories

| Category | Meaning | Repo owner |
| --- | --- | --- |
| Current scoped writes | Already accepted cells that ProductionCellDecision and the registered ProductWriter path may publish | Authority manifest, status index, cell-decision owner |
| Candidate or audit rows | Rows available for review, classification, or evidence backfill | Validation packets, adjudication index |
| Trace-matched unresolved rows | Rows with some trace-level evidence but insufficient authority | Evidence lifecycle, adjudication surfaces |
| Missing-overlay rows | Rows blocked by absent or insufficient overlay evidence | Evidence availability packets, blocker reports |
| Expansion packets | Proposed future writer changes | Status index, authority manifest, promotion contract, expected-diff packet, output tests |
| Evidence-chain packet | Structured source evidence for review or approval | Evidence lifecycle, provenance owners |
| Approval decision | Human or machine-readable approval state | Review roundtrip, authority manifest gates |

## Verification

Before changing Backfill product behavior, require the relevant subset of:

- Productization state and authority checker pass.
- Expected-diff packet for any new matrix-writing scope.
- Focused output tests covering changed matrix values and identity sidecars.
- Evidence-rule review for any new interpretation of trace, overlay, or
  candidate evidence.
- Privacy scan if historical notes or sample-level review details are touched.
- Characterization parity when moving current Backfill behavior into the
  ObservationRepair owner; do not combine that move with eligibility, RT,
  integration, or selected-value changes.
- A truth-table test proving repaired observation, membership, and selected
  value remain three independent decisions.

## Pitfalls

- Treating the candidate/audit universe as the writer pool.
- Treating overlays, galleries, blocker tokens, or sidecars as matrix authority.
- Treating `shadow_gate_ready` as a write predicate.
- Treating `alignment_review.tsv` tokens as positive support without
  provenance-valid sidecars.
- Treating a family/window match as same-peak evidence without typed anchor,
  selected mode, or PeakHypothesis-level support.
- Treating an accepted repaired observation as implicit group membership or an
  automatically writable value.
- Letting ModeTrack/Alignment own the local repair merely because trajectory
  evidence informed the request.
- Updating a topic summary while leaving the status index, authority manifest,
  or promotion contract stale after a real authority change.

## See Also

- [Historical compatibility index](../superpowers/plans/2026-06-15-productization-control-plane.md)
- [Status index](../superpowers/validation/productization_status_index.tsv)
- [Authority manifest](../superpowers/schemas/productization_authority_manifest.json)
- [Evidence rules](../lc-msms-evidence-rules.md)
- [Peak anchor and group boundary](family-hypothesis-boundary.md)
