# Architecture And Public Contracts

This file owns high-level dependency guardrails and public contract surfaces.
The fuller decomposition contract remains in
`docs/architecture-contract.md`.

## Architecture Guardrails

- Preserve dependency direction. Domain logic must not import GUI, workbook
  builders, CLI scripts, process backends, report renderers, or RAW/CSV
  adapters.
- Retain a compatibility facade only when an explicit current public contract or
  a real external consumer still names it. Tests and historical artifacts are
  not consumer evidence.
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

A lossy projection is allowed when its current consumer and reduced claim are
explicit. CI covers only counterexamples that can change that consumer's
decision; audit-only projections need no semantic mutation matrix.

### Alignment owner-migration projection map

The frozen Alignment migration defines the following future boundaries. They
do not change current runtime/public output until their structural and behavior
gates are separately delivered.

| Source owner -> target consumer | Preserved identity/cardinality | Deliberate loss / forbidden reconstruction |
| --- | --- | --- |
| Target-list owner -> Target SearchScope | `TargetKey`, exact `TargetDefinition`, ordered `TargetListSnapshot`, method/evidence constraints | Paths, mutable CSV rows, or target labels alone cannot recreate the scope |
| Discovery/provider configuration owner -> Untargeted SearchScope | exact processing request, canonical sample universe, provider configuration, and method/evidence constraints | A capture adapter cannot widen/rewrite the provider policy or infer cross-request continuity |
| application-owned scope variants -> shared SearchScope boundary | exact discriminated variant and owner-issued content; common schema validation only | The shared boundary cannot remint Target/provider identity or choose application policy |
| Discovery/capture -> CrossSample | exact request, canonical sample, owner-backed hypothesis/physical observation, evidence refs | Area/RT display values, candidate ordinals, and legacy family labels cannot mint members |
| CrossSample -> ModeTrack | immutable `trajectory_input_id`, anchors/members/apexes/order/evidence profile/exclusions | Values, renderer fields, mutable group objects, and Backfill policy are intentionally absent |
| ModeTrack -> CrossSample | typed proposal bound to the exact input snapshot and assessability evidence | Proposal does not carry group-minting, membership-mutation, or value authority |
| Backfill -> CrossSample -> optional final ModeTrack proposal -> CrossSample -> scientific alignment closure | owner-backed `ObservationRepair` decision, exact repair receipt, reconciled group state, and final CrossSample receipt | Repair and proposals do not imply membership or selected value; scientific closure cannot bind only the repair receipt or bypass final CrossSample application |
| finalized core -> workflow projection binding/subject operation | exact Target identity + `TargetRowBinding` -> `TargetCellSubject`, or exact group identity/disposition/lineage + `UntargetRowKey` -> `GroupCellSubject` | `FAM...`, center m/z/RT, order, display text, or Target label alone cannot reconstruct row/cell identity |
| explicit workflow cell subject -> cell decision | `TargetCellSubject` or `GroupCellSubject` plus closed observation/search receipts | Target evidence cannot authorize Group/Untarget cells; human reason strings are not input |
| cell decisions -> same workflow projection completion operation | exact row/cell identity plus typed write/no-write decisions -> inclusion, counts/reasons, Review Queue/archive routing | Projection cannot recompute observations or selected values |
| owner decisions -> alignment result finalizer | exact request, group/refinement/repair closure, typed row/cell/inclusion decisions | Finalizer cannot reopen RAW, regroup, refit, repair, reintegrate, select, or accept optional-mode field soup |
| typed archive owner batches -> internal typed-Parquet staged bundle/reference | native types/nulls, canonical identity/order, schema/version, retention class, exact bundle lineage | TSV strings are not the canonical intermediate; a storage adapter cannot decide science or activate the separate public Parquet-v2 surface |
| typed decisions + archive references -> Explanation | human reasons, review sections, and navigation links | Prose and references cannot mint/reseal decisions; activation and method-evidence authority stay with their owners |
| finalized workflow result + stabilized render/query surfaces -> Delivery | one immutable result variant plus owner-compiled `ArtifactPlan`, staged archive/explanation refs, and destination mechanics | Loose Matrix/metadata maps, caller-authored ledgers, emit booleans, and renderer recomputation are forbidden |

CrossSample enforces at most one identity-bearing member per canonical sample
and mints a successor identity on membership/split/merge change. ModeTrack is
proposal-only. ObservationRepair is bounded to one epoch, with at most one final
ModeTrack rerun when trajectory identity changes; CrossSample reconciles repair,
applies or rejects that proposal, and mints the final receipt. The finalized core
contains the canonical sample universe and a terminal search/observation variant
for every required subject. `ProductionCellDecision` is the sole selected-value
owner and returns either a typed write decision or one of `not_searched`,
`searched_no_observation`, `unresolved`, `rejected_observation`,
`not_quantifiable`, or `unavailable`.

CrossSample is also the sole edge-eligibility authority. Its closed result is
`eligible`, `review_only`, or `excluded`; only `eligible` edges may connect one
bounded atomic ModeTrack neighborhood, and `excluded` is not model-overridable.
CrossSample applies the complete coupled successor proposal or none. An
oversized eligible neighborhood is unassessable rather than split, truncated,
or processed greedily. A repair-derived observation may become anchor-eligible
in a later execution only through fresh method-specific independent-evidence
classification; repair acceptance itself is never that classification.

Evidence persistence, Explanation, and Delivery are separate authorities.
Persisted evidence is a bounded lazy projection of a finalized owner result,
not provisional science or a complete in-memory row list. Required artifact
failure is fail-closed and cannot silently reduce evidence or switch formats;
archive activation requires semantic parity plus total representation-cost
evidence. Query results distinguish scientific absence from lifecycle
unavailability and present-but-invalid content. ADR-0003 accepts the existing
`backfill_evidence_parquet` and `backfill_evidence_parquet_bundle` modules as the
first canonical archive implementation owner, with a second-current-owner gate
before any workflow-neutral extraction. That owner decision is closed; #313
remains blocked by #312 and requires no additional archive-owner refreeze.

The complete search receipt is required to assert
`searched_no_observation`; missing, partial, corrupt, or unbound evidence is
`unavailable`. Blank/zero and human-rendered reason text are never semantic
authority. The six no-write cases are discriminated terminal variants rather
than overlapping flags; crossed request or subject identity is rejected as a
contract error rather than classified as a no-write result.

Workflow projection has two ordered operations under one semantic owner:
binding/subject construction before cell decision, and completion after it. An
identity-bearing group member is a canonical tagged union of owner-backed
`PeakHypothesis` or physical-observation identity; wrapper type alone cannot
remint the same physical subject.

### Persisted Semantic Rehydration

A durable envelope proves its own bytes and shape, not every statement stored
inside it. Semantic rehydration is required only when persisted content crosses
a reachable boundary and a field is decision-bearing: it selects identity or
value, grants writer authority, changes product output, or determines a current
domain/runtime result.

Use the smallest validation that protects that decision:

- Name the actor, independently supplied input, affected decision, and observable
  loss before adding a rehydration boundary. A maintainer coordinating edits to a
  tracked artifact and its manifest is handled by Git review, not a runtime
  threat model.
- Route decision-bearing content through the existing semantic owner. Reuse its
  parser or binding validator; do not duplicate it or introduce a generic
  semantic manager.
- Treat audit-only provenance, labels, counts, locators, and copied hashes as
  untrusted telemetry. They may be displayed or byte-checked, but their
  completeness is not a product acceptance condition unless a current consumer
  uses them to make a decision.
- When the source required to support a claim is absent, reduce or remove the
  claim. Do not manufacture confidence with another manifest, copied digest,
  receipt, or self-sealing hash chain.
- Test the counterexamples that can change the protected decision. Do not build
  exhaustive mutation matrices for metadata that no current consumer trusts.

#### Alignment Backfill evidence records

`xic_extractor.alignment.backfill_evidence_contract` owns the immutable
Backfill record models and continues to re-export the public claim vocabulary.
The dependency-light `xic_extractor.alignment.evidence_claims` leaf owns that
generic vocabulary so outer renderers can consume typed claims without gaining
a transitive RAW-reader dependency. Backfill claim-census semantics remain with
`backfill_evidence_owner`; only the strict `backfill_evidence_tsv` and
`backfill_evidence_parquet` readers may mint `EvidenceQueryResult`. A caller-made
record/claim tuple is not a reader result and cannot cross an explanation or
other semantic boundary. The focused adapters own strict rehydration for the
existing `alignment_backfill_cell_evidence.tsv` and
`skipped_evidence_ledger.tsv` projections. This contract does not change either
writer schema, path, output level, or publication default.

- `BackfillEvidenceRecord` preserves the exact 33-field compact row and derives
  identity as `(feature_family_id, group_hypothesis_id, sample_stem)`. Its row
  content remains `structure_bound`: the projection omits the complete
  `AlignedCell` and `ProductionDecisionSet` inputs needed to recompute cell and
  product decisions. The existing writer may leave `group_hypothesis_id` or
  `public_family_id` empty; those rows remain valid `structure_bound` records,
  but omitted relationships stay `unavailable`. The owner result separates the
  canonical group assignment from the public compatibility-label relationship:
  a group assignment becomes `owner_rehydrated` only after the supplied
  group-to-feature relation, sample, and optional Discovery candidate pass;
  `public_family_id` becomes owner-bound only when a supplied group-to-public
  relation also matches. The public label remains compatibility metadata, not
  canonical identity proof. The combined cross-artifact claim closes only when
  both relationships close, and never raises decision fields or the complete
  cell to owner authority.
- `SkippedEvidenceRecord` is the complete persisted form of the existing
  Backfill-scope producer dataclass. Its parser reconstructs typed values and
  recomputes the scope/disposition, predicate, request-count, window, and source
  invariants. A supplied family/sample/scope/predicate/source context may close
  its cross-artifact binding. The persisted `full_audit_available` literal stays
  `structure_bound`; it is not a runtime execution receipt.
- Both readers require the exact current header and reject missing, unknown,
  duplicate, or reordered columns; malformed/non-finite fields; conflicting
  duplicate composite identities in a bounded lookup; stale predicate/source
  bindings; and cross-key substitution. Formula-protected machine identifiers
  are resolved against the supplied owner universe; the reader accepts the one
  owner-backed canonical spelling and rejects genuinely ambiguous raw-versus-
  apostrophe identities. Distinct records remain source-order preserving. A
  zero-row lookup always reports the absence claim as `unavailable`.
- `backfill_evidence_parquet` is an optional storage adapter over the same
  canonical string projection and owner models. Its post-run shadow bundle is
  `diagnostic_only`: it stores only the two large Backfill evidence tables,
  preserves source order with an explicit row ordinal, and derives the same
  query claims as the TSV adapter. Owner-bound predicate pushdown admits the
  canonical and current formula-protected storage spellings, then delegates the
  returned rows to the same owner rehydration result. It does not replace a TSV
  writer or become a semantic owner.
- `backfill_evidence_parquet_bundle` publishes only to a previously absent
  versioned directory after staged schema/hash/row-count validation. Its
  manifest uses relative leaf locators and binds one pre-conversion source
  snapshot by verifying every source again after conversion; optional run
  metadata remains byte-bound. Failed build or validation leaves no visible final
  bundle. Rollback is removal or disregard of the complete optional bundle while
  the TSV defaults remain authoritative compatibility surfaces.

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

For the frozen Alignment successor, the writer-adjacent input is one immutable
Targeted or Untargeted finalized result plus an owner-compiled `ArtifactPlan`
and output-location mechanics. The plan derives from the projection contract,
delivery intent, authority-manifest snapshot, and named compatibility consumers.
The delivery finalizer returns one typed receipt and publishes required claim-
closure groups atomically. It cannot accept a pre-final Matrix, loose
`AlignedCell` population, caller-authored selected values/reasons/counts, or
independent `emit_*` flags.

Executable closure requires a writer-signature oracle plus re-seal attacks over
every persisted writer-like field. A consistently re-hashed audit mutation must
either leave the owner-derived result unchanged or fail before final output; it
must never alter a writer cell or value.

## Runtime Construction Authority

`xic_extractor.execution_runtime_owner` owns one mechanical runtime aggregate
and one finalization boundary. It is the facade that applies adapter facts
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
- A stage that needs to render in-flight acquisition diagnostics may submit
  only owner-minted `TraceAcquisitionBatch` receipts to
  `derive_runtime_acquisition_metrics`. Its sealed `RuntimeAcquisitionMetrics`
  result is the sole narrow count projection; it does not construct a ledger,
  settle work, or bypass atomic attempt finalization.
- Discovery may render one in-flight physical RAW-open diagnostic without
  claiming a complete runtime lifecycle. Its parent mints one unique,
  pickleable `RawAcquisitionSessionCapability` for the exact acquisition source;
  `raw_reader.open_raw` alone privately mints the matching
  `RawAcquisitionSessionReceipt`, and only after source verification and a
  successful vendor-handle open. The process result transports that typed
  receipt, `derive_runtime_raw_open_metrics` revalidates the exact capability
  binding and derives `raw_open_count`, and `TimingRecorder` only renders the
  owner projection. One matching receipt means measured count `1`; absence means
  unavailable, never inferred zero; mismatched, cross-capability, untyped, or
  duplicate receipts fail closed. A receipt remains truthful if later science
  or output work fails because the physical open already occurred.
- The narrow RAW-open receipt proves neither cleanup nor resource/work
  settlement, artifact authority, terminal outcome, or finalization. It must not
  enter a lifecycle ledger or act as a settlement-only alternative to
  `execute_runtime_attempt`. `scripts/check_architecture_census.py` restricts its
  private production minter to `xic_extractor.raw_reader`; tests may construct
  typed fixtures only to exercise the boundary.
- Owner Backfill's internal peak-fact shadow comparison may submit bounded typed
  per-candidate work receipts, one runtime-owned RAW-open projection, and typed
  artifact-I/O telemetry to `derive_runtime_peak_fact_cost_metrics`. The runtime
  owner alone validates work-key uniqueness, derives trace/smoothing/integration
  and sealed/unavailable-identity cardinality, aggregates complete finite elapsed
  observations, and mints the returned projection. The direct scientific owner
  exposes no aggregate cost receipt, and the process boundary transports only
  the runtime-owner projection. A receipted process RAW session reports one
  measured open; a missing capability, receipt, duration, or artifact-I/O sample
  remains unavailable rather than becoming zero. This narrow diagnostic helper
  neither settles lifecycle work nor grants scientific, Matrix, writer,
  finalization, cleanup, or artifact authority, and it is not a substitute for
  `execute_runtime_attempt` on an activated public runtime path.
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
