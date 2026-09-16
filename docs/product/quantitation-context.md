# Quantitation Context

Quantitation context is the RT and trace context used to integrate the selected peak. It is distinct from the narrow candidate proposal interval and from the full search window. The context margin allows bounded expansion when a candidate interval clips real peak flanks.

## Contract

- The selected peak apex or hypothesis anchors quantitation context.
- Candidate MS1 peak boundaries may be padded by a bounded quantitation-context margin when the candidate interval clips real peak flanks.
- The current product-candidate margin is capped at **0.40 min** and by the configured RT window limit. This is a candidate rule, not a universal science claim.
- CWT, local minima, safe-merge, derivative, region-first logic, and morphology facts are proposal or model-selection evidence. No single evidence family silently becomes final integration authority.
- Selected-full-envelope evidence remains diagnostic/review context unless a later activation contract promotes it.
- A `review_only / split_supported / externalize` expansion keeps the selected resolver integration and adds boundary review. It does not by itself make that original integration non-counted; existing identity, quality, and unresolved-context exclusions still apply.

## Surfaces

| Surface | Role |
| --- | --- |
| Candidate boundary | Proposal interval and evidence context |
| Quantitation-context margin | Bounded candidate expansion policy |
| `ChromPeakSegment` | Scoped product-candidate boundary unit |
| Gaussian15 morphology facts | Shape/area evidence and current area-owner context |
| Integration result | Selected trace area and provenance |
| Expected-diff packet | Gate before area-impacting changes |

## Boundaries

- **Owns:** bounded margin expansion policy, candidate-to-integration-context mapping, `ChromPeakSegment` boundary semantics.
- **Does not own:** the selected area for any specific row, whether a candidate segment is production-ready, Backfill writer authority, or matrix activation.
- The discovery candidate interval is not the whole final trace context; the full search window is not the quantitation context either.

## Verification

- Focused tests for boundary padding, RT limits, and fallback behavior.
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
