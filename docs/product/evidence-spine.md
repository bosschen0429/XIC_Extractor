# Evidence Spine

The evidence spine is the shared layer that converts chromatographic observations into auditable peak hypotheses. It provides workflow-neutral identity facts consumed by both targeted and untargeted pipelines, but it does not own product output decisions -- those belong to each workflow's projection layer.

## Contract

- Evidence providers are inputs to `EvidenceVector`, `PeakHypothesis`, `IntegrationResult`, model selection, audit, and activation contracts. They do not write product matrices directly.
- Shared peak identity owns workflow-neutral facts: trace shape, candidate-to-anchor RT delta, competing-peak summaries, boundary assessability, and provenance validation.
- Targeted workflows add known-target policy: analyte/ISTD role, paired-ISTD RT, target RT windows, NL/MS2 opportunity, paired area ratio, and targeted expected-diff activation.
- Untargeted workflows add alignment/Backfill policy: owner construction, same-peak anchor, cell-level Backfill decision, authority manifest, and quant-matrix activation.
- A shared evidence provider may serve both workflows only if its output is a typed fact, not a hidden product decision.
- The shared-spine direction is to preserve and adapt existing typed evidence objects rather than replace workflow policies.
- The bounded CID-NL tracer now freezes the measured field intersection shared
  by Targeted `MS2ProductEvidence` and Untargeted direct `DiscoverySeed` as
  `CidNlEvidenceRef`. Target/Discovery-only fields remain in thin adapters;
  both adapters must match the request-bound acquisition source, Targeted must
  also match the exact request-pinned target snapshot and scan number, and its
  measured scan precursor must remain within that exact target's ppm tolerance;
  direct Discovery must carry a finite measured `scan_precursor_mz` equal to
  the canonical evidence precursor;
  neither projection artifact is a selection or delivery decision.
- The existing `peak_detection` owner now exposes a bounded
  `PeakObservationRef`. Its physical content is canonical sample identity, m/z,
  one contiguous selected RT/intensity subtrace, and its owner-normalized
  `IntegrationResult`. Semantically equal present integer/float numeric inputs
  receive one canonical representation before identity hashing while existing
  null semantics remain unchanged. Exact request-bound
  acquisition source plus full-trace/
  query/resolver provenance and producer request/attempt remain explicit
  without becoming physical-content identity.
- For the lab's current Untargeted acquisition method, the CID-NL owner now
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
- The existing `PeakHypothesis` model now has one bounded owner-backed branch.
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
   owner-normalized `IntegrationResult` facts. Numeric representation alone
   cannot split content identity, and normalization does not turn a null into a
   numeric observation.
2. Request-bound acquisition-source identity, full trace digest, query RT/ppm,
   trace source, source-relative indices, resolver mode, and proposal sources
   are generation provenance. Changing
   them does not re-mint physical content when the selected signal and
   integration are unchanged, but it does change the exact artifact payload.
3. Processing request/attempt identify the produced artifact instance. A retry
   of the same exact payload preserves content identity and receives a distinct
   artifact identity.
4. Target label, Targeted/Untargeted role, ISTD policy, CID-NL status, and Pt
   membership are not physical-observation content. They belong to evidence
   applicability or later hypothesis composition.
5. Selected-signal mutation changes content identity. Missing, gapped,
   reordered, or duplicate selected indices and sample/request/attempt
   substitution fail closed.
6. Mint and binding consume an already validated `ProcessingOwnerReferences`
   closure. The chosen sample must belong to that request, `Trace.sample_name`
   and `Trace.acquisition_source_id` must resolve to that owner row and exact
   acquisition binding, and integration RT bounds must remain inside the
   supplied trace before mapping to the selected contiguous subtrace under the
   existing nearest-index/bounded-interval owner semantics. A caller cannot
   validate a bad mint by repeating the same mismatched sample or attempt.

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

The owner-backed CID-NL hypothesis branch closes the next handoff under these
bounded rules:

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

This still models only the one-observation DDA CID-NL method hypothesis. The
current vertical tracer therefore proceeds to an explicit diagnostic
selection/externalization handoff and then shared group/cell decisions. Pt/Delta
Mass multi-observation composition remains deferred until its Discovery method
and owner-backed position roles are stable; array order or an unlabeled tuple
must not be guessed into method semantics.

## Surfaces

| Surface | Role |
| --- | --- |
| `EvidenceVector` / `EvidenceDecisionSemantics` | Shared typed evidence vocabulary |
| `CidNlEvidenceRef` | Attempt-, sample-, acquisition-source-, and scan-bound immutable direct measured CID-NL observation for the no-write dual-projection tracer |
| `PeakObservationRef` | `peak_detection`-owned physical MS1 content plus exact request-bound acquisition-source/generation provenance and request/attempt artifact binding; diagnostic only |
| `DdaCidNlPeakSupportRef` | CID-NL-owner DDA/ddMS2 scan-to-physical-peak relation over an exact supplied candidate universe; diagnostic only, no DIA or selection authority |
| `CidNlChromatographicSupportRef` | One-or-more exact strict DDA scan relations aggregated as one physical-peak support event; diagnostic only, no hypothesis or counting authority |
| `PeakHypothesis` | One existing model: legacy Targeted working identity plus a bounded diagnostic owner-backed CID-NL composition; neither is canonical physical-observation identity |
| `peak_hypothesis_identity` | Same-owner content ID, exact attempt artifact, and binding validation for the bounded CID-NL composition; no selection or product authority |
| `CrossSamplePeakGroupHypothesis` | Cross-sample group identity unit |
| `IntegrationResult` | Integrated signal and boundary result |
| `AuditTrail` / reason strings | Human-readable explanation and review evidence |
| Targeted product projection | Workflow-owned targeted output decision |
| Backfill / alignment projection | Workflow-owned untargeted or matrix decision |

## Boundaries

- **Owns:** workflow-neutral identity facts (trace shape, RT delta, boundary assessability, provenance), candidate hypothesis lifecycle, typed evidence vocabulary.
- **Does not own:** product matrix values, Backfill writer scope, targeted rescue activation policy, or promotion/maturity decisions.
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
- Letting untargeted Backfill rules silently become targeted rescue rules (or vice versa).
- Counting multiple typed facts as independent observations when they describe the same physical evidence.
- Promoting diagnostic-only or shadow evidence into matrix values.

## See Also

- [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md)
- [Architecture contract](../architecture-contract.md)
- [Peak anchor and group boundary](family-hypothesis-boundary.md)
- [Backfill](backfill.md) | [Discovery](discovery.md) | [Alignment](alignment.md)
- [Productization](productization.md)
