# Architecture Contract

Code organization, dependency direction and responsibility boundaries. Product
behavior is in [`docs/product/`](product/README.md); exact exports, schemas and
construction rules are in source and executable checks.

## Reading The Code

先把 XIC 理解成一套「從量測證據得到樣本格值，再交付結果」的系統。
Targeted 用已知 target 約束搜尋並輸出固定列；Untargeted 找一般候選並輸出
feature-by-sample Matrix。兩者共用科學判斷，但保留不同搜尋政策及 writer。

閱讀順序：[Repo 結構樹](project-layout.md#repository-tree) →
[入口](#entry-points-and-handoffs) → [模組責任表](#ownership-map) →
[依賴方向](#dependency-direction)。只想理解分析概念，從
[人讀版核心導覽](development/core-modules-explainer.html)開始。

### Entry Points And Handoffs

安裝後的命令由 [`pyproject.toml`](../pyproject.toml) 的 `project.scripts` 宣告。

| 要追的行為 | 從哪裡讀 |
| --- | --- |
| HTML 工作區啟動、工作與檢閱 | [`xic_workbench/cli.py`](../xic_workbench/cli.py)、[`xic_workbench/server.py`](../xic_workbench/server.py) |
| Targeted 分析與交付 | [`xic_workbench/targeted.py`](../xic_workbench/targeted.py)、[`scripts/run_extraction.py`](../scripts/run_extraction.py) → [`applications/targeted/application.py`](../xic_extractor/applications/targeted/application.py) |
| 既有 Python Targeted 入口 | [`extractor.run`](../xic_extractor/extractor.py)，進入同一 Targeted application |
| Discovery 候選產生 | [`scripts/run_discovery.py`](../scripts/run_discovery.py) → [`discovery/pipeline.py`](../xic_extractor/discovery/pipeline.py) |
| Untargeted 全流程、Alignment 與 Matrix | [`xic_workbench/untargeted.py`](../xic_workbench/untargeted.py)、[`scripts/run_alignment.py`](../scripts/run_alignment.py) → [`applications/untargeted/application.py`](../xic_extractor/applications/untargeted/application.py) |
| 完成 Matrix 後的矩陣整理 | [`xic_workbench/matrix_curation.py`](../xic_workbench/matrix_curation.py)、[`scripts/run_matrix_curation.py`](../scripts/run_matrix_curation.py) → [`matrix_curation/export.py`](../xic_extractor/matrix_curation/export.py) |
| 完成證據的只讀報告 | [`xic_inspector/cli.py`](../xic_inspector/cli.py) → [`xic_inspector/api.py`](../xic_inspector/api.py) |

檔案層級的 owner 以現行 source 為準：用 CBM 查 callers 與 consumers，再讀
module docstring。Package 名稱不是分層證明：`alignment/` 同時含科學與外層
adapter；通過 `pyproject.toml` 的 `[tool.importlinter]` 不代表所有 I/O 已隔離。

## Core Model

One concept, one representation, one source.

- **One representation:** a concept is the same object and vocabulary from
  where it is produced to where it is written.
- **One source:** a fact is decided or computed in one place. Everything else
  reads it; nothing recomputes or restates it.

| Concept | What it is |
| --- | --- |
| Run | The samples, the settings and one run id |
| Peak | One measured peak in one sample: m/z, apex, bounds, area, height, and its method evidence |
| Group | Peaks across samples treated as one feature: its centre and whether it is delivered to the Matrix |
| Cell | One group or target in one sample: a value or the reason there is none, and whether the evidence is direct or Backfill |
| Matrix | Cell values laid out by group or target and sample, derived when written |

- A new class, column or value belongs to one of these concepts. Another
  spelling of an existing concept is not added; a new concept is added to this
  table in the same change.
- Concepts that look alike stay separate. Targeted and Untargeted cells are
  decided from different evidence; a peak's measurement and its method evidence
  are different facts.
- An algorithm's working structures are its own, as long as they do not cross a
  stage boundary or become a second vocabulary.
- Validate where data enters or leaves the process: files, user input, written
  output. Inside there is one copy and nothing to reconcile; a hash, receipt,
  identity object or constructor self-check on an in-process handoff is a second
  representation.
- Refer to a sample, group or peak by its domain key, not by a minted
  identifier.
- Missing evidence means the answer is unavailable. It is never an inferred
  zero or success.
- Replacing a representation removes the old one and its adapters in the same
  change.

Code that does not follow this yet is debt to converge, not precedent.

## Design Principles

- Make data flow and ownership obvious.
- Keep domain behavior independent from I/O, UI, workbook rendering, process
  backends and CLI wrappers. Entry points orchestrate; they are not
  implementation homes.
- Add abstraction only when it removes real coupling, duplication or cognitive
  load for a current consumer. A private decomposition can help with one caller;
  shared behavior needs compatible current consumers on both sides.
- Add configuration only for a present user's meaningful choice with an
  observable effect, counting its validation, replay, UI and documentation cost.
- Reuse established primitives for the same rule; similar assertion syntax alone
  does not justify a shared validator.
- Preserve public contracts unless an approved change names the consumer,
  observable delta and rollback. When changing a public entry point, show its
  inputs, outputs and key failures with existing tests or a minimal example.

## Configuration Documents

| Owner | Responsibility |
| --- | --- |
| `configuration.targeted_method_files` | strict Targeted XLSX exchange |
| `configuration.workspace_state` | local JSON application, batch and target persistence |
| `configuration.delta_mass` | shared pure pattern settings |
| `configuration.targeted_identification` | named all/any condition intake and quantitation-relative Delta Mass conversion |

Targeted acquisition reuses Discovery's matcher and CID-NL evaluation on the
same physical candidates; only method-admitted candidates enter completion, and
`evidence.targeted_identification` keeps clause answers for output and Inspector.
`prepare_targeted_config` freezes admitted inputs into private CSV run receipts;
`load_config_snapshot` reads such a receipt for diagnostics only. Workbench and
CLI do not discover legacy CSV workspaces. Public examples are read-only
resources in `configuration/data/`.

## Change Locality

For a change to who produces a fact, how it is serialized or how it is
identified:

- Identify the production entry, the producer of the fact and its current
  consumers, and apply the [Core Model](#core-model) to that flow. This is a
  check of the affected flow, recorded in the PR, not a repository audit.
- Allocate a public or persisted identifier only when a reference must cross a
  file, table or human-facing boundary and no natural key suffices. An existing
  identifier, or a test asserting it exists, is not a reason to keep it.
- Order and break ties with raw measured facts, not aggregate centers, scores,
  hashes or execution order. If competing facts share the full key, report the
  ambiguity instead of inventing an identity.
- Check file fingerprints at file handoffs.

### Internal Defense Growth Gate

`scripts/check_architecture_census.py --internal-defense-base REF` blocks
construction-time self-validation growth in changed `xic_extractor` source
(pre-commit against HEAD, CI against the base commit): a `__post_init__` or
`__init_subclass__` body with more statements than its counterpart in REF. A
hook keeps its counterpart when it keeps its qualified name, moves with that
name to another changed module, or is renamed with an identical body; a copy
counts as new. Validation moved into helpers, plain functions, `__init__`, new wrapper
classes and restated vocabulary are not gated; each ticket's acceptance owns
those. There is no suppression flag. A blocked change removes the unnecessary
machinery or gets an explicit maintainer decision; the agent does not grant its
own exception.

### Executable Census And Dependency Gate

The architecture census is disposable evidence, not an authority registry. It
derives current module paths, static imports, declared console scripts, and
package patterns from source.

- `[tool.importlinter]` in `pyproject.toml` owns the coarse static dependency floor.
- `scripts/check_architecture_census.py` owns the census integrity, naming,
  and internal-defense growth gates.
- Dynamic loaders, installed entry points, frozen applications, workflow
  checks, diagnostics indexes, and test selection remain with their respective
  executable owners.
- A dependency ignore is temporary migration debt: it needs a named owner and
  rationale, and unmatched-ignore alerting must remove it when the edge closes.
- Optional census output belongs outside the repository or under an ignored
  path. It is review/debug evidence only.

## System Shape

```text
HTML workbench / CLI
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

Targeted and Untargeted are applications of one evidence lifecycle. Targeted
adds target-list search and fixed-row delivery; Untargeted adds general
discovery, grouping and Matrix delivery. Separate entry points and writers do
not justify duplicate capture, peak identity, cross-sample, revisit or
cell-decision owners. The scientific lifecycle is in the
[Domain Glossary](product/domain-glossary.md#product-shape),
[`alignment.md`](product/alignment.md), [`backfill.md`](product/backfill.md)
and [`decision-policy.md`](product/decision-policy.md).

CID-NL interpretation lives in explicit method modules: tag/composition and mass
envelopes for grouping, revisit eligibility and assessment-to-cell evidence.
Shared grouping keeps physical mass/RT/shape and sample uniqueness; shared cell
composition keeps exact sample/group lineage. Workflow composition supplies
`GroupingRules`; shared inference does not select a method or read method reason
strings. The CID-NL cell record (`CellObservation`) answers its own
evidence state, which shared closure consumes. These are in-memory seams, not a
registry: another method needs its own evidence adapter. A revisit does not reopen grouping, and a scheduling
partition is never a feature boundary.

## RAW Acquisition Owners

- `raw_reader` owns Thermo DLL loading, native handle lifetime and copying
  RAW/SLD facts into Python values. DLL access stays in the adapter.
- `raw_source_binding` verifies file revisions at admission, cache replay and
  before/after vendor opening; it does not interpret spectra.
- `raw_sequence` resolves analysis membership and chronology from adapter facts.
- `raw_mass_recalibration` owns in-memory restoration decisions and arithmetic;
  `raw_mass_recalibration_batch` prepares source-bound results through the adapter
  and schedules workers. Neither responsibility is implemented by a facade.

## Dependency Direction

Startup-sensitive entry points and lightweight readers keep heavy,
branch-specific dependencies inside the path that needs them. Importing a shared
model or reading completed evidence must not load scientific computation or
RAW/DLL code through an unused capability. Delaying an import does not permit a
forbidden dependency.

`[tool.importlinter]` in `pyproject.toml` is the source for every enforced edge;
this section states the shape and does not restate the contracts.

- Domain algorithms depend only on arrays, typed configuration/context and small
  domain models; never on UI, workbook builders, CLI scripts, process backends,
  report renderers, or RAW/CSV adapters. Adapters, I/O, rendering and entry
  points depend inward.
- Main chain: applications → untargeted_execution／extraction → alignment →
  discovery → peak_detection. `acquisition/` stays inner.
- `applications/` is the outermost composition layer and owns a run from
  configuration to publication; `extractor.run` is the public root entry into
  it. Entry points pass typed intent, not flattened execution arguments.
- `execution/` and `untargeted_execution/` schedule, store and clean up. Runtime
  wraps scientific completion without changing its result.
- `xic_inspector` reads completed evidence only; scientific packages never
  import it.
- Keep a compatibility facade only for a real external consumer.

## Ownership Map

| Surface | Owns | Does not own |
| --- | --- | --- |
| `configuration/` | settings, target/config loading, validation, typed runtime settings | extraction or rendering |
| `applications/targeted/` | Targeted composition, request/result interface, input binding and publication | peak selection, cell decisions, RAW acquisition or workbook formatting |
| `applications/untargeted/` | Untargeted composition from Discovery to published Matrix, run lifetime and cancellation | grouping, cell decisions, a second runtime owner or writer algorithm |
| `discovery/` | candidate discovery, seed generation, workflow-neutral discovery facts and receipt-bound candidate reading | matrix decisions or target-list policy |
| `execution/` | generic attempt lifecycle, scheduling, workers, cancellation, cleanup, result storage and runtime metrics | method-specific science, subject splitting, or writer authority |
| `extraction/` | Targeted acquisition, result models, product projection, input binding and capture population | application composition, UI, or Alignment decisions |
| `untargeted_execution/` | retained Untargeted planning, acquisition cache, partition execution and private result lifetime | Targeted policy, application composition, Matrix writing or a second runtime owner |
| `peak_detection/` | physical observations, hypotheses, resolvers, boundaries, integration, evidence composition | cross-sample grouping, workflow policy, or writers |
| `alignment/` | cross-sample grouping, repair composition, Backfill and Alignment adapters | a second runtime owner, transport authority, or reading Targeted acquisition results |
| `output/` | schema rendering and the Targeted product result model; `output/alignment/` writes Matrix artifacts | evidence recomputation, application store lifetime or RAW access |
| `alignment_explanation/` | shared SQLite format, retention codecs, atomic publication and completed-evidence queries | scientific decisions, RAW acquisition, or Inspector presentation policy |
| `diagnostics/` | reusable diagnostics and method-specific projections of completed facts | CLI parsing, production mutation, or domain recomputation |
| `xic_inspector/` | read-only review API/CLI, source adapters, selection and offline HTML rendering | RAW acquisition, scientific recomputation, identity/Matrix decisions, or write authority |
| `xic_workbench/` and `scripts/` | HTML interaction, local transport, and thin orchestration | reusable domain behavior or scientific authority |
| root facades | current public compatibility and thin orchestration | new implementation detail |

| Cross-cutting owner | Responsibility |
| --- | --- |
| `sample_metadata.py` | sample metadata parsing, RAW mapping and role inference; a run's samples are `SampleMetadata` rows |
| `matrix_curation/` | post-Alignment curation over a published Matrix and its export; never Alignment decisions or normalization |
| `evidence_artifacts.py` | completed-evidence locator: report and Matrix paths, Matrix SHA-256, attempt UUID, schema version |
| `processing_identity.py` | RAW source fingerprints checked at file handoffs |
| `execution/attempt.py` | sole attempt construction, execution, settlement and finalization authority |
| `acquisition_contract.py` | capability-bound acquisition batches and call/point facts |

Model fields, exports and construction sites are in source; query CBM before
moving an owner rather than expanding this table.

### Workbench client modules

Workbench's [Analysis Session](../xic_workbench/assets/analysis_session.js)
owns client-side job commands, response ordering, observation lifetime,
connection status and once-only completion delivery. Its consumers submit
intentions and read immutable snapshots; rendering does not advance lifecycle.
`analysis_jobs.js` composes that Module with application-setting adoption and
presentation. [Sample metadata](../xic_workbench/assets/analysis_sample_metadata.js)
owns sample rows, read recency, cross-flow sharing and ordered folder-scoped
edits and Review exclusions. Consumers read immutable page snapshots and submit
edits by RAW stem; they do not coordinate revisions or mutate retained job
results. Within one Workbench page, metadata writes and Analysis jobs exclude
one another so sequence-import jobs cannot overlap those writes. Ending Analysis
observation does not cancel the server job; execution and publication authority
remain with their Python owners.

The [Curation Session](../xic_workbench/assets/analysis_curation_session.js) owns
the source and step draft, debounced single-flight reads, and preview-backed
export admission. A draft edit retires its previous preview immediately; export
rechecks that draft after saving preferences. The view renders snapshots and
submits intentions, without request equality checks or lifecycle timers.
Preview admission concerns the frontend draft, not scientific writer authority;
curation calculations and publication remain with their Python owners.

## Evidence And Decision Boundaries

Evidence providers enter through `EvidenceVector`, `PeakHypothesis`, model
selection and `AuditTrail` before any Matrix change; activation rules are in
[`product-validation-contract.md`](agent/product-validation-contract.md).

- `peak_detection.evidence_semantics` is role-neutral and shared by candidate
  selection, product projection and Alignment, so changes there reach both
  paths. Targeted role policy is in `extraction.targeted_product_projection`;
  writers consume `output.targeted_product_model`. Resolution order is in
  [`decision-policy.md`](product/decision-policy.md).
- `peak_detection/peak_integration.py` searches and scores resolver candidates;
  `peak_detection/peak_selection.py` scores a supplied candidate set. Formal
  Targeted passes its physical candidates straight to selection; Discovery,
  capture, QC, Backfill, CID-NL acquisition and ISTD reference use integration.
  `xic_extractor.signal_processing.find_peak_and_area` remains the public
  integration entry.
- Targeted selection consumes the selected physical answer from
  `physical_peak_candidate_adapter.answer_selected_physical_candidate`, which owns
  candidate-to-segment binding and reuse of prepared evaluations.
  `candidate_scoring` owns evidence scoring and score summaries.

## Writer Transaction Authority

Only the decision owner selects a cell's value and state. A writer renders that
decision; it does not recompute it, and it does not take a value or state from
review rows, manifests, counts or diagnostics.

Output is published only after the write completed; a failed write leaves the
previous output in place. Targeted and Untargeted keep their own writers.

## Runtime Execution

`xic_extractor.execution.partitions` schedules partitioned work and decides the
batch outcome: a batch completes every partition and its global closure, or it
fails or cancels as a whole. Runtime completion grants no scientific, artifact,
writer, or publication authority.

- A real execution failure outranks cancellation observed by sibling work.
- Missing timing observations remain unavailable with a reason. They never
  become inferred zero.
- Timing counts describe cost and locality only; they do not select a cell,
  value, state, or writer decision.

## Module Naming

Name modules and commands for the capability they provide, so a reader needs no
development history: `quant_matrix_export`, not `phase7` or `issue367_bundle`.
Identifiers, schema names and artifact names carry no version suffix: when
content changes keep the name, when meaning changes choose a new name. Issue,
PR and ticket numbers are never identifiers. This does not require unrelated
renames.

## Diagnostics Contract

`tools/diagnostics/` is maintained code. CLIs parse and orchestrate; reusable
loading, models, summaries, plotting and writers live in
`xic_extractor/diagnostics/`. Scientific models do not import diagnostic readers
or writers. Diagnostic writers render; they never recompute evidence or rescan
RAW. Gate diagnostics emit a machine-readable status and reason. Optional
diagnostics use sidecars instead of changing established schemas.
Adding, removing or renaming an entry point updates
[`tools/diagnostics/INDEX.md`](../tools/diagnostics/INDEX.md) in the same change.

## Refactor And Verification

- Move behavior before changing it; do not mix structural moves with scoring,
  selection, matching or integration changes. Characterize uncovered behavior
  first.
- Keep Windows process payloads pickleable; no nested closures or
  non-pickleable factories across process boundaries.
- Split a module for coherent complexity or a distinct reason to change, not
  line count.
- Shared models or protocols are justified when they prevent an import cycle or
  schema drift.
- [`testing.md`](agent/testing.md) owns test selection and RAW/workbook validation.

## Public Contracts

A command is public when declared in `[project.scripts]` or named by an explicit
product/downstream contract. Being under `scripts/`, runnable, imported, tested,
or packaged is not sufficient.

Treat these as public unless an approved change says otherwise:

- declared console scripts and contract-bearing commands
- `xic_extractor.extractor.run`
- `xic_extractor.signal_processing.find_peak_and_area`
- `scripts.csv_to_excel.run`
- config keys and example/default settings
- TSV/CSV schemas
- workbook sheets, order, hidden states, and columns
- HTML report path naming and run metadata keys
- persisted identifiers and matrix row/cell identity
- domain-selected peaks, areas, values, and counted detections
- cross-repository handoff schemas and contracts
