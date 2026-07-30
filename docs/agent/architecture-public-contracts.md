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

## Runtime Construction Authority

Package 0 owns one mechanical runtime aggregate and one finalization boundary.
`xic_extractor.execution_runtime_owner` is the facade that applies adapter facts
to `RuntimeAttemptAggregate`, derives observability, and returns one
`RuntimeFinalizationReceipt`. The focused `execution_runtime*` model/reducer
modules remain its internal state-machine and canonical-serialization helpers;
they are not alternate construction APIs for consumers.

The boundary is closed as follows:

- Targeted, Alignment, and future consumers may translate producer results only
  into typed work settlements, resource settlements, artifact registrations,
  and acquisition receipts. They may also provide `RuntimeTelemetrySamples` for
  facts the runtime cannot independently observe: monotonic offsets, RSS, and
  runtime-mediated artifact I/O.
- Lifecycle topology, accepted work/resource/artifact state, RAW-open count,
  logical XIC request/batch/call/point counts, terminal proposal,
  acknowledgement, dispositions, and outcome are owner-derived. Callers must
  not submit a `RuntimeObservabilityLedger`, scalar mechanical counts, a
  coordinator record, or a supervisor record.
- Invalid manifest content remains an owner state, not an exception-only side
  channel. It follows `attempt_queued` -> `manifest_preflight_failed` ->
  `terminal_proposal(failed)` through the same finalization receipt and recovery
  marker, with empty registries and no fabricated `attempt_started`. The atomic
  owner entry point skips the worker-batch provider on this path, so Targeted and
  Alignment cannot dispatch work before discovering the terminal preflight state.
  Reservation plans supplied with an already rejected manifest are not iterated
  or accepted into state; finalization still completes with empty registries.
- A worker exists in observability only when an owner reservation has kind
  `PROCESS_WORKER` and its accepted open outcome proves it started. Reserved,
  `NOT_OPENED`, and `OPEN_FAILED` resources do not create `worker_started` or a
  worker RSS obligation. Metric `measurement_source` is fixed by the runtime
  owner; callers provide values, not provenance for owner-derived projections.
- One accepted finalization receipt carries the exact coordinator and supervisor
  pair through persistence, marker replay, and application results. Separate
  caller-supplied finalization halves are forbidden even when their attempt IDs
  match.
- Public consumer paths must close the full lifecycle. A settlement-only service
  or adapter entry point is a bypass, not a compatibility facade. Consumers call
  `execute_runtime_attempt`; preparation, batch application, ledger derivation,
  and finalization are owner-internal primitives. Private pure reducers may remain
  for focused state-model tests but are absent from low-level public exports.
- Replayed typed receipts are idempotent only when their complete owner facts are
  equal. Conflicting work still fails the attempt closed; acquisition telemetry
  that was physically observed remains countable even when a conflicting replay
  prevents that result from being exposed as success.

`scripts/check_architecture_census.py` enforces this construction authority with
an origin-aware AST gate over all repository Python, including `scripts/` and
`tools/`. Outside the runtime owner and its narrow state-model tests it permits
only the explicit public typed-fact and atomic execution calls; direct lifecycle,
ledger, aggregate, and finalization construction and all partial primitives fail
for absolute/relative imports, direct calls, higher-order values, or lexical
aliases. An unrelated local shadow cannot erase the imported symbol's origin,
function-local authority imports are forbidden, and an owner-internal import/
re-export bridge fails at its root when it exposes executable internal authority.
Consumer input annotations, including quoted forward references, and generated
dataclass inputs are checked by owner-derived projection type and dataclass
decorator origin, not by parameter or decorator spelling. Consumer inputs may
not carry any canonical mechanical metric as a scalar, regardless of parameter
position or spelling aliases.

Metric labels are public schema vocabulary, not capabilities. Once a consumer
can read an owner result, Python cannot make its strings non-copyable; a copied
dictionary or named record is therefore explicitly untrusted and never becomes
runtime authority merely by using canonical labels. Authority requires the
owner-minted ledger bound into the accepted finalization receipt and its exact
canonical bytes/digest. Any future persistence, report, or writer path claiming
canonical runtime metrics must consume that receipt/owner serializer; it must
not accept a free-form mapping or scalar bundle as a substitute. The AST gate
enforces construction and ingress authority, not impossible string-level DLP.

The aggregate, observability ledger, and finalization receipt also require a
private owner mint capability at runtime. The capability is a required
construction-only `InitVar`; it is not retained on the immutable instance and
therefore cannot be copied by normal `dataclasses.replace` reconstruction.
All three authority types are runtime-final, so normal subclass dispatch cannot
override mint validation. Normal downstream construction fails even if a type
annotation or a new static import shape exposes the class object; explicit
access to private owner state remains forbidden and is separately rejected at
the import root.

Every low-level module must define exactly one literal tuple `__all__` equal to
its allowlist. Static binding/mutation/reflection checks and an evaluated runtime
export invariant reject missing, dynamic, rebound, deleted, aliased, incomplete,
or expanded exports. Unrelated same-name methods remain allowed. State-model
test owners form a closed test island: shared tests may build telemetry samples
and typed worker receipts, but may not import those modules under a renamed
ledger-helper API. Direct ledger fixtures remain limited to the
observability/finalization owner tests that exercise malformed state and schema
validation.

This is a source architecture gate, not a Python security sandbox. It covers
explicit imports, aliases, callbacks, annotations, dataclass inputs, and
statically expressed reflection used by normal repository code. Deliberately
obfuscated `eval`/dynamic-module reflection is outside the static proof and is
forbidden by this contract; the immutable runtime export check remains the
executable backstop for the actual low-level public export set.

This changes construction authority, not persisted wire shape. Coordinator v3,
observability v1, recovery-marker v3, canonical bytes/digests, and replay/CAS
semantics remain unchanged.

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
- The same checker runs the targeted runtime-construction AST gate described
  above. That gate enforces a named owner boundary; it does not turn the
  disposable census into an authority registry.
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
