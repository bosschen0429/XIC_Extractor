# Execution Gates

This file owns operational gates, PR gates, and repeated local pitfalls. The
root `AGENTS.md` keeps only the highest-frequency subset.

## Before Work

- Before non-trivial edits, confirm intended worktree, branch, and dirty diff
  scope. Do not stage, rewrite, or revert unrelated user changes.
- Before Python, RAW, DLL, or validation commands, read
  `docs/agent/parameter-settings.md` and use documented runners and paths.
- When sandbox, PowerShell syntax, output path, network approval, or RAW runner
  choice is uncertain, preflight with:

```powershell
python -m scripts.agent_sandbox_doctor
```

## Outputs And Validation Status

- Keep outputs under task-specific `output/` or `docs/superpowers/` paths. New
  diagnostic output groups need a summary or index.
- State validation status explicitly: `diagnostic_only`, `shadow_ready`,
  `production_candidate`, `production_ready`, or `inconclusive`.
- Tests passing is not the same as production readiness. For extraction,
  alignment, scoring, and matrix behavior changes, report which
  method-appropriate evidence was used: synthetic tests, lane-specified
  8RAW/85RAW, targeted benchmark, method-specific real samples, or manual
  EIC/MS2 review.

## Artifact Boundary Gate

Default CI must run in a clean checkout without the user's ignored local
artifacts. A checker or test that needs any of these paths is a local validation
check, not a default CI gate, unless the required bytes are tracked minimal
fixtures:

- `output/`;
- `.worktrees/`;
- `local_validation_artifacts/`;
- rendered HTML/PNG review bundles;
- large RAW-derived TSV/CSV files externalized from version control.

For externalized artifacts, default checks may validate schema, manifest fields,
relative path shape, row counts, hash format, and committed replacement
summaries. They must not fail because the ignored local file is absent. Presence
and byte-hash checks for ignored artifacts require an explicit opt-in flag such
as `--require-rendered-local` or `--require-externalized-local`.

When a PR both removes tracked generated outputs and changes tests/checkers,
verify the same PR is self-contained: the removal, manifest update, checker
fallback, and focused tests must all live in the same PR or in an already-merged
prerequisite. Do not assume a later cleanup PR will make an earlier PR pass.

## RAW And Long Runs

- Do not launch 85RAW or likely long RAW runs through background
  `Start-Process` from the Codex shell. Use the foreground heartbeat/timing
  command shapes in `docs/agent/parameter-settings.md`, or get explicit approval
  for an external terminal or automation.
- Known approval-first commands are documented in
  `docs/agent/parameter-settings.md`: dependency sync/lock, Playwright browser
  install, RAW/DLL loading, GUI/external-terminal launch, and global Codex
  config changes should not be re-tried once in sandbox just to fail. If the
  task needs them, request the documented narrow approval up front; otherwise
  use existing artifacts or the offline path.

## Diagnostic Reuse

- Search `tools/diagnostics/INDEX.md`, relevant notes, and existing validation
  outputs before inventing a new diagnostic workflow.
- Search `docs/diagnostic-ledger.md` before rerunning known targets or failure
  modes.

## Local Pitfalls

Use the same operational playbook for repeated local pitfalls:

- guessed pytest node ids that collect zero tests;
- Windows git `.git\index.lock` or ref-lock friction;
- stale or deleted `.worktrees`;
- noisy recursive scans over generated outputs;
- locked workbook/report files.

Do not keep retrying the failing shape. Switch to the documented fixed command
pattern.

## Execution-Affecting Config

Treat `.codex/config.toml`, `.codex/hooks.json`, `.codex/rules/`, hook scripts,
execpolicy, and subagent TOML as execution-affecting config. Changes need:

- docs or handoff review;
- hook/script smoke check where applicable;
- `git diff --check`;
- secret, private local path, and accidentally tracked local config scan.

## PR Verification Gate

Before opening a PR, changing its remote head, or marking it ready, run the
CI-equivalent commands from `.github/workflows/ci.yml` in the current worktree.
Root `AGENTS.md` links here instead of mirroring this command list:

CI is layered: pull requests run all required shards sequentially in one locked
Python 3.12 self-hosted environment, while pushes to `main` or `master` also run
the same shards in one Python 3.11 compatibility environment. While the
self-hosted runner pool has only one concurrent execution slot, do not fan
shards out into a job matrix: that only adds queue and repeated setup without
adding parallel capacity. Reconsider two balanced lanes only after the pool has
two measured concurrent slots. The local PR closeout gate below mirrors the
command surface.
Hosted lint/typecheck jobs use the GitHub uv cache keyed by `pyproject.toml` and
`uv.lock`; self-hosted jobs use the same locked dependency contract, reuse one
environment per Python version and the runner-persistent local uv cache, and do
not upload that cache through GitHub Actions. They record install/per-shard
seconds in the job summary so future CI tuning is based on observed timing.
The hosted lint checkout fetches full Git history because the docs lifecycle
audit must replay registered retirement packets from their fixed activation
base through the commit that last wrote the registered packet on current
ancestry. That endpoint is the final packet revision in authoring history and
the squash commit in collapsed history, and it stays fixed across later commits
that do not revise the packet. A shallow packet/digest read is not the
acceptance oracle.

```powershell
uv run --cache-dir .uv-cache --no-project python scripts/bootstrap_worktree.py --check
$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check xic_extractor tests
$env:UV_CACHE_DIR='.uv-cache'; uv run lint-imports --no-cache
$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_architecture_census.py
$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_diagnostics_index.py
$env:UV_CACHE_DIR='.uv-cache'; uv run python tools/diagnostics/docs_management_audit.py --repo-only --fail-on-completed-transient --json
$env:UV_CACHE_DIR='.uv-cache'; uv run mypy xic_extractor
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards --check
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards docs-config -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards gui -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards targeted-core -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards alignment-core -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards product-gates-activation -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards product-gates-evidence -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards diagnostics-tools -- -v --tb=short -x
```

If a command fails because of real lint/type/test errors, fix the root cause and
rerun the failed gate. If it fails only because sandbox blocks dependency
resolution, executable spawn, or DLL loading, rerun the same command with
approval instead of substituting a narrower check. PR descriptions and closeout
notes must list exact commands and observed results.
Do not replace the shard gate with a monolithic full-suite pytest run unless
debugging the shard runner itself.

Review-thread replies or resolutions on an unchanged remote head may reuse that
head's recorded verification after confirming the head SHA and live thread
state. When an authorized fix reaches the remote head, reply with the commit and
verification evidence before resolving the thread. For a rejected finding,
reply with the disproving evidence first. Before claiming review closeout, query
the live PR and require zero unresolved threads unless each remaining thread is
explicitly deferred.
