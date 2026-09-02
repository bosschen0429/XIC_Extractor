---
name: xic-performance-diagnosis
description: Diagnose XIC performance from timing and parity artifacts before optimization. Use for missing baselines, single-run bottlenecks, baseline/candidate proof, RAW/XIC locality, worker budgets, regressions, or fast-mode review.
---

# XIC Performance Diagnosis

Treat each optimization as an artifact-driven, falsifiable claim. Produce one
diagnosis packet before issue-bounded code edits. Route RAW execution or
artifact acceptance to `xic-raw-validation`.

## 1. Bind The Decision And Evidence Branch

Name the decision, workload/sample universe, suspected surface, and one branch:

- `baseline_needed`: no trustworthy timing covers the decision;
- `single_run`: rank bottlenecks in one completed run;
- `paired_comparison`: judge baseline and candidate artifacts.

Locate the narrow artifact roots and record availability, completeness,
freshness, producing code, config, input, and backend identity. For a pair,
state the intended isolated change and every unrelated difference found. Search
`tools/diagnostics/INDEX.md` and `docs/diagnostic-ledger.md` before proposing a
new measurement path. Read
`docs/agent/parameter-settings.md#alignment-and-handoff` for the current
RAW/preset command and 85RAW launch/timing constraints.

Completion criterion: one branch is selected; every run has a workload,
artifact state, timing coverage, and producing state or an explicit `unknown`.

## 2. Prove The Timing Basis

Read `references/xic-performance-artifact-contract.md` for timing-source
priority and the XIC artifact inventory. A `single_run` must cover the suspected
stage. When baseline and candidate artifacts are both present, read the
reference's **Comparability Proof** before computing any delta.

Classify paired evidence as `comparable` or `inconclusive`. Two `timing.json`
files alone establish neither equivalent workload nor equivalent measurement.
If the proof is missing, name the smallest provenance, parity, or bounded timing
probe that would close it; this diagnosis does not widen into a RAW run by
itself.

Completion criterion: the timing basis is trustworthy for the selected branch,
or exactly one smallest missing proof is named and the result is
`baseline_needed`/`inconclusive`.

## 3. Rank The Bottleneck And Cost Delta

Use recorded summaries before recomputing metrics:

- `summaries.stage_summary` for stage coverage and elapsed cost;
- `summaries.raw_xic_locality_summary` for extraction, batching, RAW-call, and
  point-count evidence.

State the aggregation basis: external wall clock, non-overlapping stage total,
stage maximum, or paired per-sample/per-stage observations. Never sum nested
spans as if they were independent wall clock. Explain why the chosen metric
answers the decision: a stage maximum can answer a worst-case-stage question,
but not cumulative work or end-to-end wall clock.

For `single_run`, rank the top bottleneck. For `paired_comparison`, report the
target-stage and call-cost deltas only after comparability passes, then name
which cost was removed, reduced, batched, cached, or parallelized.

Completion criterion: the top bottleneck and every claimed delta name their
timing source, covered stages, decision metric, aggregation semantics, and XIC
call-cost evidence.

For `baseline_needed` or a `single_run` whose only decision is bottleneck
diagnosis, issue the Core Diagnosis Packet in Step 5 and stop. Continue when the
task asks for paired proof, implementation readiness, or acceptance.

## 4. Bind Correctness And Optimization Lane When Needed

For `paired_comparison`, select a project-appropriate parity or expected-diff
oracle before accepting a performance claim.

When the task prepares implementation or product acceptance, also state whether
public outputs, selected peak/area, counted detections, reasons/confidence,
matrix identity, schema, or persisted IDs are unchanged or covered by an
approved expected diff, then choose one lane:

- `exact_safe`: output-preserving removal, batching, caching, locality, or
  deterministic worker-budget change;
- `expected_diff`: an intentional selected-value, identity, schema, or status
  change with an expected-diff owner;
- `fast_mode_candidate`: explicit opt-in alternate semantics that leave exact
  defaults intact;
- `diagnostic_only`: measurement or analysis without product behavior change.

Keep performance proof and product validation status separate. A measured win
can remain `diagnostic_only`; tests or speed alone do not grant activation,
matrix, selected-value, workbook, or export authority.

Completion criterion: paired proof has separate correctness and performance
verdicts; implementation/acceptance work additionally has one lane, validation
gate, authority ceiling, and rollback/stop condition.

## 5. Issue One Diagnosis Packet

Use one workflow verdict:

- `baseline_needed`, `diagnosis_ready`, or `optimization_ready` before edits;
- `optimized` only after comparable evidence beats observed noise and the
  correctness gate passes;
- `inconclusive` for missing comparability, unstable timing, or absent parity
  that prevents a candidate decision;
- `rejected` for a comparable candidate within observed noise, an attributable
  regression, or a failed correctness gate.

`Candidate code` means unaccepted code for the optimization under decision. Use
this complete disposition table:

| Workflow verdict | No candidate code | Candidate code exists |
| --- | --- | --- |
| `baseline_needed` | `not_applicable` | `revert` |
| `diagnosis_ready` | `not_applicable` | `revert` |
| `optimization_ready` | `not_applicable` | `revert` |
| `inconclusive` | `not_applicable` | `revert` |
| `rejected` | `not_applicable` | `revert` |
| `optimized` | `not_applicable` | `keep` |

Pre-edit verdicts do not preserve prematurely written candidate code.
`optimization_ready` opens a fresh code-edit handoff; it does not accept an
existing candidate. Missing evidence also does not create a keep-for-later
state.

Choose the diagnostic-ledger action independently:

| Durable conclusion from this attempt | Applicable ledger conclusion exists | Action |
| --- | --- | --- |
| changes or prevents a known repeat | either | `update` |
| no change | yes | `reuse` |
| no change | no | `none` |

The existing diagnostic ledger stays the only durable performance-conclusion
owner.

Every branch returns this core:

```markdown
Workflow/performance verdict:
Candidate disposition: keep | revert | not_applicable
Diagnostic-ledger action: reuse | update | none
Product validation status:
Workload / sample universe / decision:
Evidence branch and artifact roots:
Artifact availability / completeness / freshness:
Timing source / covered stages / aggregation basis:
Top bottleneck:
Call-cost interpretation:
Next evidence / stop rule:
```

Add for `paired_comparison`:

```markdown
Producing state / intended isolated change / unrelated differences:
Comparability verdict / blocker / smallest missing proof:
Decision metric / why it answers the claim:
Baseline/candidate timing and call-cost delta:
Noise/repetition evidence:
Correctness oracle / result:
```

Add when preparing implementation or acceptance:

```markdown
Optimization lane / authority ceiling:
Validation gate / rollback:
Residual risk:
```

Performance evidence alone leaves product validation status unchanged; use the
canonical repo vocabulary for that separate field.

Only `optimization_ready` opens issue-bounded code edits. Route actual RAW
launch or artifact acceptance to `xic-raw-validation`. Consult the
productization status index, authority
manifest, and promotion contract only when the evidence could change a maturity
tier, active lane, ProductWriter authority, or product gate state.

Completion criterion: every packet field has observed evidence, a reasoned
`N/A`, or an explicit unknown for the selected branch; claims remain
source-qualified and the handoff cannot exceed the stated authority ceiling.

Read `evals/trigger-cases.md` only when tuning skill routing.
Read `evals/pressure-cases.md` only when testing candidate disposition under
sunk-cost or deadline pressure. These prose fixtures are not behavioral
automation and do not change skill routing.
