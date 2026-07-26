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
  neither projection artifact is a selection or delivery decision.
- The existing `peak_detection` owner now exposes a bounded
  `PeakObservationRef`. Its physical content is canonical sample identity, m/z,
  one contiguous selected RT/intensity subtrace, and its normalized
  `IntegrationResult`. Exact full-trace/query/resolver provenance and producer
  request/attempt remain explicit without becoming physical-content identity.
- For the lab's current Untargeted acquisition method, the CID-NL owner now
  exposes one bounded DDA/ddMS2 applicability relation. A
  `DdaCidNlPeakSupportRef` binds one exact MS2 scan and direct measured CID-NL
  artifact to exactly one physical MS1 peak within the exact supplied candidate
  universe. DIA is outside this contract because there is no mature lab DIA
  analysis method.
- Legacy family IDs are review and compatibility containers. Group identity
  belongs to `CrossSamplePeakGroupHypothesis`, while current candidate IDs and
  `PeakHypothesis` IDs remain workflow-local working identities. Neither is a
  canonical physical MS1-observation identity. Product authority belongs to
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
   contiguous selected RT/intensity points, peak bounds/apex, and all normalized
   `IntegrationResult` facts.
2. Full trace digest, query RT/ppm, trace source, source-relative indices,
   resolver mode, and proposal sources are generation provenance. Changing
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
   must resolve to that owner row, and integration RT bounds must map to the
   selected contiguous subtrace under the existing nearest-index/bounded-
   interval owner semantics. A caller cannot validate a bad mint by repeating
   the same mismatched sample or attempt.

This closes the first provider-specific evidence-to-observation relation, not
method-hypothesis composition. The DDA support owner requires one exact scan,
strict neutral-loss evidence, a unique in-scope RT/m/z match, and the complete
candidate tuple supplied to it. Reordering that tuple is identity-neutral;
mutation, substitution, missing candidates, no match, or multiple matches fail
closed. The artifact depends on both the CID-NL evidence and every supplied
candidate peak, so a caller cannot hide a non-selected candidate during later
validation.

Runtime candidate-universe completeness is not yet proven, and the current RAW
reader does not expose enough isolation/co-isolation metadata to make that a
contract. A provider-local Discovery `DetectedFeature` that lacks the
owner-required full `Trace` plus `IntegrationResult` still cannot mint a
substitute physical reference. The next contract must compose this relation
into a single-observation method hypothesis, then separately prove a
multi-observation Pt/Delta Mass composition, without promoting the current
Targeted-shaped `PeakHypothesis` into the physical owner.

## Surfaces

| Surface | Role |
| --- | --- |
| `EvidenceVector` / `EvidenceDecisionSemantics` | Shared typed evidence vocabulary |
| `CidNlEvidenceRef` | Attempt- and sample-bound immutable direct measured CID-NL observation for the no-write dual-projection tracer |
| `PeakObservationRef` | `peak_detection`-owned physical MS1 content plus exact generation provenance and request/attempt artifact binding; diagnostic only |
| `DdaCidNlPeakSupportRef` | CID-NL-owner DDA/ddMS2 scan-to-physical-peak relation over an exact supplied candidate universe; diagnostic only, no DIA or selection authority |
| `PeakHypothesis` | Current Targeted-shaped candidate working identity; not canonical physical observation identity |
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
