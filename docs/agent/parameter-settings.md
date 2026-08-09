# Agent Parameter Settings

本檔只記錄 XIC Extractor 常用、穩定、跨任務會重複踩到的本機設定。任務特定
artifact，例如某個 Phase 的 benchmark summary、一次性 gate output、plot/report，
不放在這裡；請查當前 spec、plan、validation note 或本輪 `output/` index。
跨 worktree 會重複使用的 accepted validation inputs 例外：必須放在
`local_validation_artifacts/`，不能依賴某個 `.worktrees/<branch>/output/`。

這份文件是 operational memory，不是一次性備忘錄。當某個 RAW / validation
命令實際跑通，或某個啟動方式反覆失敗，應把穩定參數形狀與教訓更新到本檔。
不要只把教訓留在單次 conversation 或 phase note。

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
| accepted P8b 8RAW discovery input | `local_validation_artifacts/discovery/accepted_p8b/8raw/discovery_batch_index.csv` | Cross-worktree reusable validation input；內部 `candidate_csv` / `review_csv` 已重寫到同一 artifact store。 |
| accepted P8b 85RAW discovery input | `local_validation_artifacts/discovery/accepted_p8b/85raw/discovery_batch_index.csv` | Cross-worktree reusable validation input；正式 85RAW 前仍需 `--expected-sample-count 85` preflight。 |
| targeted GT 8RAW default workbook | `local_validation_artifacts/targeted_gt_workbooks/8raw/xic_results_20260512_1151.xlsx` | SHA256 `788892188C8419C82DC4618C98E160B90AC6C44C38676C53609248AA529889F7`；`targeted_gt_alignment_audit.py` 的 8RAW default positive checkpoint workbook；不要用 85-sample workbook 對 8RAW alignment，否則會產生 off-scope `MISS`。 |

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
不要為了「看看結果」直接開 85RAW。先說明 blocker，修正或重訂 gate 後再進
下一層。

## Common Command Shapes

No-RAW tests:

```powershell
.venv\Scripts\python.exe -m pytest <narrow-test-shard> -q
```

Untargeted CID-NL M7 real-data bridge, one known-SUPPORT RAW canary:

```powershell
.venv\Scripts\python.exe -m scripts.run_untargeted_cid_nl_real_data `
  --discovery-batch-index <current-discovery-batch-index.csv> `
  --raw-dir $env:XIC_RAW_VALIDATION_DIR `
  --sample-metadata <sample-metadata.tsv> `
  --dll-dir $env:THERMO_RAWFILE_READER_DLL_DIR `
  --output-dir <new-task-specific-output-dir> `
  --manifest-root . `
  --sample-stem <known-support-sample-stem> `
  --expected-sample-count 1 `
  --precursor-mz <untargeted-subject-precursor-mz> `
  --timing-output <new-task-specific-output-dir>\timing.json `
  --timing-live-output <new-task-specific-output-dir>\timing.live.json
```

The source Discovery batch index does not currently own a settings sidecar.
Therefore the M7 subject and any non-default Discovery/peak settings must be
passed exactly as used by the source run; do not tune them from M7 output.
Historical accepted P8b CSVs may support no-RAW compatibility checks, but they
predate the current candidate/provenance contract and must not be used as the
real-RAW M7 authority. After the 1RAW canary succeeds, remove `--sample-stem`,
change the guard to
`--expected-sample-count 8`, and use a new output directory for the 8RAW smoke.
The runner writes `m7_summary.json`, `runtime.json`, the immutable source
artifact, and a pending production-acceptance manifest. It writes a QMV
automatically only when no Backfill acceptance row is pending; otherwise the
run remains `diagnostic_only` until a supplied acceptance manifest passes the
existing M6 gate.

Opt-in RAW-to-ModeTrack table runner for the reviewed #180 85-sample lane:

```powershell
.venv\Scripts\python.exe -m scripts.run_untargeted_cid_nl_mode_track `
  --raw-dir $env:XIC_RAW_ROOT `
  --dll-dir $env:THERMO_RAWFILE_READER_DLL_DIR `
  --sample-metadata docs\superpowers\fixtures\issue180_m9_gate_c_sample_metadata_v1.tsv `
  --preset dna_dr `
  --precursor-mz 349.215 `
  --review-bundle-dir docs\superpowers\validation\issue180_m9_gate_c_closed_writer_v3\review_transfer `
  --output-dir output\issue159-mode-track-table `
  --manifest-root . `
  --expected-sample-count 85 `
  --expected-backfill-cell-count 26 `
  --expected-backfill-family-count FAM000004=10 `
  --expected-backfill-family-count FAM000007=11 `
  --expected-backfill-family-count FAM000009=5 `
  --sample-identity-namespace issue180-m9-gate-c
```

Append `--preflight-only` to close the RAW/DLL/metadata/review/output universe
and validate the review/replay SHA, schema, 26-cell census, and 10/11/5 family
distribution without opening RAW files. Remove it only after the RAW run is
explicitly authorized.

This command is `diagnostic_only`. It runs Discovery, the current Untargeted
CID-NL RAW owner, ModeTrack bootstrap/alignment, conservative Backfill, and the
existing acceptance/QMV owner in one process. The review bundle supplies the
already-reviewed physical observations; its old checked manifest is not an
input. Fresh typed values remain authoritative in memory. The attempt directory
retains Discovery/runtime/source audit artifacts, while the requested output
directory appears atomically only after both `alignment_matrix.tsv` and
`alignment_matrix.xlsx` are complete. The published bundle also contains
`publication_receipt.json`. This compact byte-bound receipt records the exact
manual-review and observation-replay digests, its reduced
`owner_validated_physical_subject_binding_only` claim, and byte bindings for the
published tables. It does not grant authenticity or product authority after a
caller re-seals edited bytes. A failed stage leaves the requested final output
directory absent.

Full-universe provisional Matrix runner:

```powershell
.venv\Scripts\python.exe -m scripts.run_untargeted_provisional_matrix `
  --raw-dir $env:XIC_RAW_ROOT `
  --dll-dir $env:THERMO_RAWFILE_READER_DLL_DIR `
  --sample-metadata docs\superpowers\fixtures\issue180_m9_gate_c_sample_metadata_v1.tsv `
  --preset dna_dr `
  --output-dir output\issue159-full-universe-provisional-matrix `
  --manifest-root . `
  --expected-sample-count 85 `
  --raw-workers 11 `
  --raw-xic-batch-size 64 `
  --owner-build-xic-backend raw-super-window `
  --owner-backfill-window-strategy super-window `
  --owner-backfill-superwindow-span-factor 2
```

Append `--preflight-only` before the first RAW launch. This runner has no
precursor, family allowlist, or reviewed-cell input. It sends the complete fresh
Discovery batch through the existing cross-sample group and `full-audit`
Backfill owner. Its primary outputs are
`provisional_alignment_matrix.tsv`, `provisional_row_identity.tsv`,
`provisional_cell_provenance.tsv`, and `provisional_matrix_summary.json` under
the run's `workflow/alignment/` directory. Rows remain explicitly
`row_identity_status=provisional` and `identity_settlement_status=not_run`;
Backfill values are diagnostic candidates, not ProductWriter authority. The
runner does not modify or replace the default `alignment_matrix.tsv` contract.
Duplicate or later-mergeable provisional rows are an accepted limitation of
this 60-percent closure, not evidence that final chemical identity is settled.
The final directory also retains `inputs/sample_metadata.<source-suffix>` and a
byte-/structure-bound `run_manifest.json`. The retained metadata preserves the
source `.csv`, `.tsv`, or `.txt` suffix. The runner derives the revision from
its clean executing checkout. `--code-revision <sha>` is an optional equality
assertion, not the source of the recorded revision. The manifest records the
preset, creation-time owner-validated Discovery and Alignment settings, and the
complete typed runner configuration. The runtime record includes the fixed
`validation-minimal` output level, disabled audit-evidence mode, an empty
selected-family source, and retained-metadata sample order. Publication-envelope
schema v2 also records the Discovery receipt census and artifact schema versions.
It binds all four provisional artifacts by SHA-256 and byte size. The persisted
validator checks those bytes and structural invariants before atomic publish. It
does not reload the current preset, replay the Discovery owner, or parse the four
artifacts into domain owners, prove their cross-artifact semantics, or grant
authenticity, semantic replay, or product authority after caller re-sealing. A
copied bundle can support byte/structure comparison only; a stronger claim
requires a separately activated owner consumer.

For this runner, atomic publication is a **closed single-writer visibility
contract**. The publication owner and its in-process collaborators stop writing
the attempt directory before manifest binding; the final directory then appears
with one rename. This is not an OS-level seal against another same-permission
process changing staging during the last validation-to-rename interval. Such a
threat model requires a separate storage/lifecycle design. Visible bundles remain
untrusted projections, and every consumer that claims byte binding must run the
independent manifest validator against the bytes it opens.

`--raw-workers` is one shared machine budget for process-parallel Discovery and
the existing Alignment process stages. Discovery workers receive only pickleable
paths/settings/identity values, open and dispose one RAW handle inside the spawned
process, and write one sample-owned artifact directory. The parent writes the
batch index in retained sample-metadata order, independent of worker completion
order. A terminal worker failure stops new submission, cancels pending futures,
and reports the first owner failure plus every observed failed/completed job.
The mechanical census partitions completed, failed, cancelled, in-flight, and
unsubmitted ordinals. Cancellation does not become a scientific missing state.
Set `--raw-workers 1` to use the serial
rollback path. Fused Discovery + owner construction is not enabled by this
contract.

Validation / downstream handoff alignment should use the minimal machine gate
surface by default. For 85RAW validation, the current canonical shape is
`validation-minimal + production-equivalent + validation-fast + super-window +
timing heartbeat`:

```powershell
.venv\Scripts\python.exe -m scripts.run_alignment `
  --discovery-batch-index <current-spec-discovery-batch-index.csv> `
  --raw-dir $env:XIC_RAW_ROOT `
  --dll-dir $env:THERMO_RAWFILE_READER_DLL_DIR `
  --output-dir <task-specific-output-dir> `
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

`validation-minimal` writes the primary machine gate surface:

- `alignment_matrix.tsv`: downstream correction / statistics handoff.
- `alignment_review.tsv`: targeted benchmark and decision diagnostics.
- `alignment_matrix_identity.tsv`: matrix row/cell identity sidecar.
- `alignment_backfill_cell_evidence.tsv`: compact cell-level ledger for
  backfill evidence chain, gallery, and overlay review queues.

It does not write `.xlsx`, HTML, full `alignment_cells.tsv`, owner-edge,
status-matrix, event-owner, or ambiguous-owner debug outputs. Use `debug`,
`validation`, or `--emit-alignment-cells` only when a human review surface or
deep debug artifact explicitly needs the full 80-column cell ledger. In `auto`
audit mode, `validation-minimal` resolves to no heavy audit evidence unless an
explicit integration audit destination is requested. Some lightweight sidecars, such as
`skipped_evidence_ledger.tsv` and `alignment_run_metadata.json`, may still be
emitted when backfill scope needs machine-readable skip provenance.

Backfill reconciliation gallery delivery has one extra lightweight requirement:
add `--emit-alignment-backfill-seed-audit` to emit
`alignment_owner_backfill_seed_audit.tsv`. That file provides seed-specific
provenance for retained-gate review queues and overlay joins, but it does not
force the full `alignment_cells.tsv` or all-candidate audit. Use
`--emit-alignment-backfill-candidate-audit` only for deep owner-backfill debug;
it is not required for normal seed-specific or family overlay galleries.

For overlay rendering, first run retained backfill evidence gate and render only
its `alignment_retained_backfill_overlay_review_queue.tsv`. Use
`family_ms1_overlay_batch.py --no-pdf --reuse-existing` and chunk large queues
with `--start-rank` / `--limit` when needed. The batch renderer reuses completed
PNG/trace bundles and extracts RAW traces in sample-batched mode, so each chunk
opens each sample RAW at most once instead of reopening RAW per family. Within
each sample, overlapping scan windows are grouped into bounded super-windows and
cropped back to the original request windows; the batch summary JSON records RAW
opens, XIC requests, exact scan windows, super-window groups, chromatogram calls,
and trace point counts. Preset/in-process callers write the final batch outputs
once, while the standalone CLI keeps incremental summary rewrites for resumable
manual rendering. For product publication that needs compact RAW-backed evidence
but not images, use `family_ms1_overlay_batch.py --evidence-only`; this writes
trace TSV/JSON and summary rows with blank PNG/PDF fields.

For DNA dR production-style runs, prefer the preset surface instead of manually
stitching the retained gate, machine pipeline, chunk consolidation, and final
matrix publication:

```powershell
.venv\Scripts\python.exe -m scripts.run_alignment `
  --preset dna_dr `
  --discovery-batch-index <current-spec-discovery-batch-index.csv> `
  --raw-dir $env:XIC_RAW_ROOT `
  --dll-dir $env:THERMO_RAWFILE_READER_DLL_DIR `
  --output-dir <task-specific-output-dir> `
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

Use `--preset dna_dr_product_ready` for the current-run safe DNA dR product
path. It runs the same base alignment and standard-peak publication path as
`dna_dr`, but it must not depend on the fixed 85RAW-derived 84-cell Backfill
expansion activation packet. That retained packet is an authority replay /
regression artifact only; invoke the Backfill expansion clean-target selective
wrapper or an explicit custom preset only when the current alignment matrix
sample universe matches the packet expected-diff sample universe.

`--preset dna_dr` loads `xic_extractor.presets.data\dna_dr.toml`; it also stops
after the standard-peak publication runner. Both built-in presets force the
lightweight seed audit and use
`alignment_backfill_cell_evidence.tsv` on `validation-minimal`; non-minimal
output levels may still emit `alignment_cells.tsv` for debug/deep-audit review.
The built-in `dna_dr` preset defaults to
`standard_peak_backfill_publication_mode = "matrix-only"`: it extracts compact
trace/evidence artifacts, runs the existing authority/projection/product
activation chain, publishes accepted values back into `alignment_matrix.tsv`, and
does not render full PNG/gallery evidence. The shift-aware stage still writes the
machine-readable best-shift summary TSVs required by the calibration pack and
standard-peak gate, but review PNGs are reserved for gallery modes. Use
`--standard-peak-backfill-publication-mode deep-audit` to preserve the legacy
full overlay/gallery behavior. `review-gallery` keeps RAW overlay evidence
compact, but renders shift-aware review evidence and the activation-synced HTML
review surface without changing the standard-peak acceptance policy.
Non-standard peaks remain outside this preset's automatic publication policy.
For repeated method-development reruns, `--standard-peak-evidence-cache-dir`
can point at a cache seeded from matching overlay evidence; this is an opt-in
accelerator for the standard-peak matrix-only overlay evidence path, not a
default one-shot production setting. Cache hits require the current per-sample
RAW identity to match the cached trace provenance; missing or mismatched RAW
identity falls back to normal RAW extraction instead of reusing stale evidence.

When `--timing-output` or `--timing-live-output` is supplied, timing spans include
the base alignment plus the post-alignment preset stages. Backfill expansion
productization is included only for an explicit clean-target replay/custom
preset, not for the built-in sample-universe-safe `dna_dr_product_ready` path.
Use this shape for HEARTBEAT monitoring; older artifacts may only contain the base `pipeline:
alignment` timing and therefore under-report preset-tail bottlenecks.
Timing JSON now keeps the raw `records` list and also emits derived
`summaries.stage_summary` and `summaries.raw_xic_locality_summary` sections.
Use those derived summaries for no-RAW bottleneck triage before changing RAW
batching/cache code: `raw_xic_locality_summary` reports stage-level
`extract_xic_count`, `extract_xic_batch_count`, `raw_chromatogram_call_count`,
`point_count`, and per-XIC/per-batch ratios without re-reading RAW files.

The built-in `dna_dr_product_ready` alignment preset automatically runs the
current-run publication checker before returning success. To re-check an
existing output directory manually, run:

```powershell
.venv\Scripts\python.exe -m scripts.check_product_ready_preset_publication `
  --alignment-dir <task-specific-output-dir>
```

This checker reads `standard_peak_backfill_preset_summary.json` and
`standard_peak_default_matrix_manifest.json`, confirms manifest coverage and
published matrix paths, and fails if `backfill_expansion_productization_preset`
exists in the built-in run output. It is a verifier only: it writes compact
checks/summary sidecars and does not read RAW, change matrix values, or grant
Backfill expansion replay authority.

Before starting 85RAW, verify the batch index actually contains 85 samples. Do
not reuse the historical 8RAW index by path similarity. Prefer the CLI preflight
guard because it uses the same discovery-batch parser, checks candidate CSV
existence and RAW path existence, and prints the launch contract without loading
candidate CSV rows or opening RAW files:

```powershell
.venv\Scripts\python.exe -m scripts.run_alignment `
  --discovery-batch-index <current-spec-discovery-batch-index.csv> `
  --raw-dir $env:XIC_RAW_ROOT `
  --dll-dir $env:THERMO_RAWFILE_READER_DLL_DIR `
  --output-dir <task-specific-output-dir> `
  --expected-sample-count 85 `
  --output-level validation-minimal `
  --backfill-scope production-equivalent `
  --audit-evidence-mode none `
  --performance-profile validation-fast `
  --raw-workers 11 `
  --owner-backfill-window-strategy super-window `
  --owner-backfill-superwindow-span-factor 2 `
  --timing-output <task-specific-output-dir>\timing.json `
  --timing-live-output <task-specific-output-dir>\timing.live.json `
  --preflight-only
```

When `--expected-sample-count 85` is present, `scripts.run_alignment` also
enforces the canonical 85RAW launch contract: `validation-minimal`,
`production-equivalent`, `audit-evidence-mode none`, `validation-fast`,
`super-window`, timing JSON + live heartbeat, and a Python executable under this
worktree's `.venv`.

Manual fallback:

```powershell
(Import-Csv <current-spec-discovery-batch-index.csv>).Count
```

If this prints `8`, stop and locate the current 85RAW discovery artifact from
the active spec or validation note before running alignment.

## Validated Command Profiles

| Profile | Status | Reusable parameters | Evidence |
| --- | --- | --- | --- |
| 85RAW validation-minimal super-window | Verified foreground run, exit code `0`, wall-clock `620.9 s` | `.venv\Scripts\python.exe`, 85RAW RAW root, `--output-level validation-minimal`, `--backfill-scope production-equivalent`, `--audit-evidence-mode none`, `--performance-profile validation-fast`, `--raw-workers 11`, `--owner-backfill-window-strategy super-window`, `--owner-backfill-superwindow-span-factor 2`, timing output + live heartbeat | `docs\diagnostic-ledger.md` section "2026-05-26 P8b 85RAW Super-Window Acceptance" |
| 85RAW primary-delivery validation | Verified historical foreground run, exit code `0`, wall-clock `596.6 s`; that run used the now-retired worktree `.venv` junction workflow, which must not be reproduced | same canonical 85RAW shape plus `--expected-sample-count 85`; current runs use the per-worktree bootstrap above; output hashes and diagnostics fixed in durable fixtures | `docs\diagnostic-ledger.md` section "2026-05-28 Qualitative Selection / Owner-Backfill Scan Support Gate"; `docs\superpowers\fixtures\diagnostic_ledger_2026_05_28\85raw_primary_delivery_fix_summary.tsv` |

The cited `620.9 s` run predates the `--expected-sample-count 85` guard. The
guard is a later test-covered launch safety check and should be included in new
85RAW commands, but it is not part of that historical runtime evidence.

Do not treat `--performance-profile validation-fast` alone as the full 85RAW
validation profile. It sets RAW worker count and XIC batch size, and new 85RAW
commands should pin `--raw-workers 11` explicitly for this machine. The formal
85RAW shape also needs output thinning, production-equivalent backfill,
audit-evidence cutoff, super-window, and heartbeat output.

RAW / alignment commands:

```powershell
.venv\Scripts\python.exe -m scripts.run_alignment `
  --discovery-batch-index <current-spec-discovery-batch-index.csv> `
  --raw-dir $env:XIC_RAW_ROOT `
  --dll-dir $env:THERMO_RAWFILE_READER_DLL_DIR `
  --output-dir <task-specific-output-dir>
```

Long RAW alignment runs should be observable while they are running:

```powershell
.venv\Scripts\python.exe -m scripts.run_alignment `
  --discovery-batch-index <current-spec-discovery-batch-index.csv> `
  --raw-dir $env:XIC_RAW_ROOT `
  --dll-dir $env:THERMO_RAWFILE_READER_DLL_DIR `
  --output-dir <task-specific-output-dir> `
  --timing-output <task-specific-output-dir>\timing.json `
  --timing-live-output <task-specific-output-dir>\timing.live.json
```

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

## Agent Rules

1. 所有 project tests（包含 no-RAW pytest）都用目前 worktree 的
   `.venv\Scripts\python.exe -m pytest`；RAW/DLL 命令同樣使用該 worktree
   interpreter，並另外遵守本頁的 RAW preflight。Bare `python` 只保留給
   documented bootstrap driver 或 launcher-identity check。
2. 選 RAW / DLL 目錄前，先查本檔並 `Test-Path`。不要先掃 filesystem。
3. 已列在 `Approval-First Commands` 的命令不要先在 sandbox 裡撞一次；
   任務必要時直接用 narrow approval 跑固定命令，否則改用既有 artifact 或
   離線替代。
4. RAW / DLL 命令若因 sandbox、DLL loading、或 external executable spawn
   被擋，提權重跑同一條 `.venv\Scripts\python.exe ...` 命令；不要換成
   bare `python`、不要改 RAW/DLL path、不要用 no-RAW pytest 替代。
5. phase-specific artifacts 不要寫進本檔；查當前 spec、plan、validation note
   或 task output index。
6. `.mzML` 不是 production input。除非使用者明確要求，否則不要把 mzML 當成
   fallback 或替代資料源。
7. 如果 stable path 缺失，停止並回報缺失路徑與失敗命令。不要靜默換成另一份
   資料。
8. gate 回 non-zero 時，先讀 JSON/TSV status。不要在 status 還沒釐清時進入
   更大的 validation tier。
9. 85RAW 或任何可能超過 30 分鐘的 RAW run 必須加 `--timing-live-output`；
   沒有 heartbeat artifact 就不要宣稱已完成可審計 profiling。
10. validation / downstream handoff 預設用 `--output-level validation-minimal`。
    `.xlsx` 和 HTML 不是正式交付契約；只有明確需要人工檢視或 debug 時才產生。
11. 85RAW 正式驗收預設加 `--performance-profile validation-fast`,
    `--raw-workers 11` 與
    `--owner-backfill-window-strategy super-window`。如果刻意不用，必須在
    validation note 說明原因。
12. 85RAW 開跑前先檢查 discovery index sample count、candidate CSV path、
    RAW path；8RAW index 不可拿來跑 full tissue validation。正式 alignment
    run 用 `--expected-sample-count 85` 固化 sample-count 檢查。
13. 不要把 `.worktrees/<branch>/output/...` 當成 reusable validation input。
    若只有某個 worktree 有 accepted discovery artifact，先移到
    `local_validation_artifacts/` 並重寫 index 內部絕對路徑，再跑 preflight。
14. 不要用 Codex shell 的 background `Start-Process` 跑 85RAW 後就回來輪詢；
    這個模式已反覆失敗。用前景 run 搭配足夠 timeout，或先取得使用者同意
    轉到外部 terminal / automation。
15. 每次長時間 RAW run 後，若發現新的穩定參數或反覆失敗模式，更新本檔；
    不要只把教訓留在聊天紀錄。
16. PowerShell 語法錯誤、ruff E501、Markdown fence mismatch 這類可重複避免
    的錯誤，修完後要把穩定教訓寫回本檔或相關 repo-local contract。
17. 遇到已知 target 或 failure mode，例如 `d3-N6-medA` RT drift / primary
    delivery 問題，先讀 `docs/diagnostic-ledger.md`。不要把 ledger 已經
    解釋過的 RT drift、area mismatch、或 mixed-surface warning 當成新的
    hard blocker；除非 current artifact 與 ledger 明確矛盾。
