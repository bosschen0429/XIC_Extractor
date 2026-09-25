# Quantitation Context

Quantitation context is the acquired RT and trace context available to the
shared physical peak owner. A candidate proposal interval can be narrower than
this context. The selected physical component, rather than the entire search
window, supplies the integration bounds.

## Contract

- The acquired trace, configured signal profile, request context and physical
  question identify the shared evaluation. Targeted selection chooses among
  its physical components and retains the selected evaluation through capture.
- The shared owner determines whether the observed context supports complete
  peak bounds. Missing or clipped support keeps its explicit unavailable reason;
  a downstream adapter must not invent flanks or integrate a replacement region.
- Targeted NL acquisition can restore the configured target window once when a
  focused query clips the peak. The new acquired trace is evaluated by the same
  physical owner; the writer does not extend bounds.
- CWT, local minima, derivative, and alternate boundary proposals remain
  diagnostic or model-selection evidence. They do not replace the retained
  physical integration.
- Selected-full-envelope evidence remains diagnostic/review context unless a later activation contract promotes it.
- A boundary review annotation does not itself replace the physical integration
  or establish chemical absence. Workflow identity and counting decisions keep
  their existing owners.

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
- The discovery candidate interval is not the whole final trace context; the full search window is not the quantitation context either.

## Verification

- Focused tests for acquired context, RT limits, retained evaluation reuse and unavailable reasons.
- Plot-backed or row-level evidence for clipped-flank fixes and no over-merge.
- Expected-diff packet for selected area, workbook, or matrix changes.
- RAW-tier validation matching the product claim.
- Evidence-rule update if the active area owner changes.

## Pitfalls

- Treating the discovery candidate interval as the whole final trace context.
- Integrating every positive residual in a broad RT context.
- Letting safe-merge, local-minimum, CWT, or derivative signals become hidden product authority.
- Treating an 8RAW candidate result as broad production readiness.

## See Also

- [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md)
- [Targeted selection](targeted-selection.md) | [Evidence spine](evidence-spine.md) | [Quant matrix](quant-matrix.md)
- [Peak model selection](peak-model-selection.md)
