# Product Validation Contract

This file owns product-readiness language and LC-MS/MS evidence guardrails. The
full domain evidence contract remains in `docs/lc-msms-evidence-rules.md`.

## Source-Of-Truth Boundaries

Rewritten development notes may become repo source-of-truth only after their
stable claims are routed to the correct owner. This file owns product-readiness
language and product-surface discipline; it does not own active maturity tier,
active lane, current writer counts, or promotion-packet status. Those live in
`docs/superpowers/validation/productization_status_index_v1.tsv`, and
`docs/superpowers/schemas/productization_authority_manifest.v1.json`.
Dependent canonical quantitative-effect status and its package/activation
binding live separately in
`docs/superpowers/schemas/quantitative_projection_authority_manifest.v1.json`;
that manifest does not add a ProductWriter scope.
The schema at
`docs/superpowers/schemas/productization_control_plane_schema.v3.json` owns the
machine field and allowed-value contract. The dated control-plane path is a
compatibility index only; its stable lookup route remains
`docs/superpowers/plans/2026-06-15-productization-control-plane.md`.

When converting a historical note into official documentation:

- extract stable rules and remove local paths, command diaries, and sample-level
  investigation detail;
- state the validation tier behind the claim;
- point product authority claims to the status index, authority
  manifest, activation artifact, or expected-diff artifact that actually carries
  the authority; cite validation notes only as evidence/provenance;
- do not silently change selected peak, selected area, counted detection,
  matrix authority, schema, CLI/config behavior, or replay semantics.

Candidate rows, diagnostic sidecars, quality explanations, round-trip oracles,
ISTD comparisons, and review packets are evidence or routing inputs. They do
not create ProductWriter or matrix-writing authority unless a separate
activation/export contract, expected-diff gate, and current structured lane entry
say so.

A dependent quantitative effect remains non-writing while its authority
manifest is `HOLD_PENDING_VALIDATION`. A future `ACTIVE` manifest would still
require exact owner-rehydrated membership in one existing registered writer
scope for every affected cell; it cannot add write rows, selected-peak authority,
or counted-detection authority. That transition requires a separately reviewed
authority change and cannot be inferred from diagnostic evidence or lane
readiness.

## Lane Readiness And Promotion Semantics

The persisted lane readiness-status vocabulary is:

| Status | Meaning |
| --- | --- |
| `diagnostic_only` | Runnable evidence or observability surface with no product authority. It needs a promote, kill, externalize, or missing-evidence exit rule. |
| `production_candidate` | A bounded behavior and proposed product diff have reviewable evidence, but activation is not implied. |
| `production_ready` | The named capability has passed its declared validation and public-contract gate. Writer authority still requires a registered manifest scope. |
| `parked` | Deliberately not being advanced until a named reopening condition is met. |
| `blocked` | A concrete missing oracle, contract, or prerequisite prevents safe promotion. |
| `frozen` | The current contract is intentionally held stable; changes require a new authority decision. |
| `out_of_scope` | The lane is intentionally excluded from the current product boundary. |

Only values allowed by the structured schema may appear in the current status
index. Historical planning labels such as `missing`, `partial_internal`,
`shadow_ready`, and `production_surface` are not persisted current-state values.
Their durable intent is carried by the evidence and public-surface gates below,
not by maintaining a second transition ladder.

Lane readiness status is distinct from the validation evidence status owned by
`docs/product/README.md`. It is also distinct from owner-scoped artifact
contract ceilings and compound gate verdicts: a typed artifact may enforce
`shadow_ready` with no product authority even though `shadow_ready` is forbidden
from the lane status index. Owner-specific tokens are interpreted only by their
producer and must not be parsed as a shared readiness ladder.

Promotion is evidence-driven, not an automatic adjacent-status transition:

1. Name the capability owner and current structured lane record.
2. Name the public surface and the exact behavior that changes and must not
   change.
3. Bind the proposed scope to domain evidence, expected-diff evidence, focused
   tests, replay/provenance, and the appropriate real-data or manual-review tier.
4. Prove downstream schema/output behavior and a rollback path.
5. Update structured state only in the product-authority change that has
   authority to do so.

No readiness word by itself grants ProductWriter, matrix, selected-value, or
counted-detection authority. Parked, blocked, diagnostic, frozen, and
out-of-scope lanes must not write; a production-ready non-writer remains a
non-writer unless the authority manifest registers a scope.

## Promotion Packet Contract

Every proposed promotion packet must record, in the PR body or another bounded
review surface:

- capability name, previous state, proposed state, and owner;
- affected CLI, config, GUI, output, report, schema/version, and downstream
  consumer;
- domain decision owner, accepted evidence, and why a writer or renderer is not
  recomputing domain logic;
- behavior delta, required non-changes, affected rows/targets/samples, and an
  expected-diff artifact when product values or identity can change;
- synthetic/focused validation plus the method-appropriate real-data or manual
  review tier;
- replay/provenance, manifest binding, and review/action audit trail;
- stop condition, rollback action, and residual risk.

Missing owner, unresolved authority, absent expected diff for a product-value
change, insufficient evidence, or no rollback path stops promotion. Tests
passing alone never establishes production readiness.

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
