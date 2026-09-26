# Peak Model Selection

Peak model selection defines the durable product invariants for
selected-hypothesis model selection over `PeakHypothesis`.

Legacy scoring remains a compatibility oracle while public outputs still
expose legacy confidence, reason, score breakdown, selected markers, or matrix
values. Raw score arithmetic is not future product truth. The successor policy
(`selected_hypothesis_model_selection`) can become product authority only
through parity or explicit expected-diff approval.

## Contract

- Legacy scoring coverage stays protected while public projections still
  expose legacy semantics.
- Legacy scoring coverage preserves compatibility behavior. Legacy scoring
  coverage does not declare score as product authority.
- The successor can replace legacy scoring only when parity holds or when an
  approved expected-diff record allows the change.
- Blocked and inconclusive states are no-switch states. A no-switch state does
  not permit a product-candidate change.
- Synthetic fixtures alone cannot authorize final-matrix behavior changes.

## Invariant Classes

| Fixture family | Legacy surface | Product invariant | Class | Successor handling |
| --- | --- | --- | --- | --- |
| clean single peak | `score_candidate`, `select_candidate_with_confidence` | One selected hypothesis, accepted decision, same confidence/reason | future_policy | parity |
| confidence rank | confidence ordering | Stronger confidence wins unless stricter RT/evidence gate overrides | future_policy | parity first |
| role-aware RT prior / ISTD | `ScoringContext.rt_prior`, `prefer_rt_prior_tiebreak` | RT is contextual support for paired ISTD/STD, not standalone veto | future_policy | parity first, expected-diff with approved record |
| strict selection RT | `strict_selection_rt` key path | Strict RT request preserves selected RT behavior | future_policy | parity |
| final intensity tie-break | selection key fallback | Deterministic tie-break when evidence and RT are equal | compat_projection | parity while public |
| local S/N with AsLS | `local_sn_severity`, `baseline_array`, `residual_mad` | Local S/N is baseline-aware evidence, not linear-edge truth | future_policy | parity first |
| shape / width / morphology | severities and quality flags | Morphology contributes evidence and review reasons | future_policy | parity first |
| MS2 present / strict NL OK | evidence score support and caps | Candidate-aligned MS2/NL supports selected hypothesis | future_policy | parity under complete candidate evidence |
| no MS2 / NL fail | caps and reason | Missing DDA evidence is not observed by default; legacy not-counted projection remains public | future_policy | parity first |
| MS2 trace tie-break | `ms2_trace_*` support | Trace supports but cannot single-handedly override contextual selection | future_policy | parity first |
| CWT same-apex support | `cwt_same_apex_support` | CWT is support/context, not standalone authority | future_policy | parity first |
| low-scan demotion | `_low_scan_demotion_ids` | Low scan is evidence/quality concern, not hidden scoring truth | retirement_candidate | expected-diff after review |
| dominant strict-NL alternative | `_dominant_area_demotion_ids` | Dominant alternative must be explained by multi-evidence semantics | retirement_candidate | expected-diff after review |
| ADAP-like quality flags | quality and selection penalties | Quality flags project to morphology/evidence concerns | future_policy | parity first |
| stale final result fallback | `selection_decision_from_hypothesis(..., peak_result=...)` | Public fallback confidence/reason remains stable while exposed | compat_projection | parity |
| no-candidate / no-peak fallback | `PeakDetectionResult.status` | No product switch without a selected hypothesis | future_policy | parity |

## Surfaces

| Surface | Role |
| --- | --- |
| `xic_extractor.peak_detection.model_selection` | Model selection module |
| `xic_extractor.evidence_semantics` | Evidence semantics module |
| Candidate-table / workbook projections | Selected marker, confidence, reason, score breakdown, matrix value |
| Parity / blocked-diff / expected-diff tests | Behavioral locks on public output |

## Boundaries

- **Owns:** model-selection policy, invariant class definitions, parity and expected-diff gates between legacy and successor scoring.
- **Does not own:** selected-candidate switching in product outputs, workbook/TSV/GUI/ProductWriter behavior, or current parity gate implementation details.
- Legacy scoring coverage stays in place until a successor invariant covers the same public behavior.

## Verification

- Invariant classes above must remain represented by tests.
- Legacy scoring coverage is protected in: `tests/test_peak_scoring.py`, `tests/test_peak_scoring_selection.py`, `tests/test_peak_scoring_evidence.py`, `tests/test_scoring_context.py`.
- Expected-diff approval must include row identity, touched public outputs, validation tier, reviewer verdict, and matrix impact.
- Synthetic fixtures alone cannot authorize final-matrix behavior changes.
- Missing legacy selection, parity mismatch, blocked diff, and inconclusive states cannot product-switch.

## Pitfalls

- Deleting legacy scoring tests before a successor invariant covers the same public behavior.
- Treating stronger successor confidence as product authority without expected-diff approval.
- Treating raw score, score caps, or compatibility confidence as product truth.
- Allowing matrix-value changes from synthetic-only evidence.
- Treating CWT, trace, RT prior, or morphology as standalone authority.

## See Also

- [Decision policy](decision-policy.md)
- [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md)
- [Evidence spine](evidence-spine.md) | [Peak anchor and group boundary](family-hypothesis-boundary.md) | [Targeted selection](targeted-selection.md) | [Quantitation context](quantitation-context.md)
- [`tests/test_peak_model_selection.py`](../../tests/test_peak_model_selection.py)
