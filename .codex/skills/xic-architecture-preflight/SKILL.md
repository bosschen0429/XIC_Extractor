---
name: xic-architecture-preflight
description: Architecture preflight before non-trivial XIC implementation that touches diagnostics, RAW-backed evidence, matrix/activation/value-delta, model selection, or new evidence providers.
---

# XIC Architecture Preflight

Use this before writing code for non-trivial XIC features. The goal is to stop
architecture debt at the source, not clean it up after a diagnostic path already
became sticky.

This skill is a preflight, not a review closeout. It should produce a short
implementation contract before edits begin.

## North Star

XIC Extractor is not a single-dataset tool and not a CID-NL-only pipeline.

- 8RAW and 85RAW are validation fixtures and cost/stress oracles, not product
  boundaries.
- CID-NL, HCD-PI, Delta Mass, MS1 isotope/adduct pattern, RT/iRT, shape,
  library matches, standards, and future models are evidence providers.
- Evidence providers feed `EvidenceVector` / model selection. They must not
  directly become permanent matrix-writing authority without an explicit product
  contract.

## Preflight Questions

Answer these before implementation:

1. What product spine layer does the change advance?
   - `Trace` / `TraceGroup`
   - `PeakHypothesis`
   - `EvidenceVector`
   - `IntegrationResult`
   - model selection
   - `AuditTrail`
   - cleanup-only
2. Which existing owner should absorb the behavior?
   - Domain behavior belongs in `xic_extractor/...`.
   - `tools/diagnostics/...` should orchestrate, not own reusable logic.
   - Writers render; they do not recompute evidence or re-read RAW.
3. What existing helper can be reused?
   - Search `tools/diagnostics/INDEX.md`, `docs/architecture-contract.md`, and
     existing package modules before adding a local helper.
   - Prefer `xic_extractor/tabular_io.py` before new TSV/parser/scalar helpers.
   - Prefer shared overlay/evidence selectors before one-off row selection.
4. What is the simplest human-explainable product rule?
   - If the rule needs nested dataset-specific qualifiers, treat it as a
     temporary validation slice, not product policy.
   - Name the simple gate it is trying to approximate or prove.
5. What call-cost model changes?
   - RAW opens;
   - XIC extraction calls;
   - batch size and locality;
   - repeated TSV scans;
   - repeated smoothing/curve normalization;
   - per-row vs per-family/per-sample cache boundaries.
6. What public contract could drift?
   - CLI flags;
   - config keys;
   - TSV/CSV/workbook schema;
   - matrix identity;
   - activation decisions;
   - value delta;
   - output path and artifact naming.
7. What validation tier can close the decision?
   - synthetic/focused tests;
   - no-RAW artifact parity;
   - 8RAW parity;
   - 85RAW parity;
   - targeted benchmark;
   - manual EIC/MS2 review.

## Required Output Before Edits

Write a compact contract in the working message or plan:

```markdown
Goal:
Existing owner/helper to reuse:
New code location:
Evidence provider role:
Simplest product rule:
Call-cost model:
Public contracts at risk:
Validation gate:
Stop rule:
```

If the change is cleanup-only, say which behavior must remain numerically or
schema-equivalent. If it is a behavior change, name the expected diff and the
acceptance oracle before coding.

## Red Flags

Stop and rethink when:

- a new diagnostic tool needs its own TSV parser, bool/float parser, or row
  grouping helper;
- a writer computes domain evidence or reads RAW;
- a diagnostic sidecar starts to look like production behavior without an exit
  rule;
- a product gate grows by adding dataset-specific adjectives instead of a
  domain-meaningful rule and evidence test;
- 85RAW shape or CID-NL assumptions appear in core models as permanent product
  boundaries;
- correctness is preserved but RAW calls, TSV scans, or smoothing operations
  scale per row when they could be batched or cached;
- a new evidence source wants to write the matrix directly instead of becoming
  evidence for model selection.

## Minimal Search

Use targeted search before coding:

```powershell
rg -n "<concept>|<schema>|<helper>|<evidence-name>" xic_extractor tools tests docs
rg -n "read_tsv|write_tsv|optional_float|EvidenceVector|PeakHypothesis|Activation|value_delta" xic_extractor tools tests
```

Use CodeGraph only when structural caller/callee impact would answer the
ownership question faster than text search.
