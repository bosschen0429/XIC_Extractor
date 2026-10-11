# Quantitation Context

Quantitation context is the acquired RT and trace context available to the
shared physical peak owner. A candidate proposal interval can be narrower than
the quantitation context. The selected physical component supplies the
integration bounds. The entire search window does not supply them.

## Contract

- The acquired trace, configured signal profile, request context, and physical
  question identify the shared evaluation.
- Targeted selection chooses among the physical components of that evaluation.
  Targeted selection retains the selected evaluation through capture.
- The shared physical owner determines whether the observed context supports
  complete peak bounds.
- Supported lobes on the whole-trace envelope remain distinct when the valley
  persists for one canonical smoothing footprint at half its dip depth.
  Brief dropouts and unsupported ripple do not supply that separation.
- A persistent shallow valley supplies a component boundary while retaining
  overlap review. Its measured area is not deconvolved compound-specific area.
- Missing or clipped support keeps its explicit unavailable reason. A
  downstream adapter must not invent flanks or integrate a replacement region.
- If a focused query clips the peak, Targeted NL acquisition can restore the
  configured target window once.
- The same physical owner evaluates the newly acquired trace. The writer does
  not extend bounds.
- CWT, local-minimum, derivative, and alternate boundary proposals remain
  diagnostic or model-selection evidence. These proposals do not replace the
  retained physical integration.
- Selected-full-envelope evidence remains diagnostic/review context unless a
  later activation contract promotes it.
- A boundary review annotation does not by itself replace the physical
  integration or establish chemical absence.
- Workflow identity and counting decisions keep their existing owners.

## Surfaces

| Surface | Role |
| --- | --- |
| Candidate boundary | Proposal interval and evidence context |
| Acquired quantitation context | Observed support available to the physical owner |
| `ChromPeakSegment` | Scoped product-candidate boundary unit |
| Configured Gaussian/AsLS physical facts | Shared segment, shape and counts·seconds area |
| Integration result | Selected trace area and provenance |
| Expected-diff packet | Gate before area-impacting changes |

## Boundaries

- **Owns:** candidate-to-observed-context mapping and `ChromPeakSegment` boundary semantics.
- **Does not own:** the selected area for any specific row, whether a candidate segment is production-ready, Backfill writer authority, or matrix activation.
- The Discovery candidate interval is not the whole final trace context.
- The full search window is not the quantitation context.

## Verification

- Focused tests for acquired context, RT limits, retained evaluation reuse and unavailable reasons.
- Plot-backed or row-level evidence for clipped-flank fixes and no over-merge.
- Expected-diff packet for selected area, workbook, or matrix changes.
- RAW-tier validation matching the product claim.
- Evidence-rule update if the active area owner changes.

## Pitfalls

- Treating the Discovery candidate interval as the whole final trace context.
- Integrating every positive residual in a broad RT context.
- Letting safe-merge, local-minimum, CWT, or derivative signals become hidden product authority.
- Treating an 8RAW candidate result as broad production readiness.

## See Also

- [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md)
- [Targeted selection](targeted-selection.md) | [Evidence spine](evidence-spine.md) | [Quant matrix](quant-matrix.md)
