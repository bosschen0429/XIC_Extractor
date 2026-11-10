# Product Validation Contract

This file owns readiness claims, expected differences and validation discipline.
How evidence is interpreted is in [`docs/product/`](../product/README.md).

Ordinary regression verification does not need this file. Read it when a change
claims readiness, changes a scientific decision, or can change selected values,
counted detection, Matrix identity, output schema or writer behavior.

## Evidence Labels

Each label says what a run or comparison established. A label describes
evidence; it grants no authority.

| Label | Meaning |
| --- | --- |
| `diagnostic_only` | Measured or checked; changes no product behavior |
| `expected_diff` | A reviewed, intended change in product output |
| `production_ready` | Validated for production on its stated scope |
| `inconclusive` | The evidence did not establish a stronger claim |

## Readiness Claims

A readiness claim states its [evidence label](#evidence-labels). Validation notes
are evidence, never authority.

Candidate rows, diagnostic sidecars, quality explanations, round-trip oracles,
ISTD comparisons, and review packets are evidence or routing inputs. They never
grant writer authority; only the workflow writers described in
[Writer Transaction Authority](../architecture-contract.md#writer-transaction-authority)
publish values. No readiness word by itself grants Matrix, selected-value, or
counted-detection authority, and tests passing alone never establish production
readiness.

## Expected-Diff Contract

A change that can alter selected values, counted detections, Matrix identity,
output schema, CLI/config/HTML behavior or writer behavior records, in the PR
body:

- what changes, and for which outputs, rows, targets or samples;
- what must not change;
- how both were verified, with synthetic and real data named separately;
- how to roll it back, and the remaining risk.

A product-value change without an expected diff, or without a way back, stops.

## Product And Validation Discipline

- Treat 8RAW and 85RAW as validation fixtures, not product boundaries.
  Architecture stays dataset-agnostic.
- Choose validation data by Discovery method and lane. CID-NL 8RAW/85RAW
  fixtures are not default science gates for Pt pattern, HCD-PI, Delta Mass or
  future evidence providers unless the active owner names them.
- CID-NL, HCD-PI, Delta Mass, RT/iRT, MS1 pattern, shape, standards, library
  matches and learned models are evidence providers. None becomes permanent
  matrix-writing authority without an explicit activation contract.
- When manual EIC/MS2 review contradicts a diagnostic label, find whether the
  shared rule, the wording or the reviewed row is wrong. Fix the shared rule
  with regression tests; do not encode one-off sample or target exceptions.
- Behavior-preserving refactors use the existing affected-output oracle;
  numerical parity is required when numerical results are at risk. Intentional
  behavior changes name their expected difference.

## Cross-Owner Expected Differences

When a change crosses acquisition, qualification, persistence or publication,
trace the smallest affected case through the real interfaces and report both
the intended change and the required non-changes. Interacting changes need
evidence from their combined head.

An explained difference is not automatically an accepted difference. Bind each
material loss to the current acceptance or trade-off; keep it unresolved when no
such decision exists. CI establishes regression coverage, not that trade-off.
