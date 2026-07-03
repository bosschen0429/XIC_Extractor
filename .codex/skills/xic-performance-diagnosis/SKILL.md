---
name: xic-performance-diagnosis
description: Performance diagnosis before XIC preset/runtime optimization, timing analysis, RAW locality, worker-budget tuning, baseline triage, or fast-mode review.
---

# XIC Performance Diagnosis

Use this before planning or implementing non-trivial XIC performance work. This
skill turns performance work into an artifact-driven gate instead of a general
"make it faster" coding task.

Routing order:

1. Use this skill to decide whether baseline evidence exists, rank bottlenecks,
   and choose `exact_safe`, `expected_diff`, `fast_mode_candidate`, or
   `diagnostic_only`.
2. Use `xic-architecture-preflight` before code edits.
3. Use `xic-raw-validation` before launching or accepting RAW-backed validation.
4. Read or update the productization control plane only when evidence could
   change maturity tier, active lane, ProductWriter authority, or product gate
   state.

## First Response Protocol

Before proposing code changes, produce this compact block:

```markdown
Workload:
Decision this diagnosis can close:
Existing artifacts inspected:
Baseline command/status:
Timing source of truth:
Top bottlenecks:
Call-cost interpretation:
Correctness oracle:
Optimization lane:
Validation gate:
Stop rule:
Next action:
```

If timing artifacts are missing, the next action is a baseline or targeted stage
measurement, not implementation.

## Hard Gate

Do not edit performance-sensitive code until these are named:

- timing source of truth: `timing.json`, `timing.live.json`, stage replay timing,
  profiler output, or another explicit artifact;
- top bottleneck by wall-clock or validated call-cost model;
- workload class: 8RAW, 85RAW, targeted benchmark, no-RAW replay, stage replay,
  one-time import, repeated modeling run, or interactive path;
- correctness oracle: published matrix parity, review queue parity, identity
  sidecar parity, manifest hashes, focused tests, expected-diff packet, or
  declared `diagnostic_only`;
- stop rule for noisy timing, output mismatch, artifact ambiguity, or
  nondeterminism.

## Optimization Lanes

- `exact_safe`: output-preserving changes such as removing repeated RAW I/O,
  avoiding repeated TSV scans, reusing existing summaries, batching existing
  requests, searchsorted/slice-view crops with fallback, or worker-budget tuning
  with deterministic output.
- `expected_diff`: changes that may alter selected peak, area, counted
  detections, reason/confidence, matrix identity, workbook/report schema, or
  persisted identifiers. Require an expected-diff contract before implementation.
- `fast_mode_candidate`: explicit opt-in candidate using approximate/indexed
  backends or changed extraction semantics. Must not overwrite exact preset
  outputs or default behavior.
- `diagnostic_only`: measurement, replay, report, or artifact analysis that
  improves observability but does not change production behavior.

## Required Artifact Search

Search existing outputs and diagnostics before inventing a new measurement path:

- `timing.json` and `timing.live.json`;
- preset summary JSON and stage timing sidecars;
- RAW locality summaries and extraction call-count diagnostics;
- baseline vs optimized output folders;
- published matrix, review queue, identity sidecars, manifests, and product
  summary artifacts;
- `tools/diagnostics/INDEX.md`;
- `docs/diagnostic-ledger.md`.

Keep the search targeted. Do not recursively scan large `output/` or RAW
directories just to satisfy this checklist; first locate the candidate run folder
or sidecar names with `rg --files`, documented output paths, timing filenames, or
known preset labels.

## Report Rules

Lead with the verdict:

- `baseline_needed`: no trustworthy timing source exists.
- `diagnosis_ready`: bottleneck ranked; no code accepted yet.
- `optimization_ready`: lane, correctness oracle, validation gate, and stop rule
  are clear enough to edit.
- `optimized`: before/after timing improved and correctness gate passed.
- `inconclusive`: timing improved without parity, timing source is ambiguous, or
  run conditions are not comparable.
- `rejected`: optimization changed outputs unexpectedly or did not improve the
  target bottleneck.

State whether the conclusion is `diagnostic_only`, `production_candidate`, or
`production_ready`. Tests passing alone is not production readiness.

## References

Read `references/xic-performance-artifact-contract.md` for the detailed XIC
artifact, call-cost, validation, and stop-rule checklist.

Read `evals/trigger-cases.md` when tuning routing or deciding whether this skill
or a neighboring XIC skill should own a request.
