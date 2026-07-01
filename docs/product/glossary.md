# Glossary

Quick-reference definitions for terms used across product topic pages. If a
term is not listed here, it is either a standard LC-MS concept or
self-explanatory from context.

## Product Governance

| Term | Definition |
| --- | --- |
| **ProductWriter** | The exclusive authorized code path for writing values into product matrices. All matrix mutations require explicit ProductWriter authority. |
| **authority manifest** | Machine-checkable JSON file listing exactly which ProductWriter scopes are approved and what cells have write authority. |
| **maturity tier** | Classification of a capability's validation readiness: `diagnostic_only` → `shadow_ready` → `production_candidate` → `production_ready`. |
| **activation contract** | Explicit specification that must be satisfied before diagnostic or shadow evidence can be promoted to write product values. |
| **expected-diff** | A formal review gate that documents expected behavioral changes before modifying production outputs. Must be approved before value-changing updates. |
| **parity** | Requirement that a new implementation must produce identical outputs to the legacy implementation before it can replace it in production. |
| **promotion gate** | A set of conditions (tests, expected-diff, authority approval) that must pass before a capability advances to a higher maturity tier. |
| **active lane** | The current priority development area tracked in the productization control plane, with WIP (work-in-progress) capacity limits. |

## Evidence and Analysis

| Term | Definition |
| --- | --- |
| **sidecar** | An accompanying metadata file (TSV/JSON) that provides auditable context for a primary output. Sidecars explain decisions but do not hold write authority. |
| **provenance** | Complete record of inputs, settings, runtime, and decision path for a run, enabling replay and audit without relying on branch-local command diaries. |
| **seed evidence** | Initial feature indicators (MS2 neutral loss, precursor inference, MS1 traces) that trigger candidate generation in discovery. |
| **family** | Legacy review/search container for related candidate or aligned evidence. Family membership is not same-peak proof or product authority by itself. |
| **cross-sample group hypothesis** | Successor identity unit for aligned cross-sample owner groups, exposed as `CrossSamplePeakGroupHypothesis` / `group_hypothesis_id`. |
| **selected hypothesis** | The chosen peak candidate from multiple alternatives. This is the product-authoritative decision about which chromatographic peak represents the analyte. |
| **canonical projection** | The single authoritative mapping from shared evidence to a workflow-specific product output decision (e.g., targeted projection, alignment projection). |
| **lockbox** | Structured evidence-acquisition artifact containing human-reviewed peak-choice labels used for validation truth. |
| **morphology** | Peak shape characteristics (width, symmetry, quality flags) used as evidence for peak selection and area integration. |
| **evidence-chain packet** | Structured package of source evidence organized for review or approval in the Backfill promotion workflow. |
| **RAW-tier** | Classification of validation depth based on dataset size: synthetic, 8RAW (small batch), 85RAW (full batch). A claim at one tier cannot assert readiness at a higher tier. |

## LC-MS/MS Domain

| Term | Definition |
| --- | --- |
| **XIC** | Extracted-ion chromatogram — the signal intensity over time for a specific m/z value extracted from a full-scan LC-MS run. |
| **CID-NL** | Collision-Induced Dissociation Neutral Loss — a fragmentation pattern used as seed evidence for untargeted feature discovery. |
| **Delta Mass** | MS1 delta-pattern Discovery strategy family. The first planned built-in pattern is `pt` / `Pt pattern`; `pt_pattern` is a method or preset label, not a separate primitive strategy. |
| **Pt pattern** | First planned built-in `delta_mass` pattern for Pt-related MS1 feature discovery. It creates candidates and review evidence, not direct Matrix write authority. |
| **HCD** | Higher-energy Collision Dissociation — a fragmentation technique whose product-ion audit serves as a review surface (not an automatic gate). |
| **DDA** | Data-Dependent Acquisition — MS2 scan trigger mode where fragmentation events depend on MS1 signal intensity. |
| **ISTD** | Internal Standard — a spiked reference compound used for RT calibration and paired area ratios in targeted workflows. |
| **NL** | Neutral Loss — the mass difference between a precursor ion and a product ion after fragmentation. Used for compound confirmation. |

## Key Code Identifiers

| Identifier | What it is |
| --- | --- |
| `EvidenceVector` / `EvidenceDecisionSemantics` | Shared typed evidence vocabulary consumed by both targeted and untargeted pipelines. |
| `PeakHypothesis` | Domain object representing a candidate chromatographic peak with its evidence context. |
| `CrossSamplePeakGroupHypothesis` | Domain object representing cross-sample owner/group identity before product projection. |
| `IntegrationResult` | Domain object holding the integrated signal area and boundary result for a selected peak. |
| `ChromPeakSegment` | Scoped product-candidate boundary unit used in quantitation context decisions. |
| `sample_metadata_v1` | Schema for sample identity, injection order, role, batch, and QC context. |
| `review_action_v1` | Typed action import schema for converting human review decisions into machine-checkable plans. |
| `method_manifest.json` | Machine-readable run envelope (input hashes, settings, runtime, CLI args, output pointers). |
| `validation-minimal` | Standard lightweight alignment validation profile. Not sufficient alone for production-readiness claims. |
