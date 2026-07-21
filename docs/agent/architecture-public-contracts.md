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
