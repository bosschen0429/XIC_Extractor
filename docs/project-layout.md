# Project Layout

哪些檔案放哪裡、哪些要追蹤。Repo 只追蹤長期維護的程式、文件與它們真正讀取的資料；
施工計畫與交付歷史在 GitHub Issues／PRs。

## Repository Tree

省略個別實作檔案。執行入口與責任見[架構導覽](architecture-contract.md#reading-the-code)。

```text
XIC_Extractor/
├── scripts/                     已宣告 CLI、操作與驗證入口
├── xic_extractor/               分析產品的實作
│   ├── configuration/          載入設定與 target；驗證設定值
│   ├── presets/                方法配方與綁定
│   ├── applications/           產品流程組合、政策與其結果模型
│   │   ├── targeted/          正式 Targeted application、計數與 review 原因
│   │   └── untargeted/        Untargeted 執行、結果發布與暫存生命週期
│   ├── acquisition/           量測來源定位與有界資料擷取 adapter
│   ├── evidence/              已擷取資料的純科學證據解讀
│   ├── discovery/              候選探索、證據及 receipt-bound CSV 載入
│   ├── extraction/             Targeted 擷取／workflow adapter
│   ├── untargeted_execution/    保留的 Untargeted 規劃、partition 執行與完成 adapter
│   ├── peak_detection/         單次觀察、峰假設、選擇與積分
│   ├── alignment/              跨樣本組合、Backfill、格值決策及其交付 adapter
│   │   ├── completion.py       已取得 evidence → 完成的科學結果；不呼叫 runtime
│   │   ├── cid_nl_completion.py 群組及已取得證據 → 完整 CID-NL 格值結果
│   │   ├── cid_nl_inputs.py    正式 partition／diagnostic 共用的純 CID-NL input contract
│   │   ├── cross_sample/       跨樣本關聯
│   │   ├── backfill/           有限度的 observation revisit
│   │   ├── cell_observation/   每個樣本格的 observation 紀錄與群組脈絡
│   │   ├── cell_decision/      每個樣本格的科學決策
│   │   ├── projection/         Targeted 固定列前後投影／Untargeted 完成格值的 Matrix 投影
│   │   └── result/             科學結果與 application 結果
│   ├── execution/             attempt、工作分區、workers、結果暫存與終局記錄
│   ├── output/                CSV／workbook／HTML 等輸出程式
│   │   └── alignment/         Matrix 格式、artifact 選擇、串流／實體化與 atomic publishing
│   ├── instrument_qc/         儀器 QC、校正與其報告流程
│   ├── alignment_explanation/ 共用 SQLite 儲存格式、寫入、索引查詢與已發布格值讀取
│   ├── diagnostics/           可重用的診斷與說明工具
│   └── *.py                   RAW adapter、共享模型／規則、公開入口
├── xic_inspector/              完成證據的只讀檢視
│   ├── sources/               解讀來源證據
│   ├── selection/             選擇要呈現的記錄
│   └── rendering/             共用可掛載檢閱視圖、CSS／JavaScript 與單檔離線 HTML
├── xic_workbench/              本機 HTML 分析、結果檢閱、進度／取消及按需 XIC
│   ├── assets/                分析／檢閱 HTML、JavaScript 與 CSS
│   └── *.py                   loopback server、設定、工作與 workflow adapters
├── tools/                     維護工具；diagnostics/ 為診斷，testing/ 管測試選取
├── tests/                     行為測試與小型 fixtures
├── docs/
│   ├── user/                  使用者操作、欄位參考、概念 HTML 與截圖（assets/）；README.md 是索引
│   ├── product/               產品行為與科學規則；README.md 依主題找文件
│   ├── agent/                 按任務讀的執行、驗證與交付規則；含診斷結論 ledger
│   ├── architecture-contract.md  程式入口、責任與依賴方向
│   └── project-layout.md      本檔
├── config/                    未追蹤的本機狀態與設定範本；公開 Targeted 範例在 xic_extractor/configuration/data/
├── .github/                   CI 與 issue／PR 範本
├── .codex/                    Repo 的 agent skill 與規則
├── pyproject.toml             套件、依賴、已安裝 CLI、測試與 import-linter 依賴限制
├── packaging/                 Windows 打包：PyInstaller spec 與 app icon
├── launch_gui.bat / .vbs      雙擊啟動 HTML 工作區
└── output/                    本機執行產物，不追蹤
```

`xic_extractor/output/` 是輸出**程式**；根目錄 `output/` 是不追蹤的執行**產物**。
`alignment/` 同時容納演算法、流程、I/O 與交付 adapter，不是純計算層；正式 owner 見
[責任表](architecture-contract.md#ownership-map)。`docs/` 根目錄只放上面兩份文件。

## 新檔案放哪裡

Repo root 留給專案級設定、必要入口與主要目錄。Git、套件與工具依慣例讀取的
設定可留在 root；工具實作依下列責任歸位，不因執行方便而新增 root 檔案。
一次性工具輸入、分析腳本及其產物集中在 ignored `output/<purpose>/`；
有持續維護需求時才移入正式工具目錄。工具管理的 cache 與狀態沿用其設定位置，
失效後清理，不另建目錄搬存。Worktree 與驗證輸入不視為可隨手清除的 cache。

1. HTML 互動、本機 transport、workflow adapter → `xic_workbench/`。
2. Public CLI → `scripts/run_<purpose>.py`，並在 `pyproject.toml` 註冊 entry point。
3. 可重用的 domain 邏輯 → `xic_extractor/<owner>/`；owner 查 architecture contract。
4. 證據來源 adapter、selection、共用檢閱 renderer、離線匯出 → `xic_inspector/`。
5. 維護中的診斷 → `tools/diagnostics/<purpose>.py`，並更新 `tools/diagnostics/INDEX.md`。
6. 測試 → `tests/test_<module>_<behavior>.py`；共用 fixture → `tests/fixtures/`。
7. 格式契約跟著 consumer：內部資料留在 owner 的 typed model；執行時需要的
   schema/data 隨讀它的 package 安裝。不因資料是 JSON 就另建 schema。
8. 小型驗證輸入或 fixture → 放在讀它的程式或 `tests/fixtures/` 旁，必須有現存 consumer。

命名規則見 [Module Naming](architecture-contract.md#module-naming)；日期、Issue
編號與施工 phase 不能當路徑或 schema 名稱。

## 文件放哪裡

| 內容 | 位置 |
| --- | --- |
| 產品行為與科學規則 | `docs/product/<topic>.md` |
| Readiness 與 expected-diff | `docs/agent/product-validation-contract.md` |
| Writer authority | [`decision-policy.md`](product/decision-policy.md#writer-authority) |
| 架構與依賴 | `docs/architecture-contract.md` |
| 診斷結論與重跑條件 | `docs/agent/diagnostic-ledger.md` |
| 要做的事：計畫、acceptance、未決的產品決策 | GitHub Issue |
| 交付的 diff、驗證結果、review 結論 | GitHub PR body |
| 下一步 1–3 個動作、目前卡點 | 本機 `.handoffs/`（不追蹤） |
| 私人研究與推理 | Obsidian（選用；不能是唯一副本） |
| 產生的報表、完整驗證輸出、一次性 audit | ignored `output/` |

- 一條規則只有一個詳細 owner；其他文件只連過去。
- 行為改變時直接改現行文件；舊版由 Git 保留，不另建歸檔、計畫、spec 或歷史 stub。
- 刪除文件前，把仍有效的內容移到 owner 並修好連結。
- Repo 不放聊天紀錄、命令逐字稿、私人 RAW 佈局、本機路徑、憑證，或需要私人
  vault 才看得懂的內容。完整 workbook、matrix、RAW dump 與探索報告不因曾被人工
  看過就進版控。

## 不追蹤的目錄

| 目錄 | 用途 |
| --- | --- |
| `.venv/`, `.uv-cache/`, `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`, `.tmp/` | 可重建的 cache |
| `output/` | 執行產物與本機證據；不是可重用輸入 |
| `tmp_runtime/` | 可重算的 pipeline cache；清除會增加重算成本 |
| `.worktrees/` | 本機 Git worktrees；不是 cache，移除前確認 branch 與未提交變更 |
| `local_validation_artifacts/` | 跨 worktree 重用的驗證輸入，以相對路徑引用 |
| `local_raw_samples/`, `local_validation_raw/` | 私人 RAW，永不追蹤 |
| `.superpowers/`, `.remember/` | Agent 工具暫存 |
| `.handoffs/` | 本機 handoff：下一步動作與卡點，不進版控 |

清理前先確認確切目標；本檔不提供遞迴刪除命令。

## 搬移時的連動

- **Package roots：** `pyproject.toml` 的 `packages.find` 包含 `scripts*`、
  `tools*`、`xic_extractor*`、`xic_inspector*`、`xic_workbench*`，排除
  `tests*`。這些目錄須留在 repo root；搬移時驗證已安裝 CLI 與 package。
- **Frozen bundle：** `packaging/xic_extractor.spec` 從 `xic_workbench/__main__.py`
  啟動，以 repo root 解析路徑，打包設定範例、workbench／Inspector assets、preset
  TOML 與 package data；app icon 在同一目錄。
  搬移這些路徑要同步更新 spec 與 build workflow，並驗證打包後的程式；原始碼入口
  不能證明打包結果。
  `tools/distribution.py` 統一組裝交付 ZIP；build workflow 與 clean-source packaging
  test 共用此入口。它只發佈程式、離線使用指南、使用者範本與授權聲明，不複製 repo
  README、開發目錄或 Python 原始碼。文件選集由組裝入口維護；Markdown 轉成 HTML，
  與既有圖解頁分開命名，圖片及範本連結指向包內檔案。首次操作說明來自
  `docs/user/README.md`，也是程式內「使用說明」的正式入口。開發與 CLI 文件在
  `docs/development/`，不隨 ZIP 提供。`xic_workbench/user_guide.py` 統一發佈與讀取；
  wheel build 使用相同內容，不依賴 repo 文件或執行時網路。
- **Config 與 output：** `xic_extractor/configuration/loader.py` 由 config 的
  上層目錄導出 `output/`。搬移 `config/` 或 `output/` 時，驗證 HTML 工作區與 CLI
  的路徑。
