---
status: accepted
---

# Select tests by what can read the changed input

An import graph finds Python callers. It does not find a test that opens a
preset by name, reads a document listing that preset, or scans Python source
as text. Use those read paths alongside imports rather than maintaining a
second registry of tests.

- **Follow readers, not incidental names.** A resource key can name data
  without its extension. A document needs filename evidence: its stem and
  extension may be constructed separately, but a setting named `alignment`
  alone does not mean the code reads `alignment.md`. Match fixture paths
  relative to the tests directory; a bare package root is too broad.
  Exclude prose from package-source matches, comparing token positions to AST
  docstring spans in UTF-8 bytes so non-ASCII text does not shift a boundary.
  If a test names the file, select that test, not every test importing its
  helpers. If a module scans Python sources, select its test consumers when a
  Python file changes.
- **Unknown is not empty.** A known empty diff needs no dependency graph.
  A rename must include the old path: the importer still using it may be the
  test that fails. A deleted module has no current graph node. When a change
  cannot be placed, or affects shared session configuration, do not narrow
  the file selection or rule out browser coverage.
- **File selection and lane selection answer different questions.** A
  documentation index or shared basename can justify rerunning a regression
  test without justifying a Chromium run. Browser relevance follows executable
  readers, their test markers, and shared browser inputs, including fixture
  helpers. Installed and frozen builds depend on entire shipped packages and
  offline resources, not just their entry points. Those checks run in CI for
  relevant changes; locally they are opt-in.
- **Test the CI consumer separately from the selector.** Supply all, empty and
  subset plans to the actual workflow steps. Stand in for expensive child
  commands, not PowerShell: its ordering, quoting and exit codes decide what
  runs. These replays follow the workflow and its selection tooling, not every
  change to the product.

The [selector](../../tools/testing/test_selection.py) owns exact path and marker
rules. See the [testing guide](../agent/testing.md#affected-test-selection)
for commands and the [CI lane decision](0004-isolate-ci-lanes.md) for how
selected work is scheduled.
