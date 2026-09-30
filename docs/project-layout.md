# Project Layout

本檔是 XIC Extractor 的**目錄收納規則單一可信來源**。Repo 只追蹤長期維護的
產品、操作、架構、治理與機器契約；施工計畫與交付歷史由 GitHub Issues/PRs
保存，不以 dated Markdown 留在 repo。
本節也定義 documentation source-of-truth 與歷程邊界。

## Repository Tree

這是閱讀用的主要結構，省略個別實作檔案。先分清「程式放哪裡」；
再用[架構導覽](architecture-contract.md#reading-the-code)找執行入口與責任。

```text
XIC_Extractor/
├── gui/                         桌面互動；views/、sections/ 組畫面，workers/ 啟動工作
├── scripts/                     已宣告 CLI、操作與驗證入口
├── xic_extractor/               分析產品的實作
│   ├── configuration/          載入設定與 target；驗證設定值
│   ├── presets/                方法配方與綁定
│   ├── applications/           產品流程組合、政策與其結果模型
│   │   ├── targeted/          正式 Targeted application、計數與 review 原因
│   │   └── untargeted/        Untargeted 執行、結果發布與暫存生命週期
│   ├── acquisition/           有界量測資料擷取 adapter
│   ├── evidence/              已擷取資料的純科學證據解讀
│   ├── discovery/              從量測資料找候選及其證據
│   ├── extraction/             Targeted 擷取／workflow adapter 與保留的 Untargeted 執行 adapter
│   ├── peak_detection/         單次觀察、峰假設、選擇與積分
│   ├── alignment/              跨樣本組合、Backfill、格值決策及其交付 adapter
│   │   ├── cross_sample/       跨樣本關聯
│   │   ├── backfill/           有限度的 observation revisit
│   │   ├── cell_decision/      每個樣本格的科學決策
│   │   ├── projection/         Targeted／Untargeted 的前後投影
│   │   └── result/             科學結果與 application 結果
│   ├── execution_engine/      工作分區、worker pool、結果暫存
│   ├── output/                CSV／workbook／HTML 等輸出程式
│   ├── instrument_qc/         儀器 QC、校正與其報告流程
│   ├── diagnostics/           可重用的診斷與說明工具
│   └── *.py                   RAW adapter、共享模型／規則、runtime owner 與公開入口
├── xic_inspector/              完成證據的只讀檢視
│   ├── sources/               解讀來源證據
│   ├── selection/             選擇要呈現的記錄
│   └── rendering/             離線 HTML
├── tools/                     維護工具；diagnostics/ 為診斷，testing/ 管測試分片
├── tests/                     行為測試與小型 fixtures
├── docs/                      正式文件；各子目錄用途見下表
├── config/                    公開設定與範例
├── assets/                    圖示與截圖
├── .github/                   CI 與 GitHub 設定
├── .codex/                    Repo 的 agent 執行規則
├── pyproject.toml             套件、依賴、已安裝 CLI 與測試設定
├── .importlinter              可執行的靜態依賴限制
├── xic_extractor.spec          Windows 打包輸入
└── output/                    本機執行產物，不追蹤
```

`xic_extractor/output/` 是受版本控制的**輸出程式**；根目錄 `output/` 是不追蹤的
**執行產物**。`alignment/` 現在同時容納演算法、流程、I/O 與交付 adapter，
不能把整個目錄當成純計算層。`xic_extractor/` 根層也不全是 facade；
應依[責任表](architecture-contract.md#ownership-map)辨認正式 owner。

## § 1 主要規則文件的職責分工

| 文件 | 職責 |
| --- | --- |
| [`README.md`](../README.md) | 使用者入口與文件索引 |
| [`AGENTS.md`](../AGENTS.md) | 高頻 agent guardrails 與 owner routing |
| [`docs/user/`](user/) | GUI/CLI 操作手冊 |
| [`docs/product/`](product/) | 維護中的產品語義與 topic authority |
| [`docs/agent/`](agent/) | 執行、驗證、文件、架構與 PR 契約 |
| [`docs/architecture-contract.md`](architecture-contract.md) | dependency direction 與 ownership map |
| 本檔 | 檔案 placement、追蹤/忽略邊界與路徑約束 |

### Permanent root document contract

只有下列四個路徑是永久 root authority。Audit 確認路徑存在、文件有可見內容，
並檢查下列 route、target 與 referrer；標題與說明文字可隨內容調整。路徑本身
提供 placement、purpose 與 self-ownership，因此不需要 generic lifecycle metadata：

| Path | Purpose / required route |
| --- | --- |
| `AGENTS.md` | Bounded repo agent routing；必須連到 parameter、product、evidence、architecture、execution、diagnostics、project layout owners |
| `README.md` | 公開入口；必須連到 user、product、architecture、project layout 與 LC-MS/MS evidence |
| `docs/architecture-contract.md` | Architecture/dependency owner；必須由 root docs 可達 |
| `docs/project-layout.md` | File placement and documentation lifecycle owner；必須連到 architecture、product 與 [documentation boundary contract](agent/obsidian-handoff-contract.md) |

不得新增第五個 root authority exemption。

## § 2 追蹤目錄

| 目錄 | 長期用途 |
| --- | --- |
| `xic_extractor/` | Domain 與 pipeline package |
| `xic_inspector/` | Installed read-only evidence review package；source adapters、共同 selection/API 與 self-contained offline renderer |
| `gui/` | PyQt6 GUI adapters |
| `scripts/` | Public CLI、product adapters、validation scripts |
| `tools/diagnostics/` | Maintained product-adjacent diagnostics 與 docs governance |
| `tests/` | pytest tests 與小型 fixtures |
| `docs/user/` | 使用者文件 |
| `docs/product/` | 產品 topic owners |
| `docs/agent/` | Agent/validation/execution contracts |
| `docs/superpowers/schemas/` | Machine-readable schema contracts |
| `docs/superpowers/validation/` | Checker-consumed validation contracts 與最小 artifacts |
| `docs/superpowers/fixtures/` | Focused tracked evidence fixtures |
| `docs/superpowers/productization/` | Current productization compatibility/evidence surfaces |
| `assets/` | Icons and screenshots |
| `config/` | Runtime public config/examples；private values 不追蹤 |
| `.github/` | GitHub workflows |
| `.codex/` | Repo-local skills, hooks, rules, and profiles |

`docs/` 根目錄只允許 `project-layout.md`、`architecture-contract.md`、
`lc-msms-evidence-rules.md`、`diagnostic-ledger.md`。其他文件必須有明確 owner。

以下 tracked lanes 已退休：

- `docs/superpowers/plans/`
- `docs/superpowers/specs/`
- `docs/superpowers/closeouts/`
- `docs/superpowers/file-management/docs-cleanup/`
- generic `deepresearch/`、`goals/`、`notes/`、`reports/`、`pulse-reports/`、`topics/`

施工 PRD、計畫、acceptance、work decomposition 放 GitHub Issue；交付敘事放 PR
body；next 1–3 actions 放 ignored local handoff。不要建立 tracked plan、spec、
closeout、active stub 或永久歷史 stub。

踩坑後若得到可重用規則，直接更新對該決策有權威的 product、agent、validation
或 diagnostic owner；一次性命令、數字與事故敘事留在 PR／Git 歷史。不得另建
`solutions`、`lessons` 或同義的事後知識支線。

唯一的 exact-path machine exception 必須保持原路徑，但不得當作新檔模板：

- `docs/superpowers/plans/2026-06-15-productization-control-plane.md`

文件的 public/private 與維護、移除規則由
[`docs/agent/obsidian-handoff-contract.md`](agent/obsidian-handoff-contract.md)
擁有。刪除前仍須先把 durable claim 吸收到 owner 並清空 exact referrers；一次性
audit/evidence 寫到 ignored `output/`。方法變更直接更新現行文件，Git 保留舊版；
不要求另行歸檔或建立 retirement packet。

### Current authority routes

| Claim | Owner |
| --- | --- |
| Product semantics | `docs/product/<topic>.md` |
| Product maturity and promotion vocabulary | `docs/agent/product-validation-contract.md` |
| Current productization lane state | `docs/superpowers/validation/productization_status_index.tsv` |
| Writer scope and authority | `docs/superpowers/productization/status/productization_authority_manifest.json` |
| Dependent quantitative-effect authority | `docs/superpowers/productization/status/quantitative_projection_authority_manifest.json` |
| LC-MS/MS evidence semantics | `docs/lc-msms-evidence-rules.md` |
| Validation verdict and rerun policy | `docs/diagnostic-ledger.md` or current checker-consumed artifact |
| Architecture/dependency ownership | `docs/architecture-contract.md` |
| Active construction decision | GitHub Issue |
| Delivered branch evidence | GitHub PR body |

## § 3 忽略目錄

| 目錄 | 用途 / retention |
| --- | --- |
| `.venv/`, `.uv-cache/`, `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`, `.tmp/` | 可重建的 tool/runtime cache |
| `output/` | Pipeline output、generated audits、routing tables、local evidence packets |
| `tmp_runtime/` | 可重算的 pipeline/validation pickle cache |
| `.worktrees/` | Local Git worktrees；移除前逐一確認 branch 與 dirty state |
| `local_validation_artifacts/` | 可重用但不進 git 的 accepted validation inputs |
| `local_raw_samples/`, `local_validation_raw/` | 私人 RAW samples |
| `.superpowers/`, `.remember/` | Agent tool scratch/state |
| `docs/superpowers/handoffs/` | Ignored local handoff workspace only |

Validation output 預設留在 ignored storage。Repo 只保留 clean checkout 能審查
claim 的最小 contract seed、hash/row count、regeneration metadata 或 focused
fixture。完整 workbook、full matrix、RAW-derived dump 與 exploratory report 不得
因為「曾被人工看過」就升格為 tracked authority。

## § 4 不可分離的路徑約束

### Package discovery

`pyproject.toml` 的 `packages.find` 包含 `gui*`、`scripts*`、`tools*`、
`xic_extractor*`、`xic_inspector*`，並排除 `tests*`。這些 package roots 必須維持在 repo root；搬移
時要同步驗證 installed CLI/package closure。

### Frozen bundle

`xic_extractor.spec` 以相對路徑收集 `assets/`、config examples、`scripts/`、
`gui/main.py`、app icon 與 preset TOML。移動其中任一路徑必須同步更新 spec 與
build workflow，並驗證 dev/frozen 兩種模式。

### Config/output siblings

`xic_extractor/configuration/loader.py` 由 config parent 導出 `output/`。移動
`config/` 或 `output/` 時，必須同步驗證 GUI 與 CLI 的路徑導航。

## § 5 新檔案 routing

1. GUI code → `gui/`。
2. Public CLI/product adapter → `scripts/`，public CLI 同步註冊 entry point。
3. Reusable domain logic → `xic_extractor/<owner>/`；owner 查 architecture contract。
4. Installed evidence review、source adapter、selection 或 renderer → `xic_inspector/`。
5. Maintained diagnostic → `tools/diagnostics/`，並更新 `tools/diagnostics/INDEX.md`。
6. Tests → `tests/test_<module>_<behavior>.py`；shared fixture → `tests/fixtures/`。
7. Stable product/user/agent/architecture rule → 對應 canonical owner。
8. JSON schema → `docs/superpowers/schemas/<contract>.vN.json`。
9. Current small validation contract/fixture → `docs/superpowers/validation/` 或
   `docs/superpowers/fixtures/`，必須有現存 checker/test consumer。
10. Construction spec/plan → GitHub Issue；branch closeout → PR body。
11. Generated report/audit/full output → ignored `output/`。
12. Optional private research → Obsidian；throwaway context → ignored scratch。

抽象與設定依 [architecture design principles](architecture-contract.md#design-principles)
判斷目前收益與維護成本。新 artifact lane 需要長期 owner、consumer 與 retirement
rule，不能只因單次施工方便而建立。

## § 6 暫存資料安全

- Cache 可在沒有 active process 使用時清除；刪除後應可由 documented command 重建。
- `tmp_runtime/` 清除會增加重算成本，先確認需要的結果已寫入別處。
- `.worktrees/` 不是 cache；逐一確認 branch、dirty diff 與 merge state 後才移除。
- `output/` 是 evidence reference，不是 durable input store。跨 worktree accepted
  input 放 `local_validation_artifacts/`，並使用相對 locator。
- RAW 與 private validation data 永不追蹤。
- 不在本文件提供一鍵遞迴刪除命令；實際清理必須先解析 exact target。

## § 7 命名與維護

| 類別 | 慣例 |
| --- | --- |
| Schema | `docs/superpowers/schemas/<contract>.vN.json` |
| Test | `tests/test_<module>_<behavior>.py` |
| Public CLI | `scripts/run_<purpose>.py` |
| Diagnostic | `tools/diagnostics/<purpose>.py` |
| Product owner | `docs/product/<topic>.md` |

`<purpose>` 與 `<contract>` 必須描述長期 owner、行為或 artifact role；Issue／PR
編號、施工 package／phase、`P7`／`M7`、Gate B/C、日期與第一批驗證資料量
不得充當 current path 或 schema identity。凍結 provenance path、真正 schema
version、科學名稱與輸入 cardinality 不受此限制。

只有在第一層目錄、placement policy、package/bundle path constraint、ignored
storage semantics 或命名規則改變時更新本檔。內部 module 拆分與待辦不寫在這裡；
由 architecture owner 與 GitHub Issues 管理。
