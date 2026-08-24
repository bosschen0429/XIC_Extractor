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
| sandbox、approval、shell 或 launcher failure | [Preflight](#preflight) 與 [Syntax Anti-patterns](#syntax-anti-patterns) |

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
