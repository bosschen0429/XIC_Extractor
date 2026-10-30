# Hybrid CI runner capacity

`.github/workflows/ci.yml` owns execution; this document explains runner selection
and the retained validation boundary.

## Routing

| Work | Runner |
| --- | --- |
| Lint, static contracts, architecture census, typecheck | GitHub-hosted Ubuntu |
| `master` / `main` Python and packaging tests | GitHub-hosted Windows |
| PR Python and packaging tests, by default | Self-hosted |
| PR Python and packaging tests with `ci:hosted-windows` label | GitHub-hosted Windows |
| Browser lane | Independent GitHub-hosted Windows VM |
| Required test result aggregation | GitHub-hosted Ubuntu |
| Release packaging | GitHub-hosted Windows (unchanged) |

To run two independent PRs concurrently, put `ci:hosted-windows` on one and leave
it off the other. Labels select capacity, not test scope. Adding/removing a label
starts a new CI run; the existing per-ref concurrency rule cancels the older run.
All label events restart CI, including unrelated label changes. Avoid label churn.
The selected capacity still depends on runner availability and hosted quota; there
is no automatic busy-runner fallback or reservation service. Re-running an old
workflow run retains its original event payload; use a fresh label event or push
to change routing.

The required `test (Python 3.12)` check aggregates the Python and browser jobs;
both must succeed. Python tests retain the Python 3.12 lockfile install, four
pytest workers, serial packaging tests and 30-minute job timeout. Browser tests
run serially on a separate VM so they do not inherit Winsock and resource state
from scientific or build tests. Browser relevance still comes from the selection
owner; when relevant, the complete browser lane runs.
Master no longer consumes self-hosted PR capacity. Hosted PRs and master can still
compete for organization-hosted concurrency and billable minutes. No billing,
runner registration, permissions or branch-protection settings are changed.

## What needs self-hosted infrastructure?

The normal CI test jobs do not provision private RAW data or Thermo DLLs.
Browser tests use installed Playwright Chromium; packaging already has a hosted
Windows consumer in `build.yml`. Moving CI does not move or certify scientific
RAW/DLL validation: runs requiring those private inputs remain on their configured
Windows environment. Keep those separate from engineering regression claims.

## Duplicate work assessment

- Architecture census runs once in lint and is excluded from the main test pass.
- Serial packaging tests are excluded from the parallel pass and run afterward.
- PR relevance selection already skips unrelated browser, packaging-build and
  product-governance tests. `tools/testing/test_selection.py` remains the sole owner.
- Per-ref concurrency already cancels superseded runs. It must not be made global:
  one PR must not cancel another PR's required checks.
- Keep main-branch CI. PRs may omit relevance-gated tests, and the merged commit
  is not necessarily the PR-tested commit. Tagged releases require successful CI
  on exactly the release SHA through `build.yml`.

No additional tests are deleted. Eliminating the post-merge run would remove a
current release gate and broader coverage, not merely duplicate an identical
assertion on an identical SHA. Cross-run result reuse needs its own proven input
identity and release contract; it is not part of capacity routing.

## Validation and rollback

PR #764 carries the measured baseline, exact validated SHA and hosted-run receipts.
Use its labeled hosted run to inspect clean-image installation, test counts/skips,
packaging and browser results. Required hosted checks on the final remote SHA
remain merge authority; a routing change alone is not a portability proof.

If hosted Python-test capacity or compatibility fails, remove the PR label for a
fresh self-hosted Python-test run; the browser job still uses its own hosted VM.
To restore all previous routing, revert the workflow routing
change (including label event triggers); keep the test commands and check names.

GitHub reference: [runner labels select matching capacity](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/use-in-a-workflow).
