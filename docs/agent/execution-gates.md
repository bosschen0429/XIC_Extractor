# Execution Gates

This file owns clean-checkout artifact, long-RAW launch, and PR verification
gates. Runner, sandbox, and command details remain with their routed owners.

## Read Routes

| Task branch | Read |
| --- | --- |
| clean-checkout or externalized artifact gate | [Artifact Boundary Gate](#artifact-boundary-gate) |
| 85RAW or long RAW launch | [RAW And Long Runs](#raw-and-long-runs) |
| CI evidence, push, PR, or ready-for-review | [PR Verification Gate](#pr-verification-gate) |

Python, worktree bootstrap, sandbox friction, validation scale, and command
shapes route through `docs/agent/parameter-settings.md#read-routes`.
Execution-config changes route through
`docs/agent/codex-operating-system.md#change-protocol`.

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
  command shapes in
  `docs/agent/parameter-settings.md#85raw-command-contract`, or get explicit
  approval for an external terminal or automation.
## PR Verification Gate

Validation evidence belongs to a commit SHA. Run the smallest focused checks
that can detect the changed behavior during implementation and before commit.
Do not rerun the full CI surface merely because the same HEAD is being pushed,
used to open a PR, or moved between draft and ready states.

- Before push or PR creation, confirm the intended branch, staged/dirty scope,
  and exact checks already run for the current HEAD.
- Publish a PR body in this fixed order: freeze the final commit and record its
  full 40-character HEAD SHA; render the complete body from the single
  repository PR template; run the local checker against that rendered body and
  SHA with `python scripts/check_pr_body.py --body-file <file> --head-sha <40-SHA>`;
  push the commit; then immediately update the remote PR body to the
  rendered body. Keep the full template and its existing body/checker contract;
  this ordering only prevents a head/body publication race.
- The receipt workflow listens for `opened`, `reopened`, and `edited`, not
  `synchronize`. A push therefore does not create a predictable synchronize-time
  red result. Until the remote body update triggers `edited`, the remote receipt
  has not established current-head validation and may appear pending or missing;
  do not treat the PR as ready in that state. Whether a receipt check blocks
  merge is determined by the repository's current branch-protection or ruleset
  settings, not by this procedure.
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
