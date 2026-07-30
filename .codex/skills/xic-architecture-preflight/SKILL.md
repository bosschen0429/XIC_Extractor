---
name: xic-architecture-preflight
description: Preflight XIC architecture before non-trivial changes to diagnostics or RAW evidence, PeakHypothesis/EvidenceVector/model selection, matrix/activation/value-delta paths, runtime adapters/settlements/serializers, boundary projections, or new evidence providers.
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

## 3. Close Boundary Projections

Read `docs/agent/architecture-public-contracts.md` when a model, worker result,
event, artifact, settlement, receipt, serializer, recovery marker, comparator,
or writer crosses a boundary. For each non-trivial source-to-target projection,
record:

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
product rule. Read `docs/agent/architecture-public-contracts.md` when CLI,
config, schema, workbook/report, or persisted identifiers may change. Always
state whether matrix identity, activation, value delta, selected peak/area, or
counted detection changes.

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
Current XIC behavior owner and existing evidence artifact:
Reused helpers or `none`:
Observed capability gap / cheapest XIC falsification probe:
Reuse/owner decision; if a gap remains, proposed code location:
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
