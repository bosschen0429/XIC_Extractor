# Runtime Execution

`xic_extractor.execution.partitions` schedules partitioned work and decides the
batch outcome: a batch completes every partition and its global closure, or it
fails or cancels as a whole. Runtime completion grants no scientific, artifact,
writer, or publication authority.

- A real execution failure outranks cancellation observed by sibling work.
- Missing timing observations remain unavailable with a reason. They never
  become inferred zero.
- Timing counts describe cost and locality only; they do not select a cell,
  value, state, or writer decision.

How a run uses the machine has one owner, `ExecutionProfile`: worker budgets
and process or thread backend. CLI,
Workbench and diagnostic entries build one per run; Targeted settings resolve
theirs in `configuration/settings.py`. Stages receive that value instead of
worker arguments of their own. RAW header reads use the profile module's fixed
thread count.

Only `xic_extractor.execution` opens thread or process pools for analysis work.
The `only-execution-opens-pools` import contract fails any other module in
`xic_extractor`, `scripts`, `tools` or `xic_inspector` that imports `concurrent`
or `multiprocessing`; the two ignored edges are frozen-application entry points
calling `freeze_support`. The Workbench's single-thread job queues order user
requests and are not analysis parallelism.

A Windows spawn worker reads its initializer after importing the main module:
pass a shared-memory name, not a payload that serializes each worker's startup.
Overlapping batches share the executor's capacity. On stop, observe outstanding
work after the wait; a result closes its RAW handle before publication, but
failure or cancellation must drain active siblings before returning.

The partition store uses native memo references to preserve object identity.
Independently addressable tuple fields must restore without loading the whole
graph; exclusions bind by canonical candidate position. Trusted objects are
shared by memo reference, not looked up again for every scalar in a trace. Exclusive creation
does not transfer ownership of an existing file to the failed writer.
Run wall time and progress use separate clocks from stage timing; charge nested
wall time to the parent on the stack, not by summing finished stages.

Keep Windows process payloads pickleable; no nested closures or non-pickleable
factories across process boundaries.
