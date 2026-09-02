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

Use the documented runner and `xic-raw-validation` before launching or accepting
RAW-backed evidence. Name the decision, input set, timeout/heartbeat, output
location, and stop condition. A failed environment check is uncertainty, not a
scientific result.

## PR Verification

Before push, confirm the intended branch and dirty scope, run checks affected by
the diff, and record their actual results in the PR. When HEAD changes, rerun
only affected checks. `.github/workflows/ci.yml` owns the complete hosted command
surface; required hosted checks on the final remote SHA decide merge readiness.
