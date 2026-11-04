# Run Lifecycle Management

Run lifecycle management schedules the work of one processing attempt and
decides whether that attempt completed: partitioning, worker admission,
cancellation, failure, resource cleanup, cache use, and timing. It does not
decide what the evidence means or what the product publishes.

## Current support

| Surface | Current support and limit |
| --- | --- |
| Partition scheduling | [`PartitionPipelineScheduler`](../../xic_extractor/execution/partitions.py) runs the bounded Untargeted CID-NL batch. A batch either completes every partition and its global closure, or fails or cancels as a whole. |
| Sample work | [`untargeted_execution.partition`](../../xic_extractor/untargeted_execution/partition/execution.py) runs one partition's samples on thread or process workers and returns their results or the failure that stopped them. |
| Timing | [`TimingRecorder`](../../xic_extractor/execution/timing.py) records stage spans, process-memory peaks, partition timelines, and Discovery acquisition counts. It is a diagnostic record, not an external telemetry backend. |
| Dependency reuse and resume | `ValidatedDependencyToken` and cross-attempt work reuse are not implemented. Their reserved contract is separated under [Retry and resume](#retry-and-resume). |
| Product publication | Remains with workflow projection and writer owners. Runtime completion does not grant selected-value, counted-detection, Matrix, workbook, Backfill-write, or export authority. |

The lifecycle validation boundary remains `diagnostic_only`. Executable wiring,
synthetic failure coverage, and scientific/product readiness are different
claims. [Workflow integration](#workflow-integration) describes the actual
coverage of each consumer.
Runtime returns its completed result directly; it does not persist an attempt
marker or block a later attempt on a marker left on disk. Workbench's busy state
and application output-collision checks protect their existing boundaries.

## Ownership

The execution runtime runs one externally identified `ProcessingAttempt`.
Identity, dependency compatibility, and retention come from
[Result Traceability](processing-lineage.md); acquisition meaning comes from
the evidence owner.

The execution runtime owns:

- scheduling partitions serially or with bounded acquisition/composition
  overlap, and sample work onto thread or process workers;
- bounded cooperative cancellation and pending-work suppression;
- deciding the batch outcome from the first execution failure, or from
  cancellation when no execution failure occurred;
- closing RAW handles in the worker that opened them and draining process
  pools before the batch result is returned;
- keeping completed partition results readable after a failed batch;
- the acquisition cache lifecycle and timing records.

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
- scientific cache-key meaning; the acquisition owner keys cache entries;
- storage engine, schema, location, or retention policy.

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
Local source revision binding uses the same worker budget with threads. These input
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
result and error arrays. Thread and process execution use the same outcome
application in `untargeted_execution.partition.workers`; it binds each job's
facts and first error together. The coordinator owns scheduling, cancellation,
and the batch outcome.

The tissue validation plan exposes both parallel and Alignment worker budgets.
Changing from 8RAW to 85RAW does not choose a different execution profile.

## Workflow integration

### Targeted application coverage

[TargetedApplicationService](../../xic_extractor/applications/targeted/application.py)
is the Python, CLI, and HTML application entry. It snapshots inputs, acquires
once, rejects incomplete populations at the workflow handoff, composes the
scientific result, and publishes only a complete finalized Target population.
Cancellation after acquisition or composition skips publication.
The workflow consumes acquisition results directly and the writer reads the
completed publication model without mutating it. Neither handoff clones the
full result or its trace arrays. Targeted does not provide bounded termination
of an in-flight serial vendor call.


### CID-NL Alignment execution

The bounded production entry schedules initial jobs only for sample/subject
pairs with Discovery candidates. Shared sample templates retain the complete
population and acquisition settings. Missing jobs are created only after the
direct-SUPPORT group passes the Backfill population gate; unsearched cells
receive their assessment at final composition without worker dispatch.
The direct batch interface runs every declared job together.

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
Cache entries are closed before atomic replacement and validated on read, but
are not individually forced to stable storage. Power loss can require
reacquisition of recent entries; this cache does not provide durable runtime
acknowledgements.
Without a verified source expectation, the existing native-open source check
remains in force. This cache does
not preserve completed grouping graphs or turn an old failed attempt into a
successful result.

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

The scheduler polls the caller's `should_cancel` between partitions and passes
it to sample workers. Once cancellation is observed, no further partition is
admitted, running acquisition stops at its next check, and the batch raises.
The partition timeline marks never-started partitions `cancelled`. A sample
`InterruptedError` counts as cancellation only when cancellation was actually
requested; otherwise it is an ordinary execution failure. When one sample fails
and another observes the resulting cancellation, the batch reports the failure.

#### Global closure work

After every partition completes, the store is sealed and the adapter composes
the global application result. A closure failure or a cancellation at that
boundary fails the whole batch; completed partitions stay readable through the
failed-results checkpoint described below.

## Failure, Cancellation, And Cleanup

Any partition failure, phase-preparation failure, global-closure failure, or
cancellation stops admission and fails the batch with
`Partition pipeline execution failed before batch publication`; the original
exception is its `__cause__`. No partial batch is published.

- RAW handles close in the worker that opened them. A RAW open or cleanup
  failure becomes that sample's error, so it fails the partition.
- Process worker pools drain and close before the batch result returns, also
  after failure or cancellation. A pool shutdown failure fails an otherwise
  successful batch.
- A failed batch writes `failed-results.pickle` into its partition spool so
  completed partition results remain inspectable. The original exception
  carries a note with that location.

Bounded hard cancel is available only across a killable process boundary. An
in-process serial Thermo call may support cooperative cancellation but cannot
claim bounded terminalization.

## Retry And Resume

Retry never reopens a terminal attempt. The lineage owner:

- creates a new `ProcessingAttempt` identity;
- determines whether the normalized `ProcessingRequest` is unchanged;
- records the predecessor/retry relationship;
- validates immutable artifact dependencies for integrity, staleness, source,
  schema, and semantic-role compatibility.

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
- a miss or stale rejection triggers ordinary work execution rather than
  another attempt state;
- an attempt-local volatile cache is removed after a successful batch; a failed
  attempt keeps it so a retry does not reread every RAW file;
- persistent cache format, retention period, and eviction policy remain
  separate owner decisions.

Physical deletion is limited to the attempt's own spool and volatile cache.

## Minimum Observability

`TimingRecorder` is the runtime's only observability record. It keeps:

- stage spans and the live heartbeat;
- process-tree memory peaks per scope (`process_tree_sampled_peak_rss_bytes`
  and `process_tree_sampled_peak_private_bytes`);
- the partition timeline: per-partition acquire and compose spans, overlap with
  the previous partition, and `completed`, `failed`, or `cancelled` status;
- per-sample Discovery acquisition counts.

Every measurement records its scope, unit, aggregation rule, source, and
availability (`measured` or `unavailable`) plus reason. Missing optional cost
telemetry does not change scientific or attempt outcome.

| Discovery metric | Meaning |
| --- | --- |
| `raw_open_count` | RAW sessions the sample opened (one), or unavailable with a reason |
| `logical_xic_request_count` | Sum of logical trace requests presented to the adapter |
| `xic_batch_count` | Sum of adapter batch invocations |
| `adapter_chromatogram_call_count` | Sum of observed vendor/backend chromatogram calls; separate from logical requests |
| `trace_point_count` | Sum of returned trace points |

The four acquisition counts are sums over the sample's `TraceAcquisitionBatch`
records. These metrics describe cost and execution locality. They do not score
evidence quality or authorize a result.

The cache-disabled successful comparator is a hard locality gate: baseline and
candidate must have exact equality for RAW opens, logical XIC requests,
batches, adapter calls, and trace points. Elapsed time and memory use paired
baseline/candidate measurements in the same environment. There is no universal
numeric tolerance before that baseline exists: the owning implementation lane
must preregister its elapsed/memory budget before promotion, and missing
tolerance keeps the runtime at `diagnostic_only`. A hard-locality mismatch, a
lane-budget breach, or missing required measurement is a rollback/stop signal,
never a reason to relax scientific parity.

## Verification and limits

Executable contract coverage lives with the owner:

- [Scheduler tests](../../tests/test_execution_engine.py): phase barriers,
  overlap, cancellation, failure, cleanup, and the partition timeline.
- [Bounded runtime tests](../../tests/test_untargeted_cid_nl_bounded_runtime.py):
  partition parity with direct execution, worker threading, RAW handle
  lifetime, and failure modes.
- [Timing tests](../../tests/test_timing.py): measurement records and memory
  peaks.

Consumer integration additionally needs serial/process parity, unchanged
selected results and writer bytes, complete failure/cancellation containment,
and the locality measurements above. Synthetic worker tests do not establish
RAW hard-cancel behavior or a full-run publication guarantee. Use
synthetic/no-RAW checks unless a bounded vendor-call question requires RAW.
