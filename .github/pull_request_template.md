<!--
Keep this body self-contained for a reviewer who did not follow the issue.
Replace or remove every instructional comment. Delete sections that genuinely
do not apply; do not leave TBDs. Lead with behavior and decisions, not files or
command history. Do not include private paths, raw logs, secrets, or AI credit.
-->

## Outcome

<!--
In 2–4 sentences: what now works, whether it is safe to use/write/ship, the
readiness label when relevant, and the next step. Distinguish a usable product
path from scaffolding, diagnostics, or validation evidence.
-->

## Problem and observable loss

<!--
What user, operator, scientific, or engineering failure required this PR?
State the concrete loss or blocked decision, not only the internal mechanism.
-->

## What changed

<!-- Group changes by behavior or owner. Avoid a file-by-file changelog. -->

| Area | Before | After |
| --- | --- | --- |
|  |  |  |

## Scope and public contracts

- Lane: <!-- shared / targeted / untargeted / workflow-only -->
- Product output change: <!-- yes / no; describe exact expected diff -->
- Readiness: <!-- diagnostic_only / shadow_ready / production_candidate / production_ready / inconclusive / not applicable -->
- Public contracts changed: <!-- CLI, config, API, TSV/CSV, workbook, report, run metadata, persisted identity, Matrix, selected value/area, counted detection, defaults, or none -->
- Public contracts intentionally preserved:
- Out of scope / deferred:

## Authority and invariants

<!--
Required when the PR touches persisted inputs, evidence, identity, Backfill,
Matrix, activation, review, runtime settlement, or writer authority. Otherwise
state why it does not apply.

- Name the semantic owner and its authoritative inputs.
- State which caller/persisted claims are recomputed, validated, or audit-only.
- State completeness, uniqueness/cardinality, fail-closed, rollback, and atomic
  publication rules that materially protect the result.
-->

## Review guide

<!--
Give an ordered review path. For each load-bearing file/module/artifact, say
what decision or invariant to inspect. End with 1–5 concrete reviewer questions.
Generated evidence and large tables should be reviewed through a summary/index.
-->

1. `path/to/owner` —
2. `path/to/oracle` —

Reviewer questions:

1.

## Verification

<!--
Bind evidence to the exact reviewed SHA. Record only checks actually observed.
Separate test success from scientific or production readiness. If output or
scientific behavior changes, name the applicable synthetic, 8RAW/85RAW,
targeted benchmark, method-specific real sample, expected-diff, rollback, or
manual EIC/MS2 evidence. State skipped evidence and why.
-->

Reviewed SHA: `<!-- full SHA -->`

| Evidence | Command or artifact | Result | What it proves |
| --- | --- | --- | --- |
| Focused behavior |  |  |  |
| Repository contracts |  |  |  |
| Real-data / expected diff |  |  |  |

## Residual risk and follow-up

<!-- Keep material residual risks to three or fewer. Name the owner/issue. -->

1.

## Issue and delivery linkage

- Closes / tracks:
- Base or prerequisite:
- Follow-up issue/PR:
- Merge/retention note: <!-- squash by default; name intentionally retained artifacts or special handling -->

## Final checks

- [ ] The PR body distinguishes implementation, generated evidence, and actual usable behavior.
- [ ] Public-contract and expected-diff claims match the code and retained evidence.
- [ ] Diagnostics, sidecars, labels, receipts, or review packets do not silently gain product/writer authority.
- [ ] Validation claims name the exact evidence level and reviewed SHA.
- [ ] Review order, residual risks, and deferred work are explicit.
- [ ] Unrelated user changes, private paths, secrets, caches, and local artifacts are excluded.
