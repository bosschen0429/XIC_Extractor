# Agent Parameter Settings

本檔只擁有 XIC Extractor 穩定的本機 runner、env、preflight 與 RAW launch
contract。任務 output、benchmark 結論與一次性 artifact 留在 active issue、
validation note 或 `output/`；跨 worktree 的 accepted inputs 放在 ignored
`local_validation_artifacts/`，其 current locator 由 diagnostic ledger 擁有。

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

重跑昂貴 validation 前先讀 `docs/diagnostic-ledger.md`；只有 current code、
input 或 artifact freshness 足以改變既有結論時才重跑。

## Python Runner

本機 operator runtime 固定為 Windows Native。每個 worktree 的 project tests、
scripts 與 RAW commands 都使用該 checkout 的
`.venv\Scripts\python.exe`（Python `>=3.11,<3.14`）；bootstrap 驗證 locked dev
dependencies、editable install、`pytest` 與 `pythonnet`。

Bare `python` 只作 bootstrap driver 或 launcher identity probe。每個 worktree
擁有自己的 `.venv`，linked worktrees 只共用 `.uv-cache`；缺少環境或 bootstrap
check 失敗時走 [Preflight](#preflight)，不借用 root repo 或另一個 worktree 的
interpreter。85RAW runner也會拒絕不在目前 worktree `.venv` 下的 executable。
WSL 是 task-specific canary exception，其 runtime packet 由全域 Runtime KB 擁有。

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

Reusable accepted-input paths and hashes live in
[`docs/diagnostic-ledger.md#stable-inputs`](../diagnostic-ledger.md#stable-inputs).
They are ignored local artifacts, so verify the named path and run the owning
CLI with `--preflight-only` before use. Current Alignment requires a v2 index
with owner closure; retired v1 inputs are not a fallback.

標準 DNA dR Alignment run shape 必須成對提供 `--sample-info` 與
`--targeted-istd-workbook`。兩者一起建立 targeted ISTD drift prior，讓
Alignment 使用既有 injection-order／RT evidence path；CLI 仍會拒絕只給其中
一個。這兩個檔案是 lane-owned inputs，路徑依該批資料指定，不在 repo 內
硬編碼；以下 command shape 的 placeholders 必須替換成實際檔案。
`--sample-column-injection-order` 則是獨立的 optional input；它不只是重新排欄，
也會參與 Alignment 的 injection-trajectory rescue，可能改變 family partition
與 Matrix content。

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

Repo-local doctor 驗證 runtime、worktree Python、bootstrap、Git 與 workspace
write，但不讀 RAW。只有 command／sandbox／GitHub boundary 與下一步不明時才加
需要的 probe；current flags 由 CLI help 擁有：

```powershell
.venv\Scripts\python.exe -m scripts.agent_sandbox_doctor --help
```

Follow the reported top-level status and exact next action. Product debugging
starts only after the runtime result is `READY`; command rewrites, approvals,
environment repair, human decisions, and inconclusive probes remain runtime
outcomes rather than product findings.

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

`--timing-live-output` is the bounded heartbeat for timeout-prone runs;
`timing.json` is the complete final history. After an interrupted run, inspect
the live file for the last stage/sample before deciding whether to rerun.
Measurement interpretation and comparability belong to
`xic-performance-diagnosis`, not this launch contract.

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
