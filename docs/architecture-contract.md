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
  cognitive load for a current consumer. A private decomposition or concrete
  dependency adapter can be useful with one caller; shared behavior requires
  compatible current consumers. Name the present benefit and added maintenance
  cost, without building layers for unnamed future consumers.
- Add configuration for a present actor's meaningful value choice and observable
  effect. Account for validation, replay, GUI/CLI, and documentation costs;
  a hypothetical future value does not justify a new setting.
- Preserve public contracts unless an approved change names the consumer,
  observable delta, and rollback.

## Change Locality

For ownership, serialization, derived-fact, or internal-defense changes:

1. Before mutation, trace one affected production entry through its authoritative
   producer and validation to the current consumer. Locate where the needed
   invariant is established; reuse current CBM/source evidence in the task.
2. Before adding fallback, recomputation, or deep revalidation, name a reachable
   input/path and its observable loss. Justify the check by changed inputs or an
   actual trust boundary. Repair a fixed internal bypass at its owner or caller;
   uncertainty alone does not justify another defense.
3. Update consumers and retire superseded surfaces in the same responsibility
   change. The [testing contract](agent/testing.md) owns which oracles survive.
4. Before completion, take one rule or representation changed in this diff and
   identify every affected module that must know it. Justify each remaining
   dependency. An ordinary consumer independently reconstructing owner-derived
   facts or parsing other owners' internal ancestor schemas leaves the refactor
   incomplete; resolve it at the owner within the authorized scope. A separately
   required audit keeps its own oracle and does not replace the production fact.

Use source and existing observable checks for this acceptance. Record the result
in the current task/PR, not a permanent inventory or a new generic lint gate.
Established-owner local edits retain their compact workflow; this is a check of
the affected flow, not a whole-repository audit.

## Responsibility Boundaries and Anti-Paranoia Invariants

To maintain clean code and prevent cognitive overload, runaway monoliths, and defensive recomputation traps:

1. **Domain Purity & I/O Isolation**:
   - Scientific and mathematical algorithms (peak detection, alignment, hypothesis grouping, scoring) operate purely on in-memory domain models.
   - Core domain algorithms must never import, instantiate, or invoke file handles, RAW readers, vendor DLLs, multiprocessing pools, or disk persistence.
   - All I/O, concurrency orchestration, and batch chunking belong exclusively to outer orchestrators, worker harnesses, or dedicated adapters.

2. **Certified Upstream Trust (No Defensive Paranoia)**:
   - Reuse facts computed or validated by the formal owner for unchanged inputs.
     Consumers check only identity and binding needed for their use. Keep
     validation of persisted or external input at its real entry boundary;
     [Change Locality](#change-locality) governs exceptions and owner repairs.

3. **Orthogonality of Computation vs Auditing and Serialization**:
   - Domain algorithms calculate domain answers and return pure result structures (e.g., `AlignmentResult`, `HypothesisGroup`).
   - Calculating SHA-256 byte digests, serializing to JSON/TSV, and minting runtime finalization receipts are separate responsibilities owned by boundary adapters or reporting layers, never embedded within algorithmic loops.

4. **Centralized Primitive Reuse (No Private Boilerplate)**:
   - Shared mathematical arithmetic (e.g., ppm/Da mass tolerance), byte hashing, and primitive sequence validations belong to centralized utility modules (`xic_extractor.validation_primitives`, core math modules).
   - Copy-pasting private assertions, duplicate helper routines, or local tolerance formulas across business files is strictly prohibited.

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
                         │
                completed evidence ──► xic_inspector
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
- `xic_inspector` depends inward on read-only completed-evidence interfaces.
  Source adapters own method-specific record/schema interpretation; its public
  API, selection, and renderer remain method-neutral. Scientific/core packages
  must not import or invoke the Inspector.
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
| `extraction/` | Targeted orchestration, jobs, windowing, drift, backend coordination, and retained Untargeted batch execution (`untargeted_production_batch`, `untargeted_alignment`) | GUI, workbook rendering, or Alignment decisions |
| `peak_detection/` | physical observations, hypotheses, resolvers, boundaries, integration, evidence composition | cross-sample grouping, workflow policy, or writers |
| `alignment/` | cross-sample grouping, refinement/repair composition, Alignment adapters and renderers | a second runtime owner, mutable transport authority, or Targeted-only science |
| `output/` | workbook/TSV/HTML schema rendering | evidence recomputation or RAW access |
| `diagnostics/` | reusable diagnostic models, summaries, classification, and alignment explanation persistence (`alignment_explanation`) | CLI parsing, production mutation, or domain recomputation |
| `xic_inspector/` | installed read-only review API/CLI, source adapters, canonical selection, and self-contained offline HTML rendering | RAW acquisition, scientific recomputation, identity/Matrix decisions, or write authority |
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

`xic_extractor.evidence_semantics` is role-neutral and serves both candidate
selection and product projection. Targeted role policy belongs in the Targeted
projection builder, not in shared semantics. Because Alignment also consumes
the shared layer, changes there have cross-path blast radius. The complete
resolution order is in [`decision-policy.md`](product/decision-policy.md).

## Module Naming

Name new or renamed modules and commands for the capability they provide and
the responsibility they own. A reader should understand their purpose without
knowing the development history: for example, use `quant_matrix_export` for
matrix export rather than `phase7` or `issue367_bundle`.

Issue numbers, rollout phases, and dataset labels may explain provenance, but
must not substitute for a capability description. Domain terms such as a real
runtime phase, schema version, or scientific name remain appropriate when they
describe the current behavior. Judge meaning in context rather than matching
forbidden words. Preserve compatibility when considering existing names; this
guidance does not require unrelated renames.

Method specificity (e.g. CID-NL matching rules, neutral loss thresholds, scan-level
assertions) is strictly absorbed internally within `discovery/` and method models.
Downstream orchestration, batch execution, and diagnostic explanations are shared
system capabilities named by capability (e.g., `untargeted_production_batch`,
`untargeted_alignment`, `alignment_explanation`), preventing a single method from
monopolizing or leaking into shared infrastructure.

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
- Split a module to isolate coherent complexity or a distinct reason to change.
  Private decomposition can preserve the existing semantic owner; a new file
  does not require inventing another owner. Line count alone is not a reason.
- Follow [`testing.md`](agent/testing.md) for observable-based test selection
  and RAW/workbook validation.
- Shared models or protocols are justified when they prevent an actual import
  cycle or schema drift. A new shared behavior needs current consumers from
  both sides of the claimed seam; a provider-specific implementation does not
  become shared merely by moving it into a common package.
