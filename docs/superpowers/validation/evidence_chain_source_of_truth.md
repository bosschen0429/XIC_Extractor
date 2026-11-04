# Evidence Chain Reading Map

This is a reading map, not another evidence policy or live status board.
The explanatory document is `diagnostic_only`; it grants no writer authority.
The linked owners define vocabulary, scientific interpretation, method
decisions, and output behavior. Exact schema fields and enums belong to their
executable owners rather than a second inventory here.

## Evidence, decision, and output

1. **Evidence** records observations and their limits: a physical peak, an MS2
   relation, a trace, or RT context. Similarity or signal alone does not prove
   identity, membership, or a writable quantitative value.
2. **Decision** applies the relevant method and workflow contract. A measured
   peak may still be ambiguous, unsupported, or unsuitable for quantitation.
3. **Output** consumes the completed authorized decision. Rendering a number
   does not repeat peak selection, membership inference, or integration.

Ordinary live projection and an explicit diagnostic activation are different
paths. Do not require a file-activation packet for every ordinary output, or
treat a passing diagnostic replay as permission to change that output.

## Find the owning contract

| Question | Owner |
| --- | --- |
| What does a domain term mean? | [Domain Glossary](../../product/domain-glossary.md) |
| What can MS1, MS2/NL, RT, morphology, or ISTD evidence establish? | [LC-MS/MS Evidence Rules](../../lc-msms-evidence-rules.md) |
| How are observations, hypotheses, method assessments, and references represented? | [Discovery](../../product/discovery.md#cid-nl-support-relations) |
| How are sample-local candidates obtained? | [Discovery](../../product/discovery.md) |
| Which observations correspond across samples? | [Alignment](../../product/alignment.md) |
| What are group identity, disposition, and lineage? | [Alignment](../../product/alignment.md#group-identity-and-disposition) |
| How are missing observations searched and accepted? | [Backfill](../../product/backfill.md) |
| How do Targeted selection, confidence, and counted detection relate? | [Targeted Selection](../../product/targeted-selection.md) and [Decision Policy](../../product/decision-policy.md) |
| How do completed cells become Matrix values and rows? | [Quant Matrix](../../product/quant-matrix.md) |
| What grants product-writing authority? | [Writer authority](../../product/productization.md#writer-authority) |
| Which inputs and settings produced a Targeted result? | [Targeted workbook](../../product/targeted-workbook.md#run-metadata) |
| How is retained evidence inspected? | [Workbench and review](../../product/workbench.md) and the [Diagnostics Index](../../../tools/diagnostics/INDEX.md) |

A boundary reminder may appear at a consuming entry point, but the detailed
rule stays with its owner. This map changes when navigation or ownership changes,
not whenever a provider field, threshold, or implementation changes.

## Readiness and authority

[Productization](../../product/productization.md) locates the current structured
status index, writer-scope manifest, and activation contracts.
[Product Topic Index](../../product/README.md#shared-conventions) owns Evidence
labels. [Product Validation](../../agent/product-validation-contract.md) owns
method-appropriate validation requirements. Neither this map nor its
[HTML reading guide](evidence_chain_status_guide.html) replaces those surfaces.

- Live Alignment cell decisions and finalized workflow projections own ordinary
  Matrix values.
- A separately proposed activation or review mutation follows its own current
  contract, expected-diff/provenance requirements, and authority scope.
- Candidate rows, diagnostic scores, overlays, sealed machine calls, and human
  labels do not independently grant detection, scientific truth, or publication.
- Failed execution, missing artifacts, and incomplete acquisition opportunity
  must not be relabeled as scientific absence or measured zero.
- Report the layer when using words such as `ambiguous`, `rescue`, `accepted`, or
  `authority`. A diagnostic/provider status is not automatically a product state.

## Evidence records

Use the [Diagnostic Ledger](../../diagnostic-ledger.md) for retained conclusions,
producing code/data scope, and rerun conditions. Dataset queues, command diaries,
rendered packets, and large generated outputs are not a second product contract.
A retained record remains non-authoritative unless the relevant owner consumes
it for a bounded decision.
