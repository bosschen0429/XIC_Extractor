---
name: xic-architecture-preflight
description: Preflight XIC changes that create or replace a semantic owner, cross an evidence/runtime boundary, or change Matrix, activation, public contracts, or scientific authority. Do not use for focused fixes inside an established owner, ordinary docs/tests, PR comment handling, or read-only explanation.
---

# XIC Architecture Preflight

Produce one pre-edit contract. Current repo architecture and product-validation
owners remain authoritative.

## 1. Falsify The Gap

Name the affected consumer, observable loss, and unresolved XIC evidence-spine
handoff: `Trace` / `TraceGroup`, `PeakHypothesis`, `EvidenceVector`, model
selection, `IntegrationResult`, `AuditTrail`, or an authorized writer.

Read `docs/architecture-contract.md` and
`docs/agent/product-validation-contract.md`. For diagnostics, search
`tools/diagnostics/INDEX.md`; for existing evidence or a proposed rerun, search
`docs/diagnostic-ledger.md` and the current artifact owner. Choose the cheapest
probe of the exact handoff, preferring no-RAW replay before bounded RAW evidence.

Completion criterion: one precise handoff, one falsifiable probe, and its
decision consequence are named. If existing evidence closes the gap, return a
reuse/no-code verdict and stop.

When the semantic slice has plausible adjacent scope, state `Non-goals / Not
Doing (and why)`. An adjacent problem discovered during reconnaissance may be
recorded as `Noticed but not touching`; that receipt does not widen the task.

If the requirement leaves a product or domain decision unstated, return
`spec_gap` and name the missing decision. Do not let implementation or review
comments silently choose the semantics.

## 2. Bind Owner And Cost

Select exactly one behavior owner. List every existing helper it reuses, or
record `none`. Package code computes domain evidence; diagnostics orchestrate;
writers render typed results. Record the before/after or `unchanged` cost for:

- RAW opens and XIC extraction calls;
- batching, sample/window locality, and cache ownership;
- repeated TSV scans;
- smoothing, normalization, and per-row recomputation.

Completion criterion: one behavior-owner path is selected, reused helpers are
explicit, rejected alternatives are briefly explained, every cost category is
accounted for, and no parser, evidence computation, or RAW read is orphaned in
a writer/CLI.

Before proposing an abstraction or configuration surface, require both proofs:

1. **Necessity:** name its current second consumer/value or the real
   public/trust boundary that requires it.
2. **Removal consequence:** name the current consumer or behavioral acceptance
   check that would fail if the layer were deleted. A check that only asserts
   the proposed layer exists is not a removal consequence.

| Necessity proof | Removal consequence | Verdict |
| --- | --- | --- |
| present | present | continue the remaining preflight gates |
| present | missing | `reuse/no-code`; stop before edits |
| missing | present | `reuse/no-code`; stop before edits |
| missing | missing | `reuse/no-code`; stop before edits |

Continuing the preflight does not authorize code; every remaining completion
criterion must still pass.

Return `design_no_go` instead of coding when the proposed owner must interpret
an open-ended shell language, AST, schema DSL, or similar unbounded grammar
without an established parser and finite supported subset. Prefer constraining,
deleting, or replacing that mechanism. If the change needs multiple independent
new owners or cannot fit one reviewable semantic slice, split or refreeze the
plan before edits.

## 3. Close Boundary Projections

Follow `docs/agent/architecture-public-contracts.md#read-routes` when a model,
worker result, event, artifact, settlement, receipt, serializer, recovery
marker, comparator, or writer crosses a boundary. Start with
`#boundary-projection-closure` for each non-trivial source-to-target projection
and record:

| Boundary | Source owner -> target consumer | Identity / cardinality | Preserved semantics | Deliberate loss / reduced claim | Failure / provenance / durable binding | Oracle |
| --- | --- | --- | --- | --- | --- | --- |

Keep identity tuples intact and make every omitted source fact explicit. Require
the applicable cross-key identical-payload, duplicate, malformed, reordered,
partial, failure-preservation, serialization, recovery, and replay cases to go
red before the fix and green after it.

Completion criterion: every changed boundary has one row, no consumer claims a
discarded fact, and persistence/comparison receipts contain every fact they
certify. If the change has no non-trivial projection, record `N/A` and why.

## 4. Bind Authority And Acceptance

Classify the evidence-provider role and state the shortest human-explainable
product rule. Read
`docs/agent/architecture-public-contracts.md#public-contracts` when CLI, config,
schema, workbook/report, or persisted identifiers may change. Always state
whether matrix identity, activation, value delta, selected peak/area, or counted
detection changes.

Choose the lowest gate that can falsify the expected behavior: focused tests,
no-RAW artifact parity, lane-specific 8RAW/85RAW, targeted benchmark, or manual
EIC/MS2 review. Route an actual RAW launch or acceptance through
`xic-raw-validation`.

Completion criterion: every public surface is `changed` or `unchanged`, writer
authority stays with an existing owner or has an explicit activation/export
contract, and the validation gate can distinguish pass from fail.

## 5. Issue The Pre-Edit Contract

```markdown
Goal / observable loss / affected XIC spine handoff:
Non-goals / Not Doing (and why), when adjacent scope is plausible:
Noticed but not touching (optional):
Current XIC behavior owner and existing evidence artifact:
Reused helpers or `none`:
Observed capability gap / cheapest XIC falsification probe:
Reuse/owner decision; if a gap remains, proposed code location:
Abstraction/config necessity and deletion consequence, or `N/A`:
Product spine / evidence-provider role:
Simplest product rule:
Call-cost model:
Boundary projection map or reasoned N/A:
Public contracts at risk:
Validation gate and expected pass/fail consequence:
Stop / rollback / diagnostic-scaffold exit rule:
```

For cleanup, require numerical/schema parity. For behavior change, require an
expected diff and acceptance oracle. For diagnostic-only scaffolding, require
an immediate XIC consumer plus promote, kill, externalize, or retirement
condition.

Completion criterion: every field has evidence or a reasoned `N/A`; stop,
rollback, and exit conditions are checkable. Begin edits only after this
criterion is met.

Read `evals/pressure-cases.md` only when testing no-code and scope-control
decisions under pressure. These prose fixtures do not change skill routing.
