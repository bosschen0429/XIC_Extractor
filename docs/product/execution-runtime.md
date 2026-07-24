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

## Ownership Boundary

The execution runtime owns:

- validating that an owner-supplied required-work manifest is closed and has
  unique opaque work-unit keys before scheduling;
- scheduling those work units onto serial or process workers;
- reserving acquisition-handle and other resource records before allocation,
  then managing their lifetime inside workers;
- bounded cooperative cancellation, pending-work suppression, and worker-loss
  escalation;
- retry scheduling using lineage-owner-supplied attempt and dependency tokens;
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

The manifest is closed before the attempt starts. Duplicate keys, a missing
required key, submission failure, or an attempt to add later work fails closed.
If a workflow cannot state its complete work set up front, it must define
owner-visible stage manifests before runtime integration; the runtime must not
infer completion from only the units that happened to be submitted.

An acquisition session may be reused only within the same attempt, worker,
`AcquisitionSource`, and owner-supplied compatibility token. Correctness must
not depend on reuse. Before adapter construction or allocation, the runtime
creates a unique `resource_reserved` record. Unregistered handles must never be
used. Each record transitions from `reserved` to `opened` or `open_failed`, then
to one terminal cleanup outcome. Worker loss closes every non-terminal resource
record, including `reserved`, `opened`, and `open_failed`, as
`unknown_after_worker_loss`. Every transition is observable, and a
reopened source is a new runtime session.

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

1. duplicate/missing required work, submission failure, worker loss, unit
   failure, integrity failure, or cleanup `failed` /
   `unknown_after_worker_loss` makes the attempt `failed`;
2. otherwise, a cancellation accepted before the terminal decision makes it
   `cancelled`;
3. otherwise, every manifest unit settling successfully makes it `succeeded`.

An attempt can become terminal after every manifest unit has a terminal result
and every reserved resource has exactly one terminal cleanup record.
Cleanup failure or worker loss therefore blocks success, not terminalization.

All state-changing events use one monotonic sequence per attempt. Cancellation
accepted before the atomic terminal decision affects the outcome. A request
arriving after that decision is recorded as rejected/no-op. Cleanup failure
still has precedence over cancellation.

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

Artifacts produced by the current attempt for an execution-satisfied unit
become `quarantined_diagnostic` when that attempt fails or is cancelled. They
may support diagnosis or a later lineage-approved dependency, but they cannot
silently become scientific `missing`, `absent`, selected values, or product
output. An artifact referenced through `validated_dependency` keeps its prior
producer-owned disposition; only this attempt's dependency-use record becomes
non-deliverable.

All failures are retained as typed records and aggregated at the attempt
boundary. Each preserves the work-unit/resource key, stage, source reference,
failure category, readable message, and relevant event sequence. One exception
must not erase sibling failures, and a traceback must not be the only failure
contract.

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

## Cache And Temporary-Resource Lifecycle

Cache rules:

- correctness is identical on cache hit and miss;
- keys and compatibility predicates come from the owning identity/evidence
  contract;
- hits, misses, rejected stale entries, reads, writes, and evictions are
  runtime events;
- attempt-local volatile caches are released through registered idempotent
  cleanup actions;
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

Every event carries the attempt identity, monotonic sequence, event kind, and
work-unit/resource reference where applicable. The minimum event set covers:

- attempt queued, started, cancellation accepted/rejected, terminal decision,
  and terminal outcome;
- required-work manifest closed and work unit submitted, started, and settled;
- dependency reuse accepted/rejected/not used and unit satisfaction source;
- acquisition/resource reserved, open succeeded/failed, and terminal cleanup
  outcome;
- cache hit, miss, stale rejection, write, and eviction;
- runtime artifact created, quarantined, or offered to its owner;
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
| `attempt_wall_seconds` | Monotonic wall-clock span; parallel child spans are not summed into it |
| `stage_or_unit_elapsed_seconds` | Individually scoped monotonic spans; aggregation retains count/sum/max |
| `worker_peak_rss_bytes` | Maximum measured RSS per worker, with unsupported reason when unavailable |
| `process_tree_peak_rss_bytes` | Maximum observed parent-plus-child RSS, not a sum of per-worker peaks |
| `artifact_read_count/bytes` | Sum of runtime-mediated reads; excludes uninstrumented external I/O and records coverage |
| `artifact_write_count/bytes` | Sum of runtime-mediated writes; excludes uninstrumented external I/O and records coverage |

These metrics describe cost and execution locality. They do not score evidence
quality or authorize a result.

## Current Implementation Gaps

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

## Required Evidence Before Integration

The logical contract still requires implementation evidence. The first
integration must provide:

- pure state-table tests for success, duplicate/missing required work,
  submission failure, cancellation races, worker loss after `open_failed`,
  cleanup failed/unknown, retry with owner-supplied dependency tokens, and
  cancellation after dependency reuse;
- no-RAW Windows-spawn pickling smoke for work, event, failure, resource, and
  metrics envelopes;
- Targeted serial/process characterization parity;
- Alignment serial/process characterization parity as the second consumer;
- zero selected-result and writer-byte difference for successful comparator
  runs;
- assertions that failed/cancelled runtime artifacts never enter either
  product writer;
- observability fixtures proving metric scope, aggregation, and unavailable
  semantics.

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
  successful partial attempt.
- Cancellation is ordered against terminal decision, prevents new scheduling,
  and cannot publish partial output.
- Worker loss and cleanup unknown/failure aggregate without becoming scientific
  absence or preventing terminalization.
- Every resource is reserved before allocation, has one open/open-failed event
  and exactly one logical terminal cleanup record, and cleanup is idempotent.
- Bounded hard cancel is claimed only for a validated killable process boundary.
- Retry/resume receives new identity and non-overlapping work-key-bound
  dependency tokens from the lineage owner; zero tokens means full recompute.
- Cache reuse is optional, owner-keyed, observable, and correctness-neutral.
- Calls, opens, points, elapsed, memory, and artifact I/O have comparable scope,
  aggregation, source, and unavailable semantics.
- Targeted and Alignment must pass spawn, parity, and no-writer-diff gates
  without moving evidence, selection, Matrix, writer, or export authority into
  runtime.
- No integration evidence above `diagnostic_only` may be claimed until real
  consumer integration is separately implemented and artifact-validated.
