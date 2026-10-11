# Targeted Extraction

Use this workflow when you already know the compounds, m/z values, RT windows,
and optional internal-standard pairs you want to review.

![Targeted Extraction workspace](../../assets/screenshots/targeted-workspace.png)

## Workflow

1. Open `XIC_Extractor.exe`, or run `uv run xic-workbench` in a developer
   checkout.
2. Select **Targeted Extraction**.
3. Select a saved method and press **載入方法與目標**, or configure a method
   for the first time. Set the new RAW data directory; the local DLL setting is retained.
4. For a new method, import targets or choose **檢視與編輯** to open the full-height target table.
   **返回批次設定** (`Alt+Left`) preserves edits and returns to the batch summary.
5. Press **開始執行** below the workspace.
6. Start review from the generated workbook in `output/`.

**開始新批次** clears the RAW directory, injection-order source and displayed results while retaining
targets, DLL, and analysis parameters. The environment and advanced settings
remain available through their expandable summaries. Editing settings after a
completed run labels the displayed results as previous results.

The GUI and CLI use the same targeted pipeline. Packaged builds start from:

- `config/settings.example.csv`
- `config/targets.example.csv`

Runtime working copies are created after Save or Run:

- `config/settings.csv`
- `config/targets.csv`

Keep real machine paths in runtime files. Do not commit local RAW, DLL, or
private lab paths.

## Inputs

| Input | Role |
| --- | --- |
| RAW directory | Batch of Thermo `.raw` files to process |
| DLL directory | Folder containing Thermo RawFileReader DLLs |
| Target table | Labels, m/z, RT windows, ppm tolerance, NL mass, and ISTD pairing |
| Reusable analysis method | Eleven method parameters plus the complete targets/ISTD table; Gaussian smoothing is fixed |
| Resources | Optional expected-diff registry and reviewed support inputs |
| Environment and output | DLL, workers, and output controls; not method customization |

Target labels become public workbook and CSV identifiers. Rename them only when
downstream scripts and review expectations can tolerate the changed column
prefixes.

## Analysis Method And Saved Settings

Use **另存為方法** to save a named method once. The folder under
`config/methods/<name>/` contains `settings.csv` (all method parameters) and
`targets.csv` (including ISTD relations and additional target metadata).
Choose it from **已儲存的方法** for another batch. **從方法資料夾載入** accepts
the same two-file format from another location. Loading replaces the complete
method and target table in the draft, retaining the current batch and machine
settings; Save or Run applies that draft. Existing names are not overwritten:
save a new version under a new name.

Portable methods currently do not support external scientific resource bindings.
Saving or switching a resource-bound configuration is rejected rather than
silently discarding or inheriting approvals or support.
Continue using the complete workspace configuration for those methods.

The current sample's credible paired ISTD supplies the RT shift directly;
the configured analyte/ISTD midpoint difference is preserved. You do not create
or reload an RT-prior/pair calibration CSV. Keep the target identities, pairing
and RT windows appropriate for the method and batch: a detected ISTD does not
automatically establish every analyte's identity.

The CLI accepts `--method-dir <folder>` with the same method format, plus
`--data-dir <new-batch>` and the existing workspace/local settings. Its run
manifest binds frozen input CSVs, not a later mutable saved-method folder.
GUI and CLI use the same limited-identity workflow when selected by the method;
only a successful existing expected-diff gate publishes its verified final result.

`settings.csv` is the active Targeted configuration; `targets.csv` remains the
separate target table. Platform defaults are owned by the application, not by
one custom config file per concern. **自訂參數 · N 項調整** compares only the
11 scalar parameters owned by `xic_extractor.settings_schema.METHOD_KEYS`. RAW/DLL paths,
workers, output options, targets, and external resources do not increase this
count. Gaussian smoothing is fixed information, not a selectable method.

**還原方法參數** restores those parameters while preserving targets, batch, environment,
output settings and resource selections. Resources remain separately visible;
changing a resource is not hidden inside the custom-method count. Use the
existing run manifest and its bound inputs for full replay provenance.

`count_no_ms2_as_detected` is a compatibility fallback for legacy rows without
formal projection fields, not a global switch for formal Detection %. See the
[field reference](targeted-config-reference.md) for its precise semantics and
canonical smoothing-window compatibility.

## Main Output

```text
output/xic_results_YYYYMMDD_HHMM.xlsx
```

The default limited-identity workflow publishes its gate-passed workbook under
`output/<run>/`. Open it from the workspace result actions. Cancellation or a
failed gate cleans the private working files without publishing a completed
workbook.

| Workbook sheet | Purpose |
| --- | --- |
| `Overview` | First view: batch outcome, review priorities and links to detailed results |
| `XIC Results` | Sample-target Area, RT, counted status and review reason; expand secondary columns for evidence |
| `Targets` | Supporting reference: snapshot of the target table used for the run |
| `Run Metadata` | Supporting reference: method settings, provenance and gate receipt |
| `Diagnostics` (when nonempty) | Supporting reference: complete supplied diagnostic text |

The normal output directory contains one XLSX only. Temporary baseline/support/
diff files are cleaned after the limited-identity gate, including on cancellation
or failure; a failed gate does not publish a completed workbook. Nonempty supplied
sample context, identity support, and selection approvals remain in hidden sheets
so the decision inputs can be inspected without sidecar files.

Use **在結果檢閱開啟** after completion, or open the XLSX in the
workbench's result picker. Inspector shows saved values without RAW, DLLs,
recomputation or additional output files. Start with the batch overview, select
a target or review action, then a sample for its details. The workbook does not contain chromatogram curves.

## Detection And Review

`XIC Results` keeps two separate answers beside the measurements:

- **Counted Detection** is decided by the formal Targeted projection. A row
  can retain RT/area without counting; NL tokens alone do not decide it.
- **Review / Review Reason** identify rows needing attention. A counted
  detection can still require review. Filter `Review` in Excel or use the
  Inspector's attention filter, then inspect the recorded explanation.

For missing MS2, the formal projection applies the ISTD/analyte role and
support policy. `count_no_ms2_as_detected` controls only the legacy fallback
when formal projection fields are absent; it does not override the formal
result. The [Decision Policy](../product/decision-policy.md) owns these rules.

## CLI

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
| `--parallel-mode` | `serial` or `process` |
| `--parallel-workers` | Worker count for process mode |

Tracked settings currently default to `parallel_mode=process`. Use serial mode
for diagnosis or environment isolation.

## Validation

After a run, start with `Overview` or the workbench's batch overview. Follow its
review priorities into `XIC Results` or a sample detail; inspect saved peak/NL
evidence and diagnostic text before rerunning.
See the [output reference](targeted-output-reference.md) for column meanings.

For developer-level validation harness details, see
[docs/agent/parameter-settings.md](../agent/parameter-settings.md).

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `settings.csv` or `targets.csv` format error | Fix the file, row, and column named in the error |
| `data_dir` does not exist | Point Settings to the folder containing `.raw` files |
| DLL load failure | Point Settings to the folder with Thermo RawFileReader DLLs |
| One RAW file fails | Continue the run and inspect the `FILE_ERROR` diagnostic row |
