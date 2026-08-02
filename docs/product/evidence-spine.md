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
  one contiguous selected RT/intensity subtrace, and its signal-validated
  `IntegrationResult`. The owner checks raw apex/height/area against that exact
  subtrace, smoothed height against the exact selection trace, and any declared
  AsLS/Gaussian15 facts against the deterministic existing helpers before
  minting. Semantically equal present integer/float numeric inputs receive one
  canonical representation before identity hashing while existing null
  semantics remain unchanged. Exact request-bound acquisition source plus full
  raw/selection-trace, query, and resolver provenance and producer
  request/attempt remain explicit without becoming physical-content identity.
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

The CID-NL owner now also closes the complete method attempt before grouping.
`CidNlMethodAssessmentRef` binds one detector invocation, target, candidate
universe, completed-search receipt, measured evidence, and individual or
set-level relations to the existing processing request/attempt. Its technical
state (`evaluated`, `unsupported`, `failed`, or `not_evaluated`) is independent
from its CID-NL result (`SUPPORT`, `HOLD`, `REJECT`, or `None`). Absence of a
positive evidence ref is therefore not treated as proof that a search
completed, and technical failure cannot become REJECT or a human
`unassessable` disposition.

Only a scan strictly inside one non-overlapping candidate interval may retain
the existing individual support relation. Boundary, touching, and overlapping
cases use `DdaCidNlCandidateSetRelationRef`; that relation grants no individual
vote or hypothesis. A unique SUPPORT may compose the existing owner-backed
`PeakHypothesis` and enter existing provisional group construction. HOLD,
REJECT, and technical assessments remain valid without a fake hypothesis or an
invented group.

The M1 reference spine creates one minimal `CellObservationRef` only after a
SUPPORT assessment has established a provisional group. Targeted and
Untargeted diagnostic adapters receive the same exact group and cell objects;
they do not remint equal-looking DTOs. Both adapters are internal, no-write,
and carry `product_authority="none"`. Existing production callers, Backfill,
Matrix, selected value/counting, CLI/config, and writers remain unchanged and
belong to later activation work.

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
| `DdaCidNlCandidateSetRelationRef` | One scan related conservatively to one or more boundary/touching/overlapping candidates; set-level only, zero individual votes and zero hypotheses |
| `CidNlChromatographicSupportRef` | One-or-more exact strict DDA scan relations aggregated as one physical-peak support event; diagnostic only, no hypothesis or counting authority |
| `CidNlMethodAssessmentRef` | Complete target-bound CID-NL attempt with a completed-search receipt plus orthogonal technical and method-result axes; child of the existing processing attempt |
| `PeakHypothesis` | One existing model: legacy Targeted working identity plus a bounded diagnostic owner-backed CID-NL composition; neither is canonical physical-observation identity |
| `peak_hypothesis_identity` | Same-owner content ID, exact attempt artifact, and binding validation for the bounded CID-NL composition; no selection or product authority |
| `CrossSamplePeakGroupHypothesis` | Cross-sample group identity unit |
| `CellObservationRef` | Minimal sample × existing-group closure over technical state, method result, optional observation/hypothesis, evidence/reasons, and optional owner-resolved human disposition; no value or writer fields |
| CID-NL Targeted/Untargeted reference projections | Two internal views that retain the same exact group/cell refs and explicitly carry no product authority |
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

## Issue #161 Package 3B Salvage Census

The read-only quarry is
`codex/package-3b-untargeted-widening-rebuild-157` at
`44c7601241d894a5b13cf470b6e47d7797f5184e`; the M1 activation base is
`621d3312e0510587b88189edb01e29fb4bacd64c`. The complete 150-path quarry diff
is classified by the following ordered rules, so no unlisted path falls
through implicitly:

| Disposition | Paths / ordered rule | Count | M1 treatment |
| --- | --- | ---: | --- |
| `candidate_for_adaptation` | exact paths `xic_extractor/alignment/hypothesis_owner_adapter.py` and `xic_extractor/alignment/untargeted_hypothesis_alignment.py` | 2 | Inspect dependency and authority closure before any reuse |
| `evidence_only` | Package 3B 7/28–7/29 plan files; all changed `docs/superpowers/validation/` paths; Package 3B/chromatographic/Untargeted test fixtures selected by `tests/(fixtures/package3b|test_(chrom_peak_anchor|chromatographic_quantitation|cid_nl_chromatographic|package3b|untargeted))`; matching chromatographic/Package 3B diagnostic tools; and all changed `xic_extractor/diagnostics/` paths | 54 | May inform tests or risk inventory; no code or policy import |
| `out_of_scope` | every remaining path in the exact base-to-quarry diff after the two rules above | 94 | No M1 mutation or reuse; this includes the oversized candidate-universe/control-plane path, governance, runtime, RAW canaries, quantitation, Backfill, oracle, output, and productization changes |

Only one source hunk was adapted. Source commit `1a654a08` introduced
`xic_extractor/alignment/hypothesis_owner_adapter.py`; M1 adapted its
workflow-neutral measured-hypothesis → `IdentityEvent` /
`SampleLocalMS1Owner` projection into the live
`alignment/hypothesis_owner_adapter.py`. The destination replaces caller-owned
method values with exact `CidNlMethodAssessmentRef` closure, verifies that the
hypothesis uses the assessment's individual relation set, and retains no
selection, Matrix, or writer authority. This hunk serves P4a/P16a, depends only
on live owner-backed hypothesis, sample identity, assessment, and Alignment
owner types, and is `adapted_with_live_contracts` rather than byte-copied.

`untargeted_hypothesis_alignment.py` remained only a candidate: M1 did not copy
it because its quarry dependency is the rejected oversized Untargeted universe.
No whole commit was cherry-picked. The census is an audit of possible salvage,
not activation evidence or permission to merge the quarry later.

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
