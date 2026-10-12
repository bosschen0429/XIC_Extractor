# CI runner capacity and post-merge validation proposal

Status: **evaluation only**. This document intentionally does not change workflow behavior.

## Current baseline (reviewed 2026-10-12)

- `.github/workflows/ci.yml` runs on pushes to `master`/`main` and PRs targeting `master`/`main`/`integration/**`.
- `lint` and `typecheck` use `ubuntu-latest`; `test (Python 3.12)` uses a single `self-hosted` label and a 30-minute timeout.
- The test job installs with `uv`, runs pytest with `-n 4 --dist loadfile`, and executes three packaging-related serial test node IDs when required.
- `tools/testing/test_shards.py` excludes ledger snapshots and the separately executed architecture census, and conditionally excludes `browser_e2e`, `packaging_build`, and `product_governance` on PRs based on changed paths. Master runs the broader marker selection.
- Workflow concurrency is already scoped by `github.workflow` and `github.ref`, with `cancel-in-progress: true`. This cancels superseded runs on the same ref, **not** competing master and PR runs.
- `build.yml` already uses `windows-latest` for release packaging and verifies a successful same-SHA CI run for tagged releases.

## Problem and objective

A master push and independent PRs each require the self-hosted test runner; the job queue is serial even when hosted Windows capacity exists. Reduce PR completion latency without weakening required scientific/packaging evidence, silently dropping tests, or increasing unbounded paid hosted minutes.

## Options

| Option | Mechanism | Advantages | Risks / limitations |
|---|---|---|---|
| A. Master on hosted Windows | Route post-merge test job to `windows-latest`; PR test remains self-hosted | Removes master/PR contention with minimal routing logic | Master-only packaging/governance/browser tests must pass on ephemeral Windows; hosted minutes billed to org |
| B. Deterministic PR distribution | Assign a PR to hosted or self-hosted by stable PR-number parity, master to hosted | Both runner pools can work concurrently, no race in selection | Not work-conserving: hosted could be idle while self-hosted queues; PR changes must not flip assignment |
| C. Dynamic availability routing | Pre-job checks runner busy/online state, then chooses runs-on | Better opportunistic use of resources | Check/dispatch race; runner status isn't a reservation; requires API permissions and explicit fallback, not reliable balancing by itself |
| D. Separate portable and environment-specific tests | Run portable pytest on hosted, only DLL/RAW-dependent tests on self-hosted | Best long-term capacity utilization | Requires audited test ownership and a no-omission coverage contract |
| E. Reduce post-merge duplication | Replace redundant master tests with master-only gates, optionally merge queue | Saves capacity | PR tests may not cover final integrated SHA; release same-SHA CI gate must be preserved |

**Recommended sequence:** start with A as a non-required, non-disruptive hosted parity probe; if parity is proven, route master to hosted. Then test B if PR contention persists. Prefer D if portability is demonstrably broad. Do not implement C before demonstrating the need for work-conserving scheduling.

## Evaluation gates before production changes

1. **Baseline measurements:** collect at least 20 recent CI runs across master and PRs: queue wait (`started_at-created_at` at job level), run duration, overlap, cancellations, hosted billed minutes, and self-hosted availability. Record runner labels/count, org Actions budget, spending cap and existing required check names. Current connector access has not supplied this telemetry.
2. **Same-SHA hosted parity:** run a manually selected, non-required `windows-latest` test probe at the same SHA and marker selection as self-hosted. Compare collection count, selected/deselected node IDs, pass/fail/skip, dependencies, Python version, Playwright, PyInstaller outputs, and elapsed time. Preserve Windows PowerShell semantics. Do not mark unavailable native integrations as passing.
3. **Test inventory:** identify which tests require actual RAW files, Thermo DLLs, .NET, local paths, hardware, or external data; mocks alone do not imply an entire shard is portable. Explicitly audit `serial_ci`, `packaging_build`, `product_governance`, and `browser_e2e` paths.
4. **Merge correctness:** determine merge strategy, branch protection and whether required checks validate latest base / merge commit. If removing post-merge ordinary tests, document exactly what verifies the integrated state. Investigate merge queue availability under the organization plan before proposing it as a dependency.
5. **Release invariant:** tagged release `build.yml` currently requires successful `ci.yml` for exactly `GITHUB_SHA`. Never eliminate all same-SHA master CI without replacing this gate with an equally explicit verified contract.
6. **No missing checks:** retain stable required check identity `test (Python 3.12)` during transition, or coordinate branch protection change. No success-shaped skipped checks. Use explicit failing result for missing mandatory tests.
7. **Security:** PR-triggered code is untrusted; do not grant broad repository/admin tokens to arbitrary PR code for runner selection. Avoid dynamic scheduling APIs until permissions and race handling are reviewed.
8. **Cost/benefit:** compare p50/p95 PR queue and completion times, master queue impact, paid hosted minutes, and pass/skip parity. Establish rollback criteria.

## Proposed staged implementation (future PRs, not in this PR)

**Phase 1 — hosted parity probe:** optional `workflow_dispatch` or separately named, non-required Windows hosted job. Reuse existing marker-selection logic and `uv.lock`. Capture a machine-readable node ID inventory, test outcomes and timings; cap runtime and artifact retention. Keep existing CI unchanged.

**Phase 2 — master isolation:** after parity, route master test to hosted Windows, PR tests to self-hosted, retaining the same check name and full master marker/serial coverage. If hosted parity fails, do not deploy; investigate isolated environment dependencies or instead split test ownership.

**Phase 3 — parallel PRs:** evaluate deterministic PR parity routing on a subset, with a stable PR identifier and no dependence on runner busy state. Measure queue reduction and org minutes. If unsuitable, split portable/RAW suites; use a dedicated runner label for native integration.

**Phase 4 — master test reduction:** only after mapping master-only marker coverage and verifying merge SHA integrity, remove provably redundant master tests; preserve governance, release, and integration evidence. A merge queue is an optional alternative, not an assumed feature.

## Acceptance criteria

- No regression in required scientific tests, test collection, skip reporting, or release same-SHA gate.
- Master no longer queues ahead of PR work on the only self-hosted runner, after Phase 2.
- At least two independent PR test jobs can execute concurrently on different runner pools, after Phase 3.
- p95 PR completion time and hosted minute cost are measured against baseline; rollback if either exceeds agreed budget.
- A documented rollback requires only restoring previous `runs-on` routing / workflow commit.

## Open decisions

- Does Chao-hu-Lab organization have sufficient private-repo hosted Actions minutes and billing allowance?
- Are all master-only tests portable to clean Windows hosted environments?
- Is the self-hosted runner a single Windows machine, and which labels/runner groups are actually registered?
- Are merges squash, merge commit, or rebase, and which checks are branch-protection required?

## Non-goals

No workflow behavior changes, no runner installation, no billing-setting changes, no branch-protection edits, and no removal of tests in this proposal PR.
