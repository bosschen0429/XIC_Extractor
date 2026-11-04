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
[`pyproject.toml`](../pyproject.toml) 的 `project.scripts` 宣告。從入口往下的
交接由入口檔與它呼叫的 application owner 說明，這裡不重述。

| 要追的行為 | 從哪裡讀 |
| --- | --- |
| HTML 工作區啟動、工作與檢閱 | [`xic_workbench/cli.py`](../xic_workbench/cli.py)、[`xic_workbench/server.py`](../xic_workbench/server.py) |
| Targeted 分析與交付 | [`xic_workbench/targeted.py`](../xic_workbench/targeted.py)、[`scripts/run_extraction.py`](../scripts/run_extraction.py) → [`applications/targeted/application.py`](../xic_extractor/applications/targeted/application.py) |
| 既有 Python Targeted 入口 | [`extractor.run`](../xic_extractor/extractor.py)，進入同一 Targeted application |
| Discovery 候選產生 | [`scripts/run_discovery.py`](../scripts/run_discovery.py) → [`discovery/pipeline.py`](../xic_extractor/discovery/pipeline.py) |
| Untargeted 全流程、Alignment 與 Matrix | [`xic_workbench/untargeted.py`](../xic_workbench/untargeted.py)、[`scripts/run_alignment.py`](../scripts/run_alignment.py) → [`applications/untargeted/application.py`](../xic_extractor/applications/untargeted/application.py) |
| 完成 Matrix 後的矩陣整理 | [`xic_workbench/matrix_curation.py`](../xic_workbench/matrix_curation.py)、[`scripts/run_matrix_curation.py`](../scripts/run_matrix_curation.py) → [`matrix_curation/export.py`](../xic_extractor/matrix_curation/export.py) |
| 完成證據的只讀報告 | [`xic_inspector/cli.py`](../xic_inspector/cli.py) → [`xic_inspector/api.py`](../xic_inspector/api.py) |

### Finding The Right Owner

先用[模組責任表](#ownership-map)找 package 層級的 owner。檔案層級的 owner、
函式與交接以現行 source 為準：用 CBM 查 callers 與 consumers，再讀該 module
的 docstring。這裡不維護檔案清單，以免它隨每次重構失效。

Package 名稱不是可強制的分層證明：`alignment/` 包含科學與外層 adapter，
`extraction/` 只負責 Targeted 抽取；`untargeted_execution/` 擁有保留的 Untargeted
執行。純計算 module 的約束見下文；
實際 import 例外由 [`.importlinter`](../.importlinter) 明列，不能把通過該檢查
解讀成所有 I/O 已經隔離。

## Design Principles

- Make data flow and ownership obvious.
- Keep domain behavior independent from IO, UI, workbook rendering, process
  backends, and CLI wrappers.
- Let entry points orchestrate focused owners; do not turn them into permanent
  implementation homes.
- Add abstraction only when it removes demonstrated coupling, duplication, or
  cognitive load for a current consumer. A private decomposition or concrete
  dependency adapter can be useful with one caller; shared behavior requires
  compatible current consumers. Name the present benefit and added maintenance
  cost, without building layers for unnamed future consumers.
- Add configuration for a present actor's meaningful value choice and observable
  effect. Account for validation, replay, HTML/CLI, and documentation costs;
  a hypothetical future value does not justify a new setting.
- Preserve public contracts unless an approved change names the consumer,
  observable delta, and rollback.
- When changing a public entry point, use existing tests or a minimal example
  to show required inputs, expected outputs, and the meaning of key failures,
  so consumers can use it correctly without tracing its implementation. Reuse
  the current contract's tests or documentation; the
  [testing contract](agent/testing.md) governs new regression coverage.

## UI Presentation

Workbench and Inspector choose style tokens by semantic role, not by current
color: quality warnings, selection, disabled controls, and actions retain their
own meanings. If a required role is absent, extend the owning style definitions
and their defaults/fallbacks instead of borrowing an unrelated token. Verify the
affected component in the themes and fallback paths that the UI actually supports;
this does not require introducing a new theme system.

## Configuration Documents

`configuration.targeted_method_files` owns strict Targeted XLSX exchange; the
existing settings and target owners admit strategy and target intent.
`configuration.workspace_state` owns local JSON application/batch/target persistence.
`configuration.delta_mass` owns shared pure pattern settings.
`configuration.targeted_identification` owns named flat all/any condition intake
and quantitation-relative Delta Mass conversion. Targeted acquisition reuses
Discovery's pure matcher and existing CID-NL evaluation on the same physical
candidate universe. Only method-admitted candidates enter shared completion;
`evidence.targeted_identification` retains clause answers for output and Inspector.
Workbench and CLI consume that state; they do not discover legacy CSV workspaces.
`prepare_targeted_config` freezes admitted inputs into private CSV run receipts;
`load_config_snapshot` is an explicit diagnostic receipt reader, not a user-input
fallback. Public examples are read-only resources in `configuration/data/`.

## Change Locality

XIC technical evidence for ownership, serialization, derived-fact, or
internal-defense changes:

1. Identify the affected production entry, authoritative producer, validation,
   and current consumer, including where the needed invariant is established.
   Reuse current owner evidence; CBM owns structural query mechanics.
2. A fallback, recomputation, or deep revalidation needs a reachable input/path
   and observable loss. Justify the check by changed inputs or an
   actual trust boundary. Construction, dataclass replacement, and internal
   module handoff are not themselves new trust boundaries. Keep normalization
   and mathematical/domain constraints at the producer. Repair a fixed internal
   bypass at its owner or caller; uncertainty alone does not justify another
   defense.
3. Identity and ordering:
   - Allocate a public or persisted identifier only when a reference must cross
     a boundary that the object itself cannot cross (a file, table, or
     human-facing output), and no existing natural key is sufficient. Name the
     consumer, boundary, missing natural key, and scope. Within a process, reuse
     objects or domain keys that serve actual lookup and association; do not
     build a second identity or proof graph around them. Worker handoffs carry
     the necessary data and association facts, not self-verifying identities.
   - Use raw measured domain facts for deterministic ordering and tie-breaking,
     not aggregate centers, scores, hashes, or execution order. If distinct
     competing facts still share the complete key, report the ambiguity
     instead of inventing an identity.
   - Check file fingerprints at file handoff boundaries; keep an explicitly
     required row-binding checksum at its named writer/reader boundary.
     An existing identifier or a test asserting its presence is not, by itself,
     a reason to retain it.
4. Update consumers and retire superseded surfaces in the same responsibility
   change. The [testing contract](agent/testing.md) owns which oracles survive.
5. For one rule or representation changed in this diff, identify the affected
   modules that must know it and justify each remaining dependency. For in-scope
   decoupling, ordinary consumers still reconstructing owner-derived facts or
   parsing other owners' internal ancestor schemas are unmet technical acceptance.
   A separately required audit keeps its own oracle and does not replace the
   production fact.

Use source and existing observable checks for this acceptance. Record the result
in the current task/PR, not a permanent inventory.
Established-owner local edits retain their compact workflow; this is a check of
the affected flow, not a whole-repository audit.

### Internal Defense Growth Gate

`scripts/check_architecture_census.py --internal-defense-base REF` blocks
syntactic growth in mint-token uses in changed `xic_extractor` source. The
pre-commit hook compares HEAD to staged source; CI compares against the event's
base commit. A missing baseline or unreadable source fails closed. Existing
unchanged scopes are not re-certified, and no generated baseline registry is
maintained.

Validators and hashing are not gated: syntax cannot separate a producer domain
constraint, external admission check, or boundary-adapter digest from replayed
internal validation or identity minting. Change Locality and review own that
decision. Duplicate scope definitions are compared by occurrence rather than
silently overwriting one another.

Git-detected file renames use the original blob as baseline. An unambiguous,
exact-body scope rename can retain a removed scope's baseline; a copied scope
cannot reuse a still-present scope's allowance. Rename plus substantive edits,
ambiguous moves, or moves Git does not recognize can still require a maintainer
decision. This is a syntactic review trigger, not semantic necessity analysis:
token naming, indirect calls, alias rebinding, and same-count replacements
limit coverage. There is no inline suppression or
pass-anyway flag. A blocked change needs removal of unnecessary machinery or an
explicit maintainer decision to adjust the gate's scope; the agent does not
authorize its own exception. Preserve the existing product boundary while
resolving a block.

Local hooks can be bypassed, and repository code cannot prevent an actor with
permission from editing the checker or workflow. Required remote checks and
review protection are separate repository settings, not a guarantee made by
this source-level gate.

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

4. **Semantic Primitive Reuse**:
   - Reuse established math and validation primitives for the same rule and
     compatible semantics. Domain-specific constraints stay with their owner;
     similar assertion syntax alone does not justify a shared validator.
     [Design Principles](#design-principles) governs new abstractions.

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

Targeted and Untargeted are applications of the same evidence lifecycle.
Targeted adds target-list/search policy and fixed-row delivery; Untargeted adds
general discovery, grouping, and matrix delivery. Separate entry points and
writers do not justify duplicate capture, peak identity, CrossSample,
ObservationRevisit, or cell-decision owners.

The scientific lifecycle is described in the
[Domain Glossary](product/domain-glossary.md#product-shape),
[`alignment.md`](product/alignment.md), [`backfill.md`](product/backfill.md), and
[`decision-policy.md`](product/decision-policy.md). Processing identity lives in
[`processing_identity.py`](../xic_extractor/processing_identity.py); scheduling,
cancellation, cleanup, and timing live in
[`xic_extractor/execution/`](../xic_extractor/execution/).

Provider-specific evidence handling must not create a parallel downstream
decision core.

CID-NL interpretation belongs to explicit method modules: tag/composition and
method mass envelopes for grouping, revisit eligibility, and assessment-to-cell
evidence. Shared grouping keeps physical mass/RT/shape and sample uniqueness;
shared cell composition keeps exact sample/group lineage. Final numeric and
writer authority remain with the existing decision and projection owners.

Workflow composition explicitly supplies `GroupingRules`; shared inference and
edge scoring do not select a method or interpret method reason strings. CID-NL
record admission binds `CellMethodEvidence` for local/recovered state, exact
support and recovery eligibility; shared closure and conservative mapping consume
those answers. These are in-memory seams for the current algorithms, not a
registry or a new universal assessment schema. Existing CID-NL subjects and
persisted records retain their owners and encodings. Another method still needs
its own evidence adapter; no delta-mass execution is implied by these seams.

Grouping and RT bootstrap are described in [Alignment](product/alignment.md#current-implementation).
A revisit does not reopen grouping, replace the partition, or mint a second
grouping owner.

CID-NL provenance, workset compatibility, and bounded recovery subjects are
owned by the [Alignment comparison scope](product/alignment.md#inputs-and-comparison-scope) and
[Backfill](product/backfill.md#untargeted-route).
A scheduling partition or subject center cannot become a feature boundary;
result writers consume the completed groups and cells.

## Dependency Direction

Startup-sensitive entry points and lightweight report readers keep heavy,
branch-specific dependencies inside the path that needs them. Importing a shared
model or reading completed evidence must not load scientific computation or
RAW/DLL dependencies solely through an unused capability. Keep ordinary imports
unchanged where no such loading boundary exists; delaying an import does not
permit a forbidden dependency.

`extraction/` 與 `untargeted_execution/` 互不依賴；兩者共用的科學設定欄位由
`configuration/scientific_settings.py` 擁有。`.importlinter` 的 independence
contract 沒有 ignore，並包含間接依賴。

- Domain algorithms may depend on arrays, typed configuration/context, and
  small domain models.
- Domain algorithms must not import UI packages, workbook builders, CLI scripts,
  process backends, report renderers, or RAW/CSV adapters.
- Adapters, IO, rendering, and entry points depend inward on domain owners.
- `xic_inspector` depends inward on read-only completed-evidence interfaces.
  Source adapters own method-specific record/schema interpretation; its public
  API, selection, and renderer remain method-neutral. Scientific/core packages
  must not import or invoke the Inspector.
- The workbench hosts Inspector views: the renderer owns presentation, and the
  workbench owns sources, jobs and cancellation. The
  [workbench and review](product/workbench.md) page defines what review
  and export may mean.
- Entry points pass typed intent to an application, not flattened execution
  and publication arguments. The application owns the run from configuration to
  publication; Alignment owns neither that lifetime nor a second runtime or
  writer transaction. [`xic_extractor/execution/`](../xic_extractor/execution/)
  and [`untargeted_execution/`](../xic_extractor/untargeted_execution/) own
  execution, spooling and resource cleanup.
- Scientific completion is pure over owner-bound inputs. Runtime and persistence
  wrap it without changing its result, and Matrix projection receives only the
  finalized result.
- `diagnostics/` is an outer layer. Inner packages (`acquisition`, `alignment`,
  `configuration`, `discovery`, `evidence`, `execution`, `extraction`,
  `untargeted_execution`, `output`, `peak_detection`) never import it. Applications
  compose the method-specific explanation projection from completed owner facts.
- `alignment_explanation/` owns the shared SQLite format, atomic writer and
  acquisition-free readers. Inspector asks for indexed pages and scoped facts;
  it owns presentation/sampling policy, not SQL or packed cell encodings. Matrix
  curation and duplicate screening share the published-cell reader rather than
  importing each other's algorithms. Readers do not import publication or
  execution; curve validation is loaded only when curves are requested.
- `applications/` is the outermost composition layer. The inner packages above
  and `diagnostics` never import it; `extractor.run` is the public root entry
  that calls into it.
- 主幹分層：applications → untargeted_execution／extraction → alignment → discovery →
  peak_detection，由 `.importlinter` 的 `main-chain-layers` 強制。
- `acquisition/` 是內層，不 import alignment、applications、discovery、extraction、
  untargeted_execution、output，由 `acquisition-stays-inner` 強制。
- Delta-mass method modules operate only on in-memory features and traces.
  Their pipeline owns RAW acquisition and diagnostic CSV/JSON. Alignment,
  applications, output, and retained Untargeted execution do not consume these
  diagnostics. `tests/test_delta_mass_boundaries.py` enforces inward purity and
  the no-product-publication boundary.
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

Cross-cutting owners remain focused:

| Owner | Responsibility |
| --- | --- |
| `sample_metadata.py` | canonical sample identity and immutable metadata snapshots |
| `matrix_curation/` | post-Alignment curation steps over a published Matrix and their export; never Alignment decisions or normalization |
| `evidence_artifacts.py` | completed-evidence locator: report and Matrix paths, Matrix file SHA-256, attempt UUID, schema version |
| `processing_identity.py` | request, attempt, source, and retry-predecessor identity |
| `execution/attempt.py` | sole attempt construction, execution, settlement and finalization authority |
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

Targeted selection retains target policy and consumes the completed selected
physical answer from `physical_peak_candidate_adapter.answer_selected_physical_candidate`.
The adapter owns candidate-to-segment binding, no-selection questions and reuse
of prepared candidate evaluations; callers do not mint another question for an
answer already computed. `candidate_scoring` owns CWT support and score-summary
projection; selection calls the configured scorer directly and preserves its
override and context RT prior.

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
