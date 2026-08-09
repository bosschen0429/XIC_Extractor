# Architecture And Public Contracts

This file owns high-level dependency guardrails and public contract surfaces.
The fuller decomposition contract remains in
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

### Persisted Semantic Rehydration

A durable envelope proves only the bytes, schema, location, and lineage owned by
that envelope. JSON objects, TSV rows, free-form mappings, receipts, replays, and
other persisted payloads are untrusted projections after deserialization; they do
not regain domain or runtime authority merely because an embedded ID, digest,
status, count, locator, or outer-envelope hash matches.

Before a consumer may claim an embedded fact as owner-valid, it must route the
complete persisted content through the original semantic owner's existing parser,
constructor, or binding validator. That owner must reconstruct a typed value and
recompute its canonical identity and invariants from the content. Comparing a
payload's self-reported identity with another copied identity is not validation,
even when a caller can re-seal the outer envelope after tampering.

Apply these ownership rules:

- Envelope, storage, and lineage helpers may validate exact bytes/digests, schema
  version, path, provenance, and envelope cardinality. They must delegate embedded
  domain payloads to their existing semantic owners rather than duplicating owner
  constructors or introducing a generic central semantic manager.
- If the persisted target omits content required by the owner validator, the
  downstream claim must remain unavailable. Add the missing content through a
  versioned schema or reduce the claim; do not infer it from IDs, summaries,
  navigation locators, or a sibling artifact.
- Terminal status, disposition, counts, and current-state claims must be derived
  from the accepted owner result or current owner snapshot. Persisted literals may
  be compared for audit, but cannot be the source of the certified result.
- Positive tests must round-trip persisted content through the owner. Negative
  tests must mutate every owner-bearing content class while preserving the
  embedded ID, then re-seal the outer envelope and expect rejection; also cover
  unknown/duplicate fields, cross-key substitution, stale owner snapshots,
  reordered members, and partial payloads where applicable.

#### Alignment Backfill evidence records

`xic_extractor.alignment.backfill_evidence_contract` owns the immutable record
and claim models, while its focused `backfill_evidence_tsv` adapter owns strict
TSV rehydration for the existing `alignment_backfill_cell_evidence.tsv` and
`skipped_evidence_ledger.tsv` projections. This contract does not change either
writer schema, path, output level, or publication default.

- `BackfillEvidenceRecord` preserves the exact 33-field compact row and derives
  identity as `(feature_family_id, group_hypothesis_id, sample_stem)`. Its row
  content remains `structure_bound`: the projection omits the complete
  `AlignedCell` and `ProductionDecisionSet` inputs needed to recompute cell and
  product decisions. Supplied owner group, sample, and Discovery-candidate keys
  may be accepted as an `owner_rehydrated` cross-artifact binding, but that does
  not raise the decision fields or complete cell to owner authority.
- `SkippedEvidenceRecord` is the complete persisted form of the existing
  Backfill-scope producer dataclass. Its parser reconstructs typed values and
  recomputes the scope/disposition, predicate, request-count, window, and source
  invariants. A supplied family/sample/scope/predicate/source context may close
  its cross-artifact binding. The persisted `full_audit_available` literal stays
  `structure_bound`; it is not a runtime execution receipt.
- Both readers require the exact current header and reject missing, unknown,
  duplicate, or reordered columns; malformed/non-finite fields; conflicting
  duplicate composite identities in a bounded lookup; stale predicate/source
  bindings; and cross-key substitution. Distinct records remain source-order
  preserving. A zero-row lookup always reports the absence claim as
  `unavailable`.

### Closed Writer Transaction Authority

A writer-adjacent finalizer must not reconstruct authority from several loose,
caller-authored persisted projections. Proposal rows, decision rows, expected
diffs, transfer receipts, checked manifests, and terminal counts may be useful
audit or review artifacts, but an outer hash chain cannot make their combined
scientific claim complete.

For a transaction that crosses persisted human review into a current owner run:

- one non-writing rehydration boundary may validate the complete persisted
  review evidence and mint one immutable typed review set with an explicitly
  reduced claim;
- the writer finalizer accepts only the current typed scientific owner result
  plus that owner-validated review set and output-location mechanics;
- cell identity, selected value, eligibility, expected diff, manifest rows,
  counts, rollback, and terminal status are derived inside that transaction
  from the current owner result; callers may not supply those facts through
  paths, mappings, receipts, or scalar parameters;
- audit receipts may be bound into audit lineage, but their fields cannot select
  a cell, value, state, or writer decision; and
- the final output becomes visible only after the internally derived manifest,
  existing writer owner, expected-diff oracle, rollback oracle, and terminal
  receipt all succeed.

Executable closure requires a writer-signature oracle plus re-seal attacks over
every persisted writer-like field. A consistently re-hashed audit mutation must
either leave the owner-derived result unchanged or fail before final output; it
must never alter a writer cell or value.

## Runtime Construction Authority

Package 0 owns one mechanical runtime aggregate and one finalization boundary.
`xic_extractor.execution_runtime_owner` is the facade that applies adapter facts
to `RuntimeAttemptAggregate`, derives observability, and returns one
`RuntimeFinalizationReceipt`. The focused `execution_runtime*` model/reducer
modules remain its internal state-machine and canonical-serialization helpers;
they are not alternate construction APIs for consumers.

The boundary is closed as follows:

- Targeted, Alignment, and future consumers may submit one owner-bound
  `RuntimeCancellationRequest` and translate producer results only into typed
  work settlements, resource settlements, artifact registrations, and
  acquisition receipts. The runtime owner validates the request against the
  exact processing attempt and alone appends the accepted-cancellation event;
  once accepted before dispatch, the atomic owner skips the worker-batch
  provider, settles every manifest work unit as cancelled, and closes reserved
  resources as not opened without accepting artifacts or acquisitions.
  Rejected manifest preflight remains failed rather than being relabeled as
  cancelled. Consumers may also provide `RuntimeTelemetrySamples` for facts
  the runtime cannot independently observe: monotonic offsets, RSS, and
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
- A successful Targeted or Alignment application result is a complete
  acquisition projection. An empty `successful_observation_batches` value is
  valid only when the owner ledger's logical request, batch, adapter-call, and
  trace-point counts are all zero. Failed or quarantined results may deliberately
  hide observation batches while retaining physically observed nonzero counts;
  that loss must never be reused as a successful no-acquisition claim.
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

Package 1 comparator receipts follow the same construction rule. One public
experiment entry creates a dedicated workspace and product-output root,
overwrites the Targeted request's output paths, pins the root identity, and
constructs the Targeted service with the comparator-owned legacy-runner
sentinel. It accepts only exact primitive no-RAW jobs; fixed Targeted and
Alignment workers execute and drain synchronously before the private receipt
mint. Callers cannot provide request factories, lifecycle/result providers,
finished application/process results, executor factories, split begin/mint
operations, counts, paths, or receipt construction/replacement. The root
inventory proves final-state path/byte parity, not transient filesystem-I/O
absence. Legacy writer non-invocation is instead owned by the sentinel, fixed
worker ingress, and Alignment dependency boundary. Scientific and Matrix
absence likewise remains a separate direct integration and dependency-boundary
claim rather than a self-asserting status field.

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

The active write schemas are coordinator v4, observability v1, and
recovery-marker v4. Recovery-marker reads retain canonical v3 outer-schema
compatibility. V4 adds a pre-bound conditional-reservation fact and the typed
`condition_not_activated` resource outcome so an owner-declared conditional
reservation can close without allocating a fake resource. Required reservations
cannot use that outcome; `not_opened` without accepted cancellation remains
fail-closed. The
exact-equality/fail-closed replay and CAS rules are unchanged. Causal chronology
normalization intentionally changes canonical
event order, bytes, and digests for affected running attempts; pre-correction
diagnostic markers therefore do not claim byte parity with corrected output.

## Executable Census And Dependency Gate

- The architecture census is derived and disposable. It has no tracked TSV or
  JSON registry and grants no owner, lifecycle, public-output, or product
  authority.
- Human decisions remain in this contract, `docs/architecture-contract.md`,
  `docs/project-layout.md`, and the existing artifact/product governance
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
