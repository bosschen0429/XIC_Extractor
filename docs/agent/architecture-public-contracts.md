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

## Boundary Projection Closure

Treat every non-trivial source-to-target conversion as a contract boundary:
domain model to adapter envelope, worker result to settlement, typed record to
map or grouping, state to canonical bytes, receipt to recovery marker, or
producer output to comparator/writer. Before implementation, record:

| Field | Required decision |
| --- | --- |
| source owner and target consumer | Which owner defines the facts and which consumer relies on them |
| identity and cardinality | The complete identity tuple and allowed one-to-one, one-to-many, or many-to-many shape |
| preserved semantics | Values, ordering, terminal state, failure detail, provenance, and authority the target must retain |
| deliberate loss | Every source fact intentionally omitted, why the consumer does not need it, and which claim must therefore remain unavailable |
| durable binding | Schema version, canonical bytes/digest, replay, recovery, and event correlation where the target persists or certifies facts |
| oracle | The test or artifact that proves preservation and makes crossed or lossy projections go red |

Apply these invariants:

- Keep composite identity composite through sets, maps, joins, registries,
  comparisons, digests, and persisted receipts. A leaf artifact/row ID must not
  stand in for `(owner scope, work key, ID)` when the owner contract uses the
  tuple.
- Validate cardinality at the semantic owner. Exercise matching and conflicting
  duplicates, identical payloads under different keys, reversed order, partial
  input, and missing members.
- Preserve typed terminal failure and provenance through aggregation and durable
  recovery. A separately persisted event ledger may carry event sequence, but
  it must be exact-bound and retain the correlation identity.
- Version a persisted/canonical schema when its semantic shape changes. Include
  every fact that the receipt, comparator, acknowledgement, or replay claims to
  certify.
- Keep adapters translational. They may reject invalid projections, but must not
  silently recompute, collapse, or invent owner facts.

A lossy projection is allowed only when the omitted fields and reduced claim are
explicit. CI closure for a contract-bearing projection requires a source-to-
terminal round trip plus the applicable cross-key, duplicate, malformed,
reordered, partial, failure, serialization, recovery, and replay counterexamples.

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
- Import Linter checks indirect static dependencies. Its 17 exact baseline
  ignores freeze current source-boundary edges that collapse onto four
  underlying `raw_reader` dependencies in `neutral_loss`, `extractor`,
  `extraction.istd_prepass`, and `extraction.pipeline`; #117 R6 and #122 own
  those shared-kernel and projection debts. New caller edges remain forbidden.
  A new ignore is permitted only for active migration debt, must cite an owner
  and rationale next to the contract, and must set unmatched-ignore alerting to
  `error` so removal of the edge retires the exception mechanically.
- Optional JSON output is permitted only outside the repository or under an
  ignored path. It records HEAD, dirty state, and a census content digest; it is
  a review/debug projection, not a canonical registry.

## Public Contracts

Command exposure and lifecycle are separate. A command is public when it is
declared in `[project.scripts]` or named by an explicit product/downstream
contract. Being located under `scripts/`, directly runnable, imported, tested,
or included in package discovery does not by itself make it public. A produced
artifact may still be public even when its command is an uninstalled operator
surface. Lifecycle decisions and the bounded census live in
[`script-tool-lifecycle.md`](script-tool-lifecycle.md).

Treat these as public unless an approved plan explicitly changes them:

- declared console scripts and contract-bearing commands
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
