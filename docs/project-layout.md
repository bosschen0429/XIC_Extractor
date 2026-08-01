# Project Layout

本檔案是 XIC Extractor 的**目錄收納規則單一可信來源**。新檔案要放哪、暫存目錄何時可清、哪些路徑被外部約束鎖死，都查這裡。

## § 1 主要規則文件的職責分工

| 文件 | 給誰看 | 內容 |
|------|--------|------|
| [`README.md`](../README.md) | 使用者 | 下載、quick start、workflow routing、文件索引 |
| [`AGENTS.md`](../AGENTS.md) | 寫程式碼的人 | 高頻開發 guardrails、canonical references、public contract 摘要 |
| [`docs/agent/`](agent/) | 寫程式碼的人、reviewer、subagent | AGENTS 拆出的 nested contracts：communication、execution gates、planning、validation、architecture、Codex OS |
| [`docs/user/`](user/) | 使用者、寫文件的人 | GUI/CLI 操作手冊；解釋怎麼使用，不承接 product authority |
| [`docs/product/`](product/) | 寫程式碼的人、reviewer、future agent | 可公開的 product-topic 代表文件；目前先收斂 Backfill、Discovery、Alignment、Presets、Productization、Untargeted GUI 等主題，後續可擴充 |
| [`docs/architecture-contract.md`](architecture-contract.md) | 做程式結構調整的人 | 設計原則、所有權地圖、依賴規則、重構紀律、測試結構規則 |
| **本檔** | 想知道「檔案放哪」的人 | 目錄地圖、外部約束、新檔決策樹、暫存目錄清理規則、命名慣例 |

這些檔案職責不重疊。本檔**不**描述「程式怎麼寫」（那是 AGENTS.md 與
`docs/architecture-contract.md`），**不**描述「軟體怎麼用」（那是 README.md
與 `docs/user/`）。

### Permanent root document contract

Only the following exact four paths are permanent root authority documents.
Their path supplies repo placement, kind, purpose, and self-ownership, so they
do not need generic `Doc lifecycle` or `Doc exit rule` metadata. They still must
retain every positive landmark and resolvable route below; no fifth path may
inherit this exemption.

| Path | Purpose / owner | Required landmarks and routes |
| --- | --- | --- |
| `AGENTS.md` | Repo agent routing contract / self | `Task Routes`; parameter settings; product index; evidence/validation; architecture; execution/review; diagnostics/solutions; productization; project layout and agent system |
| `README.md` | Public project entrypoint / self | `Documentation`; user, product, architecture, project-layout, and LC-MS/MS evidence routes |
| `docs/architecture-contract.md` | Architecture/dependency owner / self | ownership declaration; `Dependency Direction`; `Ownership Map`; reachable from `AGENTS.md`, `README.md`, and this file |
| `docs/project-layout.md` | File-placement and documentation-lifecycle owner / self | file-placement purpose; docs source-of-truth/lifecycle route; architecture, product, and [Obsidian handoff](agent/obsidian-handoff-contract.md) routes |

This exemption does not apply to other canonical owners or other Markdown at
the repository or `docs/` root. Placement, purpose, owner, and route validation
remain mandatory for all four paths.

## § 2 目錄地圖

第一層完整清單。「PyInstaller」欄位指該目錄是否被打進 release bundle（由 `xic_extractor.spec` 決定）。

### 根層檔案

| 檔案 | 用途 | Git |
|------|------|-----|
| `pyproject.toml` | 專案 metadata、依賴、CLI entry points、setuptools.packages.find、pytest 設定 | 追蹤 |
| `uv.lock` | uv 套件鎖定檔 | 追蹤 |
| `.importlinter` | 靜態 dependency floor；只解析 static import graph | 追蹤 |
| `xic_extractor.spec` | PyInstaller 打包設定 | 追蹤 |
| `.gitattributes` | repo-wide text / line-ending / diff contract | 追蹤 |
| `.gitignore` | git 忽略規則 | 追蹤 |
| `README.md` | 使用者文件 | 追蹤 |
| `AGENTS.md` | 開發契約與程式設計規則 | 追蹤 |
| `launch_gui.bat` | Windows 本機開發快速啟動 GUI；release 打包後由 `.exe` 接手，不是 PyInstaller 必需輸入 | 追蹤 |

### 第一層子目錄（追蹤）

| 目錄 | 用途 | PyInstaller |
|------|------|------|
| `xic_extractor/` | 主程式庫（domain logic、所有 pipeline 階段） | 經由 `packages.find` |
| `gui/` | PyQt6 GUI 層；entry: `gui/main.py` | entry script |
| `scripts/` | 已註冊 CLI、具產品契約的 adapter，以及開發 / 驗證腳本；public exposure 與 lifecycle 依 [`docs/agent/script-tool-lifecycle.md`](agent/script-tool-lifecycle.md) 判斷 | 經由 `datas` |
| `tools/diagnostics/` | Maintained product-adjacent diagnostics。`packages.find` 目前把 `tools*` 列為 package-discovery candidate；實際 installed artifact closure 另由 R2a 驗證。個別工具的 consumer／lifecycle／retirement disposition 由 [`docs/agent/script-tool-lifecycle.md`](agent/script-tool-lifecycle.md) 與 `tools/diagnostics/INDEX.md` 共同承接。`docs_policy.py` 是 docs governance 路徑常數與分類邏輯的中央模組 | 未作為 datas 明列；實際 import closure 另驗 |
| `tests/` | pytest 測試（扁平結構 + `fixtures/`） | 否，exclude |
| `docs/` | 使用者文件（`docs/user/`）、產品主題文件（`docs/product/`）、agent contracts（`docs/agent/`）、architecture support（`docs/architecture/`）、JSON schema contract（`docs/superpowers/schemas/`）、短期 Markdown 規格（`docs/superpowers/specs/`）、計畫（`docs/superpowers/plans/`）、reusable solution notes（`docs/solutions/`） | 否 |
| `assets/` | `app_icon.png`、`screenshots/` | 經由 `datas` |
| `config/` | runtime 設定；**只 `env.example`、`*.example.csv` 與固定 public CSV（如 `MixSTDs.csv`）被追蹤** | 範本 CSV |
| `.github/` | GitHub Actions workflows + dependabot | 否 |
| `.codex/` | Repo-local Codex skills, hooks, rules, and subagent profiles | 否 |

### `docs/` source-of-truth 與歷程邊界

`docs/` 可以放正式 source-of-truth 文件，但不是所有開發歷程都應該留在 repo。
把 repo 當公開文件面：留下可公開、可審查、可被下一個 agent 執行的正式規則與
sanitized summary；把 Obsidian 當私人工作筆記：保存長篇推理、開發歷程、命令
diary、分支過程、私人 local context。

`docs/` 根目錄不是一般文件入口。根目錄 Markdown 只允許少數跨域高權威
文件：

- `docs/project-layout.md`
- `docs/architecture-contract.md`
- `docs/lc-msms-evidence-rules.md`
- `docs/diagnostic-ledger.md`

其他 durable 文件必須放到明確 owner 目錄：`docs/agent/`,
`docs/product/`, `docs/architecture/`, `docs/validation/`, `docs/user/`,
`docs/solutions/`, 或 `docs/superpowers/` 下的具名 public artifact lane。
dated report、branch note、method report、runbook、quick reference、research
snapshot 不得直接新增到 `docs/` 根目錄。
Obsidian-backed migration and repo-stub rules live in
`docs/agent/obsidian-handoff-contract.md`; that contract is the standing policy,
not a one-branch experiment.
Durable docs routing has only three final routes:

- `obsidian_original`: original full text belongs in Obsidian; repo keeps no
  long body, only a compact pointer/stub if referrers or provenance require it.
- `repo_distilled_plus_obsidian_original`: stable claims are distilled into a
  repo owner; original full text and private reasoning go to Obsidian.
- `repo_product_doc`: the file itself is the compact product, validation,
  governance, or operating document and stays in repo.

Audit-only `needs_route_decision` rows must be resolved to one of those routes
before move/delete work. When a repo document points to a private original, use
`source_repo_path:<repo path>` as the stable lookup key; do not write private
vault absolute paths into repo docs.
Routes are final destinations, not lifecycle. New lifecycle-managed public
plans and specs must carry `Doc kind:`, `Doc lifecycle:`, and `Doc exit rule:`
so an active execution plan or short-lived Markdown spec can live in repo while
it is useful, then close out through product-owner absorption and Obsidian
source-copy instead of becoming permanent residue. Same-path stubs are rare
active/referrer-bound exceptions, not the normal completed-doc exit.
`docs/superpowers/specs/` is
Markdown-only; machine-readable JSON contracts belong in
`docs/superpowers/schemas/`.

Completed transient plans/specs/notes have a mechanical exit:

- while active, keep a short tracked repo plan/spec/stub; do not hide active
  execution context in `.gitignore` or Obsidian only;
- at closeout, update the durable owner, run product-absorption review, source
  copy the original to Obsidian, and produce a retirement evidence JSON packet;
- use `tools/diagnostics/retire_docs.py --evidence <json> --execute` for the
  destructive local retirement step;
- use `tools/diagnostics/docs_management_audit.py --repo-only
  --fail-on-completed-transient` as the repo-visible gate; it does not require a
  private vault;
- staged lifecycle-managed Markdown deletions need a staged
  `docs/superpowers/file-management/docs-cleanup/*retirement-evidence*.json`
  packet with matching `source_hash` and no remaining exact repo referrers, so
  accidental `git rm` cannot bypass the evidence gate. That evidence packet is
  not itself a live referrer; every other exact repo reference still blocks
  deletion or requires a same-path stub.

Routes are also not topic uniqueness. `repo_product_doc` only means the file can
stay in repo; it does not prove the repo has a single source of truth for that
subject. Each durable topic should have one canonical owner. Other repo files on
the same topic must be supporting artifacts, validation packets, manifests,
closeouts, or active stubs that point back to that owner instead of carrying a
parallel definition.
Route-retained files are not automatically digested. The docs-management audit
reports `route_retained_files` and `digestion_status` separately so public docs,
checker-readable artifacts, stubs, or referrer-blocked files can still be
reviewed for owner absorption, support-surface demotion, lifecycle closeout, or
Obsidian original routing.
Do not maintain a tracked `docs/superpowers/topics/` tree. `docs/product/` is
the repo topic layer; a separate tracked topic-index folder duplicates that
meaning and drifts quickly after retirement sweeps. When browsing support
surfaces still helps a cleanup pass, generate temporary topic indexes through
`tools/diagnostics/docs_management_audit.py --topic-index-dir
output/docs-topic-indexes`; `output/` is ignored and the generated pages must
not become source-of-truth docs.
When a route needs private-vault work, use the wiki skill chain rather than ad
hoc file edits: `wiki-status`/`wiki-query` for read-side lookup,
`wiki-ingest`/`wiki-update` for original or project knowledge writes,
`obsidian-markdown` for manual note syntax, `wiki-lint` for health checks, and
`wiki-stage-commit` for staged write promotion.
For private-vault mechanics, use the installed `obsidian-wiki` model: staged
writes, `index.md`/`log.md`/`.manifest.json`, frontmatter summaries, provenance
markers, and compiled project pages. The older flat-root Obsidian convention is
legacy for XIC; new LLM-authored XIC wiki pages should start in `_staging/` and
promote to `projects/xic-extractor/` only after review. The existing `XIC/`
subtree remains the private migration/archive/workbench area.

跨越多份 dated plans/specs/validation 的產品主題，先收斂成
`docs/product/` 下的小型代表文件。這不是完整 taxonomy；目前第一批 owner 是
Backfill、Discovery、Alignment、Presets、Productization、Untargeted GUI、Evidence Spine、
Quant Matrix、Run Provenance、Review Roundtrip、Sample Metadata/QC、Targeted
Selection、Quantitation Context、Instrument QC/Calibration。使用者操作手冊放
`docs/user/`，不要塞回 README。之後若出現同等全局主題，新增
`docs/product/<topic>.md`，不要讓 repo 讀者只能從私人 Obsidian 或一串歷史 note
重建產品規則。

歷史 notes 要升格前，先把穩定 claim 分流到既有 owner：

| Claim 類型 | Repo owner |
|------|------|
| Backfill / Discovery / Alignment / Presets / Productization / Evidence Spine / Quant Matrix / Run Provenance / Review Roundtrip / Sample Metadata/QC / Targeted Selection / Quantitation Context / Instrument QC/Calibration 等全局產品主題 | `docs/product/` 對應主題文件；實際 tier、writer authority、schema、validation verdict 仍查下列 owner |
| active lane 與當前 readiness state | `docs/superpowers/validation/productization_status_index_v1.tsv` |
| writer scope 與當前 product authority | `docs/superpowers/schemas/productization_authority_manifest.v1.json` |
| 成熟度語彙、promotion 規則與 packet contract | `docs/agent/product-validation-contract.md` |
| 穩定 lane key 與舊 anchor 的 compatibility lookup | `docs/superpowers/plans/2026-06-15-productization-control-plane.md`；只路由到上述 owner，不儲存 mutable state |
| LC-MS/MS evidence rule、Backfill evidence semantics、Gaussian15 area owner | `docs/lc-msms-evidence-rules.md` |
| product-readiness wording、public-surface discipline | `docs/agent/product-validation-contract.md` |
| validation verdict、rerun policy、known target/failure conclusion | `docs/diagnostic-ledger.md` 或 compact validation artifact |
| output/artifact placement、stub/retention hygiene | 本檔 |
| architecture ownership、dependency direction | `docs/architecture-contract.md` |

長篇開發 diary、探索性策略重置、命令 transcript、本機絕對路徑、私人 RAW
layout、sample-level investigation、obsolete PR sequencing、過渡 observation
note，預設進私人 Obsidian / ignored handoff / ignored `output/` artifact，不直接
當 repo source-of-truth。若 keep-repo 檔案仍引用該歷史 note，先新增或更新 repo
內正式摘要，並優先把 referrer retarget 到正式 owner；只有 active handoff 或
無法立即改掉的 exact path referrer 才保留 sanitized stub。不要把 repo
referrer 改成只能在私人 Obsidian 才能讀懂。

`docs/superpowers/` 下的 public repo artifacts 必須先選 owner，不要回到單一
archive 大桶：

| Artifact | Repo target |
|------|------|
| productization compatibility lane-key registry | `docs/superpowers/productization/status/` |
| compact productization evidence summary | `docs/superpowers/productization/evidence/` |
| exact cleanup manifest、referrer audit、approval record、source-of-truth queue | `docs/superpowers/file-management/` |
| intentionally public PR/branch closeout summary | `docs/superpowers/closeouts/` |
| ignored local active handoff or rare tracked compatibility stub | `docs/superpowers/handoffs/` |
| checker-readable validation packet、inventory、status index、lockbox public artifact | `docs/superpowers/validation/` until a focused checker-aware migration moves it |
| generated read-side status report | ignored `output/` or Obsidian; promote only compact public evidence to `docs/superpowers/productization/evidence/` |
| public HTML/story/report | formal `docs/product/` or `docs/user/` page when durable; otherwise ignored `output/` or Obsidian |
| fixture/probe/transition list | `docs/superpowers/fixtures/`, `docs/superpowers/validation/`, or ignored `output/` depending on checker use |

`docs/superpowers/handoffs/current/` 與 `docs/superpowers/handoffs/archive/`
是 local handoff workspace；不要把 productization anchors、file-management
manifest、closeout summary 當作 handoff archive 例外塞回去。

如果歷史 note 內有重要但尚未整理的內容，先把穩定 public claim 寫進上表
canonical owner，再把原文當 private context 移交 Obsidian。不能因為 Obsidian
有完整原文，就讓 repo 只剩一個需要私人 vault 才能理解的引用。

新增文件時先判斷公開面：

- 先決定 `Doc placement:`：
  `formal_repo_doc`、`repo_active_stub`、`branch_closeout_summary`、
  `repo_stub_plus_obsidian`、`repo_stub_plus_formal_doc`、
  `private_obsidian_note`、`ignored_artifact`，或
  `throwaway_scratch`。
- 新增 `docs/superpowers/plans/` 這類 lifecycle-managed public docs
  時，同步寫 `Doc kind:`、`Doc lifecycle:`、`Doc exit rule:`。
- 不要新增 tracked `docs/deepresearch/`、`docs/superpowers/deepresearch/`、
  `docs/superpowers/notes/`、`topics/`、`goals/`、`pulse-reports/`、`reports/`。
  觀察筆記走 private-first；需要 public repo
  surface 時，先升格成 active stub、正式 owner、validation artifact、file-management
  manifest、productization evidence，或 branch closeout。
- 會改 public behavior、schema、validation policy、product authority、agent
  workflow rule 的內容，寫進上表 canonical owner；若不在 canonical owner
  path，commit 前必須有 `Doc placement:` 與 `Repo owner:`。
- 只是探索、開發日誌、review 細節、命令 transcript、私人資料位置，寫進
  Obsidian staged draft 或 ignored artifact，不要先 commit 再回頭 `git rm`。
- active execution plan 不能 Obsidian-only；repo 必須有短 stub，能交代
  objective、scope、constraints、next 1-3 actions、verification、stop rule。
- completed transient plan/spec/note 不能只手動 `git rm`；先吸收進 durable
  owner、備份/讀回 Obsidian 原文，產生 retirement evidence JSON，再用
  `retire_docs.py --evidence` 退場。repo-only audit 與 staged placement guard
  會擋掉缺少 evidence 的完成文件或 lifecycle-managed deletion。
- 如果私人筆記對接手有幫助，repo 只保留短 stub：現在狀態、正式 owner、已跑驗證、
  blocker、下一步。stub 不能要求讀者一定要有私人 vault 才能理解下一步。
- 不要把整個 `docs/` 設成 Obsidian 自動 ingest 來源；正式 repo docs 預設留在 repo，
  只有明確核准要成為私人 context 的來源才進 vault raw inbox 或 migration manifest。

### 第一層子目錄（忽略）

| 目錄 | 用途 |
|------|------|
| `.venv/` | uv 建立的 Python 虛擬環境 |
| `.uv-cache/` | uv 套件下載快取 |
| `.mypy_cache/` | mypy 型別檢查快取 |
| `.pytest_cache/` | pytest 快取 |
| `.ruff_cache/` | ruff linter 快取 |
| `.tmp/` | pip / pytest 臨時檔 |
| `output/` | pipeline 執行產物（xlsx / tsv / html） |
| `tmp_runtime/` | pipeline 與 validation_harness 產生的 pickle 快取 |
| `.worktrees/` | git worktrees（superpowers/using-git-worktrees skill 建立） |
| `local_validation_artifacts/` | 跨 worktree 重複使用的本機 accepted validation inputs，例如 discovery index + candidate/review CSV；私人且忽略 |
| `.superpowers/` | Claude superpowers 工件（brainstorm 快照等） |
| `.remember/` | Claude 代理記憶日誌 |
| `local_raw_samples/`、`local_validation_raw/` | 本地測試 RAW 樣本（私人資料） |
| `xic_extractor.egg-info/` | setuptools 安裝元資料 |

Validation output 的預設去處是 ignored storage，不是 repo。個人同機開發時，
完整 workbook、full matrix、RAW-derived dump、exploratory diagnostics 可以留在
`output/` 或 `local_validation_artifacts/`；repo 只留能讓 clean checkout 審查
產品 claim 的最小 contract seed：summary、manifest、hash、row count、再生指令、
authority/status 欄位，或 focused test 需要的小型 fixture。若某個 validation 檔
只是「我本機看過的完整結果」，不要為了方便把它升格成 tracked artifact。
若它是 checker/test/PR review 需要的契約證據，先把完整表縮成最小 fixture 或
summary，再考慮外部化原始 full dump。

### `xic_extractor/` subpackage 分工

由 `docs/architecture-contract.md § Ownership Map` 規範。一句話摘要：

| Subpackage | 用途 |
|------------|------|
| `alignment/` | 跨樣本對齊、ownership clustering、primary consolidation |
| `discovery/` | Untargeted feature discovery（MS2 NL seed → MS1 backfill） |
| `extraction/` | Targeted XIC 提取、anchor windowing、pipeline orchestration |
| `peak_detection/` | Resolver-specific peak finding（`legacy_savgol`, `local_minimum`） |
| `configuration/` | Settings schema、CSV IO、validation |
| `output/` | Workbook sheet rendering、Excel / TSV / HTML 輸出 |
| `diagnostics/` | Audit 與診斷模組 |
| 根層 `extractor.py` `peak_scoring.py` 等 | Facade 與跨 subpackage 公開介面 |

詳細所有權邊界查 `docs/architecture-contract.md § Ownership Map`。

## § 3 三道不可動的約束

這 3 處外部約束鎖死了目錄結構。**動之前必讀**。

### (a) `pyproject.toml` packages.find

```toml
[tool.setuptools.packages.find]
include = ["gui*", "scripts*", "tools*", "xic_extractor*"]
exclude = ["tests*"]
```

→ `gui/`、`scripts/`、`tools/`、`xic_extractor/` **必須在根目錄**，不能搬進 `src/` 或任何子目錄。
→ 違反後果：package discovery 與 6 個已宣告 console scripts 可能失效；實際 installed artifact closure 由 R2a 驗證。

### (b) `xic_extractor.spec` 寫死的相對路徑

```python
from PyInstaller.utils.hooks import collect_data_files

preset_datas = collect_data_files(
    "xic_extractor.presets",
    includes=["data/*.toml"],
)

datas=[
    ("assets", "assets"),
    ("config/settings.example.csv", "config"),
    ("config/targets.example.csv", "config"),
    ("scripts", "scripts"),
] + preset_datas
entry: ["gui/main.py"]
icon: "assets/app_icon.png"
```

→ 移動或重命名 `assets/`、`config/settings.example.csv`、`config/targets.example.csv`、`scripts/`、`gui/main.py`、`assets/app_icon.png`、`xic_extractor/presets/data/*.toml` 之中任何一個 → CI build workflow 或 frozen GUI startup 失敗。
→ 若要動，必須同時更新 `xic_extractor.spec` 與 `.github/workflows/build.yml`。

### (c) `xic_extractor/configuration/loader.py:29` 寫死 config-output 同級

```python
output_dir = config_dir.parent / "output"
```

→ `config/` 與 `output/` **必須是同一級的目錄**（都在根目錄）。
→ 若要動位置，必須改 `loader.py` 邏輯，並驗證 GUI（`gui/config_io.py:7-12`）、CLI（`scripts/run_extraction.py:20-21`）的對應路徑導航。

### Bonus：frozen vs dev 雙模式

`gui/config_io.py` 與 `gui/main_window.py` 都用：
```python
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).parent      # 用戶可寫
    _BUNDLE = Path(sys._MEIPASS)            # PyInstaller bundle，唯讀
else:
    ROOT = Path(__file__).resolve().parent.parent
    _BUNDLE = ROOT
```

意思是：打包後 `config/`、`output/` 在執行檔旁（用戶可改），但 `assets/`、`scripts/` 在 bundle 內（唯讀）。動這些目錄時要兩種模式都驗證。
Root `launch_gui.bat` 只服務本機開發雙擊啟動；release artifact 以
`XIC_Extractor.exe` 為入口，不依賴這個批次檔。

## § 4 新檔案該放哪？

```
要加新檔
├── 程式碼
│   ├── GUI（PyQt6 widget、worker、styles）
│   │   └→ gui/                       （子目錄：sections/、workers/）
│   ├── 安裝型 public CLI 入口
│   │   └→ scripts/run_<purpose>.py   （需在 pyproject.toml.[project.scripts] 註冊）
│   ├── 具產品／下游契約但未安裝的 adapter
│   │   └→ scripts/<purpose>.py       （需有正式 product/downstream owner）
│   ├── 開發 / 驗證腳本（會被 PyInstaller 打包）
│   │   └→ scripts/<purpose>.py
│   ├── Maintained product-adjacent diagnostics（installed/package lifecycle 另驗）
│   │   └→ tools/diagnostics/<purpose>.py
│   └── Domain 邏輯
│       └→ xic_extractor/<subpackage>/
│           └─ 不確定哪個 subpackage？查 docs/architecture-contract.md § Ownership Map
│
├── 測試
│   └→ tests/test_<module>_<behavior>.py
│       （扁平結構，不鏡像 xic_extractor/ 子目錄）
│       共用 fixture → tests/conftest.py 或 tests/fixtures/
│
├── schema / 規格 / 計畫文件
│   ├── stable domain glossary → docs/product/domain-glossary.md
│   ├── 測試架構指南 → docs/agent/testing.md
│   ├── milestone changelog → docs/history/CHANGELOG.md
│   ├── JSON schema contract → docs/superpowers/schemas/<contract>.v1.json
│   ├── 過渡性 Markdown 規格 → docs/superpowers/specs/<dated-topic>.md；完成後 product owner 吸收，原文轉 Obsidian
│   ├── 全局控制面 / 命名 owner → docs/superpowers/plans/<explicit-owner>.md
│   ├── productization status/evidence → docs/superpowers/productization/<status|evidence>/
│   ├── file-management manifest/audit/approval → docs/superpowers/file-management/
│   ├── public branch closeout / PR-body seed → docs/superpowers/closeouts/
│   ├── active implementation context → ignored docs/superpowers/handoffs/current/ACTIVE.local.md 或 repo_active_stub
│   └── generated pulse/report/observation → ignored output/ 或 Obsidian；不得新增 tracked generic deepresearch/notes/reports/goals
│
├── 可重用解法 / 工作流知識
│   └→ docs/solutions/<category>/<slug>.md
│       （完成非直覺修正、產品化決策、validation lesson 後手動用 $xic-compound 產生）
│
├── 執行產物（自動忽略）
│   └→ output/
│
├── pickle 快取（自動忽略）
│   └→ tmp_runtime/
│
├── 設定範本（只 example 才被追蹤）
│   ├── local env template → config/env.example
│   └── runtime CSV template → config/<name>.example.csv
│
└── 資源檔（icon、screenshot）
    └→ assets/
```

## § 5 暫存目錄定義與安全清理

「暫存目錄」= 在 `.gitignore` 中、由工具或執行時自動產生、可被安全重建的目錄。本節給每個暫存目錄訂出（i）誰建的、（ii）何時可清、（iii）安全清理指令。

> 本檔不主動執行清理。下列指令請在你確認時機合適時自行跑。

### `.mypy_cache/`

- **誰建**：`mypy` 第一次跑時自動建立
- **何時可清**：任何時候（會自動重建）
- **指令**：
  ```powershell
  Remove-Item -Recurse -Force .mypy_cache
  ```

### `.pytest_cache/` `.ruff_cache/` `.uv-cache/` `.tmp/`

- **誰建**：對應工具第一次執行時
- **何時可清**：任何時候（會自動重建）
- **指令**（按需擇一）：
  ```powershell
  Remove-Item -Recurse -Force .pytest_cache, .ruff_cache, .tmp
  ```
  `.uv-cache/` 較大但跨專案共享 uv 套件快取，刪除後下次 `uv sync` 會重抓所有套件，視磁碟空間決定。

### `tmp_runtime/`

- **誰建**：`scripts/validation_harness.py`、`scripts/local_minimum_param_sweep.py`、pipeline 執行時產生的 pickle 快取（`case_*` 子目錄）
- **何時可清**：所有 alignment / discovery 任務跑完且結果已寫到 `output/` 後
- **風險**：清掉後重跑會慢（需重算 pickle）
- **現況**：截至 2026-05-16，548 個 `case_*` 子目錄共 2.28 GB
- **指令**：
  ```powershell
  Remove-Item -Recurse -Force tmp_runtime\case_*
  ```

### `.worktrees/`

- **誰建**：`git worktree add ...`，或 superpowers/using-git-worktrees skill 自動建立
- **何時可清**：個別 worktree 的分支已合入 `master`，且 worktree 內無未提交修改
- **風險**：盲清會丟失未合的工作分支；目前 24 個活躍 worktree、20.2 GB
- **續接規則**：每次續接舊任務前先跑 `git worktree list` 與
  `git -C .worktrees\<name> status`。不要假設上一輪 worktree 還存在；若路徑已
  被刪，重新建立 worktree 或改用 `local_validation_artifacts/` 裡的 durable
  input，不要從記憶中的 `.worktrees\<branch>\output\...` 繼續。
- **Git friction**：`.git\index.lock`、ref-lock、或 permission denied 常是
  Windows sandbox / ACL 摩擦。先確認正確 worktree，再用 `git -C <repo> ...` 或
  narrow approval 重跑同一 git 操作；不要用 `git reset --hard` / `git clean`
  當通用修復。
- **安全步驟**：
  ```powershell
  # 1. 列出所有 worktree
  git worktree list

  # 2. 對每個 worktree，確認無未提交修改
  git -C .worktrees\<name> status

  # 3. 確認該分支已合入 master
  git -C .worktrees\<name> branch --show-current
  git log master..<branch-name> --oneline   # 若空輸出代表已合

  # 4. 移除個別 worktree
  git worktree remove .worktrees\<name>

  # 5. 清剩餘元資料
  git worktree prune
  ```

### `output/`

- **誰建**：pipeline 寫入結果（`xlsx`、`tsv`、`html`、中間 CSV）
- **何時可清**：結果已備份 / 驗收後
- **現況**：截至 2026-05-16，340 MB，含 2026-04-07 到 2026-05-14 的舊輸出
- **規則**：`output/` 是 evidence reference，不是 durable input store。跨
  worktree 要重用的 accepted input 必須搬到 `local_validation_artifacts/` 並重寫
  內部路徑。
- **掃描規則**：不要用 blanket `Get-ChildItem -Recurse` 掃整個 `output/` 來找
  artifact；先用目前 spec / validation note / `tools/diagnostics/INDEX.md`，再用
  targeted `rg` 或 `Get-ChildItem -Filter <exact-pattern>`。
- **鎖檔規則**：`xlsx`、HTML、PNG 若被 Excel/browser/previewer 開著，overwrite
  可能回 `Permission denied`。優先寫 timestamped / suffixed 新檔並回報鎖檔可能，
  不要強刪或反覆覆蓋。
- **指令**：依檔案需求手動刪除子集，不一鍵全清

### `local_raw_samples/` `local_validation_raw/`

- **誰建**：手動放入本地 RAW 樣本
- **何時可清**：依本地驗證需求，無自動規則
- **規則**：被 `.gitignore` 忽略，避免私人 RAW 資料進入 git

### `local_validation_artifacts/`

- **誰建**：手動或 agent 在 accepted validation input 需要跨 worktree 重用時建立
- **用途**：保存可重用但不進 git 的 validation inputs，例如
  `discovery_batch_index.csv` 及其 per-sample `discovery_candidates.csv` /
  `discovery_review.csv`
- **規則**：不要指向 `.worktrees/<branch>/output/` 作為長期輸入。搬入本目錄時
  必須重寫 `discovery_batch_index.csv` 裡的 `candidate_csv` / `review_csv` 到
  本目錄下的實際路徑，並跑 preflight 檢查 sample count、candidate CSV、RAW path
- **何時可清**：只有在對應 validation note 已不再需要，或可從 RAW 重新產生時
- **指令**：依 artifact set 手動刪除；不要一鍵清整個目錄

### `.superpowers/` `.remember/`

- **誰建**：Claude superpowers / 代理工具
- **何時可清**：任何時候（會自動重建）
- **指令**：除非有明確記錄需求，否則可保留

## § 6 既有命名慣例（從現況歸納）

| 類別 | 慣例 | 範例 |
|------|------|------|
| JSON schema contract | `docs/superpowers/schemas/<contract>.vN.json` | `productization_control_plane_schema.v2.json` |
| 過渡性 Markdown spec | `docs/superpowers/specs/<yyyy-mm-dd-topic>.md` | `2026-07-01-example-contract.md` |
| 產品治理 owner | stable topic、status index、authority manifest、schema 各自擁有 | `docs/product/productization.md` |
| Active stub | ignored local `docs/superpowers/handoffs/current/ACTIVE.local.md`; force-added repo stub only when intentionally public | `ACTIVE.local.md` |
| 測試檔 | `tests/test_<module>_<behavior>.py`，**扁平結構，不鏡像 src** | `test_alignment_owner_clustering.py` |
| CLI 入口 | `scripts/run_<purpose>.py` | `scripts/run_extraction.py` |
| CLI 輔助 | `scripts/<verb>_<noun>.py` 或 `<purpose>.py` | `scripts/csv_to_excel.py` |
| 診斷工具 | `tools/diagnostics/<purpose>.py` | `tools/diagnostics/alignment_decision_report.py` |
| Subpackage `__init__.py` | 對外公開模組才導出 `__all__`（目前部分缺失，見 § 7） | `xic_extractor/alignment/__init__.py` |

關於「測試為何不鏡像 src 結構」：`docs/architecture-contract.md` 明文採用 flat test layout 與 `tests/test_<module>_<behavior>.py` 命名策略，避免 `tests/alignment/test_*.py` vs `xic_extractor/alignment/` 的雙層維護。

## § 7 已知內部重構狀態（指向後續工作，不在本檔範圍）

下列問題已被識別但**不屬於目錄收納規則**，由 `docs/architecture-contract.md § Current Decomposition Targets` 與既有 spec 接手。

- `xic_extractor/alignment/` 內 `_ppm()` 在 10 處重複定義（待抽 utils）
- `extraction/`、`output/`、`configuration/`、`peak_detection/` 4 個 subpackage 缺 `__all__` 公開 API 宣告
- `xic_extractor/peak_scoring.py`（1014 行）→ `docs/architecture-contract.md § Current Decomposition Targets` 已列名
- `xic_extractor/extractor.py` 與 `xic_extractor/signal_processing.py` 的 facade 化重構（待處理）
- `xic_extractor/alignment/primary_consolidation.py` 的 characterization-first 拆分（待處理）

已完成的責任切分：

- `tools/diagnostics/alignment_decision_report.py`、`single_dr_production_gate_decision_report.py`、`targeted_istd_benchmark.py`、`family_ms1_backfill_review_report.py`、`analyze_rt_normalization_anchors.py`、`family_ms1_overlay_plot.py`、`untargeted_alignment_guardrails.py`、`seed_aware_backfill_review.py`、`targeted_nl_dropout_root_cause_audit.py`、`peak_candidate_score_calibration_report.py`、`evidence_spine_consistency.py`、`area_integration_uncertainty_audit.py`、`cwt_peak_candidate_audit.py`、`cross_report_evidence_consistency.py`、`targeted_gt_alignment_audit.py` 已拆出 loading / report model / matching / summary / analysis / rendering / style / guardrail 或 writing helpers。
- `xic_extractor/alignment/pipeline.py` 已拆出 `pipeline_outputs.py` 與 `raw_sources.py`，保留 `run_alignment(...)` 作 orchestration facade。

相關 architecture owner：

- [`docs/architecture-contract.md`](architecture-contract.md)
- [`docs/agent/architecture-public-contracts.md`](agent/architecture-public-contracts.md)

Retired dated decomposition specs are retained only as migration/history stubs
after their stable claims are folded into the architecture owners above.

## § 8 維護本檔的時機

本檔需要更新當且僅當：

1. 第一層目錄新增 / 移除 / 改名 → 更新 § 2
2. `pyproject.toml.packages.find`、`xic_extractor.spec`、`configuration/loader.py:29` 任一處被改 → 更新 § 3
3. 既有暫存目錄被改名或新增 → 更新 § 5
4. 命名慣例改變 → 更新 § 6

僅內部 subpackage 結構調整（例如 `alignment/` 內檔案移動）→ **不需**更新本檔，由 `docs/architecture-contract.md § Ownership Map` 負責。
