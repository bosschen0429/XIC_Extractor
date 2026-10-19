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

## Test Placement

Pytest discovery and dependencies are owned by pyproject.toml. Keep tests under
tests/test_*.py and name them after observable behavior. Use tmp_path for files,
function-scoped fixtures by default, and tests/fixtures for reusable payloads.
Split a file when it accumulates unrelated responsibilities; line count alone
is not the contract.

## Affected-Test Selection

tools.testing.test_selection derives which tests a change can affect from the
import graph and the paths tests and code name; a new test file needs no
registration. Pull-request CI runs that selection, master runs every test, and
anything the derivation cannot place runs every test. Locally:

    .venv\Scripts\python.exe -m tools.testing.test_selection
    .venv\Scripts\python.exe -m tools.testing.test_selection --all

Do not copy its rules into another hook or document.

Tests marked ledger_snapshot pin recorded validation values and are excluded
individually from default CI. Run that lane with:

    .venv\Scripts\python.exe -m tools.testing.test_selection --snapshots

A legitimate re-analysis may change a snapshot. Keep rejection behavior and
dependency-failure checks in ordinary CI when their oracle is still current.

## Browser Workbench Checks

`browser_e2e` uses Python Playwright's sync API against an ephemeral loopback
server and isolated config, with fake application runners instead of RAW/DLL
analysis. After bootstrap, install the locked Chromium once and run the lane:

    .venv\Scripts\python.exe -m playwright install chromium
    .venv\Scripts\python.exe -m pytest tests/test_workbench_browser.py -q

CI uses bundled Chromium; `XIC_PLAYWRIGHT_CHANNEL=chrome` (or `msedge`) is an
explicit local opt-in. Browser deadlines are hang watchdogs: CI sets
`XIC_BROWSER_DEADLINE_SCALE=3` to widen every one of them for runner stalls,
while local runs keep Playwright's defaults. Failed tests retain `browser-trace.zip` under their
`tmp_runtime/` test directory; CI uploads those traces. Successful traces are
cleaned with pytest's temporary files. `tools.testing.test_selection` owns
change relevance; master runs the lane.

## RAW And Scientific Validation

Default pytest must not depend on local RAW paths. Synthetic/unit tests prove
engineering regression coverage only. Manual-2RAW, lane-specific subsets, and
85RAW runs are validation evidence only when the active Issue and method owner
require them.

Use the method-specific acceptance check when RAW-backed evidence is needed.
The runner owns input checks and run metadata; tests do not require a separate
RAW preparation packet. Do not infer scientific or production readiness from
passing pytest.

## Verification

Pytest manages temporary files under the worktree's ignored `tmp_runtime/`.
Failed tests retain their `tmp_path` files for inspection within pytest's
retention window; passing tests are cleaned. Concurrent sessions use distinct
numbered directories. `PYTEST_DEBUG_TEMPROOT` and `--basetemp` remain available
for explicit overrides; do not share a fixed `--basetemp` across concurrent runs.

Run the narrowest existing tests that can falsify the change, then expand in
proportion to affected consumers and public surfaces. The complete hosted
command surface lives in .github/workflows/ci.yml; do not cache its command list
here.

Report commands actually run, skipped evidence, and remaining risk. A missing
stable oracle is an unknown to resolve, not a reason to add a test that only
checks structure.
