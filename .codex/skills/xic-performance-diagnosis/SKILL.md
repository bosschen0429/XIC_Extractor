---
name: xic-performance-diagnosis
description: Diagnose XIC performance from timing and parity artifacts before optimization. Use for missing timing baselines, bottlenecks, performance comparisons, RAW/XIC locality, worker budgets, performance regressions, or fast-mode review.
---

# XIC Performance Diagnosis

Use timing and correctness evidence to answer the current performance question.
Reuse a still-applicable diagnosis; no separate packet is required before edits.
Output-parity-only refactors without a timing or call-cost question use their
owning Issue and regression/parity checks, not this optimization workflow.

## Choose The Evidence Needed

- No trustworthy timing: identify the smallest baseline that covers the question.
- One completed run: rank its bottlenecks; it cannot prove an optimization win.
- Baseline and candidate: establish comparability before calculating a speedup.

Locate the relevant artifacts and producing code, config, inputs, and backend.
Search `tools/diagnostics/INDEX.md` and `docs/diagnostic-ledger.md` before adding
measurement machinery. Use `docs/agent/local-execution.md#alignment-and-handoff`
for RAW commands and preset limits; normal runner execution performs input checks.

Read `references/xic-performance-artifact-contract.md` for timing sources and,
for paired evidence, its Comparability Proof. Two timing files alone do not
prove equivalent workload or coverage. Name missing evidence when it prevents
the decision, and obtain the smallest useful probe within the task's scope.

Successive optimizations use the accepted cumulative baseline, including an
operator-selected validated prototype. Comparing with an older formal version
answers a separate integration question. Rollback covers only the current
unaccepted delta.

## Interpret Cost And Correctness

Prefer recorded `summaries.stage_summary` and
`summaries.raw_xic_locality_summary`. Identify the timing source, covered stages,
and aggregation basis. Never add nested spans as independent wall time. A stage
maximum answers a worst-case question; it does not measure cumulative work.

For a single run, report the top bottleneck and supporting call-cost evidence.
For a comparable pair, report the decision metric, timing delta, observed noise,
and cost removed, reduced, batched, cached, or parallelized. Select the relevant
parity or approved expected-diff oracle before accepting the candidate.

Keep correctness, performance, and product readiness separate. Identify changed
public outputs, selected values, counts, identities, or schemas when applicable:

- `exact_safe`: output-preserving cost or worker-budget changes.
- `expected_diff`: intentional semantic changes with an expected-diff owner.
- `fast_mode_candidate`: explicit opt-in alternate semantics preserving defaults.
- `diagnostic_only`: measurement without product behavior changes.

Tests or speed alone do not grant writer, Matrix, workbook, or export
authority. Consult [Writer Authority](../../../docs/product/decision-policy.md#writer-authority)
only when those decisions are actually in scope.

## Report The Decision

Give the conclusion, supporting artifact locators, correctness result when
applicable, and material uncertainty or next action. Use the relevant verdict:

- `baseline_needed`: trustworthy timing is missing.
- `diagnosis_ready`: the bottleneck question is answered.
- `optimization_ready`: evidence supports a bounded implementation.
- `optimized`: comparable evidence beats observed noise and correctness passes.
- `inconclusive`: comparability, timing stability, or parity is insufficient.
- `rejected`: a comparable candidate is within noise, regresses, or fails correctness.

Accept performance optimizations only with `optimized`. Otherwise retain them
unaccepted while closing bounded missing proof; a rejected candidate calls for rollback.
Disposition does not grant mutation authority: execute rollback only within
authorization, preserving unrelated work and the accepted baseline. A read-only
diagnosis reports the scoped action without changing files.

Update the diagnostic ledger only when a durable conclusion changes or prevents
a known repeat. Reuse an applicable conclusion; otherwise no entry is required.
The ledger remains the durable performance-conclusion owner.

Routing scenarios live in `evals/trigger-cases.md`; candidate-disposition
pressure scenarios live in `evals/pressure-cases.md`. These are manual fixtures,
not observed agent behavior or automated acceptance.
