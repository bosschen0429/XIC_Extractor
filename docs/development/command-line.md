# 從原始碼執行與命令列

本頁供開發、命令列操作及驗證使用；Windows 應用程式使用者請從[使用說明](../user/README.md)開始。

## Source Setup

依[開發者入口](README.md#從原始碼執行)安裝與啟動。開發環境、RAW 路徑與驗證尺度依 [Local Execution](../agent/local-execution.md)。

## Reader CLI

從 source checkout 手動安裝時，先執行 `uv run xic-install-thermo-reader --help`，
閱讀其中連結的 Thermo 授權，再使用 `--accept-thermo-license`。
預設資料夾是 `%LOCALAPPDATA%\XIC_Extractor\RawFileReader-8.0.42`；
`--destination` 可選另一個位置。CLI 安裝保留既有工作區設定，安裝後需在工作區選取該資料夾。
工作區內的安裝按鈕則會一併設定 reader 路徑。

啟動參數見 `uv run xic-workbench --help`。明確的 `--dll-dir` 或
`THERMO_RAWFILE_READER_DLL_DIR` 會影響下一次啟動；安裝 reader 不會修改外部覆寫值。


## Targeted CLI

Developer checkout:

```powershell
uv run python -m scripts.run_extraction --base-dir .
```

Installed entry point:

```powershell
uv run xic-extractor-cli --base-dir .
```

Useful overrides:

| Argument | Meaning |
| --- | --- |
| `--base-dir` | Directory containing `config/` and `output/` |
| `--data-dir` | One-run RAW directory override |
| `--targets` | Target-list XLSX (`Targets`, plus Delta Mass sheets when used) |
| `--injection-order-source` | Explicit injection order for this batch |

Execution scheduling is automatic; actual workers are recorded in run metadata.


## Targeted Validation

Result review is described [Targeted result review](../user/targeted.md#看結果). Operator validation uses
`.venv\Scripts\python.exe tools\diagnostics\targeted_validation.py --help` for suite
selection, dry-run and baseline comparison through the finalized Targeted
application. Manual-2RAW calibration separately compares acquisition RT/area
against the supplied manual measurements. For developer environment and
RAW-run constraints, see [Local Execution](../agent/local-execution.md).
Regression checks do not establish method-wide scientific readiness.


## Untargeted CLI Workflows

[CLI workflow map](../user/assets/untargeted-modes.svg)

| Mode | Use when | Input |
| --- | --- | --- |
| `full` | You want discovery plus cross-sample alignment | RAW directory |
| `discovery_only` | You want to inspect candidates from one RAW file | Single RAW file |
| `align_only` | Discovery was already run and you want to align existing outputs | RAW directory plus `discovery.sqlite` |

These partial workflows are CLI uses. The GUI always runs the complete analysis
from a RAW directory, including Backfill; it has no mode selector.


## DDA Exclusion List CLI

從空白針產生 Targeted Mass Exclusion 清單：先建立 recipe，填入空白 RAW、各方法載入的
inclusion list、要排除的已知物質，以及每份清單的 scan range、exclusion 容差與是否帶 RT，再執行：

```powershell
uv run xic-exclusion-list --new recipe.toml
```

```powershell
uv run xic-exclusion-list recipe.toml
```

每個 `[[lists]]` 在 recipe 旁產生一份可匯入的 `<name>.csv`，另有 `exclusion_report.txt`
列出每一列因 target 被移除或切開的原因。規則見 [DDA exclusion lists](../product/dda-exclusion-list.md)。
若輸入檔名或 target 的萬用字元會涵蓋輸出檔，請用 `--output-dir results` 將結果放到不同目錄。


## Alignment CLI Inputs

The Alignment CLI resolves ISTD input before RAW acquisition. An explicit
targets CSV or reference workbook takes precedence; otherwise it uses the
[bundled target template](../../xic_extractor/configuration/data/targets.xlsx) and prints the
resolved source and ISTD count. Missing or invalid input fails before RAW work.
An intentional uncorrected CLI run requires `--allow-uncorrected`, which cannot
be combined with either ISTD input. The Python API retains its explicit optional
input contract.

Sample metadata is supplied to Alignment separately from Discovery; see
[Alignment sample input](../product/sample-metadata-qc.md#alignment-input).
Use current CLI help for the complete option list.


## Saved Methods With CLI

Pass the same TOML to both stages:

```powershell
.venv/Scripts/python.exe -m scripts.run_discovery --raw-dir RAW_DIR --dll-dir DLL_DIR --preset method.toml --output-dir output/discovery
.venv/Scripts/python.exe -m scripts.run_alignment --discovery output/discovery/discovery.sqlite --raw-dir RAW_DIR --dll-dir DLL_DIR --preset method.toml --output-dir output/alignment --output-level production
```

`discovery.sqlite` is the whole Discovery result and can be moved on its own.
It records each sample's RAW file and acquisition identity; Alignment refuses a
RAW that changed after Discovery, and a file from an older Discovery must be
regenerated.

Discovery tuning flags override only explicitly supplied values. `--preset`
cannot be combined with separate tag selectors such as `--feature-list` or
`--neutral-loss-da`; edit the method file to change its tags.

---


## Targeted Method Input

CLI uses the same method admission and execution:

```powershell
.venv\Scripts\python.exe -m scripts.run_extraction --targets C:\lists\my-targets.xlsx
```
