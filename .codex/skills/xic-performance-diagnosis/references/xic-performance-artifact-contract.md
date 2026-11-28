# XIC Performance Artifact Contract

This is the XIC-specific artifact, comparability, and parity reference
for `xic-performance-diagnosis`. It does not define product authority or a
universal benchmark threshold.

## Timing Source Priority

Prefer timing sources in this order:

1. Fresh stage timing from the exact command under discussion.
2. `timing.live.json` / heartbeat timing for a long foreground RAW run.
3. `timing.json` from the completed run.
4. Stage replay timing with matching input-artifact identity.
5. RAW locality or call-count summaries that explain elapsed time.
6. A profiler for the code region it actually covers.

When sources disagree on the top bottleneck, classify the result as
`inconclusive` and choose a narrower measurement.

When reporting elapsed time, put the producing state, workload, and timed
entry/exit scope beside the number. For Discovery/Alignment work, state whether
Discovery was rerun or reused, whether RAW acquisition was included or results
were replayed, and whether the number is one continuous wall time or a sum of
separate runs. Name excluded stages; reserve "end-to-end" for the stated scope.
Report a local speedup within its measured scope. A later stage optimization
does not update an earlier whole-run time without a corresponding observation.

## Narrow Artifact Inventory

Inspect or name the absence of only the artifacts needed for the decision:

- output root, exact command, completion evidence, and producing code state;
- `timing.json`, `timing.live.json`, or stage replay timing;
- run metadata, input hashes, config/preset, runtime/backend, worker budget,
  output level, and cache state when retained;
- RAW locality and extraction call-count summaries;
- profiler sidecars and their covered region;
- correctness evidence: public output parity, an approved expected diff, or a
  focused test result.

Use filename-driven discovery and targeted readers. A diagnostic sidecar is a
measurement input unless an active product contract gives it delivery authority.

## Comparability Proof

Before computing a baseline/candidate delta, compare these axes:

| Axis | Required decision |
| --- | --- |
| Workload | Same method/preset, sample universe, input scale, and user-visible timed scope. |
| Inputs | Matching artifact/RAW identities and hashes, or a documented reason the difference is irrelevant. |
| Producing state | Baseline and candidate code refs, dirty state, config, runtime, backend/DLL, and dependencies are known. |
| Intended change | The relevant code/config difference is isolated; unrelated differences are accounted for. |
| Execution | Same output level, enabled stages, worker budget, timeout, and completion condition unless that field is the measured change. |
| Cache | Cold and warm states are identified and compared within the same state. |
| Coverage | Timing sources cover the same stages with the same record semantics. |
| Aggregation | Both sides use the same external wall clock, non-overlapping total, maximum, or paired observation rule. |
| Decision metric | The selected metric and aggregation can answer the claimed end-to-end, cumulative-work, worst-case, tail, stage, or per-sample question. |
| Correctness | The same parity oracle is evaluated, or both sides are covered by one approved expected-diff contract. |

Return `comparable` only when every decision-relevant axis is supported or its
difference is the isolated optimization. Otherwise return `inconclusive` with
the smallest missing proof. A matching pipeline name or two complete timing
files is not enough. Matching metrics that do not answer the decision are still
`inconclusive`.

Timing records may contain nested stages such as a stage plus its
`.extract_xic` children. Record the aggregation rule and avoid presenting the
sum of nested spans as wall clock.

## Repetition And Noise

For expensive 8RAW/85RAW work, choose a design that can still falsify the claim:

- prefer paired per-sample or per-stage observations from the same sample set;
- use whole-run wall clock as context, not the only proof when one run per side
  cannot characterize noise;
- document why the available repetition is representative and what variability
  remains unknown;
- reduce to a representative bounded stage before repeating a full RAW run.

Do not adopt one fixed repetition count, CV cutoff, or minimum percentage as an
XIC-wide product rule. Apply these workflow verdicts in order:

- use `inconclusive` when comparability or the observed-noise basis is missing
  or unstable enough to prevent a candidate decision;
- use `rejected` when comparable paired evidence does not clear the observed
  noise for the chosen design;
- use `optimized` only when comparable evidence clears the observed noise and
  the correctness gate passes.

A measured delta may still be retained as diagnostic evidence. That retention
description does not replace or weaken the workflow verdict.

## Correctness Oracles

Choose the oracle before an edit or an accepted paired-performance claim:

- byte-identical public artifacts, or parity of the published Matrix;
- focused tests for the boundary the change touches (crop, fallback, worker
  ordering, replay reuse, cache invalidation, writer);
- the [Expected-Diff Contract](../../../../docs/agent/product-validation-contract.md#expected-diff-contract)
  for an intentional semantic change.

A failed or missing correctness oracle blocks `optimized`, regardless of elapsed
time or call-count improvement.

## Stop Conditions

Return `baseline_needed`, `inconclusive`, or `rejected` when the relevant
condition occurs:

- timing is stale, incomplete, or excludes the suspected stage;
- comparability proof is missing or cache/run conditions differ ambiguously;
- inputs, output roots, or artifact hashes drift unexpectedly;
- public outputs, ordering, identity, or statuses differ without an expected
  diff;
- the target bottleneck is unchanged even if another stage becomes faster;
- worker changes introduce nondeterminism;
- an exact-path smaller gate fails before a larger RAW gate;
- artifact discovery would require an unbounded output/RAW scan.
