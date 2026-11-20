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
   review covers the same observable loss on the same actual path; matching
   expected values alone do not establish equivalent boundary coverage;
5. the check remains useful after the current branch or migration ends.

For a production behavior change with a stable oracle, make the test red on the
loss before fixing it. Do not freeze private helpers, temporary file layout, or
cleanup state without a current consumer. Temporary compatibility tests name
their removal condition.

Owner replacement retires tests of obsolete helpers, duplicate parsing, and
superseded internal representations in the same change. Keep the correctness oracle
with the owner and consumer-visible behavior with the consumer. A test alone
does not justify retaining a retired interface or compatibility branch.

A rejection test needs a reachable producer of the rejected input: external or
persisted data, a user action, or an actual caller path. Hand-assembling forged,
crossed, tampered, or substituted internal objects only to show that a
constructor rejects them tests the defense itself, not product behavior; it
does not justify adding or retaining that defense. Removing an unneeded
internal check retires its rejection tests in the same change.

## Oracle, Attribution, And Contract Boundaries

1. **Independent oracle:** derive expected results from an analytical formula,
   independent chemical standard, or confirmed product contract. Do not use the
   production calculation under test to generate its own expected result or
   turn unverified historical run numbers into golden truths.
2. **Sample attribution:** when a change can mix extraction, alignment, batch,
   or writer results between samples, use at least two samples with distinct
   expected values and assert each sample's result. For other observables,
   such as invalid-input rejection, cancellation, an empty result, or workbook
   sheet visibility, use the smallest fixture that reproduces that behavior.
   File existence, non-empty collections, and mock calls alone do not prove
   numerical or attribution correctness. When publication or absence itself is
   the contract, assert the expected artifact set.
3. **Stable contracts:** assert owner results and consumer-visible behavior.
   Workbook coordinates or formatting are valid oracles when the published
   layout or review meaning requires them; name that contract. Avoid coupling
   tests to incidental helper names, widget geometry, or object identity.
   Source/AST checks are appropriate for maintained dependency or construction
   boundaries owned by the architecture gate. One-time scans for retired
   identifiers belong in PR evidence and Git history.

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

For asynchronous work, assert events, results, and resource lifetime. Scheduling
outcomes and elapsed time are correctness oracles only when scheduling or latency
is itself a product contract. Otherwise, timeouts serve only as hang watchdogs.

## Test Placement

Pytest discovery and dependencies are owned by pyproject.toml. Keep tests under
tests/test_*.py and name them after observable behavior. Use tmp_path for files,
function-scoped fixtures by default, and tests/fixtures for reusable payloads.
Split a file when it accumulates unrelated responsibilities; line count alone
is not the contract.

Keep shared domain builders in a fixture module, such as
`tests/membership_fixtures.py`, rather than importing another test's private
helper. State synthetic evidence defaults there; cases changing evidence
strength name their overrides beside the expected outcome.

## Affected-Test Selection

tools.testing.test_selection derives which tests a change can affect from the
import graph and the paths tests and code name; a new test file needs no
registration. Pull-request CI runs that selection, master runs every test, and
anything the derivation cannot place runs every test. See the
[selection decision](../adr/0007-select-tests-by-their-inputs.md) for what counts as a reader.

For local changes, use the selector as the default validation entry point:

    .venv\Scripts\python.exe -m tools.testing.test_selection

Like CI, the selector runs the selection under xdist (`-n auto --dist
worksteal`, with `OPENBLAS_NUM_THREADS=1` unless already set), then runs
`serial_ci` tests in a separate session without workers. Pass xdist options
after `--` to override the worker count; `-n 0` runs one session in one
process. Local runs skip `packaging_build`: CI builds the wheel and frozen
archive for every change that can affect them. Use `--all-markers`, or run
`tests/test_pyinstaller_spec.py` directly, to build locally.

For a known failure or a bounded behavior, run its existing pytest file or node
directly. Use the selector's `--all` option when intentionally broadening to all
test files. Plain `pytest` without a target still collects the whole suite; it
does not derive affected tests.

Pytest's cache provider stays enabled, retaining state for `--lf` and `--ff`
under ignored `.pytest_cache/`. This state is not an affected-test selection or
a cache of imported test modules; default coverage remains unchanged.

Use `--print` to inspect the marker expression and candidate files without
running tests. Caller `-m` filters are included;
this preview does not collect individual tests or resolve `-k` filters. It is
optional, not a second required invocation. CLI help owns the remaining options;
do not copy selection rules into another hook or document.

## Browser Workbench Checks

Use the smallest real environment that exposes the loss: data rules stay with
their actual owner, DOM/SVG behavior uses directly mounted browser tests, and
cross-boundary wiring retains representative integration journeys. Limit each
scenario's setup to what exposes that loss; split long journeys that combine
unrelated purposes.

`browser_e2e` uses Python Playwright's sync API for directly mounted Inspector
payloads or Workbench journeys against an ephemeral loopback server and isolated
config. Interactive demonstrations and UI tests use production components with
controllable fixtures, not visual replicas. Keep writes and analysis side effects
isolated from real user state; provide a reset when exploration can leave fixtures
confusing. Workbench uses fake application runners instead of RAW/DLL analysis.
After bootstrap, install the locked Chromium once and run the Workbench checks:

    .venv\Scripts\python.exe -m playwright install chromium
    .venv\Scripts\python.exe -m pytest tests/test_workbench_browser.py -q

CI uses bundled Chromium; `XIC_PLAYWRIGHT_CHANNEL=chrome` (or `msedge`) is an
explicit local opt-in. The Windows test launcher mitigates the
[randomized TCP port defect](diagnostic-ledger.md#windows-randomized-tcp-ports-can-fail-at-low-connection-counts)
while retaining Playwright's stock feature settings; product browsers are unchanged.
A browser lane skipped by the coverage plan is not a passing browser run.
For CI jobs, sharding and watchdog settings, see the
[workflow](../../.github/workflows/ci.yml). On a browser failure, inspect
`browser-trace.zip` and `browser-events.json` under `output/browser-failures/`
(or `XIC_BROWSER_ARTIFACT_DIR`); CI uploads these artifacts. Evidence includes
Inspector contexts and setup or teardown failures.
Request failures are diagnostic, since navigation/download can intentionally
abort requests. Workbench console errors and page exceptions fail tests;
Inspector contexts preserve their page-exception gate and record console errors
as diagnostics.

## RAW And Scientific Validation

Default pytest must not depend on local RAW paths. Synthetic/unit tests prove
engineering regression coverage only. Manual-2RAW, lane-specific subsets, and
85RAW runs are validation evidence only when the active Issue and method owner
require them.

Use the [local execution guide](local-execution.md#validation-scale) for the
method-specific RAW runner. The hosted `test gate` check aggregates test-lane
results; its name does not identify a Python version.

## Verification

Pytest manages temporary files under the worktree's ignored `tmp_runtime/`.
The lightweight `tools.testing.pytest_temp` plugin is loaded by project
`addopts`, including `--noconftest` runs such as the census. Keep project
`addopts`; runs that override it load this plugin explicitly with `-p`.
Temporary-directory initialization errors stop before test assertions with
`ENVIRONMENT_BLOCKED` and exit code 4; they are environment failures, not
census or product assertion failures. Other test errors retain their usual
failure status.
Ordinary `tmp_path` retention uses the call outcome, not teardown failures;
browser evidence follows the full-lifecycle policy above. Concurrent sessions use
distinct numbered directories. `PYTEST_DEBUG_TEMPROOT` and `--basetemp` allow explicit
overrides; do not share a fixed `--basetemp` across concurrent runs.

Run the narrowest existing tests that can falsify the change, then expand in
proportion to affected consumers and public surfaces. The complete hosted
command surface lives in .github/workflows/ci.yml; do not cache its command list
here.

When a run fails, start with its failing node and full failure block, then read
the fixture and expected-value source. Re-run that node after the fix; expand
only for affected consumers. Do not regenerate expected values from the failing
implementation. A selection with no eligible tests is a skip, not evidence of
correctness, even when the selector exits successfully. Use a known relevant
pytest node when the change still needs verification.

Report commands actually run, skipped evidence, and remaining risk. A missing
stable oracle is an unknown to resolve, not a reason to add a test that only
checks structure.
