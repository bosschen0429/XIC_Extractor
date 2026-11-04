# Product Validation Contract

This file owns readiness claims, expected differences and validation discipline.
How evidence is interpreted is in [`docs/product/`](../product/README.md).

Ordinary regression verification does not need this file. Read it when a change
claims readiness, changes a scientific decision, or can change selected values,
counted detection, Matrix identity, output schema or writer behavior.

## Readiness Claims

A readiness claim states its validation tier with the Evidence labels in the
[Product Topic Index](../product/README.md#shared-conventions). Validation notes
are evidence, never authority.

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
- Keep audit and production gates separate. Sparse, extrapolated or
  low-coverage evidence stays review-only until a production policy exists.
- When manual EIC/MS2 review contradicts a diagnostic label, find whether the
  shared rule, the wording or the reviewed row is wrong. Fix the shared rule
  with regression tests; do not encode one-off sample or target exceptions.
- Use the documented runner for RAW execution and the method's actual
  acceptance check for its results. A diagnostic, method, or RAW label alone
  does not create another gate.
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
