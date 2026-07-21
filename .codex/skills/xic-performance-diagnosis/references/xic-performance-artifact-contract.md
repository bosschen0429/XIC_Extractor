# XIC Performance Artifact Contract

This is the XIC-specific artifact, comparability, call-cost, and parity reference
for `xic-performance-diagnosis`. It does not define product authority or a
universal benchmark threshold.

## Timing Source Priority

Prefer timing sources in this order:

1. Fresh stage timing from the exact command under discussion.
2. `timing.live.json` / heartbeat timing for a long foreground RAW run.
3. `timing.json` / preset summary timing.
4. Stage replay timing with matching input-artifact identity.
5. RAW locality or call-count summaries that explain elapsed time.
6. A profiler for the code region it actually covers.

When sources disagree on the top bottleneck, classify the result as
`inconclusive` and choose a narrower measurement.

## Narrow Artifact Inventory

Inspect or name the absence of only the artifacts needed for the decision:

- output root, exact command, completion evidence, and producing code state;
- `timing.json`, `timing.live.json`, preset summary, or stage replay timing;
- method manifest, run metadata, input hashes, config/preset, runtime/backend,
  worker budget, output level, and cache state when retained;
- RAW locality and extraction call-count summaries;
- profiler sidecars and their covered region;
- correctness evidence: public output hashes, matrix/review/identity parity,
  expected-diff packet, or focused test result.

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

For short bounded stages or commands:

- isolate fixture/setup time unless it is user-visible work;
- separate cold-cache and warm-cache observations;
- use warmups when the measured production path is normally warm;
- collect repeated samples, report median and dispersion, and treat p95 from a
  small sample only as descriptive;
- interleave baseline/candidate observations when thermal or background drift
  could bias sequential runs.

For expensive 8RAW/85RAW work, choose a design that can still falsify the claim:

- prefer paired per-sample or per-stage observations from the same sample set;
- use whole-run wall clock as context, not the only proof when one run per side
  cannot characterize noise;
- document why the available repetition is representative and what variability
  remains unknown;
- reduce to a representative bounded stage before repeating a full RAW run.

Do not adopt one fixed repetition count, CV cutoff, or minimum percentage as an
XIC-wide product rule. `optimized` requires an effect that clears the observed
noise for the chosen design; otherwise report the measured delta as
`inconclusive` or diagnostic evidence.

## Call-Cost Model

Classify the dominant cost and name what changes:

- RAW opens and Thermo DLL setup;
- XIC extraction requests, batches, and vendor chromatogram calls;
- repeated extraction over the same sample/window;
- per-chunk replay of matrix-only evidence;
- repeated large TSV/CSV scans;
- sorting, grouping, joining, and full-table memory pressure;
- peak finding, cropping, smoothing, correlation, or audit loops;
- matrix/report/overlay rendering;
- cache/index build cost versus reuse benefit;
- worker startup, scheduling, IPC, and deterministic ordering.

## Workload Class

- `8RAW`: lane-owned feedback/parity fixture before a named larger gate.
- `85RAW`: lane-owned stress oracle for wall clock and locality.
- `stage_replay`: targeted diagnosis, not readiness evidence by itself.
- `one_time_import`: simple batching/streaming often dominates index reuse.
- `repeated_modeling`: reusable indexes, manifests, and caches may repay setup.
- `production_preset`: requires public-output parity or an approved expected diff.
- `fast_mode_candidate`: opt-in alternate semantics; exact outputs remain default.

Missing baseline evidence closes at command shape, required artifacts, and the
decision the measurement can change.

## Correctness Oracles

Choose the oracle before an edit or an accepted paired-performance claim:

- byte-identical public artifacts or stable normalized hashes;
- published matrix, review queue, and matrix-identity parity;
- manifest/input SHA parity;
- workbook/report schema and public sheet parity;
- focused tests for crop boundaries, fallbacks, worker ordering, replay reuse,
  cache invalidation, and writers;
- an expected-diff packet for intentional semantic change.

A failed or missing correctness oracle blocks `optimized`, regardless of elapsed
time or call-count improvement.

## Exact-Safe Opportunities

Common candidates include:

- reuse of global summaries instead of per-chunk evidence regeneration;
- sample-local scan/RT/window caches with explicit lifetime;
- monotonic slice/search operations with defined fallbacks;
- keyed or targeted loads instead of repeated full-table reads;
- extraction batching by sample/window locality;
- deterministic worker-budget tuning;
- reuse of replay manifests and artifact hashes.

Shared mechanics move into a package owner only when the architecture contract
justifies reuse; method policy remains with its lane owner.

## Expected-Diff And Fast-Mode Evidence

For non-exact paths, bind the existing expected-diff owner and inspect:

- feature/family/sample coverage;
- missing/extra cells and status deltas;
- apex, boundary, and area drift;
- counted-detection, identity, confidence, and reason deltas;
- held/failed buckets;
- runtime and call-cost deltas;
- opt-in profile and rollback path.

Performance evidence does not make an alternate backend the default or activate
product writes.

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
