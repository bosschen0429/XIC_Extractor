# Architecture Contract

This file owns the current code-organization, dependency, and responsibility
boundaries of XIC Extractor. Detailed product behavior belongs in
[`docs/product/`](product/README.md); exact exports, schemas, and construction
rules belong to source and executable checks. Migration history and
decomposition plans belong in Issues, PRs, and Git.

## Design Principles

- Make data flow and ownership obvious.
- Keep domain behavior independent from IO, GUI, workbook rendering, process
  backends, and CLI wrappers.
- Let entry points orchestrate focused owners; do not turn them into permanent
  implementation homes.
- Add abstraction only when it removes demonstrated coupling, duplication, or
  cognitive load.
- Preserve public contracts unless an approved change names the consumer,
  observable delta, and rollback.

## System Shape

```text
GUI / CLI
    │
configuration ──► extraction / discovery
                         │
                  peak_detection
                         │
                     alignment
                         │
                 output renderers
```

Targeted and Untargeted are applications of the same evidence lifecycle.
Targeted adds target-list/search policy and fixed-row delivery; Untargeted adds
general discovery, grouping, and matrix delivery. Separate entry points and
writers do not justify duplicate capture, peak identity, CrossSample,
ModeTrack, ObservationRepair, or cell-decision owners.

The detailed scientific lifecycle is owned by
[`shared-processing-kernel.md`](product/shared-processing-kernel.md),
[`alignment.md`](product/alignment.md), and
[`decision-policy.md`](product/decision-policy.md). Processing identity and
delivery lineage are owned by
[`processing-lineage.md`](product/processing-lineage.md); scheduling,
cancellation, cleanup, recovery, and observability are owned by
[`execution-runtime.md`](product/execution-runtime.md).

Targeted and Untargeted now close through the shared scientific-owner
interfaces while retaining their application-specific search scope,
projection, and writer contracts. Discovery providers may interpret different
inputs and publish different typed evidence, but they enter one shared
observation, Alignment, ObservationRepair, Backfill, and cell-decision
lifecycle. Provider-specific evidence handling must not create a parallel
downstream decision core.

CID-NL acquisition/assessment subjects remain exact provenance while compatible
subjects may share an alignment workset. The method-subject owner validates
workset compatibility, execution keeps that workset atomic, and the existing
CrossSample/cell owners retain exact support bindings and select the bounded
recovery subject. A scheduling partition or subject center cannot become a
feature boundary. The [Alignment contract](product/alignment.md) owns this
policy; result writers consume the completed groups and cells.

## Dependency Direction

- Domain algorithms may depend on arrays, typed configuration/context, and
  small domain models.
- Domain algorithms must not import GUI, workbook builders, CLI scripts,
  process backends, report renderers, or RAW/CSV adapters.
- Adapters, IO, rendering, and entry points depend inward on domain owners.
- `alignment.pipeline.run_alignment` may dispatch pure single-provider CID-NL
  work to the retained execution adapter in `extraction/` after Alignment's
  output-collision guard. Alignment may only consume the completed typed batch
  result; it must not duplicate that runtime or let the adapter write outputs.
- Retained CID-NL execution partitions complete subjects across the sample cohort
  under one runtime attempt. It adapts method acquisition and composition to the
  generic execution engine in `execution_engine/`. The engine's private result store
  spools completed scientific results for same-process reuse; it grants no scientific,
  activation, or writer authority. Scientific and projection owners restore their
  own process-local completion seals, and the runtime owner compacts acquisition
  facts after those scientific results have been stored.
- Keep a compatibility facade only for a current public/downstream contract or
  real external consumer. Tests and historical artifacts are not consumers.
- When replacing an internal surface, remove its obsolete implementation,
  alias, tests, and migration shim unless a current compatibility consumer
  remains.
- A diagnostic or shadow path must name the production or adapter transfer
  that would make it useful; collecting more evidence is not itself an exit
  rule.

`.importlinter` is the coarse executable dependency floor.
[`architecture-public-contracts.md`](agent/architecture-public-contracts.md)
owns public, persisted, writer, and runtime authority rules.

## Ownership Map

Use this map to choose the reason a package changes. File placement is owned by
[`project-layout.md`](project-layout.md).

| Surface | Owns | Does not own |
| --- | --- | --- |
| `configuration/` | settings, target/config loading, validation, typed runtime settings | extraction or rendering |
| `discovery/` | general candidate discovery, seed generation, workflow-neutral discovery facts | matrix decisions or target-list policy |
| `execution_engine/` | generic partition scheduling, worker-pool admission/concurrency, streamed result store, and bounded spool lifecycle | method-specific science, subject splitting, or writer authority |
| `extraction/` | Targeted orchestration, jobs, windowing, drift, backend coordination, and retained Untargeted CID-NL execution adapter | GUI, workbook rendering, or Alignment decisions |
| `peak_detection/` | physical observations, hypotheses, resolvers, boundaries, integration, evidence composition | cross-sample grouping, workflow policy, or writers |
| `alignment/` | cross-sample grouping, refinement/repair composition, Alignment adapters and renderers | a second runtime owner, mutable transport authority, or Targeted-only science |
| `output/` | workbook/TSV/HTML schema rendering | evidence recomputation or RAW access |
| `diagnostics/` | reusable diagnostic models, summaries, and classification | CLI parsing, production mutation, or domain recomputation |
| `gui/` and `scripts/` | interaction and thin orchestration | reusable domain behavior |
| root facades | current public compatibility and thin orchestration | new implementation detail |

Cross-cutting owners remain focused:

| Owner | Responsibility |
| --- | --- |
| `sample_metadata.py` | canonical sample identity and immutable metadata snapshots |
| `review_records.py` | review subject/action identity and supersession |
| `evidence_artifacts.py` / `artifact_lineage.py` | exact-byte artifact identity and supplied-lineage validation |
| `processing_identity.py` | request, attempt, source, and retry-predecessor identity |
| `execution_runtime_owner.py` | atomic runtime execution, lifecycle, observability, recovery, and finalization |
| `acquisition_contract.py` | capability-bound acquisition batches and mechanical call/point facts |
| `delivery_lineage.py` | Targeted delivery-cell, revision, stage, and seal-candidate identity |

Exact model fields, companion modules, public exports, and allowed construction
sites are source-owned. Query CBM and inspect the current consumer before moving
an owner; do not expand this table into a mirrored source inventory.

## Evidence And Decision Boundaries

XIC Extractor is an LC-MS evidence and model-selection system, not an
8RAW/85RAW- or CID-NL-specific tool. Fixtures are validation oracles, not
architecture boundaries.

- Evidence providers enter through `EvidenceVector`, `PeakHypothesis`, model
  selection, and `AuditTrail` before matrix/export mutation.
- A new evidence source states whether it is audit-only, hypothesis
  enumeration, calibration, production candidate, or retirement.
- Direct matrix writes require an activation/export contract, expected diff,
  and focused output oracle.
- Name current modules and contracts after owned behavior, not an Issue or PR,
  milestone, numbered phase/package, opaque gate label, dated attempt, or first
  validation dataset.

`xic_extractor.evidence_semantics` is role-neutral and serves both candidate
selection and product projection. Targeted role policy belongs in the Targeted
projection builder, not in shared semantics. Because Alignment also consumes
the shared layer, changes there have cross-path blast radius. The complete
resolution order is in [`decision-policy.md`](product/decision-policy.md).

## Diagnostics Contract

`tools/diagnostics/` is maintained product-adjacent code.

- CLIs parse, validate, and orchestrate only.
- Reusable loading, classification, models, summaries, plotting, and writers
  belong in focused package modules.
- Diagnostic writers render; they do not recompute evidence or rescan RAW.
- Gate diagnostics emit machine-readable status/reason plus a short blocker
  and next action.
- Optional diagnostics use sidecars rather than silently changing established
  output schemas.
- Adding, removing, or renaming a diagnostic entry point updates
  [`tools/diagnostics/INDEX.md`](../tools/diagnostics/INDEX.md) in the same
  change.

## Refactor And Verification

- Move behavior before changing behavior. Do not mix structural moves with
  scoring, selection, matching, or integration changes.
- Characterize uncovered behavior before moving it.
- Keep Windows process payloads pickleable; do not pass nested closures or
  non-pickleable factories across process boundaries.
- Responsibility count matters more than line count. Split a module when a
  change adds a second owner, not to satisfy an arbitrary size budget.
- Follow [`testing.md`](agent/testing.md) for observable-based test selection
  and RAW/workbook validation.
- Shared models or protocols are justified when they prevent an actual import
  cycle or schema drift. A new shared behavior needs current consumers from
  both sides of the claimed seam; a provider-specific implementation does not
  become shared merely by moving it into a common package.
