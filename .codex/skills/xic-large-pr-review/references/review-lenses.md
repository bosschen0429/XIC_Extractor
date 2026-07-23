# Conditional Review Lenses

Load only the sections triggered by the review-closure ledger. These lenses
supplement the core closure process; they are not a flat checklist for every PR.

## Contents

- [Shared Helper Or Dependency Boundary](#shared-helper-or-dependency-boundary)
- [Schema, Writer, Reader, Or Public Output](#schema-writer-reader-or-public-output)
- [Identity, Join, Grouping, Or Cache](#identity-join-grouping-or-cache)
- [RAW Access, Evidence, Or Performance](#raw-access-evidence-or-performance)
- [Packaging, Entrypoint, Platform, Or Frozen Artifact](#packaging-entrypoint-platform-or-frozen-artifact)
- [Configuration, Defaults, Or Feature Flags](#configuration-defaults-or-feature-flags)
- [Diagnostics, Reports, Or Productization](#diagnostics-reports-or-productization)
- [Concurrency, Process, Retry, Or Cleanup](#concurrency-process-retry-or-cleanup)
- [Security Or Trust Boundary](#security-or-trust-boundary)
- [CI, Hooks, Generated Config, Or Governance](#ci-hooks-generated-config-or-governance)

## Shared Helper Or Dependency Boundary

Trigger: shared helper, parser, loader, formatter, model, default, dependency
rule, adapter, or import boundary changed.

- Enumerate every live caller and sibling boundary.
- Check direct and transitive effects, cycle direction, optional imports, and
  fallback paths.
- Verify a rule's full graph semantics rather than only the first reported path.
- Require exceptions/ignores to name an owner, rationale, and mechanical
  retirement condition.

Closure: the caller/dependency universe is reproducible and new members cannot
silently bypass the gate.

## Schema, Writer, Reader, Or Public Output

Trigger: TSV/CSV/JSON/workbook/report schema, encoding, path, writer, loader, or
projection changed.

- Trace writer-to-reader round trips and every public consumer.
- Check column/sheet order, newlines, encoding, tokens, null/zero/error
  semantics, hidden fields, deterministic row order, and backward reading.
- Compare produced bytes or normalized public values when the contract requires
  exact parity.
- Check partial writes, atomic publication, rollback, and post-write mutation.

Closure: producer and consumer agree on one contract, and the compatibility
oracle detects reorder, omission, duplication, and malformed input.

## Identity, Join, Grouping, Or Cache

Trigger: dict/set construction, grouping, deduplication, cache key, cross-file
join, provenance, hash, or persisted identity changed.

- State normalized key and cardinality.
- Check blank keys, duplicate matching/conflicting payloads, reversed order,
  collisions, supersession, cache reconstruction, and stale identity.
- Reject silent last-row-wins, double counting, or identity derived from display
  text.

Closure: illegal cardinalities fail closed and round-trip identity is stable.

## RAW Access, Evidence, Or Performance

Trigger: RAW/DLL access, XIC requests, scan/RT windows, batching, cache, evidence
availability, fallback, or performance claim changed.

- Count RAW opens, vendor calls, XIC requests, batch/super-window groups, trace
  points, elapsed time, memory, and artifact I/O where relevant.
- Verify one result per requested family/sample/seed, crop-back behavior,
  missing-window behavior, cancellation, retry, and partial failure.
- Separate call-count optimization from scientific parity.
- Match validation to the lane: synthetic, lane-specific RAW, benchmark, or
  manual EIC/MS2 evidence.

Closure: correctness and cost oracles cover the same workload and no fallback
silently changes evidence availability.

## Packaging, Entrypoint, Platform, Or Frozen Artifact

Trigger: `pyproject.toml`, package discovery, console scripts, GUI/CLI entry
points, resources, wheel, editable install, PyInstaller, DLL, or platform logic
changed.

- Distinguish source checkout, editable install, built wheel, installed command,
  and frozen application.
- Run the oracle outside the source tree when claiming installed behavior.
- Verify packaged files/resources from the built artifact, not source patterns.
- Check Windows/Linux path casing, separators, optional GUI libraries, and
  current-working-directory leakage.

Closure: each artifact claim is proved from that artifact; lower-layer
declarations are not presented as runtime closure.

## Configuration, Defaults, Or Feature Flags

Trigger: config key, default, CLI flag, environment variable, preset, or
activation condition changed.

- Trace absent, explicit false/zero/empty, invalid, legacy, and conflicting
  values through CLI, GUI, API, serialization, and replay.
- Check precedence, default drift, unknown-key behavior, and compatibility.
- Cross conditions so one true branch cannot mask another broken branch.

Closure: every supported value and precedence edge has one owner and executable
behavioral oracle.

## Diagnostics, Reports, Or Productization

Trigger: diagnostics, sidecars, overlays, reports, audit artifacts, promotion
gates, selected values/areas/counts, Matrix, workbook, activation, or export
changed.

- Identify whether each artifact observes, supports, selects, writes, counts, or
  promotes.
- Trace where authority enters and ensure renderers/writers do not recompute it.
- Check diagnostic-only, shadow, activation, rollback, kill, and externalization
  boundaries.
- Require method-appropriate evidence; artifact existence proves observability
  only.

Closure: the strongest granted authority is no stronger than the reviewed
evidence and explicit product contract.

## Concurrency, Process, Retry, Or Cleanup

Trigger: worker/process scheduling, queues, cancellation, retry, timeout,
temporary files, atomic replace, or cleanup changed.

- Check double execution, lost result, partial aggregate, cancellation timing,
  retry identity, exception aggregation, and cleanup failure.
- Verify deterministic ordering and exactly-once publication where claimed.
- Inspect failure after partial writes or cache mutation.

Closure: failure injection cannot leave a falsely successful or unreplayable
result.

## Security Or Trust Boundary

Trigger: external input, SQL, HTML, shell/process execution, file upload/path,
webhook, auth/authz, or secret handling changed.

- Validate and normalize at the boundary.
- Check injection, traversal, escaping, authorization, secret exposure, and
  untrusted artifact rendering.
- Exercise rejected input and audit behavior.

Closure: invalid or unauthorized input fails closed before side effects.

## CI, Hooks, Generated Config, Or Governance

Trigger: workflow, test shard, hook, `.codex` config/skill, checker, generated
registry, docs owner, or release gate changed.

- Verify clean-checkout command availability and exact shell/platform semantics.
- Check that the test executes the claimed behavior rather than grepping its own
  configuration.
- Validate stale-entry retirement, generated/source synchronization, and
  changed-path predicates.
- Run the owning smoke/scan contract and inspect fail-open behavior.

Closure: the gate goes red when its protected claim is broken and cannot pass
because of local state, skip behavior, or a mirrored assertion.
