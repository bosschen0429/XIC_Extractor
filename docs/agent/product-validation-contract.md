# Product Validation Contract

This file owns product-readiness language and LC-MS/MS evidence guardrails. The
full domain evidence contract remains in `docs/lc-msms-evidence-rules.md`.

## Read Routes

Choose by the claim being made. Ordinary regression verification does not
require reading readiness or expected-diff rules. Use each matching row
when a change affects more than one claim.

| Need | Read |
| --- | --- |
| Choose evidence for a refactor, reliability/performance change, or workflow edit | [Product And Validation Discipline](#product-and-validation-discipline), then [Testing](testing.md#read-routes) |
| Change a scientific decision or interpret method evidence | [Product And Validation Discipline](#product-and-validation-discipline) and [Domain Evidence Guardrails](#domain-evidence-guardrails) |
| Trace an intended change across acquisition, qualification, persistence, or publication | [Cross-Owner Expected Differences](#cross-owner-expected-differences) |
| Claim readiness or change selected values, counted detection, Matrix identity, output schema, or writer behavior | [Readiness Claims](#readiness-claims) and [Expected-Diff Contract](#expected-diff-contract) |

## Readiness Claims

Rewritten development notes become repo source-of-truth only after their stable
claims move to the correct owner. When converting a note into official
documentation:

- extract stable rules and remove local paths, command diaries, and sample-level
  investigation detail;
- state the validation tier behind the claim, using the Evidence labels defined
  in the [Product Topic Index](../product/README.md#shared-conventions);
- cite validation notes as evidence or provenance, never as authority;
- do not silently change selected peak, selected area, counted detection,
  Matrix identity, schema, CLI/config behavior, or replay semantics.

Candidate rows, diagnostic sidecars, quality explanations, round-trip oracles,
ISTD comparisons, and review packets are evidence or routing inputs. They never
grant writer authority; only the workflow writers described in
[Writer Transaction Authority](architecture-public-contracts.md#writer-transaction-authority)
publish values. No readiness word by itself grants Matrix, selected-value, or
counted-detection authority, and tests passing alone never establish production
readiness.

## Expected-Diff Contract

A change that can alter selected values, counted detections, Matrix identity,
output schema, CLI/config/HTML behavior, review/replay behavior, or writer
behavior records, in the PR body or another bounded review surface:

- the capability, its owner, and the domain decision owner;
- affected CLI, config, GUI, output, report, schema/version, and downstream
  consumer, and why a writer or renderer is not recomputing domain logic;
- behavior delta, required non-changes, affected rows/targets/samples, and an
  expected-diff artifact when product values or identity can change;
- synthetic/focused validation plus the method-appropriate real-data or manual
  review tier;
- replay/provenance and review/action audit trail;
- stop condition, rollback action, and residual risk.

A missing owner, absent expected diff for a product-value change, insufficient
evidence, or no rollback path stops the change.

## Product And Validation Discipline

- Classify work by its actual observable effect: scientific decisions, public
  behavior, reliability/performance, or documentation/workflow. Name the affected
  scientific owner when those decisions change. GUI usability, recovery, and
  maintainability work do not need to advance a scientific data type to be
  worthwhile.
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
- When validation work meets the boundary in `docs/architecture-contract.md`,
  the Issue binds the owner, consumer, expected delta, oracle, and stop rule;
  verify current structure with CBM and source. Use the documented runner for
  RAW execution and the method's actual acceptance check for its results. A
  diagnostic, method, or RAW label alone does not create another gate.
- Establish a migration seam or dual-write contract when a current migration
  needs it and its compatibility/rollback costs are justified. Speculative
  future architecture is not a prerequisite for an authorized local fix.
- CWT, WIS, local minima, curvature, derivative, and region-first logic are
  evidence or hypothesis sources. A phase touching them must declare one mode:
  audit-only, hypothesis enumeration, model-selection calibration, production
  candidate, or retirement.
- Science phases require independent domain evidence capable of disproving false
  confidence. Median RSD alone is not enough.
- Behavior-preserving refactors use the existing affected-output oracle;
  numerical parity is required when numerical results are at risk. Reuse valid
  evidence rather than building a separate baseline ritual for non-numerical
  changes. Intentional behavior changes name their expected difference.
- Documentation and diagnostic phases require consistency and reviewer
  readability; no numerical gate language applies.

## Cross-Owner Expected Differences

When a change crosses acquisition, qualification, persistence, or publication,
the existing expected-diff evidence distinguishes the affected observations,
qualification states, and consumer-visible outputs. For example, retaining a
measured peak for review does not imply NL support or Matrix eligibility.

Trace the smallest affected case through the real owner interfaces and report
both the intended change and the required non-changes. Use an existing regression
or artifact comparison when it already supplies that oracle. Add coverage only
for a reachable, stable gap under [Testing](testing.md), not every combination of
states or owners. Interacting changes need evidence from their combined head.

An explained difference is not automatically an accepted difference. Bind each
material loss to the current acceptance/trade-off; keep it unresolved when no
such decision or evidence exists. CI establishes regression coverage, not that
scientific or product trade-off.

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
