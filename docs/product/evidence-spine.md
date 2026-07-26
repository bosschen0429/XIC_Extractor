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
  belongs to `CrossSamplePeakGroupHypothesis`, candidate-peak identity belongs
  to `PeakHypothesis`, and product authority belongs to workflow projection.

## Surfaces

| Surface | Role |
| --- | --- |
| `EvidenceVector` / `EvidenceDecisionSemantics` | Shared typed evidence vocabulary |
| `CidNlEvidenceRef` | Attempt- and sample-bound immutable direct measured CID-NL observation for the no-write dual-projection tracer |
| `PeakHypothesis` | Candidate chromatographic identity unit |
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
