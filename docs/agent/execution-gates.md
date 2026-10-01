# Execution Gates

## Read Routes

| Task | Read |
| --- | --- |
| clean-checkout or generated artifact | [Artifact Boundary](#artifact-boundary) |
| RAW or long run | [RAW And Long Runs](#raw-and-long-runs) |
| push, PR, or merge readiness | [PR Verification](#pr-verification) |

## Artifact Boundary

Validate tracked or shipped artifacts from the interface that consumes them.
Run-specific output stays under ignored `output/`; a clean-checkout check is
needed only when packaging, installation, CI, or tracked generation changes.

## RAW And Long Runs

Use the runner in [Local Execution](parameter-settings.md). Normal execution
performs its input checks and records its plan; a separate preflight invocation
or validation packet is not a routine prerequisite. Use `--preflight-only` when
inspecting settings without running. For a long job, keep its logs and timing
under the task's output directory and use the existing background/wakeup workflow
when appropriate. Report results against the task's actual acceptance check.

## PR Verification

Before push, confirm the intended branch and dirty scope, run checks affected by
the diff, and record their actual results in the PR. When HEAD changes, rerun
only affected checks. `.github/workflows/ci.yml` owns the complete hosted command
surface; required hosted checks on the final remote SHA decide merge readiness.
