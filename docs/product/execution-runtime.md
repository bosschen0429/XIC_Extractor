# Run Lifecycle Management

Run lifecycle management records what executed during one processing attempt:
scheduling, cancellation, worker loss, cleanup, cache use, and finalization.
It does not decide what the evidence means or what the product publishes.

## Current support

| Surface | Current support and limit |
| --- | --- |
| `AttemptRuntime`, required-work manifest, resource settlement, finalization receipt, and recovery marker | Executable owner contracts in [execution](../../xic_extractor/execution/attempt.py), consumed by Targeted and Untargeted application paths. A receipt covers its declared work, not automatically the entire product run. |
| Observability ledger | The owner derives ordered lifecycle facts and mechanical counts from accepted receipts; callers supply measured timing, RSS, and runtime-mediated I/O. This is not a general public `RuntimeEvent` API or an external telemetry backend. |
| Failed-attempt marker rollover | `OwnerPathRuntimeMarker.allow_failed_attempt_retry` can retain the old finalized failed/cancelled marker when a new attempt starts at the same location. |
| Dependency reuse and resume | `ValidatedDependencyToken` and cross-attempt work reuse are not implemented. Their reserved contract is separated under [Retry and resume](#retry-and-resume). |
| Product publication | Remains with workflow projection and writer owners. Runtime completion does not grant selected-value, counted-detection, Matrix, workbook, Backfill-write, or export authority. |

The lifecycle validation boundary remains `diagnostic_only`. Executable wiring,
synthetic failure coverage, and scientific/product readiness are different
claims. [Workflow integration](#workflow-integration) describes the actual
coverage of each consumer.

## Ownership

One runtime state machine manages one externally identified `ProcessingAttempt`,
a closed required-work manifest, runtime resources, and non-authoritative
temporary artifacts. Identity, dependency compatibility, and retention come
from [Result Traceability](processing-lineage.md); acquisition meaning comes
from the evidence owner.

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

## Worker Settings

`xic_extractor.configuration.execution_profile` owns the shared execution profile and worker
defaults. The `application` section in `config/workspace.json` owns the saved `parallel_workers`
value used by Targeted, Discovery CLI/HTML, and tissue validation. When absent,
the profile supplies the CPU-count-minus-one default (at least one). An explicit
CLI override takes precedence for that invocation; it does not rewrite saved
settings. Scientific presets do not carry machine resource settings.

Targeted accepts a blank or positive-integer `parallel_workers` and uses this
same execution-profile owner. Its backend remains `process`; the retired
`parallel_mode` key is not accepted; legacy CSV workspaces are not imported. Worker
configuration does not reopen retired scientific overrides.

Alignment defaults to four workers in the same profile. Its explicit worker
override remains available, including one for serial execution. Discovery
bounds its active workers by sample count; that cap is scheduling, not another
default. The profile is a settings snapshot, not a shared persistent worker pool.

For Alignment, `--raw-workers` also bounds persisted physical-input indexing.
Alignment defaults to the `process` RAW worker backend
(`DEFAULT_ALIGNMENT_WORKER_BACKEND`), because Thermo reads scale poorly across
threads in one process. `--raw-worker-backend thread` remains available.
The process backend runs the CPU-heavy sidecar parsers in separate
processes; only compact indices and private spool ownership cross that boundary.
Source fingerprinting uses the same worker budget with threads. These input
phases finish before RAW runtime, so their pools do not multiply the RAW worker
budget. A value of one keeps serial behavior; concurrency does not change
scientific identity or sample order.

The bounded CID-NL process backend retains one worker pool per acquisition
lane for the processing attempt. Partitions lease those pools and share the
same immutable owner bootstrap; local candidates and physical facts remain
partition-bounded. The combined pool capacity stays within the configured RAW
worker budget. Sample RAW handles still close at their acquisition boundaries.
Process pools drain and close before terminal acknowledgement, including on
failure or cancellation; a cleanup failure prevents successful publication.

Sample-phase workers return work-unit-keyed outcomes rather than parallel
result, observation, and error arrays. Thread and process execution use the
same outcome application in `untargeted_execution.partition.workers`; it binds
each job's facts and first error together with the shared acquisition/cleanup
facts. The coordinator owns scheduling, cancellation, and terminal settlement.

The tissue validation plan exposes both parallel and Alignment worker budgets.
Changing from 8RAW to 85RAW does not choose a different execution profile.

## Workflow integration

### Targeted application coverage

[TargetedApplicationService](../../xic_extractor/applications/targeted/application.py)
is the Python, CLI, and HTML application entry. It snapshots inputs, acquires
once, rejects incomplete populations at the workflow handoff, composes the
scientific result, and publishes only a complete finalized Target population.
Cancellation after acquisition or composition skips publication.

Foreground acquisition and the per-Target runtime receipt are separate
lifecycles. The receipt acknowledges the retained-acquisition handoff, not
foreground RAW execution, scientific composition, or publication. It does not
provide a single full-run supervisor acknowledgement or bounded termination
of an in-flight serial vendor call.


### CID-NL Alignment execution

The bounded production entry schedules initial jobs only for sample/subject
pairs with Discovery candidates. Shared sample templates retain the complete
population and acquisition settings. Missing jobs are created only after the
direct-SUPPORT group passes the Backfill population gate; unsearched cells
receive their assessment at final composition without worker dispatch.
The direct batch interface retains its declared complete manifest.

Worksets remain indivisible. Scheduling packs them by 100 Da ownership and a
1,024-candidate budget; a workset may cross either bound. Partitions do not
restrict scientific comparison or RT calibration. SearchScope preserves the
upstream workset rather than re-partitioning it by resolver intervals.
Initial assessments and grouping inputs are spooled; only compact anchor RT
pairs accumulate globally. All bootstrap partitions finish before the batch
warp is frozen. Failure or cancellation before that point prevents Backfill
admission and final publication.

Each final partition restores its spooled inputs and acquisition records,
regroups once with the frozen warp, and runs Backfill without reacquiring its
initial traces or reading another partition's grouping. Direct and bounded
execution share in-memory preparation; only the bounded adapter owns spool I/O.
The opt-in grouping experiment prepares comparison inputs separately and stops
before production Backfill.

Acquisition planning counts direct-SUPPORT membership without constructing a
dense group-by-sample cell population. Backfill process requests include only
scheduled subjects; responses update those work-unit keys and retain the other
initial results. Distinct groups can share one acquired trace while retaining
their own group-bound attempts. Repeated or unordered complete XIC queries,
including mass, are canonicalized at acquisition. Combining overlapping RT
contexts retains each candidate's extraction m/z and ppm; only identical
resulting requests share acquisition.

The bounded Untargeted runtime keeps exact source-bound XIC and MS2 acquisition
facts in the output directory's private `.cid_nl_acquisition_cache`, or in the
directory given by `--acquisition-cache-dir`. That directory belongs to no
single run: runs into different output directories share it, and it is never
removed. Entries are keyed by acquisition source and request and verified on
read, so sharing cannot reuse another RAW file's or request's facts. A retry
uses a new processing attempt and recomputes scientific decisions; matching
queries reuse acquisition facts without repeating the vendor query. Source or
query changes require acquisition for the affected entries. Sample batches with
an input-owner-verified source check its current path identity before replay;
they open a native RAW handle only when a missing query or metadata needs it.
Runtime resource receipts report whether that native open actually occurred.
Cache entries are closed before atomic replacement and validated on read, but
are not individually forced to stable storage. Power loss can require
reacquisition of recent entries; this cache does not provide durable runtime
acknowledgements.
Without a verified source expectation, the existing native-open source check
remains in force. This cache does
not preserve completed grouping graphs or turn an old failed attempt into a
successful result. The Alignment entry point archives a finalized failed or
cancelled runtime marker before launching a new attempt in the same directory.

Targeted and Untargeted CID-NL close through the shared scientific workflow.
`applications.untargeted.application.run_untargeted_application` adapts pure CID-NL inputs to that workflow
and renders its finalized result. It requires explicit pure CID-NL discovery
settings; the Alignment CLI supplies them through a bound preset. Unsupported
discovery settings are rejected before acquisition or output creation.

Process workers project peak-fact observations through the provenance owner
before IPC. They send typed physical rows, bounded selected-integration data,
and feature/sample order binding, not raw traces or prepared-signal graphs.
The parent validates those delivery bindings. Completion order does not change
canonical subject, Matrix or sidecar ordering; failed runs do not publish
partial results.

Initial inference retains operation counts for candidate lookup, RT pruning,
edge evaluations, index updates and assignment solver calls. Index axes update
only when their extrema or composition key change. Single-owner/single-feature
competitions use the analytical optimum and next-best cost, including the
unassigned option; general components use the joint solver. A stable ID only
selects the diagnostic representative of an exact tie.

### Pipeline overlap and cancellation

#### Bounded admission

`PartitionPipelineScheduler` supports concurrent acquisition of partition \(P_{n+1}\)
while composing partition \(P_n\) without multiplying RAW acquisition resources:

- **Admission credits**: exactly 2 credits via a semaphore bound in-flight work
  units (acquiring, in handoff queue, and composing).
- **Handoff queue**: bounded queue with `maxsize=1` enforces backpressure so that
  acquisition does not outrun composition.
- **Independent acquisition**: CID-NL can acquire two complete mass-connected
  partitions concurrently when overlap is enabled and the configured worker
  budget is at least two. The budget is divided between the two partitions;
  the same two admission credits cover acquisition, queued results and composition.
  Out-of-order acquisition results wait for ordered, single-writer composition.
- **Bilateral abort**: producer acquisition failures immediately signal the
  consumer thread to drain and abort; consumer composition failures signal the
  producer to stop further acquisition, cancel pending tasks, and clean up spools.
- **Serial fallback**: automatically falls back to single-threaded serial execution
  when partition count is 1 or when `pipeline_overlap=False`.

#### In-flight cancellation

`RuntimeCancellationIngress` provides thread-safe cancellation signaling to active
runtime attempts:

- Signals are atomically recorded and verified against attempt identity.
- Accepted before terminalization proposal, transitioning the attempt cleanly to
  `CANCELLED` without leaving dangling reservations or unmanaged resources.
- Active work units are settled as `CANCELLED`, while un-opened resources transition
  to `NOT_OPENED`.

#### Global closure work

For workflows with post-partition global assembly (such as CID-NL composition):

- The required-work manifest explicitly pre-declares a `global_closure_work_unit_key`.
- If global closure fails, only the closure work unit is settled as `FAILED`,
  leaving already settled partition units as `SUCCEEDED`.

## Minimal Runtime Vocabulary

These concepts are internal mechanics, not user-visible review tags.

| Term | Meaning | Not the same as |
| --- | --- | --- |
| `AttemptRuntime` | Mutable in-memory state for exactly one lineage-identified `ProcessingAttempt` | The attempt identity or a product run |
| `RequiredWorkManifest` | Immutable, owner-supplied complete set of unique opaque work-unit keys required for this attempt | A discovered result or dynamic success list |
| `WorkUnit` | One manifest member that can be independently scheduled and settled | A scientific observation or delivery cell |
| `Worker` | Serial context or process that executes work units and declares its hard-cancel capability before scheduling | A stable identity or authority boundary |
| `AcquisitionSession` | One reserved resource record in one worker for one attempt, source, and opaque compatibility token; it may own one adapter handle | An `AcquisitionSource` identity |
| `RuntimeEvent` (reserved API) | Ordered fact about scheduling, resource, outcome, cleanup, cache, or metrics | Scientific evidence |
| `ValidatedDependencyToken` (not implemented) | Lineage-owner approval bound to the new attempt and exactly one manifest work-unit key, plus an opaque immutable dependency reference | Runtime-created identity or compatibility proof |

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
are unsupported. If a workflow cannot state its
complete non-empty work universe up front, stop and redraw the seam rather than
infer completion from only the units that happened to be submitted.

Settlements are bound to declared work keys. An identical replay is
idempotent; conflicting or undeclared settlements prevent success. Failed or
cancelled settlements carry typed failure details; successful settlements
carry same-request/same-attempt owner-minted artifact references, not opaque
strings or failure payloads.

A settled work unit may report both initial and recovery acquisitions. The
runtime retains distinct acquisition capabilities and deduplicates an exact
receipt replay; conflicting content for the same capability remains an error.
Batch adapters publish the acquisitions held by each phase directly, so a
later recovery cannot hide the initial acquisition from accounting.

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

The runtime keeps its reducer and detailed event ledger in memory. It uses
one small durable supervisor marker to close the terminal handoff without
claiming a durable queue:

1. before launch, the lineage owner or entry-point supervisor atomically records
   the owner-created attempt identity, attempt-scoped temporary-artifact
   namespace, and coordinator launch;
2. after work, cleanup, and artifact-disposition proposal closure, the
   coordinator produces one immutable canonical `CoordinatorTerminalRecord`
   `execution_runtime_coordinator_terminal` byte sequence containing the
   proposed outcome, manifest and per-unit outcomes, typed per-unit failure
   details, typed resource kinds plus open/cleanup outcomes, artifact registry and proposed
   dispositions, failures, coordinator metrics ending at terminal proposal,
   and the coordinator event-ledger digest through that proposal;
3. the owner-path supervisor fsyncs those exact coordinator bytes and the exact
   runtime-owner-derived observability-ledger bytes inside the
   self-contained `execution_runtime_recovery_marker` payload, then
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

The marker embeds the exact coordinator and validated observability-ledger
bytes with separate content digests. An exclusive sibling lock serializes
read/validate/replace: a concurrent writer waits and revalidates; an unresolved
stale lock fails closed for owner recovery instead of being auto-deleted.
The owner supplies the path, namespace, retention, and publication policy.
This is not a storage engine, durable queue, current-delivery pointer, or
product seal.


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

### Callback failures

`execution.attempt.execute_runtime_attempt(...)` also owns the exception
boundary for its three direct callbacks. A thrown `Exception` is classified as
one `RuntimeExecutionPhaseError` carrying the exact owner-issued
`ProcessingAttemptIdentity` and one closed `RuntimeExecutionPhase`:
`worker_batch_provider`, `telemetry_provider`, or `persister`. The original
exception remains available only as Python `__cause__`; its message and type do
not become runtime facts. A worker failure skips telemetry and persistence, a
telemetry failure skips persistence, and a persister failure returns no
accepted receipt even when the persister's own marker side effect is later
readable. Invalid callback returns continue through the existing
`RuntimeFinalizationContractError` validation path, and `BaseException` is not
wrapped. The owner invokes each callback at most once and adds no retry or
cleanup authority. Only an exact-equal persister return reaches
`accept_runtime_finalization_receipt`, which is the sole runtime result that a
workflow consumer may treat as accepted finalization.

## Retry And Resume

Retry never reopens a terminal attempt. The lineage owner:

- creates a new `ProcessingAttempt` identity;
- determines whether the normalized `ProcessingRequest` is unchanged;
- records the predecessor/retry relationship;
- validates immutable artifact dependencies for integrity, staleness, source,
  schema, and semantic-role compatibility.

For a caller that enables `OwnerPathRuntimeMarker.allow_failed_attempt_retry`,
launching a new attempt in the same output directory first archives the prior
finalized failed/cancelled marker under its attempt digest. The exact terminal
receipt remains available; replacing the current marker does not promote old
artifacts or change the predecessor's outcome. Active and successful attempts
are not replaced. Without this option, a different attempt at the same marker
path continues to be rejected.

### Reserved dependency-reuse contract

The following requirements apply to a future implementation; the current
runtime does not accept dependency tokens or resume completed work.

The runtime would receive the new attempt identity plus zero or more
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
acceptance, delivery `staged`, or sealing eligibility.

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
- dependency reuse accepted/rejected/not used and unit satisfaction source
  (reserved with the unimplemented dependency-reuse contract);
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

The executable runtime ledger uses one attempt-scoped `sum` record for the
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

For the Targeted and Alignment comparator contract, `raw_open_count`,
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

The cache-disabled successful comparator is a hard locality gate:
baseline and candidate must have exact equality for RAW opens, logical XIC
requests, batches, adapter calls, trace points, and runtime-mediated artifact
I/O. Elapsed time and RSS use paired baseline/candidate measurements in the
same environment. There is no universal numeric tolerance before
that baseline exists: the owning implementation lane must preregister its
elapsed/RSS budget before promotion, and missing tolerance keeps the runtime at
`diagnostic_only`. A hard-locality mismatch, a lane-budget breach, or missing
required measurement is a rollback/stop signal, never a reason to relax
scientific parity.

### Observability ownership

Callers supply `RuntimeTelemetrySamples`, not an assembled ledger. The runtime
derives lifecycle topology and mechanical counts from closed coordinator state.
Logical XIC requests, batches, backend calls, and trace points come from typed
`TraceAcquisitionBatch` receipts correlated to submitted successful work.

For a successful application result, an empty observation-batch projection is
valid only when all four acquisition counts are zero. Failed or quarantined
results may suppress exposed success batches while retaining physically
observed counts. Missing, extra, substituted, or conflicting success-projection
facts fail before acknowledgement.

Only an opened `PROCESS_WORKER` reservation creates `worker_started` and requires
worker RSS. Unopened, inactive conditional, and failed-open reservations do not
invent a worker. Monotonic offsets, RSS, and runtime-mediated I/O remain external
samples; provenance for owner-derived facts is fixed to
`execution_runtime_owner:v1`. Persistence consumes the accepted receipt's
owner-minted ledger and canonical bytes/digest, never a caller-built mapping.


## Verification and limits

Executable contract coverage lives with the owner:

- [State and manifest tests](../../tests/test_execution_runtime_events.py):
  preflight failure, ordered cancellation, and terminal-state rules.
- [Owner tests](../../tests/test_execution_runtime_owner.py) and
  [finalization tests](../../tests/test_execution_runtime_finalization.py):
  work/resource/artifact closure, callback failure, and exact receipt acceptance.
- [Marker tests](../../tests/test_execution_runtime_marker.py):
  launch/finalization persistence, competing writes, and coordinator-loss recovery.
- [Observability tests](../../tests/test_execution_runtime_observability.py):
  event ordering, metric availability, provenance, and receipt binding.

Consumer integration additionally needs serial/process parity, unchanged
selected results and writer bytes, complete failure/cancellation containment,
and the locality measurements above. State-table and synthetic worker tests do
not establish RAW hard-cancel behavior or a full-run publication guarantee.
Use synthetic/no-RAW checks unless a bounded vendor-call question requires RAW.
