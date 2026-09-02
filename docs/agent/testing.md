# Testing

Tests protect current observable behavior. They are not a record of how a
change was constructed.

## Add A Test Only When It Can Fail Usefully

Every change needs evidence, but not every change needs a new test. Add or
modify a test only when all are true:

1. it protects a reachable behavior, public contract, or semantic-owner boundary;
2. its input reproduces the failure mechanism;
3. its assertion distinguishes the expected result from the observable loss;
4. no existing test, validator, schema check, artifact inspection, or focused
   review supplies the same oracle;
5. the check remains useful after the current branch or migration ends.

For a production behavior change with a stable oracle, make the test red on the
loss before fixing it. Do not freeze private helpers, temporary file layout, or
cleanup state without a current consumer. Temporary compatibility tests name
their removal condition.

## Choose The Observable

| Surface | Protect |
| --- | --- |
| CLI/config | accepted input, defaults, invalid-input error, output schema |
| CSV/workbook | headers, sheet/column order, key values, visibility, safe text |
| scoring/selection | selected peak, reason, confidence, candidate-scoped evidence |
| GUI | settings round-trip, defaults, enable/disable, validation error |
| parallel work | serial/process equivalence, cancellation, progress, readable errors |
| report | user-visible sections, escaped text, critical markers |

Assert the artifact or result, not only that a helper was called. Mock only
external or expensive boundaries at the import seam used by production code;
do not mock away the policy under test.

## Test Placement

Pytest discovery and dependencies are owned by pyproject.toml. Keep tests under
tests/test_*.py and name them after observable behavior. Use tmp_path for files,
function-scoped fixtures by default, and tests/fixtures for reusable payloads.
Split a file when it accumulates unrelated responsibilities; line count alone
is not the contract.

## CI Shards

tools.testing.test_shards owns ordinary-CI discovery, assignment, exclusions,
and the pre-commit coverage check. Add a new test file to that owner and run:

    .venv\Scripts\python.exe -m tools.testing.test_shards --check

Do not copy shard globs into another hook or document. Hosted CI is the final
remote backstop.

Tests marked ledger_snapshot pin recorded validation values and are excluded
individually from default CI. Run that lane with:

    .venv\Scripts\python.exe -m tools.testing.test_shards --snapshots

A legitimate re-analysis may change a snapshot. Keep rejection behavior and
dependency-failure checks in ordinary CI when their oracle is still current.

## RAW And Scientific Validation

Default pytest must not depend on local RAW paths. Synthetic/unit tests prove
engineering regression coverage only. Manual-2RAW, lane-specific subsets, and
85RAW runs are validation evidence only when the active Issue and method owner
require them.

Before launching or accepting RAW-backed evidence, use xic-raw-validation and
the method-specific validation contract. Do not infer scientific or production
readiness from passing pytest.

## Verification

Run the narrowest existing tests that can falsify the change, then expand in
proportion to affected consumers and public surfaces. The complete hosted
command surface lives in .github/workflows/ci.yml; do not cache its command list
here.

Report commands actually run, skipped evidence, and remaining risk. A missing
stable oracle is an unknown to resolve, not a reason to add a test that only
checks structure.
