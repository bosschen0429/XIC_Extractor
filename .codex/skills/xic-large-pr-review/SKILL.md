---
name: xic-large-pr-review
description: Large XIC PR review for diffs touching diagnostics, architecture, preset performance, parity, 8RAW/85RAW, activation/value-delta, matrix identity, or RAW locality.
---

# XIC Large PR Review

Use this after adopting the normal code-review stance: findings first, ordered by
severity, with exact file and line references when findings exist.

This skill is for review only. Do not edit files, stage, push, update PRs, or
merge unless the user separately asks.

## Scope Check

Start with read-only state:

```powershell
git status --short --branch
gh pr view --json number,title,state,isDraft,baseRefName,headRefName,mergeable,commits,statusCheckRollup,url
$base = gh pr view --json baseRefName --jq ".baseRefName"
git diff --stat "origin/$base...HEAD"
git diff --name-only "origin/$base...HEAD"
```

If the worktree is dirty, say whether the dirty files affect the review. Do not
revert or stage anything.

## Review Strategy

Do not try to spend equal attention on every changed file in a huge PR. Build a
blast-radius map and review in this order:

1. Package-neutral or shared helpers used by many writers or diagnostics.
2. Public contract surfaces: CLI flags, config keys, TSV/CSV/workbook schemas,
   matrix identity, activation decisions, value deltas, and output paths.
3. Diagnostic architecture boundaries between `tools/diagnostics/` orchestration
   and reusable `xic_extractor/diagnostics/` package logic.
4. RAW access locality, batching, reuse, caching, and any fallback path that can
   silently change evidence availability.
5. Product-vs-diagnostic claims: whether wrappers, sidecars, reports, or gates
   are being overclaimed as production behavior.
6. Focused tests, parity gates, and real-data evidence that match the PR intent.

For mechanical writer migrations, verify representative callers and the shared
formatter rather than reading every identical call site. Check whether custom
formatters, line terminators, encodings, hidden schema fields, and row order are
preserved.

## Finding Expansion Gate

Treat a review comment as evidence of one failure instance, not as the boundary
of the fix. First classify the mechanism and its owner. Expand the review beyond
the commented line when the finding involves any of these shared
responsibilities:

- a shared helper, default, cache, loader, writer, parser, settings object, or
  domain invariant;
- fail-open/fail-closed behavior, exception translation, fallback, optional
  suppression, or missing-artifact handling;
- identity, hash, provenance, mutation, or writer/reader symmetry;
- a hook predicate or validation rule whose conditions can interact.

For an expanded finding, inspect:

1. the owning implementation and every changed or live callsite;
2. sibling boundaries that express the same responsibility;
3. illegal states and adversarial inputs, including `NaN`, `Inf`, malformed or
   reordered data, missing keys, blank identity, and invalid cross-field
   combinations where relevant;
4. writer-to-loader round trips, cache reconstruction, and post-write mutation
   resistance when artifacts cross a trust boundary;
5. negative tests for the failure class, including crossed conditions such as
   "accept existing evidence without launching a new run".

If evidence shows the issue is truly local, state why the owner and siblings are
not affected. Do not invent a repository-wide redesign for an isolated bug.

One same-class finding triggers this bounded class sweep before re-delivery. A
second same-class finding on the same PR is a stop signal: freeze comment-by-
comment resolution, refresh the PR head snapshot, and rerun the relevant lenses
across the whole blast radius. Resolve comments only after the root fix is on the
remote head and the class-level negative tests and fresh review pass.

Reviewer and subagent findings are leads, not authority. Reproduce or ground
them in the domain contract, changed code, public surface, or test gap before
accepting their proposed fix.

## XIC Evidence Labels

State the strongest evidence actually reviewed:

- `synthetic_only`
- `focused_tests`
- `ci_green`
- `8RAW_parity`
- `85RAW_parity`
- `targeted_benchmark`
- `manual_eic_ms2_review`
- `diagnostic_only`
- `inconclusive`

Do not treat `8RAW` evidence as proof of `85RAW` readiness. If 85RAW was
intentionally skipped, say so as residual risk, not as a failure.

## Parity And Public Contract Checks

When the PR mentions matrix-only, deep-audit, activation, or value delta, look
for evidence covering:

- activation decisions;
- activation value delta;
- matrix cell values and sample identity;
- matrix identity sidecars;
- row ordering or deterministic duplicate policy;
- publication mode differences such as image rendering disabled but evidence
  TSVs preserved.

When the PR touches RAW access or overlay extraction, check that batching and
cache changes preserve:

- one output row per requested family/sample/seed;
- fallback behavior when scan-window or retention-time lookup is unavailable;
- trace cropping back to the original request window;
- explicit counts for RAW opens, XIC requests, batch calls, super-window groups,
  and trace points when those metrics exist.

## Output Format

Use this review shape:

```markdown
**Findings**

- [P1/P2/P3] <issue title> — <file:line>
  <why this can break behavior, schema, validation, or reviewability>

**Open Questions / Assumptions**

<Only include if they affect the verdict.>

**Verification Reviewed**

<CI, focused local tests, parity evidence, or artifacts inspected.>

**Residual Risk**

<What remains unproven, especially because of PR size or skipped RAW tier.>
```

If no blocking issue is found, say that clearly. Do not invent findings just to
make the review look useful.

## Stop Rules

Stop and report the limitation when:

- the PR base cannot be determined;
- CI status cannot be checked and no local gate can substitute;
- the diff is too large for full manual coverage and no meaningful blast-radius
  map can be built;
- a suspected issue cannot be grounded in a changed file, public contract, or
  focused test gap.

For subagent review, use repo routing in `docs/agent/subagent-routing.md` only
when the user explicitly asks for subagents or when runtime policy permits it.
