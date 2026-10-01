# Architecture Contract

This file owns the current code-organization, dependency, and responsibility
boundaries of XIC Extractor. Detailed product behavior belongs in
[`docs/product/`](product/README.md); exact exports, schemas, and construction
rules belong to source and executable checks. Migration history and
decomposition plans belong in Issues, PRs, and Git.

## Reading The Code

先把 XIC 理解成一套「從量測證據得到樣本格值，再交付結果」的系統。
Targeted 用已知 target 約束搜尋並輸出固定列；Untargeted 找一般候選並輸出
feature-by-sample Matrix。兩者共用科學判斷，但保留不同搜尋政策及 writer。

建議閱讀順序：

1. [Repo 結構樹](project-layout.md#repository-tree)：辨認程式、文件與本機產物。
2. [實際入口與交接](#entry-points-and-handoffs)：從你要改的使用情境進入。
3. [模組責任表](#ownership-map)：找語義的正式 owner，避免在 caller 重算。
4. [依賴方向](#dependency-direction)：確認哪些知識應留在內部。

若只想先理解分析概念，可從[人讀版核心導覽](user/core-modules-explainer.html)
開始；精確函式、型別與可用選項仍以連結的現行 source 為準。

### Entry Points And Handoffs

以下列出閱讀起點，不是完整 export 清單；安裝後的命令由
[`pyproject.toml`](../pyproject.toml) 的 `project.scripts` 宣告。

| 要追的行為 | 從哪裡讀 | 下一個負責的 module／交接 |
| --- | --- | --- |
| GUI 建立視窗與工作 | [`gui/main.py`](../gui/main.py)、[`gui/main_window.py`](../gui/main_window.py) | `views/`、`sections/` 處理互動；`workers/` 將設定轉成工作要求 |
| Targeted 分析與交付 | [`scripts/run_extraction.py`](../scripts/run_extraction.py)、[`gui/workers/pipeline_worker.py`](../gui/workers/pipeline_worker.py) | [`TargetedApplicationService.execute_application`](../xic_extractor/applications/targeted/application.py) 組合 acquisition、科學處理與交付；同一 module 組裝正式 application |
| 既有 Python Targeted 入口 | [`extractor.run`](../xic_extractor/extractor.py) | 進入同一 application；`extractor.acquire` 是它使用的 acquisition 入口；結果模型在 `extraction/result_models.py` |
| Discovery 候選產生 | [`scripts/run_discovery.py`](../scripts/run_discovery.py) | [`discovery/pipeline.py`](../xic_extractor/discovery/pipeline.py) 的 `run_discovery`／`run_discovery_batch`，交出候選檔與 batch index |
| Untargeted GUI 全流程 | [`gui/workers/discovery_worker.py`](../gui/workers/discovery_worker.py) | 呼叫 Discovery，再將 batch index 交給 Alignment |
| Untargeted Alignment 與 Matrix | [`scripts/run_alignment.py`](../scripts/run_alignment.py)、[`gui/workers/discovery_worker.py`](../gui/workers/discovery_worker.py) | [`run_untargeted_application`](../xic_extractor/applications/untargeted/application.py) 準備輸入並呼叫 retained execution；[`publication`](../xic_extractor/applications/untargeted/publication.py) 消費完成結果、發布 [`AlignmentRunOutputs`](../xic_extractor/output/alignment/pipeline_outputs.py) 並關閉暫存 |
| 完成 Matrix 後的 feature 篩選 | [`scripts/run_feature_quality.py`](../scripts/run_feature_quality.py) | [`feature_quality.py`](../xic_extractor/feature_quality.py) 計算 availability；[`feature_quality_io.py`](../xic_extractor/feature_quality_io.py) 讀寫分析集合，保留既有值 |
| 完成證據的只讀報告 | [`xic_inspector/cli.py`](../xic_inspector/cli.py) | [`api.generate_report`](../xic_inspector/api.py) 串起來源解讀、選擇及 HTML rendering |

### Finding The Right Owner

| 想改什麼 | 先找哪裡 | 交接時要分清的責任 |
| --- | --- | --- |
| 峰選擇、邊界、面積 | `peak_detection/`（`signal_processing.py` 只保留公開的 `find_peak_and_area` 入口） | 單峰／單次觀察的答案；跨樣本群組在 Alignment |
| Targeted 正式選峰與 successor approval | `extraction/targeted_peak_selection.py` | 消費已取得的 trace／MS2 evidence，組合 Targeted 選峰政策並完成 physical answer 與選峰事實同步；[Targeted selection](product/targeted-selection.md#physical-evaluation) 定義完成責任，單峰演算法仍在 `peak_detection/` |
| Targeted 輸入轉成共用峰假設 | `extraction/targeted_peak_hypotheses.py` | 選峰、candidate table 與 boundary callers 共用同一 translation；audit callers 不依賴正式選峰政策 |
| Targeted 計數、角色例外與 review 原因 | `extraction/targeted_product_projection.py` | 消費已完成的科學結果，決定產品狀態；純結果模型在 `output/targeted_product_model.py`，writer 只呈現它 |
| Untargeted 保留批次執行 | `untargeted_execution/` | 規劃、partition 與 completion adapter；科學決策仍由 Alignment，發布由 application／output 負責 |
| 跨樣本配對、RT 漂移、群組 | `alignment/grouping_workflow.py`、`alignment/cross_sample/` | 群組決定先完成，Backfill 與格值決策消費同一結果 |
| 從已取得的 evidence 完成科學結果 | `alignment/completion.py` | Targeted／Untargeted 共用 capture、grouping、cell 與 projection；不執行 runtime 或保存暫存 |
| Untargeted CID-NL 的 sample assessment／Backfill inputs | `alignment/cid_nl_inputs.py` | 正式 partition 與 diagnostic projection 共用相同 input types、payload validation 和 method default；不依賴 artifact rendering |
| 版本化 Matrix 的 artifact projection 與 acceptance | `diagnostics/quant_matrix/` | `golden_path.py` 組合 owner facts；`acceptance.py` 驗 manifest snapshot／source hashes 並 mint checked receipt；`version.py` 投影 table rows；不改 default product authority |
| Packaged quantitative-effect promotion authority | `alignment/quantitative_projection_authority.py` | 封住 packaged resource、strict manifest rehydration 與 canonical rendering；是 promotion contract boundary，不是數值算法或 active application guard |
| 缺格補證及格值 | `alignment/backfill/`、`alignment/cell_decision/` | 新觀察、科學決策與檔案 writer 各自有 owner |
| RAW 讀取或 DLL | `raw_reader.py` 及使用它的 acquisition adapter | 讀入量測資料；不因此取得群組、格值或發布決定權 |
| Discovery 已保存輸入 | `discovery/csv_reader.py`、`discovery/input_source.py` | 依 Discovery receipt 讀取 batch/candidate CSV，提供整批或有界 row loading；Alignment 只消費候選 |
| 樣本 RAW 檔案定位 | `acquisition/raw_paths.py` | 依宣告的 sample/raw 路徑與既有 fallback 規則定位檔案；不做科學判定 |
| Targeted／Alignment RT-window MS2 擷取與 NL 解讀 | `acquisition/ms2_windows.py` 擷取單一樣本的有界視窗；`evidence/neutral_loss.py` 解讀已擷取事件 | 前者持有 reader 呼叫，後者只處理 `Ms2Window` 與候選幾何；Discovery 宣告 scan 的 strict receipt 保留原 owner |
| Untargeted ISTD 參考資料取得 | `acquisition/istd_reference.py` | 讀取 workbook 或依既有 target 邊界擷取 RAW trace；兩種來源都交由 Alignment 的 drift evidence owner 建立同一 reference，科學 owner 不開檔 |
| 欄位、workbook 或 Matrix 格式 | `output/`；Alignment Matrix 的 schema、格式及 atomic publishing 在 `output/alignment/` | 呈現 owner 的答案；不要從格式變更反向改變選峰或計數 |

Package 名稱不是可強制的分層證明：`alignment/` 包含科學與外層 adapter，
`extraction/` 只負責 Targeted 抽取；`untargeted_execution/` 擁有保留的 Untargeted
執行。純計算 module 的約束見下文；
實際 import 例外由 [`.importlinter`](../.importlinter) 明列，不能把通過該檢查
解讀成所有 I/O 已經隔離。

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

XIC technical evidence for ownership, serialization, derived-fact, or
internal-defense changes:

1. Identify the affected production entry, authoritative producer, validation,
   and current consumer, including where the needed invariant is established.
   Reuse current owner evidence; CBM owns structural query mechanics.
2. A fallback, recomputation, or deep revalidation needs a reachable input/path
   and observable loss. Justify the check by changed inputs or an
   actual trust boundary. Repair a fixed internal bypass at its owner or caller;
   uncertainty alone does not justify another defense.
3. Update consumers and retire superseded surfaces in the same responsibility
   change. The [testing contract](agent/testing.md) owns which oracles survive.
4. For one rule or representation changed in this diff, identify the affected
   modules that must know it and justify each remaining dependency. For in-scope
   decoupling, ordinary consumers still reconstructing owner-derived facts or
   parsing other owners' internal ancestor schemas are unmet technical acceptance.
   A separately required audit keeps its own oracle and does not replace the
   production fact.

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
ObservationRevisit, or cell-decision owners.

The detailed scientific lifecycle is owned by
[`shared-processing-kernel.md`](product/shared-processing-kernel.md),
[`alignment.md`](product/alignment.md), and
[`decision-policy.md`](product/decision-policy.md). Processing identity and
delivery lineage are owned by
[`processing-lineage.md`](product/processing-lineage.md); scheduling,
cancellation, cleanup, recovery, and observability are owned by
[`execution-runtime.md`](product/execution-runtime.md).

Shared scientific interfaces and application-specific search, projection, and
writer contracts are defined in the [Shared Processing Kernel](product/shared-processing-kernel.md#current-contract).
Provider-specific evidence handling must not create a parallel downstream
decision core.

Grouping, RT bootstrap, and immutable-result handoffs are defined in the
[Alignment grouping contract](product/alignment.md#cross-sample-grouping-and-batch-finalization).
A revisit does not reopen grouping, replace the partition, or mint a second
grouping owner. `CrossSamplePartitionTransition` owns only the mathematical before/after
complete-partition transition; `InitialGroupingScope` is a plain immutable
m/z/RT-bounds-plus-groups handoff with no separate domain identity or persisted
contract.

CID-NL provenance, workset compatibility, and bounded recovery subjects are
owned by the [Alignment method-subject contract](product/alignment.md#method-subjects).
A scheduling partition or subject center cannot become a feature boundary;
result writers consume the completed groups and cells.

## Dependency Direction

`extraction/` 與 `untargeted_execution/` 互不依賴；兩者共用的科學設定欄位由
`configuration/scientific_settings.py` 擁有。`.importlinter` 的 independence
contract 沒有 ignore，並包含間接依賴。

- Domain algorithms may depend on arrays, typed configuration/context, and
  small domain models.
- Domain algorithms must not import GUI, workbook builders, CLI scripts,
  process backends, report renderers, or RAW/CSV adapters.
- Adapters, IO, rendering, and entry points depend inward on domain owners.
- `xic_inspector` depends inward on read-only completed-evidence interfaces.
  Source adapters own method-specific record/schema interpretation; its public
  API, selection, and renderer remain method-neutral. Scientific/core packages
  must not import or invoke the Inspector.
- `applications.untargeted.application.run_untargeted_application` prepares
  inputs, rejects output collisions before acquisition, and invokes retained
  CID-NL execution in `untargeted_execution/`. Its publication boundary consumes the
  completed typed batch, delegates Matrix publishing to `output/alignment/`,
  and closes its private stores on every exit;
  [Alignment outputs](product/alignment.md#surfaces) owns streaming/materialization policy.
  CLI and GUI receive only
  published output paths and durable evidence locators. Alignment owns neither
  this application lifecycle nor a duplicate runtime or writer transaction.
- Retained CID-NL execution partitions complete subjects across the sample cohort
  under one runtime attempt. It adapts method acquisition and composition to the
  generic execution engine in `execution/`. The engine's private result store
  spools completed scientific results for same-version process transport and preserved
  read-only reopen; it grants no scientific,
  activation, or writer authority. Scientific and Untarget projection owners
  restore their own process-local completion seals. Target projection retains its
  validated immutable decisions without graph serialization. The runtime owner
  compacts acquisition facts after scientific results have been stored.
- `alignment.completion` completes pure owner-bound inputs and returns the
  existing finalized application results; the
  [Targeted acquisition handoff](product/execution-runtime.md#current-implementation-gaps) owns pre-acknowledgement population validation.
  Untargeted execution combines the same completion with partition persistence;
  its `untargeted_execution.execution_result` carrier binds runtime success to the
  finalized batch and owns the close action. Application publication consumes
  that carrier, while Matrix projection receives only the pure finalized batch.
- `diagnostics/` is an outer layer. Inner packages (`acquisition`, `alignment`,
  `configuration`, `discovery`, `evidence`, `execution`, `extraction`,
  `untargeted_execution`, `output`, `peak_detection`) never import it; applications may compose its
  explanation persistence. A schema that production reads lives with its
  production reader, and diagnostic producers import it from there.
- `applications/` is the outermost composition layer. The inner packages above
  and `diagnostics` never import it; `extractor.run` is the public root entry
  that calls into it.
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
| `applications/targeted/` | Formal Targeted application composition and request/result interface, its production workflow and output boundary | scientific peak selection, numeric cell decisions, RAW acquisition or rendering |
| `applications/untargeted/` | Untargeted input preparation, Discovery-to-provisional-Matrix composition, execution-to-publication orchestration, cancellation, completed-store and explanation lifetime; delegates Matrix publishing to `output/alignment/` | scientific grouping, cell decisions, a second runtime owner or writer algorithm |
| `discovery/` | general candidate discovery, seed generation, workflow-neutral discovery facts, receipt-bound CSV reading and bounded candidate loading | matrix decisions or target-list policy |
| `execution/` | generic attempt execution/finalization, partition scheduling, worker execution, cancellation, cleanup, reusable worker admission pools, one-shot process jobs, streamed result storage, recovery, and runtime timing/process-memory measurement | method-specific science, subject splitting, or writer authority |
| `extraction/` | Targeted acquisition adapters, result models and product projection (product state, role/applicability policy, counted detection and reason overlays), jobs, windowing, drift, backend coordination, output dispatch, and Targeted input binding | application composition, GUI, or Alignment decisions |
| `untargeted_execution/` | retained Untargeted input planning, acquisition cache, partition execution/composition, completion, result stores and provisional run manifests | Targeted extraction policy, application composition, Matrix writing or a second runtime owner |
| `peak_detection/` | physical observations, hypotheses, resolvers, boundaries, integration, evidence composition | cross-sample grouping, workflow policy, or writers |
| `alignment/` | cross-sample grouping, refinement/repair composition, Alignment adapters and renderers | a second runtime owner, mutable transport authority, or Targeted-only science |
| `output/` | workbook/TSV/HTML schema rendering and the pure Targeted product result model they render; `output/alignment/` owns Matrix artifact selection, streaming/materialization coordination, atomic writes and fingerprint receipts | evidence recomputation, application store lifetime or RAW access |
| `diagnostics/` | reusable diagnostic models, summaries, classification, and alignment explanation persistence (`alignment_explanation`) | CLI parsing, production mutation, or domain recomputation |
| `xic_inspector/` | installed read-only review API/CLI, source adapters, canonical selection, and self-contained offline HTML rendering | RAW acquisition, scientific recomputation, identity/Matrix decisions, or write authority |
| `gui/` and `scripts/` | interaction and thin orchestration | reusable domain behavior |
| root facades | current public compatibility and thin orchestration | new implementation detail |

Cross-cutting owners remain focused:

| Owner | Responsibility |
| --- | --- |
| `sample_metadata.py` | canonical sample identity and immutable metadata snapshots |
| `feature_quality.py` / `feature_quality_io.py` | post-finalization feature availability/selection and its bound analysis-set export; never Alignment decisions or normalization |
| `review_records.py` | review subject/action identity and supersession |
| `evidence_artifacts.py` / `artifact_lineage.py` | exact-byte artifact identity, completed-evidence locators, and supplied-lineage validation |
| `processing_identity.py` | request, attempt, source, and retry-predecessor identity |
| `execution/attempt.py` | sole attempt construction, execution, settlement and finalization authority; private lifecycle/receipt/observability companions support it |
| `acquisition_contract.py` | capability-bound acquisition batches and mechanical call/point facts |

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

`xic_extractor.peak_detection.evidence_semantics` is role-neutral and serves both candidate
selection and product projection. Targeted role policy belongs in
`extraction.targeted_product_projection`. `extraction.result_assembly` creates
the extraction result and delegates to `project_targeted_result`; writers consume
the pure `output.targeted_product_model` without importing orchestration. Because Alignment also consumes
the shared layer, changes there have cross-path blast radius. The complete
resolution order is in [`decision-policy.md`](product/decision-policy.md).

Peak detection has two explicit entries: `peak_detection/peak_integration.py`
searches resolver candidates and selects/scores them; `peak_detection/peak_selection.py`
selects/scores a supplied candidate result. Formal Targeted passes its physical
candidates directly to selection, with preferred-RT recovery restricted to that
set. Discovery, capture, QC, Alignment Backfill, CID-NL acquisition and ISTD
reference use the full integration entry. The public
`xic_extractor.signal_processing.find_peak_and_area` remains the integration entry.

## Module Naming

Name new or renamed modules and commands for the capability they provide and
the responsibility they own. A reader should understand their purpose without
knowing the development history: for example, use `quant_matrix_export` for
matrix export rather than `phase7` or `issue367_bundle`.

Identifiers, schema names, algorithm labels, and artifact names carry no version
suffix. When a contract's content changes, update the content and keep its name;
when its meaning changes, give it a new semantic name. Issue, PR, and ticket
numbers are never identifier values. A real runtime phase or scientific name
remains appropriate when it describes current behavior. Preserve compatibility
when considering existing names; this guidance does not require unrelated renames.

CID-NL is the currently supported Untargeted method. Its input interpretation
lives in Discovery; typed method subjects and capture authorization live in
`peak_detection/cid_nl_method_subject.py` and `peak_detection/search_scope.py`.
Application composition selects these capabilities. Adding another scientific
method requires its own validated subjects, authorization and evidence adapter;
it does not require a second scheduling, settlement or output lifecycle. This
is an explicit extension boundary, not an implemented multi-method registry.
Shared execution and delivery are named for their capabilities and consume the
completed domain results rather than selecting CID-NL scientific rules.

## Diagnostics Contract

`tools/diagnostics/` is maintained product-adjacent code.

- CLIs parse, validate, and orchestrate only.
- Reusable loading, classification, models, summaries, plotting, and writers
  belong in focused `xic_extractor/diagnostics/` packages. Backfill evidence
  file binding/serialization and quantitative report/promotion artifacts each
  keep their related implementation together.
- Pure Alignment record, claim and quantitative authority models remain with
  their scientific owners; they do not import diagnostic file readers or writers.
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
