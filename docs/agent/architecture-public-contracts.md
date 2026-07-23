# Architecture And Public Contracts

This file owns high-level dependency guardrails, CodeGraph usage, and public
contract surfaces. The fuller decomposition contract remains in
`docs/architecture-contract.md`.

## Architecture Guardrails

- Preserve dependency direction. Domain logic must not import GUI, workbook
  builders, CLI scripts, process backends, report renderers, or RAW/CSV
  adapters.
- Keep public entry points and compatibility facades thin while moving behavior
  into focused modules.
- Treat `tools/diagnostics/` as maintained product-adjacent code. CLIs
  orchestrate; reusable loading, classification, models, summaries, plotting,
  and writers belong in package modules.
- Diagnostic writers render only. They must not recompute domain evidence or
  re-scan RAW files.
- Shared dataclasses and protocols belong in small model/contract modules when
  they prevent circular imports or schema drift.
- Move behavior before changing behavior. Add characterization tests before
  moving uncovered behavior.
- Separate real-data validation from normal unit tests.

## CodeGraph

- Prefer the `codegraph` CLI for broad indexed search, status, files, and
  context-building when it can answer the question cleanly.
- Use CodeGraph MCP when the query needs capabilities the CLI does not expose or
  exposes less clearly, especially caller/callee/impact tracing, single-symbol
  source lookup, or explicit MCP-requested structural context.
- For subagent reviews, tell reviewers to start with `codegraph` CLI, `rg`, and
  targeted file reads for simple no-use checks. Allow CodeGraph MCP when the
  review asks a structural caller/callee/impact question or CLI output is
  insufficient.

CodeGraph is a reconnaissance aid, not a clean-checkout CI authority. Its local
index can belong to another worktree or contain unresolved references. The
clean-checkout architecture baseline therefore uses two deliberately narrow
surfaces: `scripts/check_architecture_census.py` for disposable checkout facts,
and Import Linter's `.importlinter` contracts for blocking static dependency
directions.

## Executable Census And Dependency Gate

- The architecture census is derived and disposable. It has no tracked TSV or
  JSON registry and grants no owner, lifecycle, public-output, or product
  authority.
- Human decisions remain in this contract, `docs/architecture-contract.md`,
  `docs/project-layout.md`, and the existing artifact/product control-plane
  owners. Census rows point to those owners instead of copying their claims.
- The checker rebuilds Python module paths, Grimp's static internal import graph,
  declared console scripts, and declared package-discovery patterns from current
  repo state. Console scripts and package patterns are declarations only; they
  do not prove an installed wheel, a callable command wrapper, or artifact
  closure.
- `.importlinter` is the single coarse static dependency floor. It covers
  Python's static import graph, including relative and type-checking imports. It
  deliberately does not interpret `importlib`, `__import__`, loader wrappers,
  plugin registries, or other runtime module resolution. Architecture-sensitive
  dynamic loaders need a targeted owner contract instead of another repository-
  wide alias parser.
- The census and dependency floor do not interpret PyInstaller specs, validate
  installed entry points, or duplicate workflow, diagnostics-index, and pytest-
  shard gates. Those remain with their executable owners. Installed package and
  frozen-application closure belong to their later artifact-level work.
- Import Linter ignores are permitted only for active migration debt. A new
  ignore must cite an owner and rationale next to the contract, and must set
  unmatched-ignore alerting to `error` so removal of the import retires the
  exception mechanically. No ignores exist in the baseline.
- Optional JSON output is permitted only outside the repository or under an
  ignored path. It records HEAD, dirty state, and a census content digest; it is
  a review/debug projection, not a canonical registry.

## Public Contracts

Treat these as public unless an approved plan explicitly changes them:

- CLI commands under `scripts/`
- `xic_extractor.extractor.run`
- `xic_extractor.signal_processing.find_peak_and_area`
- `scripts.csv_to_excel.run`
- config keys and example/default settings
- TSV/CSV schemas
- workbook sheet names, order, hidden states, and columns
- HTML report path naming
- run metadata keys
- persisted identifiers and matrix row/cell identity
- domain-selected peaks, areas, values, and counted detections
- cross-repo handoff schemas and contracts
