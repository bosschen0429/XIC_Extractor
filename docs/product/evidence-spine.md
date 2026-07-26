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

The next contract must resolve, rather than assume:

1. whether an existing peak/feature owner can expose the required immutable
   physical-observation reference, or whether existing owner-backed references
   should be composed without another owner;
2. which trace, query, boundary, integration, sample, and attempt facts belong
   to observation content versus generation provenance and applicability;
3. how a single-observation interpretation and a multi-observation Pt pattern
   express hypothesis composition without inheriting provider-specific fields;
4. which generation/evaluation changes invalidate an observation, hypothesis,
   evidence application, or selection decision.

No attempt binding, content boundary, invalidation rule, new observation ID, or
final owner is established here. The test-local comparison tuples are
characterization probes only.

## Surfaces

| Surface | Role |
| --- | --- |
| `EvidenceVector` / `EvidenceDecisionSemantics` | Shared typed evidence vocabulary |
| `CidNlEvidenceRef` | Attempt- and sample-bound immutable direct measured CID-NL observation for the no-write dual-projection tracer |
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
