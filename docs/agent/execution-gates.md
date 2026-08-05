# Execution Gates

This file owns operational gates, PR gates, and repeated local pitfalls. The
root `AGENTS.md` keeps only the highest-frequency subset.

## Before Work

- Before non-trivial edits, confirm intended worktree, branch, and dirty diff
  scope. Do not stage, rewrite, or revert unrelated user changes.
- Before Python, RAW, DLL, or validation commands, read
  `docs/agent/parameter-settings.md` and use documented runners and paths.
- Before product checks, require the per-worktree bootstrap and Windows-native
  runtime acceptance:

```powershell
uv run --cache-dir .uv-cache --no-project python scripts/bootstrap_worktree.py --check
.venv\Scripts\python.exe -m scripts.agent_sandbox_doctor --strict --probe-write
```

The doctor returns a machine-actionable status. `READY` may run in the current
sandbox; `REWRITE_COMMAND` must change shape before execution;
`APPROVAL_REQUIRED` goes directly to a bounded approval;
`ENVIRONMENT_REPAIR_REQUIRED` stops product debugging until the named runtime
layer changes; `HUMAN_DECISION_REQUIRED` waits for the operator; and
`INCONCLUSIVE` collects only the finding's named next evidence. Add
`--probe-github` only when the task needs GitHub; a runtime that advertises
disabled sandbox networking must not first launch a predictably failing canary.

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

- hook/script smoke check where applicable;
- `codex execpolicy check` for changed `.rules` decisions;
- `git diff --check`;
- secret, private local path, and accidentally tracked local config scan.

## PR Verification Gate

Validation evidence belongs to a commit SHA. Run the smallest focused checks
that can detect the changed behavior during implementation and before commit.
Do not rerun the full CI surface merely because the same HEAD is being pushed,
used to open a PR, or moved between draft and ready states.

- Before push or PR creation, confirm the intended branch, staged/dirty scope,
  and exact checks already run for the current HEAD.
- Required hosted checks on the final remote SHA are the merge-readiness
  authority. Run a local same-layer check when hosted CI is unavailable or when
  the change affects CI configuration, packaging, installation, or another
  runtime layer that needs a pre-push smoke.
- When HEAD changes, rerun the checks affected by the diff. When it does not,
  reuse the recorded SHA-bound evidence instead of repeating it.
- Real lint, type, test, or clean-checkout failures remain blockers. A sandbox,
  dependency, or DLL failure is environment uncertainty; report it rather than
  claiming a narrower check proved the unavailable layer.
- `.github/workflows/ci.yml` is the only owner of the complete hosted command
  surface. Do not mirror its shard list in this file, a skill, or a handoff.

Record exact commands, results, and the checked SHA in the PR or closeout
surface. Review-thread replies on an unchanged remote head may reuse that
head's evidence after confirming the live thread state.
