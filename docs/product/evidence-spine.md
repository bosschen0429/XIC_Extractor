# Evidence Spine

The evidence spine is the shared layer that converts chromatographic
observations into auditable peak hypotheses. The spine provides
workflow-neutral identity facts to both Targeted and Untargeted pipelines. The
spine does not own product output decisions. The projection layer of each
workflow owns those decisions.

## Contract

- Evidence providers are inputs to `EvidenceVector`, `PeakHypothesis`,
  `IntegrationResult`, model selection, audit, and activation contracts.
  Evidence providers do not write product matrices directly.
- Shared peak identity owns workflow-neutral facts: trace shape, candidate-to-anchor RT delta, competing-peak summaries, boundary assessability, and provenance validation.
- Targeted workflows add known-target policy: analyte/ISTD role, paired-ISTD RT, target RT windows, NL/MS2 opportunity, paired area ratio, and Targeted expected-diff activation.
- Untargeted workflows add alignment/Backfill policy: owner construction, same-peak anchor, cell-level Backfill decision, authority manifest, and quant-matrix activation.
- A shared evidence provider may serve both workflows only if its output is a typed fact, not a hidden product decision.
- The shared-spine direction is to preserve and adapt existing typed evidence objects rather than replace workflow policies.
- The bounded CID-NL tracer freezes the measured field intersection shared by
  Targeted `MS2ProductEvidence` and Untargeted direct `DiscoverySeed` as
  `CidNlEvidenceRef`.
  - Target-only and Discovery-only fields remain in thin adapters.
  - Both adapters must match the request-bound acquisition source.
  - The Targeted adapter must also match the exact request-pinned target
    snapshot and scan number. Its measured scan precursor must remain within
    the ppm tolerance of that exact target.
  - Direct Discovery must carry a finite measured `scan_precursor_mz` equal to
    the canonical evidence precursor.
  - Neither projection artifact is a selection or delivery decision.
- The existing `peak_detection` owner exposes a bounded `PeakObservationRef`.
  - The physical content of a `PeakObservationRef` is canonical sample
    identity, m/z, one contiguous selected RT/intensity subtrace, and its
    signal-validated `IntegrationResult`.
  - Before minting, the owner checks raw apex/height/area against that exact
    subtrace, smoothed height against the exact selection trace, and any
    declared AsLS/Gaussian15 facts against the deterministic existing helpers.
  - Semantically equal present integer/float numeric inputs receive one
    canonical representation before identity hashing. Existing null semantics
    remain unchanged.
  - The exact request-bound acquisition source, full raw/selection-trace,
    query, and resolver provenance, and producer request/attempt remain
    explicit. These fields do not become physical-content identity.
- For the lab's current Untargeted acquisition method, the CID-NL owner
  exposes one bounded DDA/ddMS2 applicability relation. A
  `DdaCidNlPeakSupportRef` binds one exact MS2 scan and direct measured CID-NL
  artifact to exactly one physical MS1 peak within the exact supplied candidate
  universe. DIA is outside this contract because there is no mature lab DIA
  analysis method.
- `CidNlChromatographicSupportRef` then preserves one or more exact strict DDA
  scan relations while collapsing them to one chromatographic support event
  for that physical peak. Scan reorder is identity-neutral; duplicate scans or
  mixed peak, candidate-universe, policy, sample, request, or attempt refs fail
  closed.
- The existing `PeakHypothesis` model has one bounded owner-backed branch.
  `compose_cid_nl_peak_hypothesis` consumes one `PeakObservationRef` and one
  `CidNlChromatographicSupportRef` bound to the same sample, request, attempt,
  and physical peak. It does not mint Targeted context, selection semantics, or
  a second hypothesis type.
- Legacy family IDs are review and compatibility containers. Group identity
  belongs to `CrossSamplePeakGroupHypothesis`. Legacy candidate and
  `build_peak_hypotheses` IDs remain workflow-local working identities; the
  bounded owner-backed CID-NL branch instead uses a typed content ID without
  turning it into physical-observation identity. Product authority belongs to
  workflow projection.

## Observation / Hypothesis / Evidence decomposition

The no-RAW characterization in
`tests/test_peak_identity_decomposition.py` closes one design unknown and
rejects promoting the current Targeted-shaped `PeakHypothesis` as the single
cross-workflow identity object:

- A physical MS1 trace/integration can remain unchanged while Targeted or
  Untargeted interpretation context produces different working hypothesis and
  trace-group IDs.
- Changing direct CID-NL/MS2 evidence can leave the MS1 integration and current
  working hypothesis ID unchanged. Evidence content therefore needs its own
  immutable reference and applicability validation.
- In CID-NL Discovery, an MS2 seed selects the precursor XIC and RT search
  window before MS1 peak evaluation. Provider evidence can participate in
  candidate generation as well as candidate evaluation; generation provenance
  is part of invalidation, not merely an annotation added after selection.
- A Pt/Delta Mass hypothesis composes four sample-local MS1 feature
  observations. It cannot be represented as one evidence field attached to one
  peak.

The operator selected the existing-owner route: deepen `peak_detection`, not a
new lineage package and not a second feature/sample owner. The first owner API
closes the following bounded rules:

1. Physical content identity includes canonical sample identity, m/z, the
   contiguous selected RT/intensity points, peak bounds/apex, and all
   signal-validated `IntegrationResult` facts. A typed value or content hash is
   not proof that independently assembled integration numbers came from the
   trace: minting first verifies the raw apex/height/area, exact selection-trace
   apex/height, and any declared AsLS/Gaussian15 facts. Numeric representation
   alone cannot split content identity, and normalization does not turn a null
   into a numeric observation.
2. Request-bound acquisition-source identity, full raw- and selection-trace
   digests, query RT/ppm, trace source, source-relative indices, resolver mode,
   and proposal sources are generation provenance. Changing
   them does not re-mint physical content when the selected signal and
   integration are unchanged, but it does change the exact artifact payload.
3. Processing request/attempt identify the produced artifact instance. A retry
   of the same exact payload preserves content identity and receives a distinct
   artifact identity.
4. Target label, Targeted/Untargeted role, ISTD policy, CID-NL status, and Pt
   membership are not physical-observation content. They belong to evidence
   applicability or later hypothesis composition.
5. Selected raw-signal mutation invalidates a stale integration before identity
   minting; a re-integrated signal receives new physical content identity.
   Selection-transform mutation with unchanged physical facts preserves
   physical content but changes generation provenance and exact artifact
   identity. Missing, gapped, reordered, or duplicate selected indices and
   sample/request/attempt substitution fail closed.
6. Mint and binding consume an already validated `ProcessingOwnerReferences`
   closure. The chosen sample must belong to that request, `Trace.sample_name`
   and `Trace.acquisition_source_id` must resolve to that owner row and exact
   acquisition binding, and integration RT bounds must remain inside the
   supplied trace before mapping to the selected contiguous subtrace under the
   existing nearest-index/bounded-interval owner semantics. The exact selection
   trace defaults to the raw trace only when no separate transform is supplied.
   A caller cannot validate a bad mint by repeating the same mismatched sample,
   attempt, or internally contradictory integration facts.

This closes the first provider-specific evidence-to-observation relation, not
method-hypothesis composition. The DDA support owner requires one exact scan,
strict neutral-loss evidence, a unique integration-bounds RT/m/z match, and the
complete candidate tuple supplied to it. Selected trace padding is provenance,
not physical peak scope. Reordering that tuple is identity-neutral;
mutation, substitution, missing candidates, no match, or multiple matches fail
closed. The artifact depends on both the CID-NL evidence and every supplied
candidate peak, so a caller cannot hide a non-selected candidate during later
validation.

Runtime candidate-universe completeness is not yet proven, and the current RAW
reader does not expose enough isolation/co-isolation metadata to make that a
contract. A provider-local Discovery `DetectedFeature` that lacks the
owner-required full `Trace` plus `IntegrationResult` still cannot mint a
substitute physical reference. The aggregate event closes the lab rule that
repeated DDA/NL scans inside one selected MS1 peak are multiple scans but one
chromatographic support event. It is not a second `PeakHypothesis` and grants
no independent support votes.

### Owner-backed CID-NL hypothesis

The owner-backed CID-NL hypothesis branch closes the handoff from
`CidNlChromatographicSupportRef` to `PeakHypothesis` under these bounded rules:

1. Hypothesis content identity includes the method, canonical sample, physical
   peak content ID, and chromatographic-support artifact content ID. Scan or
   peak mutation therefore invalidates the hypothesis.
2. Exact artifact content additionally records the peak-observation artifact
   content ID, so generation-provenance changes invalidate the exact artifact
   without pretending the physical peak changed.
3. Retry with identical content preserves `hypothesis_id` and exact artifact
   content identity, but a new attempt receives a distinct artifact identity
   and exact upstream closure.
4. Target label, role, ISTD pairing, trace-group identity, decision semantics,
   selection audit, counting, and workflow projection are prohibited on this
   branch. Selection-decision, the `PeakModelSelectionResult` value boundary,
   model-selection, `ExtractionResult`, and
   Targeted result-assembly boundaries reject this diagnostic branch before it
   can expose RT/area or produce a counted projection. The legacy Targeted
   constructor remains unchanged.
5. Missing refs, physical-peak/event mismatch, request/attempt/sample
   substitution, stale cross-attempt applicability, event substitution, and
   identity tamper fail closed.

### Method subject

The CID-NL owner also closes the complete method attempt before grouping.
`CidNlMethodSubject` is the smallest shared Targeted/Untargeted subject: exact
precursor m/z, precursor tolerance, configured neutral loss, and an immutable
subject ID. A multi-tag CID-NL subject instead binds the ordered tag/loss
requirements and their OR/AND combination; it does not represent the first
loss as the whole method. Single-loss subject/assessment/chromatographic schemas
remain v1/v3/v1; composite methods use v2/v4/with explicit loss bindings.
A Targeted adapter retains its exact target snapshot/key/definition
inside the subject; an Untargeted request pins the same subject schema directly
and never fabricates a `ResolvedTargetDefinition`.

The subject owner reads the request's scalar, composite or batch configuration
through `cid_nl_method_subjects_from_request`, using the existing strict payload
contracts. Runtime jobs retain that validated subject configuration for candidate
membership and scheduling; SearchScope and capture consume the same owner reader.
Physical capture, settings and candidate-universe validation retain their existing
owners. The derived runtime value does not mint or change request identity.

### Candidate assignment to method subjects

The Untargeted batch factory assigns each Discovery candidate to one bounded
method-query subject. Within each scalar loss or full configured composition,
candidates are ordered by physical query m/z and candidate ID. The first mass
fixes a subject center; later candidates join only within its configured ppm
tolerance, without moving the center or chaining through another candidate.
Before this assignment, source-bound records with the same selected apex and
identical raw RT/intensity throughout their shared peak interior may form one
indivisible workset. Every contributor must share an extraction m/z interval
and an admissible method-center interval; pairwise overlap cannot form a chain
without an all-member intersection. A fixed center can accept the workset only
when it covers every physical query; otherwise the workset uses the midpoint
of its common admissible center interval. Candidates without this evidence keep
the fixed-first behavior. This prevents one observed peak from being split into
separate method subjects merely because its queries straddle a bucket boundary.

The shared interior establishes permission to process the candidates together;
it is not a physical-observation ID or proof that their fitted tails and areas
are identical. The runtime still acquires the shared context, selects peaks and
assesses the complete MS2 relation universe. Distinct candidate identities,
per-query physical facts and original MS2 evidence remain intact.

This declared query membership lets nearby measured masses in different samples
reach evaluation of the same method subject. Each physical query retains its own
exact source m/z, RT bounds, trace and acquisition identity. Both single-subject
and batch SearchScope apply the same method-center ppm compatibility to the
active query. A retained physical evaluation separately carries its original
XIC center, so moving that center requires fresh evaluation without changing
the original receipt. Query membership grants no
cross-sample identity or Matrix eligibility; those decisions remain with the
existing assessment and CrossSample owners.

The real-data CID-NL adapter retains the shared capture owner's
`SampleLocalCapture` results alongside its assessment. Production composition
uses their bound traces and candidate bindings; it does not reconstruct those
relationships from acquisition arrays or Discovery rows. Acquisition receipts
remain with the runtime owner, and the shared workflow still validates its own
complete search scope before composing downstream results.

### Method assessment

`CidNlMethodAssessmentRef` binds one detector invocation, method subject, candidate
universe, completed-search receipt with the exact DDA support policy, measured
evidence, and individual or set-level relations to the existing processing
request/attempt. Every qualifying scan that matches that candidate universe
must have exactly one of those relation kinds; different scans may retain
individual and set-level relations in the same assessment. Its technical state
(`evaluated`, `unsupported`, `failed`, or `not_evaluated`) is independent from
its CID-NL result (`SUPPORT`, `HOLD`, `REJECT`, or `None`). Absence of a
positive evidence ref is therefore not treated as proof that a search
completed, and technical failure cannot become REJECT or a human
`unassessable` disposition.

The initial Untargeted Discovery adapter records `not_evaluated` with
`method_result=None` and `no_search_eligible_discovery_input` when it has neither
an eligible scan-seed input nor a feature-backed MS1 candidate. This includes an
empty local subject universe and inferred candidates removed by the Discovery
eligibility filter. Its search receipt stays incomplete; it does not represent
a completed method evaluation. Discovery eligibility exclusions remain owned by
`UntargetedCandidateExclusion`.

### Backfill of unsearched and rejected samples

An existing owner-backed group can guide a bounded Backfill for a sample whose
initial Discovery assessment is `not_evaluated` with
`no_search_eligible_discovery_input`, after the conservative sample-role and
direct-support gates pass. Missing input alone grants no Backfill authority. A group below the
support threshold retains the exact no-request proof and a `not_searched` cell.
A Backfill that actually acquires or consumes its bound XIC trace and evaluates
the candidate universe records a completed search, even when the Backfill finds
no peaks or qualifying CID-NL evidence. That completed result remains `HOLD`.
This completed `HOLD` result is distinct from an initial unsearched slot and
does not prove method rejection.

An evaluated `REJECT` with the exact reason
`qualifying_nl_evidence_without_any_ms1_candidate_peak` remains the result of its
original assessment. Retained CID-NL production can independently authorize one
group-guided Backfill after the same frozen-group, direct-support, and
sample-role gates. The adapter validates the original sample, acquisition source, method and
processing lineage, reuses only its immutable qualifying MS2 evidence within the
new local window, and assesses fresh MS1 peaks with the existing individual/set
relation rules. A new `SUPPORT` may reach CrossSample and the final cell owner;
MS1 presence alone cannot promote a cell. The original rejection, Discovery
exclusions and new assessment remain queryable together in the explanation store.

### Evidence keys and relation rules

Untargeted initial assessments reconstruct Discovery's declared scan receipts
against the bound RAW source. Their `declared_scans` evidence scope retains
those exact MS2 facts even outside the shared subject's mass window. The
physical candidate universe and scan-to-peak mass/RT relation still determine
SUPPORT; a scan link alone cannot grant it. Targeted assessments and independent
subject searches retain `subject_window` scope. A Backfill reusing declared
evidence preserves its scope and narrows it to the new RT window. Canonical
CID-NL evidence continues to distinguish measured and product-derived precursor
mass; the physical MS1 reading never overwrites either receipt.

For single-loss methods, one assessment can contain at most one measured
evidence ref for each exact `(acquisition_source_id, scan_number)` key.
Composite methods add the configured loss to that evidence key, so one physical
scan can support two distinct required losses without being counted as two scans.
Duplicate evidence for the same scan/loss is rejected. Multiple matched product
centroids for one loss must be resolved by the evidence provider before
assessment minting. When multiple distinct strict scans support one physical
peak for a single-loss method, the Alignment adapter projects their arithmetic
mean measured product m/z and observed neutral loss as one order-neutral
chromatographic identity event. The exact per-scan evidence remains preserved
upstream, and the aggregate does not create additional support votes.

Only a scan strictly inside exactly one candidate interval, with no positive-width
overlap with another m/z-compatible interval, may retain the existing individual
support relation. Adjacent intervals may share a valley endpoint without blocking
that interior support. A scan exactly on an interval boundary, or a selected
interval with positive-width overlap even away from the scan, uses
`DdaCidNlCandidateSetRelationRef`; that relation grants no individual vote or
hypothesis. Set relations retain the connected boundary/overlap candidate set;
an otherwise unique interior scan cannot be downgraded to a set relation merely
because another interval shares an endpoint. At least one strict unique relation
makes the method result
SUPPORT for a single-loss method even when other scans retain set-level relations.
For composite methods, OR requires a qualifying configured loss and AND requires
every configured loss on the same physical peak in the same sample. Different
peaks, samples, or unclosed tag labels cannot pool evidence into AND support.
Initial Discovery and Backfill apply the same requirement. Set-only evidence is
HOLD, and REJECT requires qualifying CID-NL evidence with no usable physical
MS1 peak; non-qualifying scans cannot manufacture a negative result. A unique
SUPPORT may compose the existing owner-backed `PeakHypothesis` and enter
existing provisional group construction. HOLD, REJECT, and technical
assessments remain valid without a fake hypothesis or an invented group.
If a CID-NL subject has complete assessment coverage for every sample,
all evaluated results are HOLD/REJECT, and remaining slots have exactly the
unsearched Discovery-input reason above, the existing
`UntargetedCandidateExclusion` owner retains
those exact assessments under `cid_nl_requirement_not_supported` for a single
method or `composite_requirement_not_supported` for a composite method. The
exclusion requires at least one evaluated result and complete current sample and
request/attempt binding. Each feature-backed candidate must have its own sample's
evaluated HOLD/REJECT assessment; an unsearched slot records coverage only and
cannot supply negative evidence. Failed, unsupported, or other incomplete
assessments cannot stand in for this evidence.
The subject does not enter ModeTrack group construction. An all-excluded batch
completes with an empty canonical Matrix and preserves its assessment evidence.

### Cell observation reference

The CellObservationRef reference spine creates one minimal `CellObservationRef` only after a
SUPPORT assessment has established a provisional group. Targeted and
Untargeted diagnostic adapters receive the same exact group and cell objects;
they do not remint equal-looking DTOs. Cell artifact identity binds both the
group label and its complete canonical hypothesis membership, method subject,
optional Targeted compatibility identity, and the exact SUPPORT assessment
closure that formed the group. Every SUPPORT,
HOLD, REJECT, or technical cell must consume that owner-validated group context;
a reused display label, equal-looking group, or group from another method
subject/Target cannot be substituted. The cell artifact keeps those SUPPORT assessment artifacts
in its upstream closure. An optional owner-resolved human disposition must cite
the exact current method-assessment artifact, not merely the same sample/target
subject. Both adapters are internal, no-write, and carry
`product_authority="none"`. Current CID-NL execution consumes their evidence
through the retained shared batch; canonical writers alone project finalized
values. An assessment or cell reference does not itself grant writer authority.

When ModeTrack retains multiple provisional groups for one method subject, the
shared batch constructs the complete group × sample cell population. A sample's
SUPPORT assessment can belong to a sibling group without supporting the current
cell's group. Such a cell preserves the original assessment and carries an
explicit outside-group relation bound to that exact sibling group context and
supported hypothesis. Its group-local observation and hypothesis remain empty.
The cell artifact binds this relation; existing cells retain their canonical
payload and semantic contract. The existing `local_owner_not_supersedable`
coverage yields a blank, `not_searched` cell without asserting absence. ModeTrack
and CrossSample retain identity authority, and the outside-group lane cannot
duplicate a value supplied by another lane after groups merge.

Pt/Delta Mass multi-observation composition remains deferred until its
Discovery method and owner-backed position roles are stable; array order or an
unlabeled tuple must not be guessed into method semantics.

## Surfaces

| Surface | Role |
| --- | --- |
| `EvidenceVector` / `EvidenceDecisionSemantics` | Shared typed evidence vocabulary |
| `CidNlEvidenceRef` | Attempt-, sample-, acquisition-source-, and scan-bound immutable direct measured CID-NL observation for the no-write dual-projection tracer |
| `PeakObservationRef` | `peak_detection`-owned physical MS1 content plus exact request-bound acquisition-source/generation provenance and request/attempt artifact binding; diagnostic only |
| `DdaCidNlPeakSupportRef` | CID-NL-owner DDA/ddMS2 scan-to-physical-peak relation over an exact supplied candidate universe; diagnostic only, no DIA or selection authority |
| `DdaCidNlCandidateSetRelationRef` | One scan with boundary ambiguity or positive-width candidate overlap; connected set only, zero individual votes and zero hypotheses |
| `CidNlChromatographicSupportRef` | One-or-more exact strict DDA scan relations aggregated as one physical-peak support event; diagnostic only, no hypothesis or counting authority |
| `CidNlMethodSubject` | Workflow-neutral CID-NL precursor/loss subject; Targeted retains exact configured-target identity while Untargeted is request-pinned without a fake Target |
| `CidNlMethodAssessmentRef` | Complete method-subject-bound CID-NL attempt with a policy-bound completed-search receipt, unique measured-event keys, per-qualifying-scan relation closure, and orthogonal technical and method-result axes; child of the existing processing attempt |
| `PeakHypothesis` | One existing model: legacy Targeted working identity plus a bounded diagnostic owner-backed CID-NL composition; neither is canonical physical-observation identity |
| `peak_hypothesis_identity` | Same-owner content ID, exact attempt artifact, and binding validation for the bounded CID-NL composition; no selection or product authority |
| `CrossSamplePeakGroupHypothesis` | Cross-sample group identity unit |
| `CellObservationRef` | Minimal sample × existing-group closure over exact method-subject group/SUPPORT membership plus optional Targeted compatibility identity, technical state, method result, optional observation/hypothesis, evidence/reasons, and an optional exact-assessment-bound owner-resolved human disposition; no value or writer fields |
| CID-NL Targeted/Untargeted reference projections | Two internal views that retain the same exact group/cell refs and explicitly carry no product authority |
| `IntegrationResult` | Integrated signal and boundary result |
| `AuditTrail` / reason strings | Human-readable explanation and review evidence |
| Targeted product projection | Workflow-owned Targeted output decision |
| Backfill / alignment projection | Workflow-owned Untargeted or matrix decision |

## Boundaries

- **Owns:** workflow-neutral identity facts (trace shape, RT delta, boundary assessability, provenance), candidate hypothesis lifecycle, typed evidence vocabulary.
- **Does not own:** product matrix values, Backfill writer scope, Targeted rescue activation policy, or promotion/maturity decisions.
- A typed fact shared across workflows is evidence; a product decision shared across workflows is a bug.

## Verification

- Characterization tests for existing evidence semantics.
- Focused tests for new typed evidence facts.
- Expected-diff framing when selected peak, area, detection count, confidence, reason, workbook, TSV, or matrix values can change.
- RAW-tier evidence label matching the claim.
- Productization owner update when product authority changes.

## Pitfalls

- Treating shared evidence as shared product authority.
- Treating family membership as same-peak proof or ProductWriter authority.
- Letting Untargeted Backfill rules silently become Targeted rescue rules (or vice versa).
- Counting multiple typed facts as independent observations when they describe the same physical evidence.
- Promoting diagnostic-only or shadow evidence into matrix values.

## See Also

- [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md)
- [Architecture contract](../architecture-contract.md)
- [Peak anchor and group boundary](family-hypothesis-boundary.md)
- [Backfill](backfill.md) | [Discovery](discovery.md) | [Alignment](alignment.md)
- [Productization](productization.md)
