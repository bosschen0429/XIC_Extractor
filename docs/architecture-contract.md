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
[人讀版核心導覽](user/core-modules-explainer.html)開始。

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
adapter；通過 [`.importlinter`](../.importlinter) 不代表所有 I/O 已隔離。

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
- Reuse facts the owner already computed for unchanged inputs. Validate
  persisted or external input at its real entry boundary, not again downstream.
- Computing a domain answer is separate from hashing, serializing or minting
  receipts; those belong to boundary adapters, never inside algorithm loops.
- Reuse established primitives for the same rule; similar assertion syntax alone
  does not justify a shared validator.
- Preserve public contracts unless an approved change names the consumer,
  observable delta and rollback. When changing a public entry point, show its
  inputs, outputs and key failures with existing tests or a minimal example.

## UI Presentation

Workbench and Inspector choose style tokens by semantic role, not by current
color: warnings, selection, disabled controls and actions keep their own
meanings. If a role is missing, extend the owning style definitions and their
fallbacks instead of borrowing an unrelated token. Verify the component in the
themes and fallbacks the UI actually supports.

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

For ownership, serialization, derived-fact or internal-defense changes:

1. Identify the production entry, the producer that establishes the needed fact,
   and its current consumers.
2. A fallback, recomputation or deep revalidation needs a reachable path and an
   observable loss. Construction, dataclass replacement and internal module
   handoffs are not trust boundaries. Repair an internal bypass at its owner or
   caller; uncertainty alone does not justify another defense.
3. Identity and ordering:
   - Allocate a public or persisted identifier only when a reference must cross
     a file, table or human-facing boundary and no natural key suffices. Within
     a process, reuse objects and domain keys; worker handoffs carry data, not
     self-verifying identities.
   - Order and break ties with raw measured facts, not aggregate centers,
     scores, hashes or execution order. If competing facts share the full key,
     report the ambiguity instead of inventing an identity.
   - Check file fingerprints at file handoffs. An existing identifier, or a test
     asserting it exists, is not a reason to keep it.
4. Update consumers and retire superseded surfaces in the same change.
5. Consumers that still rebuild owner-derived facts or parse another owner's
   internal schema are unfinished decoupling.

This is a check of the affected flow, recorded in the PR, not a repository audit.

### Internal Defense Growth Gate

`scripts/check_architecture_census.py --internal-defense-base REF` blocks
growth in mint-token uses in changed `xic_extractor` source (pre-commit against
HEAD, CI against the base commit). It is a syntactic trigger: validators and
hashing are not gated, and renames or indirect calls limit coverage. There is no
suppression flag. A blocked change removes the unnecessary machinery or gets an
explicit maintainer decision; the agent does not grant its own exception.

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
strings. CID-NL admission binds `CellMethodEvidence`, which shared closure
consumes. These are in-memory seams, not a registry: another method needs its
own evidence adapter. A revisit does not reopen grouping, and a scheduling
partition is never a feature boundary.

## Dependency Direction

Startup-sensitive entry points and lightweight readers keep heavy,
branch-specific dependencies inside the path that needs them. Importing a shared
model or reading completed evidence must not load scientific computation or
RAW/DLL code through an unused capability. Delaying an import does not permit a
forbidden dependency.

- Domain algorithms depend only on arrays, typed configuration/context and small
  domain models; never on UI, workbook builders, CLI scripts, process backends,
  report renderers, or RAW/CSV adapters. Adapters, I/O, rendering and entry
  points depend inward.
- `extraction/` and `untargeted_execution/` do not depend on each other; shared
  scientific settings live in `configuration/scientific_settings.py`.
- Main chain: applications → untargeted_execution／extraction → alignment →
  discovery → peak_detection (`.importlinter` `main-chain-layers`).
  `acquisition/` stays inner (`acquisition-stays-inner`).
- `applications/` is the outermost composition layer; nothing inner imports it.
  It owns the run from configuration to publication. `extractor.run` is the
  public root entry into it. Entry points pass typed intent, not flattened
  execution arguments.
- `execution/` and `untargeted_execution/` own execution, spooling and cleanup.
  Scientific completion is pure over owner-bound inputs; runtime wraps it
  without changing the result, and Matrix projection receives only the
  finalized result.
- `diagnostics/` is an outer layer; inner packages never import it.
- `alignment_explanation/` owns the shared SQLite format, writer and
  acquisition-free readers. Readers do not import publication or execution.
  Matrix curation and duplicate screening share the published-cell reader.
- `xic_inspector` reads completed evidence only. Source adapters interpret
  method-specific records; API, selection and renderer stay method-neutral.
  Scientific packages never import Inspector. The workbench hosts Inspector
  views and owns sources, jobs and cancellation; [workbench and
  review](product/workbench.md) defines what review may mean.
- Delta-mass method modules work on in-memory features and traces; their
  pipeline owns RAW acquisition and diagnostic CSV/JSON, which no product path
  consumes (`tests/test_delta_mass_boundaries.py`).
- Keep a compatibility facade only for a real external consumer. When replacing
  an internal surface, remove the old implementation, alias, tests and shim.

[`architecture-public-contracts.md`](agent/architecture-public-contracts.md)
owns public, persisted, writer and runtime authority rules.

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
| `sample_metadata.py` | canonical sample identity and immutable metadata snapshots |
| `matrix_curation/` | post-Alignment curation over a published Matrix and its export; never Alignment decisions or normalization |
| `evidence_artifacts.py` | completed-evidence locator: report and Matrix paths, Matrix SHA-256, attempt UUID, schema version |
| `processing_identity.py` | request, attempt, source and retry-predecessor identity |
| `execution/attempt.py` | sole attempt construction, execution, settlement and finalization authority |
| `acquisition_contract.py` | capability-bound acquisition batches and call/point facts |

Model fields, exports and construction sites are in source; query CBM before
moving an owner rather than expanding this table.

## Evidence And Decision Boundaries

XIC Extractor is an LC-MS evidence and model-selection system, not an
8RAW/85RAW- or CID-NL-specific tool; fixtures are validation oracles. Evidence
providers enter through `EvidenceVector`, `PeakHypothesis`, model selection and
`AuditTrail` before any Matrix change. Direct Matrix writes need an activation
contract, expected diff and output oracle.

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
  `candidate_scoring` owns CWT support and score summaries.

## Module Naming

Name modules and commands for the capability they provide, so a reader needs no
development history: `quant_matrix_export`, not `phase7` or `issue367_bundle`.
Identifiers, schema names and artifact names carry no version suffix: when
content changes keep the name, when meaning changes choose a new name. Issue,
PR and ticket numbers are never identifiers. This does not require unrelated
renames.

CID-NL is the supported Untargeted method. Its input interpretation lives in
Discovery; method subjects and capture authorization live in
`peak_detection/cid_nl_method_subject.py` and `peak_detection/search_scope.py`.
Another method needs its own subjects, authorization and evidence adapter, not a
second scheduling, settlement or output lifecycle.

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
