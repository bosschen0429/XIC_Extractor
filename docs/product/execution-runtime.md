# Execution Runtime Contract

This page defines the minimum workflow-neutral execution contract for Issue
#128. It covers how one lineage-identified `ProcessingAttempt` is scheduled,
observed, stopped, cleaned, and retried without taking over scientific or
product decisions.

No integration evidence above `diagnostic_only` currently exists for this
contract. That evidence posture belongs to future validation artifacts, not to
this topic page. The contract grants no selected-value, counted-detection,
Matrix, workbook, Backfill-write, activation, publication, or export authority.

## Decision

Targeted extraction, Alignment, and RAW-backed diagnostics currently implement
their own mixtures of process scheduling, RAW session ownership, cancellation,
failure collection, caching, and timing. The shared seam is not a generic
pipeline or another evidence model. It is a small mechanical runtime:

> One runtime state machine manages the lifecycle of one externally identified
> `ProcessingAttempt`, a closed required-work manifest, runtime resources,
> events, and non-authoritative temporary artifacts. It reports what executed;
> it does not decide what the evidence means or what the product publishes.

The runtime consumes request, attempt, source, artifact-dependency, and
acquisition-session compatibility tokens from their existing owners. It must
not derive substitutes from paths, timestamps, worker order, sample display
names, process IDs, or runtime-local cache keys.

The minimum contract succeeds when three properties are mechanically
testable:

1. for an attempt with a durable terminal acknowledgement, no listed work unit,
   reserved resource, or produced artifact can disappear from the terminal
   attempt record;
2. cancellation, worker loss, retry, cleanup, and cache reuse cannot turn
   incomplete execution into scientific absence or deliverable output; and
3. Targeted and Alignment can report comparable execution cost without moving
   either workflow's scientific or writer authority into the runtime.

## Ownership Boundary

The execution runtime owns:

- validating that an owner-supplied required-work manifest is closed and has
  unique opaque work-unit keys before scheduling;
- scheduling those work units onto serial or process workers;
- reserving acquisition-handle and other resource records before allocation,
  then managing their lifetime inside workers;
- bounded cooperative cancellation, pending-work suppression, and worker-loss
  escalation;
- scheduling a lineage-owner-authorized retry attempt using owner-supplied
  attempt and dependency tokens;
- typed per-unit, scheduler, worker-loss, and cleanup failure aggregation;
- one terminal cleanup record for every reserved resource and idempotent
  cleanup execution;
- volatile cache lifecycle and persistent-cache access telemetry;
- attempt, work-unit, resource, cache, artifact, and metrics events;
- calls, opens, points, elapsed time, memory, and artifact-I/O metrics;
- runtime-temporary artifacts being quarantined or offered to their owner.

It does not own:

- the meaning of an acquisition query, scan selection, trace, or negative
  evidence; those remain with the acquisition evidence owner;
- `ProcessingRequest`, `ProcessingAttempt`, or `EvidenceArtifact` identity,
  dependency compatibility, integrity, staleness, and lineage semantics; those
  remain with [`processing-lineage.md`](processing-lineage.md);
- candidate identity, `EvidenceVector`, `PeakHypothesis`,
  `IntegrationResult`, Alignment, `CellObservation`, or Backfill decisions;
- Targeted or Untargeted policy, review conclusions, counted detections,
  selected areas, Matrix cells, workbook rows, activation, or export;
- scientific cache-key or acquisition-session compatibility meaning. The
  runtime accepts opaque owner-supplied tokens and cannot invent or reinterpret
  them;
- storage engine, schema, location, or retention policy. It may only execute
  owner-supplied lifecycle actions for registered temporary resources and
  caches while respecting lineage pins.

## Minimal Runtime Vocabulary

These concepts are internal mechanics, not user-visible review tags.

| Term | Meaning | Not the same as |
| --- | --- | --- |
| `AttemptRuntime` | Mutable in-memory state for exactly one lineage-identified `ProcessingAttempt` | The attempt identity or a product run |
| `RequiredWorkManifest` | Immutable, owner-supplied complete set of unique opaque work-unit keys required for this attempt | A discovered result or dynamic success list |
| `WorkUnit` | One manifest member that can be independently scheduled and settled | A scientific observation or delivery cell |
| `Worker` | Serial context or process that executes work units and declares its hard-cancel capability before scheduling | A stable identity or authority boundary |
| `AcquisitionSession` | One reserved resource record in one worker for one attempt, source, and opaque compatibility token; it may own one adapter handle | An `AcquisitionSource` identity |
| `RuntimeEvent` | Ordered fact about scheduling, resource, outcome, cleanup, cache, or metrics | Scientific evidence |
| `ValidatedDependencyToken` | Lineage-owner approval bound to the new attempt and exactly one manifest work-unit key, plus an opaque immutable dependency reference | Runtime-created identity or compatibility proof |

The manifest is closed before the attempt starts. An accepted running manifest
contains at least one work unit. An empty owner universe, duplicate or blank
keys, a missing owner completeness attestation, submission failure, or an
attempt to add later work fails closed. The runtime cannot discover that the
owner omitted a unit from the manifest; its enforceable invariant is that every
key in an owner-attested manifest settles exactly once. Manifest preflight
failure terminalizes the attempt as `failed` before any work or resource
reservation. It uses an explicit queued-to-terminal path: `attempt_queued` and
`manifest_preflight_failed` are recorded, `attempt_started` is not fabricated,
and the empty work, resource, and artifact registries permit an immediate
terminal proposal. Dynamic manifest expansion and later-added stage manifests
are outside the first integration. If Targeted or Alignment cannot state its
complete non-empty work universe up front, stop and redraw the seam rather than
infer completion from only the units that happened to be submitted.

An acquisition session may be reused only within the same attempt, worker,
`AcquisitionSource`, and owner-supplied compatibility token. Correctness must
not depend on reuse. Before adapter construction or allocation, the runtime
creates a unique `resource_reserved` record with pre-bound
`owner_conditional=false` (required) or `owner_conditional=true`. Required is
the default; only the semantic owner that binds the consumer condition before
allocation may reserve a conditional upper bound. Unregistered handles must
never be used. Each
record transitions from `reserved` to `opened`, `open_failed`,
`not_opened`, `condition_not_activated`, or
`open_state_unknown_after_worker_loss`, then to one terminal cleanup outcome.
`not_opened` covers pre-allocation cancellation and performs an idempotent
no-op cleanup. `condition_not_activated` records that an owner-declared
conditional resource was reserved as an upper bound but its already-bound
consumer condition did not activate it. Allocation must not occur, cleanup is
an idempotent no-op, and the outcome does not fail an otherwise successful
attempt. A `required` reservation reporting `condition_not_activated` is an
integrity failure. The outcome cannot stand in for an acquisition or satisfy
work whose accepted owner result depends on that resource. If a worker
disappears while the parent still sees `reserved`, the runtime records
`open_state_unknown_after_worker_loss`; it must
not infer whether allocation occurred. Worker loss closes every non-terminal
resource record as `unknown_after_worker_loss`. Every transition is observable,
a late child report cannot rewrite a terminal resource record, and a reopened
source is a new runtime session.

## Small State Model

Avoid multiplying user-facing or internal intermediate tags. Runtime state
uses two orthogonal axes:

| Axis | Values |
| --- | --- |
| Phase | `queued`, `running`, `terminal` |
| Terminal outcome | `succeeded`, `failed`, `cancelled` |

Cancellation intent is an ordered event, not a fourth outcome or another
scientific status. Partial work is represented by settled work units and
runtime artifact disposition, not by a `partial` attempt outcome.

Terminal outcome is deterministic:

1. manifest precondition failure, submission failure, worker loss, unit
   failure, integrity failure, cleanup `failed` /
   `unknown_after_worker_loss`, or a `not_opened` resource without an accepted
   cancellation makes the attempt `failed`;
2. otherwise, a cancellation accepted before the terminal decision makes it
   `cancelled`;
3. otherwise, every manifest unit settling successfully makes it `succeeded`.

An attempt can become terminal after every manifest unit has a terminal result
and every reserved resource has exactly one terminal cleanup record, and every
current-attempt runtime artifact has a proposed terminal disposition. Cleanup
failure, worker loss, or artifact-disposition failure therefore blocks success,
not terminalization. A manifest preflight failure has an empty work, resource,
and artifact registry and can immediately propose `failed`.

All state-changing events use one monotonic sequence per attempt. Appending a
required lifecycle event and applying its state transition are one atomic
in-memory reducer operation; a required append failure becomes an
`integrity_failure` and prevents success. Export to an external log or metrics
sink is a non-authoritative copy: export failure is recorded when possible as a
telemetry failure but cannot erase the in-memory ledger. Cancellation accepted
before the atomic terminal decision affects the outcome. A request arriving
after that decision is recorded as rejected/no-op. Cleanup failure still has
precedence over cancellation. The in-memory terminal decision is a frozen
proposal; it becomes the terminal outcome only through the durable
acknowledgement below.

## Coordinator And Host-Loss Boundary

The first runtime keeps its reducer and detailed event ledger in memory. It uses
one small durable supervisor marker to close the terminal handoff without
claiming a durable queue:

1. before launch, the lineage owner or entry-point supervisor atomically records
   the owner-created attempt identity, attempt-scoped temporary-artifact
   namespace, and coordinator launch;
2. after work, cleanup, and artifact-disposition proposal closure, the
   coordinator produces one immutable canonical `CoordinatorTerminalRecord`
   `execution_runtime_coordinator_terminal_v4` byte sequence containing the
   proposed outcome, manifest and per-unit outcomes, typed per-unit failure
   details, typed resource kinds plus open/cleanup outcomes, artifact registry and proposed
   dispositions, failures, coordinator metrics ending at terminal proposal,
   and the coordinator event-ledger digest through that proposal;
3. the owner-path supervisor fsyncs those exact coordinator bytes and the exact
   runtime-owner-derived observability-ledger bytes inside the
   self-contained `execution_runtime_recovery_marker_v4` payload, then
   atomically replaces the launch marker with one immutable
   `SupervisorFinalizationRecord` containing the coordinator-record digest and
   an atomic event batch continuing the coordinator sequence:
   `terminal_acknowledged`, one committed disposition event for every
   current-attempt artifact, and `terminal_outcome`. It also contains the
   finalization offset and `attempt_wall_seconds`. Existence of this finalization
   record is the durable acknowledgement that commits the attempt outcome and
   corresponding logical artifact dispositions; no disposition event is
   emitted before this atomic finalization exists.

Coordinator-record write/integrity failure or supervisor-finalization failure
cannot commit success. The coordinator and finalization records are recovery
metadata, not delivery evidence or ProductWriter artifacts. Neither record
contains or digests facts that occur only after that record is finalized.
Public Targeted and Alignment application results revalidate the exact
coordinator/finalization pair; matching attempt identity alone is insufficient.

`raw_open_count` counts only opened `acquisition_session` reservations. Opened
`process_worker` reservations still emit complete resource lifecycle facts but
cannot inflate acquisition-session telemetry.

If the coordinator exits before durable terminal acknowledgement:

- the supervisor reports `coordinator_loss`, and the lineage owner records the
  old attempt as failed from the durable launch marker;
- the old attempt can never become `succeeded`, resume in place, or lend its
  current-attempt artifacts to delivery;
- the whole attempt namespace is logically quarantined, while physical
  reclamation remains an owner-supplied idempotent action;
- no per-resource cleanup result is fabricated from the lost in-memory
  registry; cleanup is conservatively unknown at attempt scope; and
- retry requires a new `ProcessingAttempt` identity and fresh runtime.

If the coordinator exits after acknowledgement, the durable acknowledgement is
authoritative: the lineage owner verifies the supervisor finalization and its
referenced coordinator record rather than downgrading the attempt to
`coordinator_loss`. Replaying the same coordinator-record digest, outcome, and
finalization facts is idempotent; a conflicting finalization for the same
attempt fails closed.

The launch/terminal marker is recovery evidence, not a second attempt identity
or a durable runtime queue. A production integration that needs per-resource
crash recovery before terminal proposal must first add and validate a durable
registry or equivalent supervision contract. Until then, abrupt
coordinator-loss handling remains a tested `diagnostic_only` boundary.

## Cancellation, Worker Loss, Partial Work, And Failure

After cancellation is accepted:

- no new work unit may be scheduled;
- unscheduled or cancellable pending units settle as `cancelled`;
- already running units receive a cooperative stop request;
- a killable process worker escalates after an entry-point-configured and
  recorded finite positive deadline, terminates the worker, and records
  affected units as failed due to worker loss;
- every reserved resource receives a terminal cleanup record;
- no artifact becomes deliverable merely because a unit finished before the
  cancellation request.

Before scheduling, each worker declares `hard_cancel_supported`. Bounded hard
cancel is available only across a killable process boundary. An in-process
serial Thermo call may support cooperative cancellation but cannot claim
bounded terminalization. If an entry point requires bounded cancellation, it
must run that work in a dedicated process or fail preflight. A legacy serial
consumer with `hard_cancel_supported=false` may be characterized for successful
execution, but it does not satisfy the bounded-cancel acceptance gate.

Worker termination cannot prove that child-owned cleanup such as vendor
`Dispose()` ran. The contract therefore requires exactly one logical terminal
cleanup outcome per resource:

- `succeeded`;
- `failed`;
- `unknown_after_worker_loss`.

Cleanup operations must be idempotent so parent-side reaping or retry does not
double-apply destructive effects. Unknown or failed cleanup makes the attempt
`failed` but still lets it reach terminal state.

Every runtime-mediated artifact is registered before its first write. Artifacts
produced by the current attempt remain `runtime_temporary` through the
in-memory terminal proposal. Durable terminal acknowledgement atomically
commits a successful attempt and every such artifact to `offered_to_owner`; a
failed/cancelled acknowledgement, or coordinator loss before acknowledgement,
logically changes every registered artifact, including partial bytes from a
failed unit, to `quarantined_diagnostic`. The
`SupervisorFinalizationRecord` contains exactly one
`artifact_offered_to_owner` or `artifact_quarantined_diagnostic` event for each
current-attempt artifact, in the same atomic finalization that commits the
terminal outcome. Failure of an owner-supplied physical quarantine action is
aggregated as cleanup failure, but never makes the artifact deliverable. These
artifacts may support diagnosis or a later
lineage-approved dependency, but they cannot silently become scientific
`missing`, `absent`, selected values, or product output. An artifact referenced
through `validated_dependency` keeps its prior producer-owned disposition; only
this attempt's dependency-use record becomes non-deliverable.

All failures are retained as typed records and aggregated at the attempt
boundary. Each preserves the work-unit/resource key, stage, source reference,
failure category, and readable message. The terminal acknowledgement separately
binds the exact observability ledger carrying the relevant work-event sequence,
using the same work-unit key for correlation. One exception must not erase
sibling failures, and a traceback must not be the only failure contract.

## Retry And Resume

Retry never reopens a terminal attempt. The lineage owner:

- creates a new `ProcessingAttempt` identity;
- determines whether the normalized `ProcessingRequest` is unchanged;
- records the predecessor/retry relationship;
- validates immutable artifact dependencies for integrity, staleness, source,
  schema, and semantic-role compatibility.

The runtime receives the new attempt identity plus zero or more
`ValidatedDependencyToken` values. Each token exposes the new attempt identity
and exactly one manifest work-unit key to the runtime while keeping scientific
compatibility opaque. Tokens cannot overlap. A manifest unit settles either by
execution or by one token reference with
`satisfaction_source=validated_dependency`; it can never be silently skipped.
The runtime records mandatory dependency-reuse accepted, structurally rejected,
or not-used events. It cannot mint identity, approve scientific compatibility,
relabel an old artifact, or mutate the predecessor.

“Resume” is therefore an execution optimization on a new attempt, not mutation
of the old attempt. Zero tokens means a full recomputation. Only a claimed
reuse whose token is missing, malformed, bound to another attempt/key, or
overlaps another token fails closed. Semantically stale or incompatible work
must be rejected by the lineage owner before scheduling reaches the runtime.
The runtime performs no hidden same-attempt automatic retry: retry policy first
returns to the lineage owner for a new attempt identity and, where applicable,
new validated dependency tokens.

## Cache And Temporary-Resource Lifecycle

Cache rules:

- correctness is identical on cache hit and miss;
- keys and compatibility predicates come from the owning identity/evidence
  contract;
- hits, misses, rejected stale entries, reads, writes, rebuilds, and evictions
  are runtime events;
- a miss or stale rejection may trigger ordinary work execution; any resulting
  cache rebuild is recorded once with `succeeded`, `failed`, or `not_attempted`
  plus reason rather than becoming another attempt state;
- an eviction request is recorded once with `succeeded`, `failed`, or
  `rejected_pinned` plus reason; a rejected or failed eviction does not mutate
  the existing entry;
- every attempt-local volatile cache is registered as a runtime resource and
  released through the same terminal, idempotent cleanup contract on success,
  failure, cancellation, or worker loss;
- persistent cache format, retention period, and eviction policy remain
  separate owner decisions.

Runtime artifact disposition uses names that cannot be confused with delivery
staging:

- `runtime_temporary`;
- `quarantined_diagnostic`;
- `offered_to_owner`.

`offered_to_owner` means only that execution completed and the owning lineage
or projection layer may validate the artifact. It does not mean lineage
acceptance, delivery `staged`, sealing eligibility, or writer authority.

The runtime must not delete an immutable artifact referenced by a delivery
seal, active review, lockbox, expected diff, or retained audit claim. Physical
deletion is limited to owner-supplied actions on registered temporary
resources.

## Minimum Observability

Every coordinator event carries the attempt identity, monotonic sequence,
monotonic offset from the runtime origin established by `attempt_queued`, event
kind, and work-unit/resource reference where applicable. The coordinator ledger
ends at `terminal_proposal`. The supervisor finalization continues from the last
coordinator sequence and durably adds acknowledgement, committed artifact
dispositions, and outcome; it is not part of the coordinator-record digest. The
queued-to-terminal manifest-preflight path omits `attempt_started`; events
listed below as started exist only after successful preflight. The minimum
event set covers:

For a running attempt, the owner normalizes causal facts into one canonical
phase order: attempt start, all resource reservations, all resource open
outcomes, started workers, manifest-order work submission/start/settlement,
runtime artifact registration, all resource cleanup, terminal proposal, and
then supervisor acknowledgement/dispositions/outcome. Monotonic offsets bind
that logical topology; they do not claim to recover physical interleaving among
concurrent workers inside a phase. A ledger that reorders these phases fails
closed even when every individual resource or work-unit lifecycle is complete.

- coordinator: `attempt_queued`, `manifest_preflight_failed` when applicable,
  `attempt_started` after successful preflight, cancellation accepted/rejected,
  and `terminal_proposal`;
- supervisor finalization: `terminal_acknowledged`, exactly one
  `artifact_offered_to_owner` or `artifact_quarantined_diagnostic` per
  current-attempt artifact, and `terminal_outcome`;
- required-work manifest closed and work unit submitted, started, and settled;
- dependency reuse accepted/rejected/not used and unit satisfaction source;
- acquisition/resource reserved, `opened`, `open_failed`, `not_opened`,
  `condition_not_activated`, or `open_state_unknown_after_worker_loss`, and
  terminal cleanup outcome;
- cache hit, miss, stale rejection, write, rebuild outcome, and eviction
  outcome;
- coordinator-side runtime artifact creation;
- worker, scheduler, integrity, telemetry, and cleanup failures.

Every metric records its scope (`attempt`, `stage`, `work_unit`, `worker`, or
`resource`), unit, aggregation rule, measurement source, and availability
(`measured`, `unsupported`, or `failed`) plus reason. Missing optional cost
telemetry does not change scientific or attempt outcome; missing required
lifecycle events fail closed.

| Metric | Required meaning and aggregation |
| --- | --- |
| `raw_open_count` | Sum of acquisition sessions opened; source is the runtime resource registry |
| `logical_xic_request_count` | Sum of logical trace requests presented to the adapter |
| `xic_batch_count` | Sum of adapter batch invocations |
| `adapter_chromatogram_call_count` | Sum of observed vendor/backend chromatogram calls; separate from logical requests |
| `trace_point_count` | Sum of returned trace points |
| `coordinator_work_seconds` | Monotonic span from committed `attempt_started` through frozen terminal proposal; stored in the coordinator record; `unsupported:not_started_preflight_failure` when preflight terminalizes from `queued` |
| `attempt_wall_seconds` | Monotonic span from committed `attempt_queued` through supervisor finalization after cleanup; stored only in the finalization record; parallel child spans are not summed into it |
| `stage_or_unit_elapsed_seconds` | Individually scoped monotonic spans; aggregation retains count/sum/max |
| `worker_peak_rss_bytes` | Maximum measured RSS per worker, with unsupported reason when unavailable |
| `process_tree_peak_rss_bytes` | Maximum observed parent-plus-child RSS, not a sum of per-worker peaks |
| `artifact_read_count/bytes` | Sum of runtime-mediated reads; excludes uninstrumented external I/O and records coverage |
| `artifact_write_count/bytes` | Sum of runtime-mediated writes; excludes uninstrumented external I/O and records coverage |

The executable Package 0/1 ledger uses one attempt-scoped `sum` record for the
count and artifact-I/O metrics, attempt-scoped `last` records for coordinator
and wall spans, one work-unit-scoped `sum` elapsed record per declared work key,
worker-scoped `max` RSS, and attempt-scoped `max` process-tree RSS. Metric
identity is `(name, scope, scope_key)` and duplicates or unregistered names fail
closed. The queued-to-terminal preflight path has no work/worker scope, so its
explicitly unavailable elapsed/RSS records use the attempt scope and
`not_started_preflight_failure` reason.

For a running attempt, resource lifecycle events use only the registered
resource subject and contain exactly one reservation, one open outcome, and one
cleanup outcome in that order. Each registered runtime artifact likewise has
exactly one registration followed by one offered/quarantined disposition.
Elapsed metrics must equal their correlated monotonic event spans. A serial run
with no child-worker event retains one attempt-scoped
`worker_peak_rss_bytes=unsupported:not_applicable_serial` record; it does not
invent a worker identity or omit the required metric.

These metrics describe cost and execution locality. They do not score evidence
quality or authorize a result.

For the first Targeted and Alignment consumers, `raw_open_count`,
`logical_xic_request_count`, `xic_batch_count`,
`adapter_chromatogram_call_count`, `trace_point_count`,
`coordinator_work_seconds`, `attempt_wall_seconds`,
`stage_or_unit_elapsed_seconds`, and runtime-mediated artifact read/write
counts and bytes are required `measured` for attempts that enter `running`.
Manifest-preflight terminalization instead reports measured zero for
resource/work/artifact-I/O counts, measured `attempt_wall_seconds` from
`attempt_queued`, and `unsupported:not_started_preflight_failure` for
`coordinator_work_seconds`, stage/unit elapsed time, and worker-derived
metrics; it must not emit measured zero durations for work that never started.
Process-mode comparators must also measure per-worker and process tree peak RSS;
serial mode must measure process peak RSS and may report
per-worker RSS as `unsupported:not_applicable_serial`. Artifact-I/O coverage is
complete only when every registered runtime-artifact read and write contributes
to the counters; direct legacy writer I/O stays explicitly out of scope rather
than being reported as measured zero.

The first cache-disabled successful comparator is a hard locality gate:
baseline and candidate must have exact equality for RAW opens, logical XIC
requests, batches, adapter calls, trace points, and runtime-mediated artifact
I/O. Elapsed time and RSS use paired baseline/candidate measurements in the
same environment. #128 intentionally does not invent a numeric tolerance before
that baseline exists: the owning implementation lane must preregister its
elapsed/RSS budget before promotion, and missing tolerance keeps the runtime at
`diagnostic_only`. A hard-locality mismatch, a lane-budget breach, or missing
required measurement is a rollback/stop signal, never a reason to relax
scientific parity.

## Current Implementation Gaps

The first Package 0 event-contract slices are executable in
`xic_extractor.execution_runtime`: they bind `attempt_queued`,
`attempt_started`, and `cancellation_accepted` to one owner-issued
`ProcessingAttemptIdentity`, enforce contiguous append-only ordering, and
prevent an accepted queued cancellation from later becoming a started attempt.
They also encode the coordinator-only
`attempt_queued` -> `manifest_preflight_failed` ->
`terminal_proposal(failed)` path without fabricating `attempt_started` or a
committed outcome. The Package 0 runtime owner now consumes owner-supplied
manifest content into an attempt-bound immutable `RequiredWorkManifest` with
closed unique opaque work-unit keys. Missing completeness attestation, duplicate
or blank keys, and attempt substitution drive that existing queued-to-failed
proposal instead of relying on a caller to declare failure manually. That
preflight-terminal state now continues through the same owner finalization
receipt and v4 recovery marker with empty work/resource/artifact registries,
measured-zero mechanical counts, and `unsupported:not_started_preflight_failure`
for work- and worker-derived metrics. Targeted and Alignment enter through the
same atomic owner operation; it does not invoke their worker provider when
preflight has already terminalized, so the public consumer path cannot allocate
or dispatch work for a rejected manifest. Any reservation plans passed with that
terminal state remain unconsumed and the final receipt retains empty registries.
An accepted manifest starts one pure
`AttemptRuntime`; terminal work settlements are
accepted exactly once per declared opaque key, identical replay is idempotent,
and conflicting or undeclared settlement records one immutable integrity
failure that prevents attempt success. Failed work also prevents success.
Failed and cancelled settlements require a typed `WorkUnitFailureDetail`
containing category, stage, source reference, and readable message, while a
successful settlement rejects failure detail. The canonical coordinator v4
bytes retain that exact detail. A successful settlement accepts only an
owner-minted `EvidenceArtifactRef` bound to the same processing request and
attempt; an opaque string is no longer sufficient.

The bounded Package 1 Targeted adapter in
`xic_extractor.extraction.targeted_runtime_adapter` owns one canonical
process-result identity payload over the closed work key and exact digest/size
of owner-supplied result bytes. It does not serialize or hash legacy
`RawFileExtractionResult` / `RunOutput`. `TargetedProcessResultRef` binds that
work key and owner-payload identity to the underlying `EvidenceArtifactRef`, so
an artifact minted for one work key cannot settle another. A pickleable
worker-result envelope carries that producer-owned reference across a true
spawned process, and
the full-lifecycle Targeted service consumes reordered envelopes through the
shared runtime aggregate and settles the existing runtime in manifest order.
There is no public settlement-only service or adapter bypass. This boundary is
`wired_no_raw` and remains `diagnostic_only`; default legacy execution and all
product outputs are unchanged.

The next bounded Targeted lifecycle seam is also executable no-RAW.
`execution_runtime_owner.RuntimeAttemptAggregate` is the single construction
facade over the focused state-model companions. It reserves resources before the
process runner, accepts exactly one open and cleanup outcome per reservation,
closes worker loss conservatively, registers current-attempt immutable artifacts,
and freezes a content-bound `CoordinatorTerminalRecord`. The owner then mints
one atomic acknowledgement batch containing `terminal_acknowledged`, one
committed disposition per registered artifact, and `terminal_outcome`.
`TargetedApplicationService.execute_runtime_lifecycle` supplies the first real
consumer: a true spawned process returns typed work/resource/artifact/acquisition
facts, and an owner-supplied persister must return the exact proposed
`RuntimeFinalizationReceipt` before the application result exists. Success
offers every registered artifact only to its owner; failure, cancellation, or
worker-loss cleanup unknown quarantines the whole registered current-attempt
set. Conflicting or missing cleanup and acknowledgement fail closed.
Targeted runtime configuration must provide exactly one finalization authority:
either `runtime_finalization_persister` or `runtime_recovery_marker`. The marker
is itself the sole persister when selected; simultaneous independent surfaces
are rejected before request resolution, launch recording, or work dispatch.

This remains `diagnostic_only`. `OwnerPathRuntimeMarker` is the bounded physical
persistence seam: the owner supplies its path, namespace, retention, and later
publication policy; the v4 marker embeds the exact coordinator and validated
observability-ledger bytes, with separate content digests, and uses a
cross-platform exclusive sibling lock around read/validate/replace. A concurrent
writer waits for the current transaction and then revalidates; an unresolved
stale lock fails closed for owner recovery instead of being auto-deleted. The
reader retains exact canonical v3 outer-marker compatibility; new writes use
v4 because the coordinator resource outcome vocabulary now includes
`condition_not_activated`.
The repo has not selected a storage engine, durable queue, current pointer, product
seal, publication mechanism, telemetry producer, or telemetry backend.
Alignment is now the second no-RAW consumer. Callers supply only typed
`RuntimeTelemetrySamples` for monotonic offsets, serial-or-process RSS, and
runtime-mediated artifact I/O. The runtime owner derives the complete event
topology and mechanical counts, while the focused
`execution_runtime_observability` companion validates elapsed-event agreement
and required finite/integral metric availability without collecting live
telemetry or selecting an event store or telemetry backend. It also enforces
the global phase boundary: coordinator
resource lifecycle and artifact registration end before `terminal_proposal`;
artifact dispositions occur only after `terminal_acknowledged` and before
`terminal_outcome`. Before supervisor acknowledgement, the finalization owner
derives the ledger's complete mechanical lifecycle facts from the closed
coordinator state, including `raw_open_count` from opened resource records.
Logical XIC request, batch, backend-call, and trace-point counts are derived from
typed `TraceAcquisitionBatch` receipts correlated to submitted successful work;
for a successful application result, an empty observation-batch projection is
accepted only when all four owner counts are zero. Failed or quarantined results
may suppress exposed success batches while retaining physically observed counts.
Missing, extra, substituted, or conflicting success-projection facts fail closed
before acknowledgement. RSS, monotonic offsets, and runtime-mediated artifact-I/O remain
external telemetry samples because the coordinator has no independent source
for them; callers cannot assemble or submit the ledger itself. Only an opened
`PROCESS_WORKER` reservation creates `worker_started` and requires a correlated
worker RSS sample; `not_opened`, `condition_not_activated`, and failed-open
reservations cannot invent a worker. The owner fixes metric provenance to
`execution_runtime_owner:v1`, so a
caller cannot alter canonical bytes by relabeling owner-derived facts.
Metric names remain readable public schema vocabulary. A caller may copy those
names, but a caller-built mapping or named record is untrusted and is not an
observability contract. Persistence and future runtime-metric writers must use
the owner-minted ledger carried by the accepted finalization receipt and its
content-bound canonical bytes/digest; no scalar or mapping substitution is
accepted by the runtime boundary.
The owner-path durable launch/finalization marker and coordinator-loss
recovery are also executable for both Targeted and Alignment. These contracts
do not make the legacy runners use a new storage or writer path.

Package 1 now composes that mechanical lifecycle with the existing acquisition
and physical-observation owners. `peak_detection.observation_batch` performs
one capability-aware `XICRequest` batch only after resolving the sample's
request binding and requiring the capability's acquisition source to match.
It retains truthful native/conversion/RT coordinates and mechanical
request/call/point counts, closes every coordinate against the capability
source/kind, freezes copied signal arrays, and asks
`peak_detection.observations` to validate owner-supplied `IntegrationResult`
facts against each acquired trace before minting a `PeakObservationRef`.
Targeted and Alignment process-result artifacts bind the same canonical batch
receipt and exact observation-artifact upstream closure. Their existing runtime
carriers register both wrapper and observation artifacts, and manifest-order
normalization makes serial and deliberately reordered true-spawn results
canonical-equal. Identical replay coalesces; a conflicting replay fails the
attempt and exposes no observation batch through the success surface. Runtime
does not select a peak or interpret the integration. A tracked
`DifferenceManifest` binds the one explained internal carrier change to the
immutable Package 0 comparator and one comparator-owned experiment. That single
entry overwrites caller output paths with a dedicated identity-pinned root,
constructs the Targeted service with the forbidden legacy runner, accepts only
primitive no-RAW jobs, and runs fixed Targeted and Alignment workers to a
synchronous executor drain. It inventories final paths/bytes before privately
minting the owner-derived receipt. There is no public request factory,
worker/result/executor provider, begin/mint, or finished-result handoff, and
callers cannot construct or replace the receipt. The inventory claim is
final-state parity, not transient filesystem-I/O absence; legacy writer
non-invocation is proved separately by fixed ingress plus the sentinel and
Alignment dependency boundary.
This path remains opt-in no-RAW and `diagnostic_only`; legacy execution, product
writers, CLI/config, output schemas, selected values, and Matrix authority are
unchanged.

1. Targeted process execution can stop scheduling and cancel pending futures,
   but running work drains and no attempt/dependency state exists.
2. Targeted GUI cancellation can suppress later workbook/summary work after
   extraction has already produced partial files; CLI has no equivalent stop
   surface.
3. Untargeted GUI checks interruption mainly between phases; Alignment workers
   do not share the Targeted cancellation contract.
4. Targeted and Alignment aggregate process-worker errors, while serial and
   diagnostic paths preserve different partial-result shapes.
5. Targeted, Alignment, and overlay diagnostics each own different RAW session,
   cache, incremental-output, and timing behavior.
6. Existing timing captures useful calls, batches, adapter calls, points, and
   elapsed values, but not one attempt/event stream, memory pressure, cleanup
   outcomes, or artifact-I/O coverage.
7. Some modules named as runtime helpers currently apply model-selection or
   product policy. Those decisions must remain with evidence/projection owners.

## Alternatives And Trade-Offs

| Option | Decision | Reason |
| --- | --- | --- |
| Keep separate workflow schedulers as the long-term contract | Rejected | Existing cancellation, failure, cleanup, cache, and timing semantics already diverge and cannot support comparable rollback evidence. |
| Promote the Targeted scheduler into a generic pipeline engine | Rejected | It would make current Targeted behavior the architecture boundary and invite scientific and writer policy into the runtime. |
| Introduce a database, durable queue, or public event API first | Rejected | #128 needs enforceable lifecycle semantics before persistence or distribution choices. |
| Add one small mechanical runtime behind thin Targeted and Alignment adapters | Selected | Two real consumers can falsify the seam while legacy scientific and writer paths remain authoritative. |

## Required Evidence Before Integration

The logical contract still requires implementation evidence. The first
integration must provide:

- pure state-table tests for success, duplicate required-work keys, missing
  owner completeness attestation, queued-to-terminal manifest preflight
  terminalization without `attempt_started` and with explicit metric
  availability,
  submission failure, cancellation before resource open, cancellation races,
  required-event append failure, worker loss while still `reserved`, worker
  loss after `open_failed`, late cleanup reports, cleanup failed/unknown,
  retry with owner-supplied dependency tokens,
  and cancellation after dependency reuse;
- synthetic hung-process tests proving deadline escalation, bounded parent
  terminalization, duplicate reap idempotency, and the race between a late child
  cleanup result and parent `unknown_after_worker_loss`;
- synthetic coordinator-loss tests on both sides of terminal acknowledgement:
  before acknowledgement the old attempt and namespace stay non-deliverable,
  no per-resource cleanup is invented, and retry uses a new attempt identity;
  after acknowledgement the content-bound terminal record, exact outcome, and
  artifact dispositions survive, while a missing/corrupt record or conflicting
  acknowledgement fails closed; fixtures also prove the coordinator record ends
  at terminal proposal, supervisor events continue its sequence, every
  current-attempt artifact has one committed disposition event in the atomic
  finalization, and `attempt_wall_seconds` starts at `attempt_queued` and exists
  only in the finalization layer;
- volatile-cache cleanup tests across success, failure, cancellation, and
  worker loss;
- no-RAW Windows-spawn pickling smoke for work, event, failure, resource, and
  metrics envelopes;
- Targeted serial/process characterization parity;
- Alignment serial/process characterization parity as the second consumer;
- zero selected-result and writer-byte difference for successful comparator
  runs;
- assertions that failed/cancelled runtime artifacts never enter either
  product writer, that every partial artifact is registered, and that
  current-attempt artifact disposition closes atomically with terminal outcome;
- observability fixtures proving metric scope, aggregation, and unavailable
  semantics, including preflight-not-started, cache rebuild, eviction outcomes,
  one RSS record per observed worker, finite/integral count and byte values, and
  exact observability-ledger recovery-byte binding.

This evidence must begin no-RAW. Thermo RAW validation is justified only if a
later bounded question requires cooperative-call or worker-termination
behavior that synthetic workers cannot answer.

## Implementation Route And Stop Rule

The first implementation should be a pure runtime state/event module plus one
thin adapter around the existing Targeted process scheduler:

1. consume a #123-defined attempt identity and owner-validated dependency
   tokens rather than adding another `run_id`;
2. emit typed work-unit, cancellation, resource, failure, cleanup, and metrics
   events without changing extraction results or writers;
3. preserve current collect-before-write behavior and quarantine cancelled or
   failed-attempt runtime artifacts;
4. add Alignment as the second real consumer and run the required parity gates;
5. only then consider moving diagnostic overlay mechanics onto the same owner.

Do not begin with a repository-wide backend rewrite, a database, persistent
resume framework, cache eviction service, or public event API. Stop and redraw
the seam if the second consumer requires scientific flags or workflow-specific
meaning in the runtime core.

## Acceptance Mapping

- One `AttemptRuntime` maps to one lineage-identified `ProcessingAttempt`.
- A closed unique required-work manifest prevents omitted work from becoming a
  successful partial attempt within the owner-attested work universe; the
  runtime does not claim to discover owner omissions.
- Cancellation is ordered against terminal decision, prevents new scheduling,
  and cannot publish partial output.
- Worker loss and cleanup unknown/failure aggregate without becoming scientific
  absence or preventing terminalization.
- A terminal proposal is not success: only its idempotent durable
  acknowledgement commits outcome and artifact dispositions; coordinator loss
  before acknowledgement fails/quarantines the old attempt, while loss after
  acknowledgement preserves the acknowledged result.
- Every resource is reserved before allocation, has one `opened`,
  `open_failed`, `not_opened`, `condition_not_activated`, or
  `open_state_unknown_after_worker_loss` event and exactly one logical terminal
  cleanup record, and cleanup is idempotent.
- Bounded hard cancel is claimed only for a validated killable process boundary.
- Retry/resume receives new identity and non-overlapping work-key-bound
  dependency tokens from the lineage owner; zero tokens means full recompute.
- Cache reuse is optional, owner-keyed, observable, and correctness-neutral.
- Volatile caches are registered resources whose cleanup must close before
  terminalization.
- Current-attempt artifacts remain temporary until terminal success; failure or
  cancellation can only quarantine them.
- Calls, opens, points, elapsed, memory, and artifact I/O have comparable scope,
  aggregation, source, and unavailable semantics.
- Targeted and Alignment must pass spawn, parity, and no-writer-diff gates
  without moving evidence, selection, Matrix, writer, or export authority into
  runtime.
- No integration evidence above `diagnostic_only` may be claimed until real
  consumer integration is separately implemented and artifact-validated.
