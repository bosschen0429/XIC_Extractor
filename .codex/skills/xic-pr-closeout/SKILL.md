---
name: xic-pr-closeout
description: Manual XIC overlay for the pr-closeout workflow — readiness labels, validation tier, RAW artifacts, downstream handoff, and merge/history expectations.
disable-model-invocation: true
---

# XIC PR Closeout

This is a repo-specific overlay. The reusable workflow lives in the global
`pr-closeout` skill. Do not duplicate the global workflow here.

If the global skill is unavailable, report `global skill unavailable` and use
`AGENTS.md`, `docs/agent/subagent-routing.md`, and the XIC additions below as
the fallback closeout contract.

Use the global skill first for PR/branch narrative, verification, residual risk,
and follow-up structure. Then apply the XIC-specific additions below.

If the branch is part of a stacked, superseded, retargeted, or split PR series,
use `xic-pr-stack-repair` first. Do not treat stale bases, repeated global
ledger edits, or missing ignored artifacts in clean checkout as ordinary CI
bugs until the stack boundary and artifact ownership have been mapped.

## XIC Closeout Additions

Include these when relevant:

- PR CI-equivalent gate before opening, updating, or marking a PR ready:

  ```powershell
  $env:UV_CACHE_DIR='.uv-cache'; uv run ruff check xic_extractor tests
  $env:UV_CACHE_DIR='.uv-cache'; uv run mypy xic_extractor
  $env:UV_CACHE_DIR='.uv-cache'; uv run pytest -v --tb=short -x
  ```

  Treat real lint, typecheck, or test failures as blockers. Fix them and rerun
  the failed CI-equivalent gate before PR. If the sandbox blocks dependency
  resolution, executable spawn, or DLL loading, rerun the same command with
  approval instead of substituting a narrower check.
- readiness label: `diagnostic_only`, `shadow_ready`, `production_candidate`,
  `production_ready`, or `inconclusive`;
- validation tier: synthetic, focused tests, 8RAW, 85RAW, targeted benchmark,
  manual EIC/MS2 review, or CI shard;
- downstream handoff impact, especially whether `alignment_matrix.tsv` is
  preserved or changed;
- whether `peak_candidates.tsv` is a debug/audit projection or a production
  contract in this PR;
- important artifacts or output indexes under `output/` or `docs/superpowers/`;
- skipped RAW validation and what future evidence would close the risk;
- merge mode and whether branch history, worktree contents, or local `output/`
  artifacts must be preserved.

## XIC Overclaim Guard

Do not claim production behavior from wrappers, reports, sidecars, or TSV
diagnostics alone. State what was observed and what remains unproven.

Do not let a PR description imply handoff productization progress unless the PR
actually advances `TraceGroup`, `PeakHypothesis`, `EvidenceVector`,
`IntegrationResult`, `AuditTrail`, or downstream matrix handoff.
