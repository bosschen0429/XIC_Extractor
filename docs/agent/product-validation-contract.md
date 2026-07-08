# Product Validation Contract

This file owns product-readiness language and LC-MS/MS evidence guardrails. The
full domain evidence contract remains in `docs/lc-msms-evidence-rules.md`.

## Source-Of-Truth Boundaries

Rewritten development notes may become repo source-of-truth only after their
stable claims are routed to the correct owner. This file owns product-readiness
language and product-surface discipline; it does not own active maturity tier,
active lane, current writer counts, or promotion-packet status. Those live in
`docs/superpowers/plans/2026-06-15-productization-control-plane.md`,
`docs/superpowers/validation/productization_status_index_v1.tsv`, and
`docs/superpowers/schemas/productization_authority_manifest.v1.json`.

When converting a historical note into official documentation:

- extract stable rules and remove local paths, command diaries, and sample-level
  investigation detail;
- state the validation tier behind the claim;
- point product authority claims to the control plane, status index, authority
  manifest, activation artifact, or expected-diff artifact that actually carries
  the authority; cite validation notes only as evidence/provenance;
- do not silently change selected peak, selected area, counted detection,
  matrix authority, schema, CLI/config behavior, or replay semantics.

Candidate rows, diagnostic sidecars, quality explanations, round-trip oracles,
ISTD comparisons, and review packets are evidence or routing inputs. They do
not create ProductWriter or matrix-writing authority unless a separate
activation/export contract, expected-diff gate, and current control-plane entry
say so.

## Product And Validation Discipline

- P-specs, C-specs, and implementation plans must state whether they advance
  `Trace` / `TraceGroup`, multi-source `PeakHypothesis`, `EvidenceVector`,
  `IntegrationResult`, model selection, or `AuditTrail`. If they advance none,
  label them cleanup-only.
- Treat 8RAW and 85RAW as validation fixtures and stress oracles, not product
  boundaries. Architecture must stay dataset-agnostic unless an approved
  product contract explicitly narrows the scope.
- Choose validation data by Discovery method and lane. CID-NL 8RAW/85RAW
  fixtures are not default science gates for Pt pattern, HCD-PI, Delta Mass, or
  future evidence providers unless the active owner explicitly names them.
- Treat CID-NL, HCD-PI, Delta Mass, RT/iRT, MS1 pattern, shape, standards,
  library matches, and future learned models as evidence providers. They feed
  `EvidenceVector` and model selection; they must not directly become permanent
  matrix-writing authority without an explicit activation/export contract.
- Diagnostic TSVs, shadow reports, wrappers, and sidecars prove observability,
  not product usability.
- Before non-trivial diagnostics, RAW-backed evidence, preset performance,
  matrix activation, or new evidence-provider work, run a reuse/call-cost
  preflight: existing owner/helper, spine layer, call-cost model, public
  contract risk, validation gate, and stop rule.
- Prefer establishing the future spine or dual-write contract before polishing
  legacy DTOs, resolver names, or scoring split points likely to move during
  handoff migration.
- CWT, WIS, local minima, curvature, derivative, and region-first logic are
  evidence or hypothesis sources. A phase touching them must declare one mode:
  audit-only, hypothesis enumeration, model-selection calibration, production
  candidate, or retirement.
- Science phases require independent domain evidence capable of disproving false
  confidence. Median RSD alone is not enough.
- Cleanup phases require numerical parity against the settled baseline; behavior
  changes relabel the phase.
- Engineering phases require characterization parity and maintainability gain;
  do not bundle behavior changes.
- Documentation and diagnostic phases require consistency and reviewer
  readability; no numerical gate language applies.

## Domain Evidence Guardrails

- Prefer evidence chains over single-metric authority. RT, CWT, WIS, iRT, local
  minima, RT models, shape similarity, product ions, neutral losses, adducts,
  and in-source fragments are evidence inputs, not silent vetoes.
- RT is contextual evidence. It must not prove analyte absence or override
  co-eluting, candidate-aligned MS1/MS2 evidence unless an explicit hard RT
  exclusion policy exists.
- Missing DDA MS2/product/NL evidence is `not_observed` by default. Treat it as
  negative evidence only when acquisition opportunity and comparable controls
  show it should have been observable.
- Clean standards can support audit, library, and instrument interpretation, but
  cannot alone justify production correction of biological matrices.
- Keep audit and production gates separate. Sparse, extrapolated, or low
  coverage evidence stays review-only until a production policy exists.
- Targeted outputs may be benchmarks or shared low-level evidence, but target
  labels and targeted pass/fail logic must not leak into untargeted production
  matrix identity without an approved contract.
