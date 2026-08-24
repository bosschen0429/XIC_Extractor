# Agent Parameter Settings

本檔只記錄 XIC Extractor 常用、穩定、跨任務會重複踩到的本機設定。任務特定
artifact，例如某個 Phase 的 benchmark summary、一次性 gate output、plot/report，
不放在這裡；請查當前 spec、plan、validation note 或本輪 `output/` index。
跨 worktree 會重複使用的 accepted validation inputs 例外：必須放在
`local_validation_artifacts/`，不能依賴某個 `.worktrees/<branch>/output/`。

這份文件是 operational memory，不是一次性備忘錄。當某個 RAW / validation
命令實際跑通，或某個啟動方式反覆失敗，應把穩定參數形狀與教訓更新到本檔。
不要只把教訓留在單次 conversation 或 phase note。

## Read Routes

先選一個 task branch；只有命令實際跨界時才加讀另一列。其餘段落是 reference，
不是每次都要載入的前置清單。

| Task branch | Read |
| --- | --- |
| project Python 或 no-RAW tests | [Python Runner](#python-runner) 與 [No-RAW Tests](#no-raw-tests) |
| 新 worktree 或 PR verification bootstrap | [Python Runner](#python-runner) 與 [Preflight](#preflight) |
| RAW/DLL input 或 local validation artifacts | [Python Runner](#python-runner)、[Stable Local Paths](#stable-local-paths) 與 [Preflight](#preflight) |
| 選擇 validation scale | [Validation Tiers](#validation-tiers)，再讀任務指定的 method-specific validation owner |
| Untargeted CID-NL real-data bridge | [Untargeted CID-NL Bridge](#untargeted-cid-nl-bridge) |
| ModeTrack diagnostic | [ModeTrack Diagnostic](#modetrack-diagnostic) |
| provisional full-universe Matrix | [Provisional Matrix](#provisional-matrix) |
| Alignment、Owner Backfill 或 Matrix handoff | [Alignment And Handoff](#alignment-and-handoff) |
| 85RAW launch 或 profiling | [Alignment And Handoff](#alignment-and-handoff) 與 [85RAW Command Contract](#85raw-command-contract) |
| sandbox、approval、shell 或 launcher failure | [Sandbox Friction Playbook](#sandbox-friction-playbook) 與 [Syntax Anti-patterns](#syntax-anti-patterns) |

跨 worktree 會重複遇到的診斷結論放在
`docs/diagnostic-ledger.md`。在重跑昂貴 RAW validation 或把已知 target
重新標成 blocker 前，先讀 ledger；只有 current code 或 artifact freshness
足以改變結論時才重跑。

維護規則：

- 只固定跨任務會重複使用的 runner、資料根目錄、DLL 目錄、validation tier、
  command shape、preflight check、以及反覆踩雷的 anti-pattern。
- 任務特定 output path、phase-specific discovery index、一次性 benchmark 結論
  留在 validation note；本檔只引用該 note 作為 evidence。
- Current `discovery_batch_index_v2` producers write `candidate_csv` and
  `review_csv` relative to the index. Copy the complete Discovery directory to
  `local_validation_artifacts/...` without rewriting those locators. Historical
  indexes that contain worktree-absolute paths require a one-time migration to
  index-relative locators before reuse.
- 成功 run 要記錄「可重用的參數形狀」與 evidence note；失敗 run 要記錄
  「不可再用的啟動方式」與替代做法。
- 若後續 P-spec / C-spec 改變正式驗收形狀，先更新本檔再開長時間 RAW run。

## Python Runner

本機 operator runtime 固定為 Windows Native；每個 worktree 的唯一 project
runner 是該 worktree 自己的 `.venv\Scripts\python.exe`：

| Surface | Runner | 契約 |
| --- | --- | --- |
| local dev/test/RAW | `.venv\Scripts\python.exe` | Python `>=3.11,<3.14`；locked dev dependencies、`pytest` 與 `pythonnet` 由 bootstrap 驗證 |
| hosted primary CI | workflow-provided Python 3.12 | clean-checkout default gate |
| hosted compatibility CI | workflow-provided Python 3.11 | lower-bound compatibility gate |
| `.venv/bin/python` | WSL 內、已有 task-specific CoreCLR/Thermo net8 canary 的 bounded RAW run | Package 3A canary 使用 Python 3.12.3、`PYTHONNET_RUNTIME=coreclr` 與官方 Thermo net8 `8.0.37` assemblies 通過；不是未經 canary 的通用替代 runner |

Bare `python` 只可作為 bootstrap driver 或檢查 launcher identity；不得用來跑
project tests、scripts 或 RAW。即使 bare launcher 恰好有 pytest，也不代表它
符合 locked environment、editable install 或 pythonnet 契約。

規則：

- 會讀 `.raw`、呼叫 Thermo DLL、或 import `xic_extractor.raw_reader` 的命令，
  預設用 `.venv\Scripts\python.exe`。WSL 只有在 task-specific packet 已封存
  current `.venv/bin/python`、`PYTHONNET_RUNTIME=coreclr`、官方 Thermo net8 DLL
  identity 與同 lane RAW canary 時才可使用；Package 3A 的 bounded canary owner
  是 `output/package3a_trace_canary_20260727/README.md`。
- no-RAW pytest、repo scripts 與 RAW validation 都使用 worktree `.venv`。
- worktree 尚未建立 `.venv` 或 bootstrap check 失敗時，先執行本頁的
  `scripts/bootstrap_worktree.py` bootstrap；不得改用 bare `python` 跑 project
  tests，也不得借用其他 worktree 的 `.venv`。
- 在 worktree 內跑正式 85RAW 時，`scripts.run_alignment
  --expected-sample-count 85` 會要求 Python executable 位於該 worktree 的
  `.venv` 底下。不要用 root repo 的絕對路徑
  `"${env:XIC_REPO_ROOT}\.venv\Scripts\python.exe"` 直接啟動
  worktree 的 85RAW；這會被 canonical guard 拒絕。
- 每個 worktree 必須有自己的 `.venv`；只有 uv package cache
  (`.uv-cache`) 在 linked worktrees 間共用。不要建立或保留指向 root repo
  `.venv` 的 junction，否則 editable install 與 bootstrap stamp 會指向錯誤
  checkout。
- 舊 worktree 若仍使用 shared-venv junction，先確認它確實是 junction，再只
  移除 junction 本身，最後執行本頁的 bootstrap：

```powershell
$venv = Get-Item .venv -Force
if ($venv.LinkType -ne 'Junction') { throw '.venv is not a junction; stop and inspect it' }
Remove-Item .venv
uv run --cache-dir .uv-cache --no-project python scripts/bootstrap_worktree.py
```

## Stable Local Paths

Exact machine-specific values must live in ignored local env files such as
`.env.xic-local`; the public template is `config/env.example`. Repo docs use
env names so the repository can stay public-safe without losing the local
execution contract.

Load the local env before RAW-backed runs when the shell does not already have
these values:

```powershell
Get-Content .env.xic-local |
  Where-Object { $_ -match '^\s*[^#=]+=' } |
  ForEach-Object {
    $name, $value = $_ -split '=', 2
    Set-Item "Env:$name" $value
  }
```

| 用途 | 路徑 | 備註 |
| --- | --- | --- |
| Thermo RAW root，85RAW tissue set | `$env:XIC_RAW_ROOT` | Full tissue validation。 |
| Thermo RAW validation subset | `$env:XIC_RAW_VALIDATION_DIR` | Historical 8RAW validation subset。 |
| Thermo RawFileReader DLL dir | `$env:THERMO_RAWFILE_READER_DLL_DIR` | RAW commands 必須使用這個 DLL dir。 |
| manual 2RAW truth data | `$env:XIC_MANUAL_2RAW_ROOT` | Resolver / manual-truth calibration。 |
| accepted 8RAW discovery input（v2） | `local_validation_artifacts/discovery/accepted_v2/8raw/discovery_batch_index.csv` | 目前唯一可用的 8RAW accepted input。`discovery_batch_index_v2`、index-relative locators、每個 sample 帶 `discovery_physical_facts.v1.json`。2026-08-18 驗證：8RAW alignment 輸出與 `output/20260818-compact-backfill-8raw-final-c9d26f76/` 五個 artifact 逐 byte 相同。 |
| retired：accepted P8b 8RAW / 85RAW discovery input | `local_validation_artifacts/discovery/accepted_p8b/{8raw,85raw}/discovery_batch_index.csv` | **v1，`scripts.run_alignment` 直接拒絕**：`discovery_batch_index_v2 owner closure is required`（`--preflight-only` exit 2）。兩者皆為絕對 locator、0 個 physical-facts sidecar。85RAW 目前**沒有**可用的 v2 accepted input。 |
| targeted GT 8RAW default workbook | `local_validation_artifacts/targeted_gt_workbooks/8raw/xic_results_20260512_1151.xlsx` | SHA256 `788892188C8419C82DC4618C98E160B90AC6C44C38676C53609248AA529889F7`；`targeted_gt_alignment_audit.py` 的 8RAW default positive checkpoint workbook；不要用 85-sample workbook 對 8RAW alignment，否則會產生 off-scope `MISS`。 |

標準 DNA dR Alignment run shape 必須成對提供 `--sample-info` 與
`--targeted-istd-workbook`。兩者一起建立 targeted ISTD drift prior，讓
Alignment 使用既有 injection-order／RT evidence path；CLI 仍會拒絕只給其中
一個。這兩個檔案是 lane-owned inputs，路徑依該批資料指定，不在 repo 內
硬編碼；以下 command shape 的 placeholders 必須替換成實際檔案。
`--sample-column-injection-order` 則是獨立的 optional input；它不只是重新排欄，
也會參與 Alignment 的 injection-trajectory rescue，可能改變 family partition
與 Matrix content。

`local_validation_artifacts/` 是 gitignored，所以新 worktree 不會自動帶到
accepted input。開跑前先 `Test-Path`；缺了就從既有 v2 Discovery 目錄複製，
不要退回 `accepted_p8b`。

辨識一份 index 是否可用（三項都要成立）：

- 第一欄 `schema_version` 是 `discovery_batch_index_v2`；
- `candidate_csv` / `review_csv` 是 index-relative，不是絕對路徑；
- 每個 sample 目錄有 `discovery_physical_facts.v1.json`。

`require_owner_closure=True` 硬編碼在 `scripts/run_alignment.py`，**沒有 CLI
開關**可以繞過 v1 index。要新建一份 accepted input，就把來源 Discovery 目錄的
`discovery_batch_index.csv` 與每個 sample 的三個檔案（`discovery_candidates.csv`、
`discovery_review.csv`、`discovery_physical_facts.v1.json`）整份複製到
`local_validation_artifacts/discovery/<name>/`，不要改寫 locators，然後用
`--preflight-only` 確認 exit 0。當前 8RAW accepted input 即由
`output/8raw-full-batch-continuity-final-gui-20260817/result/`（30,748 candidates）
這樣複製而來。

實驗性 mzML 路徑不列為常用 setting。即使本機有 mzML，production premise 仍是
直接讀 `.raw`，不轉檔。只有使用者明確要求 external-reference audit 時才查
mzML 相關路徑。

## Preflight

涉及本機 Python、RAW、DLL 或 validation data 時，先用這組最小檢查：

```powershell
uv run --cache-dir .uv-cache --no-project python scripts/bootstrap_worktree.py --check
.venv\Scripts\python.exe -m scripts.agent_sandbox_doctor --strict --probe-write
Test-Path "$env:XIC_RAW_ROOT"
Test-Path "$env:THERMO_RAWFILE_READER_DLL_DIR"
```

新建 worktree 尚未通過第一行的離線檢查時，只執行一次：

```powershell
uv run --cache-dir .uv-cache --no-project python scripts/bootstrap_worktree.py
```

這是 Windows / WSL 共用且可重複執行的完整 dev bootstrap；它使用 locked
`dev` extra 與 dependency group，並把環境綁定目前 worktree。不要複製其他
worktree 的 ignored `.venv`，也不要臨時逐套件安裝。

如果本檔列出的 stable path 存在，就使用它。除非 `Test-Path` 失敗或使用者
提供更新路徑，否則不要重新搜尋，也不要自行替換成 sibling directory。

repo-local doctor 預設會執行 bounded runtime canaries：Windows runtime owner、
worktree Python、bootstrap state、Git read 與 workspace write。它不讀 RAW：

```powershell
.venv\Scripts\python.exe -m scripts.agent_sandbox_doctor --strict --probe-write --command "<command-to-check>"
```

任務確實需要 GitHub 時才加 `--probe-github`。如果目前 Codex runtime 已宣告
sandbox network disabled，doctor 不會先執行一條必敗的 `git ls-remote`；它會直接
回報 `github_network_approval_required`。如果 network 已啟用，canary 才會區分
DNS、connection、auth/access failure：

```powershell
.venv\Scripts\python.exe -m scripts.agent_sandbox_doctor --strict --probe-github
```

Doctor 的 top-level status 直接決定下一步：

| Status | 固定下一步 |
| --- | --- |
| `READY` | 在目前 sandbox 執行 |
| `REWRITE_COMMAND` | 依 finding 改寫命令，不執行原形狀 |
| `APPROVAL_REQUIRED` | 對同一個必要命令提出 bounded approval；不要先製造一次失敗 |
| `ENVIRONMENT_REPAIR_REQUIRED` | 停止產品除錯，修復 named runtime layer 後再跑 doctor |
| `HUMAN_DECISION_REQUIRED` | 等待 credentials、destructive action 或風險取捨的人工決定 |
| `INCONCLUSIVE` | 只取得 finding 指定的下一個證據，不猜測 |

只想分類命令、不做 runtime acceptance 時才加 `--command-only`。
`--probe-git-write` 是 Git metadata write canary，不是一般 preflight。一般
`git status` 已包含在 doctor；staging 前仍先核對 dirty scope。Repo 不定義 permission
profile，也不覆寫 host sandbox。任何需要額外 filesystem、Git 或 network 權限的命令，
都對同一個精確命令使用 **Approve for me**。

```powershell
.venv\Scripts\python.exe -m scripts.agent_sandbox_doctor --command-only --strict --command ".venv\Scripts\python.exe -m scripts.run_alignment --expected-sample-count 85 ..."
```

## Sandbox Friction Playbook

Repo 不擁有 permission profile、sandbox mode、GitHub allowlist 或 shell-environment
permission workaround。Host 的 **Approve for me** 是唯一 permission escalation path。
Doctor 只分類 exact failure 並指出同一個必要命令；它不要求修改 project config。

```powershell
.venv\Scripts\python.exe -m scripts.agent_sandbox_doctor --strict --probe-write --probe-git-write --probe-github
gh api rate_limit
```

第一個命令驗證 workspace、Git metadata 與 GitHub origin。第二個命令直接驗證
`api.github.com`；兩者都是 read-only network canary。若 sandbox 阻擋必要命令，
直接對同一個精確命令使用 **Approve for me**。

### Approval-First Commands

下列類型在這台 Windows / Codex sandbox 上已反覆出現「先跑一次被拒，再用同
一條命令提權重跑」的浪費。不要把這些當未知問題重複試錯；如果任務需要它們，
直接用 `sandbox_permissions=require_escalated` 送同一條命令，並使用窄
`prefix_rule`。如果不需要它們，改用已存在 artifact 或離線替代，不要先跑。

| 命令類型 | 固定處理 | 不要做 |
| --- | --- | --- |
| `uv lock`、`uv sync --extra dev --group dev`、會下載 dependency 的 `uv run --with ...` | 任務需要更新 lock/env 時直接提權；保留 `$env:UV_CACHE_DIR='.uv-cache'`；prefix 只給 `uv lock` 或 `uv sync` 這種窄命令。本 repo 同時使用 optional `dev` extra 與 dependency group；需要完整 dev env 時用 `uv sync --extra dev --group dev` | 不要先在 sandbox 裡等 PyPI 被擋；不要只跑 `uv sync --group dev`，那會漏掉 optional dev extra 裡的 pytest stack；不要因為 `uv sync` 被擋就改成未 lock 的臨時安裝 |
| `uv run python -m playwright install chromium`、browser binary install/update | 只有 browser binary 缺失時才提權安裝；gallery smoke 先用 `tools\diagnostics\gallery_browser_smoke.py`，它會 fallback 到 system Chrome/Edge | 不要用 MCP timeout 或 Chrome extension 狀態當作自動化 smoke 的唯一驗收 |
| 會讀 `$env:XIC_RAW_ROOT` / `$env:XIC_RAW_VALIDATION_DIR` RAW 或載入 `$env:THERMO_RAWFILE_READER_DLL_DIR` DLL 的命令 | 先跑本檔 Preflight 的 `Test-Path` 與 `.venv\Scripts\python.exe` runtime check；命令本身用 `.venv\Scripts\python.exe`；若 sandbox / DLL loading / external executable spawn 被擋，直接提權重跑同一條命令 | 不要先用 bare `python` 撞一次；不要掃 sibling directory 猜 RAW/DLL；不要把較窄 no-RAW pytest 當成 RAW 驗證替代 |
| GUI/browser 開啟、外部 terminal、`Start-Process` 類命令 | 只有使用者明確需要互動視窗或外部 terminal 時才提權；長 RAW run 優先用前景 heartbeat command，不用 GUI | 不要用背景 helper 隱性取代可審計的 foreground RAW run |
| 寫入 `$CODEX_HOME` / `.codex` config、plugin/skill install、全域 hook/config | 只有使用者明確要求改 agent environment 時才提權；完成後寫 smoke / rollback note | 不要為了單次 repo 任務擴大成全域設定 |

如果命令不在上表，才回到「先 doctor、再決定」：

```powershell
.venv\Scripts\python.exe -m scripts.agent_sandbox_doctor --strict --command "<command-to-check>"
```

常見 friction 不要全部用放寬 sandbox 解決：

| 症狀 | 先做什麼 | 不要做什麼 |
| --- | --- | --- |
| 寫入 RAW/DLL storage、桌面資料夾、`$CODEX_HOME` / `%USERPROFILE%\.codex` 失敗 | 把 output / cache / sidecar 改到目前 worktree 的 `output/...` 或 sandbox-provided temp dir；若真的要改全域 Codex 設定，先明確取得使用者要求 | 不要切 `danger-full-access` 或擴大 writable root 只為了省一次路徑修正 |
| package install、`npx`、下載文件、GitHub/網路失敗 | 確認這是任務必要條件後，用 narrow approval / prefix；成功後記錄穩定命令或替代離線路徑 | 不要把 network access 永久打開當預設 |
| pytest / Python 產生 cache 被擋 | 在 worktree 內跑；必要時加 `$env:PYTHONDONTWRITEBYTECODE='1'` 和 `-p no:cacheprovider`；verification run 可 workspace-write 只為 verification side effects，驗證後要回報 `git status` 是否只剩預期 side effects | 不要讓 verification run 改 source |
| pytest 回 `no tests ran` 或 `not found` | 先用 `rg -n "<test_name>|def test_" tests\...` 找實際名稱；必要時跑 `.venv\Scripts\python.exe -m pytest --collect-only -q <test-file>` 後再下精準 node id | 不要把 `no tests ran` 當成測試失敗或通過；不要猜 node id 一直重跑 |
| RAW / DLL 讀不到 | 先跑本檔 Preflight 的 `Test-Path` 與 Python runner check；RAW 命令用 `.venv\Scripts\python.exe` | 不要用 bare `python` 重試 RAW，也不要掃 sibling directory 猜路徑 |
| PowerShell 語法錯誤 | 改成 PowerShell 語法；多行命令用 backtick；inline Python 用 `.venv\Scripts\python.exe -c` 或 PowerShell here-string pipe 到同一 worktree interpreter；不確定時先跑 `scripts.agent_sandbox_doctor` | 不要貼 Bash heredoc、`export`、`&&` |
| 長時間 85RAW 卡住 | 用 foreground command + `--timing-live-output`；先看 heartbeat / timing artifact 再重跑 | 不要背景 `Start-Process` 後回來輪詢 |
| `git add` / `git commit` / `git stash` / `git worktree add` 出現 `.git\index.lock`、ref-lock、`.gitconfig` unreadable 或 permission denied | 先跑 doctor 並確認 worktree；對同一個精確 Git 命令使用 **Approve for me** | 不要用 `git add -A`、`git add --all` 或 `git add .` 擴大 staging scope；不要新增 repo-local permission profile、sandbox override 或 shell-environment workaround；不要把 host permission failure 誤判成程式碼問題 |
| 要續接舊 worktree / 舊 artifact | 先 `git worktree list` 與 `Test-Path` exact artifact；若 worktree/output 不存在，改用 `local_validation_artifacts/` 或明確重建 smoke path | 不要假設上一輪 worktree 還存在；不要把 `.worktrees/<branch>/output/...` 當長期可重用 input |
| 遞迴掃描太慢、掃到 access denied、或 output/build/.worktrees 噪音 | 優先用 `git ls-files`、`rg --files`、targeted `rg`，再讀特定 range | 不要用 blanket `Get-ChildItem -Recurse` 掃整個 repo、`output/`、`.worktrees/`、或 build/cache 目錄 |
| output / workbook overwrite `Permission denied` | 先假設檔案可能被 Excel/browser/previewer 鎖住；改寫到 timestamped 或 suffixed output，並回報原檔可能被鎖 | 不要強刪或反覆覆蓋；不要把 permission denied 當成 pipeline 邏輯失敗 |

已獲准的 Git metadata mutation 若在 elevated Windows user 下回報 `dubious
ownership`，先確認目前目錄就是目標 worktree，再只對這次 Git invocation 加
exact exception：

```powershell
$xicRepoRoot = (Resolve-Path .).Path.Replace('\', '/')
git -c "safe.directory=$xicRepoRoot" add -- <task-files...>
git -c "safe.directory=$xicRepoRoot" push
```

後續 commit／push 同樣把 `-c "safe.directory=$xicRepoRoot"` 放在 subcommand
前。`git -c` 由 repo execpolicy 的獨立 `prompt` rule 擁有；pre-tool guard 只放行
直接、非 wrapper 的 `git -c ... push` 進入該 prompt，其他 optioned push 仍會
拒絕並要求改寫。明確的 `bash -c`、`pwsh -Command`、`cmd /c`、`wsl`、`env`、
`command`、`exec` wrapper 不進行 payload 解析，一律回
`REWRITE_COMMAND`／hook denial；把 payload 改成目前 shell 的直接命令。
不要寫入 global `safe.directory`，也不要把其他 repo 加進例外。

Repo command policy 只解析 canonical lowercase、單一直接 `git` invocation。
`git.exe`／`git.cmd`／`git.bat`／`git.ps1`／`git.com`／`git.sh`、大小寫或
path-qualified executable alias、compound block、assignment
RHS、redirection、command substitution、malformed quoting 與不支援的 Git
global option 都 fail closed；不要為了繞過這個 gate 再包一層 shell。Quoted Git
文字只有在頂層 `grep`、`findstr`、`Select-String`、`Write-Host`、`Write-Output`、
`echo` 或 `printf` 中視為普通資料；`rg` 的 `--pre` 可啟動外部 command，因此不在
inert allowlist。Outer wrapper executable 即使前面有 assignment、call operator
也 fail closed；普通 simple command 的 argument／path／option value
位置可把 wrapper 文字當資料。Bounded locator 只辨識 outer executable，包括
POSIX assignment prefix 或 initial call operator 後的位置；redirection prefix、
PowerShell assignment RHS、later compound segment、shell control words 與 dynamic
executable expression 都不由 repo wrapper parser 解讀。任何 non-direct
Git-bearing shape 仍 fail closed。Git
subcommand 必須是 canonical lowercase，避免 Windows 執行成功但 exact execpolicy
prefix 沒命中。無法命中 prompt prefix 的 optioned `clone`、`fetch`、
`ls-remote`、`pull`、`push` 都由 hook 與 doctor 一致要求 rewrite。任意 Python、
eval 或其他程式內部動態啟動 subprocess 不由 repo parser 宣稱覆蓋，仍由 host
sandbox 與 global destructive-action contract 管理。
其他 launcher／control prefix 也不由 repo parser 模擬，仍由相同 host/global
authority 管理。

只有在「失敗模式重複、doctor 或現有 preflight 無法降低風險、且人工 approval
本身變成主要摩擦」時，才考慮新增 project-local execpolicy 或 hook。新增前
必須回答：

1. 這個規則是否只允許一個可預期、可重複的安全命令形狀？
2. 它是否會允許任意 Python / shell / deletion / network access？
3. 它是否比把 output 改到 worktree、固定 runner、或更新本檔更好？
4. 它是否有 smoke check，且不會把使用者真正該審的決策藏起來？

Repo 不以 `.codex/config.toml` 定義 permission profile 或 Git global-config
isolation。`.codex/rules/xic-safety.rules` 只保留 destructive／external action 的
block 或 prompt；所有額外 permission 由 host **Approve for me** 處理。

## Validation Tiers

Validation datasets are lane- and method-specific. 8RAW and 85RAW are reusable
fixtures only when the active goal, product owner, or validation packet names
them. They are not universal Discovery gates; for example, Pt pattern validation
uses Pt-method real samples, trace/co-elution evidence, and manual oracle review
instead of inheriting CID-NL 8RAW/85RAW gates by default.

| Tier | 使用時機 | 常用資料 | Gate 意義 |
| --- | --- | --- | --- |
| synthetic/unit tests | code behavior、schema、CLI parsing | none | regression only，不代表 production readiness |
| manual-2raw | resolver calibration 對 manual truth | manual 2RAW truth data | manual truth / calibration evidence |
| 8RAW | lane-specified smoke、parity、benchmark gate，先於昂貴的大規模 run | validation subset 或當前 spec 指定的 8RAW artifacts | 只有該 lane 的 gate docs 通過後，才可說 `shadow_ready` 或 `production_candidate` |
| 85RAW | lane-specified full tissue validation | 85RAW tissue root | 昂貴 gate；只有該 lane 明確要求且前置 gate 通過後才跑 |

如果某 lane 明確使用 8RAW -> 85RAW gate，而 8RAW gate 是 `inconclusive`，
先讀其 JSON/TSV status 並關閉 named blocker，再進下一層。

## Common Command Shapes

### No-RAW Tests

```powershell
.venv\Scripts\python.exe -m pytest <narrow-test-shard> -q
```

### Untargeted CID-NL Bridge

```powershell
.venv\Scripts\python.exe -m scripts.run_untargeted_cid_nl_real_data --help
```

The source Discovery batch index does not currently own a settings sidecar.
Therefore the M7 subject and any non-default Discovery/peak settings must be
passed exactly as used by the source run. Start with one known-support sample;
only widen the sample count after that canary succeeds. This bridge remains
`diagnostic_only` while Backfill acceptance is pending.

### ModeTrack Diagnostic

```powershell
.venv\Scripts\python.exe -m scripts.run_untargeted_cid_nl_mode_track --help
```

Run `--preflight-only` before opening RAW files. This opt-in runner is a
`diagnostic_only` review path, not normal Alignment or a productization lane;
its publication receipt proves byte binding, not authenticity or product
authority.

### Provisional Matrix

```powershell
.venv\Scripts\python.exe -m scripts.run_untargeted_provisional_matrix --help
```

Run `--preflight-only` before opening RAW files. Its rows remain provisional and
Backfill values remain diagnostic candidates; this runner does not replace the
default Matrix contract or grant ProductWriter authority. A copied bundle proves
only the bytes and structure validated by its manifest.

### Alignment And Handoff

```powershell
.venv\Scripts\python.exe -m scripts.run_alignment --help
```

Use `docs/product/alignment.md` for output and handoff semantics,
`docs/product/presets.md` for preset compatibility, and
`tools/diagnostics/INDEX.md` for retired Standard Peak history. The reusable
85RAW launch, preflight, heartbeat, and profiling shape lives only in
[85RAW Command Contract](#85raw-command-contract). Current CLI help owns the
flag inventory; task-specific paths and accepted parameter values belong to the
active issue or validation owner.

## 85RAW Command Contract

Historical runtime and acceptance receipts belong in `docs/diagnostic-ledger.md`
and checker-consumed fixtures. This operating contract owns only the reusable
launch shape below; it does not copy elapsed-time snapshots or retired
worktree procedures.

Do not treat `--performance-profile validation-fast` alone as the full 85RAW
validation profile. It sets RAW worker count and XIC batch size, and new 85RAW
commands should pin `--raw-workers 11` explicitly for this machine. The formal
85RAW shape also needs output thinning, production-equivalent backfill,
audit-evidence cutoff, super-window, and heartbeat output.

Canonical launch shape:

```powershell
.venv\Scripts\python.exe -m scripts.run_alignment `
  --discovery-batch-index <current-spec-discovery-batch-index.csv> `
  --raw-dir $env:XIC_RAW_ROOT `
  --dll-dir $env:THERMO_RAWFILE_READER_DLL_DIR `
  --output-dir <task-specific-output-dir> `
  --sample-info <sample-info.csv> `
  --targeted-istd-workbook <targeted-istd-workbook.xlsx> `
  --expected-sample-count 85 `
  --output-level validation-minimal `
  --backfill-scope production-equivalent `
  --audit-evidence-mode none `
  --performance-profile validation-fast `
  --raw-workers 11 `
  --owner-backfill-window-strategy super-window `
  --owner-backfill-superwindow-span-factor 2 `
  --timing-output <task-specific-output-dir>\timing.json `
  --timing-live-output <task-specific-output-dir>\timing.live.json
```

Run the same command with `--preflight-only` before the first RAW launch.

`--timing-live-output` is the heartbeat artifact for timeout-prone 85RAW runs.
The versioned `timing.live.v2` artifact contains bounded `recent_records` and
`recent_measurement_observations`, plus total/dropped counters and the latest
stage/sample heartbeat. It does not expose a truncated legacy `records` field or
rewrite the complete timing history for every event. `timing.json` retains the
complete final history. If the process is killed by an external timeout, use the
live file to identify the last completed stage/sample before rerunning anything.
Each live update uses a temporary sibling and atomic replacement, so a failed
requested update raises without corrupting the last-good heartbeat artifact.

W0 diagnostic timing is additive and remains `diagnostic_only`. The final timing
payload records `measurement_contract` (cache state, aggregation, task-specific
repetition/noise design, and programme budgets) and mechanical
`measurement_observations`. A disabled recorder performs no implicit heartbeat,
observation, contract, or live-sidecar work. Parallel Discovery reports partial
pickle payload-size estimates only for successfully submitted requests and
observed results; executor framing and confirmed transport bytes remain outside
the current owner. Other workflow IPC and RSS without a reliable owner remain
`unavailable` or `partial` with a reason, never measured zero. Nested spans are
reported as inclusive nested locality separately from the exclusive wall
attribution that counts every wall span once. Measurement summaries execute the
declared `sum`, `max`, or `last` aggregation and reject mixed unit/aggregation
contracts for one metric scope. Instrumented fingerprint attempts emit exactly
one measured or failed observation; failed stable-file checks retain bytes read
when known.
The runner records
cache state as `uncontrolled_os_cache_no_eviction`: it never claims cold-cache
evidence because the validation contract does not flush the host OS cache. Paired
repetitions should reverse baseline/candidate order when practical. The current programme
decision uses a first 85RAW milestone of 2700 seconds with at least 25% paired
comparable improvement, followed by 1800 seconds; these are not XIC-wide rules.

Do not launch 85RAW from the Codex shell with `Start-Process` / background mode
and then let the shell command return. In this environment, background launch
attempts have repeatedly exited or been cleaned up without completing, sometimes
with empty stdout/stderr. The verified 85RAW acceptance run used a foreground
process with heartbeat sidecars. If background execution is required, it must be
an explicitly user-approved external terminal / automation, and the heartbeat
artifact must still be polled.

For micro-profiling, start with 8RAW or a scoped 85RAW diagnostic:

```powershell
.venv\Scripts\python.exe -m scripts.run_alignment `
  --discovery-batch-index <current-spec-discovery-batch-index.csv> `
  --raw-dir $env:XIC_RAW_ROOT `
  --dll-dir $env:THERMO_RAWFILE_READER_DLL_DIR `
  --output-dir <task-specific-output-dir> `
  --profile cprofile `
  --profile-output-dir <task-specific-output-dir>\profile
```

`--profile cprofile` writes `profile.prof` and `profile_top.txt` only when the
Python process exits cleanly. For a likely timeout, rely on
`--timing-live-output` first.

Targeted extraction / workbook harness details live in
[`docs/validation/harness.md`](../validation/harness.md). That harness is not the
canonical 85RAW alignment acceptance runner unless it is explicitly updated to
emit the same minimal machine contract and heartbeat shape documented here.

## Syntax Anti-patterns

- This repo is normally operated from PowerShell. Do not paste Bash heredocs such
  as `python - <<'PY'`; PowerShell treats `<` as redirection and fails before
  Python starts. Use `.venv\Scripts\python.exe -c "..."`, a short PowerShell
  loop, or a temporary checked-in helper when the logic is worth keeping.
- For multiline PowerShell commands, use backtick continuations exactly as shown
  in this file, or split the command into separate lines in the shell. Do not use
  `&&` or Unix-style `export`.
- For Markdown edits, run a cheap fence check before finishing when code blocks
  were added or removed.
