# Targeted Extraction

Use this workflow when you already know the compounds, m/z values, RT windows,
and optional internal-standard pairs you want to review.

![Targeted Extraction workspace](../../assets/screenshots/targeted-workspace.png)

## Workflow

1. Open `XIC_Extractor.exe`, or run `uv run xic-workbench` in a developer
   checkout.
2. Select **Targeted Extraction**.
3. Set the RAW data directory and Thermo DLL directory.
4. Import targets or choose **檢視與編輯** to open the full-height target table.
   **返回批次設定** (`Alt+Left`) preserves edits and returns to the batch summary.
5. Press **開始執行** below the workspace.
6. Start review from the generated workbook in `output/`.

**開始新批次** clears the RAW directory and displayed results while retaining
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
| Analysis method | Eleven scalar method parameters; Gaussian smoothing is fixed |
| Resources | Optional RT prior/calibration, registry, and reviewed support inputs |
| Environment and output | DLL, workers, and output controls; not method customization |

Target labels become public workbook and CSV identifiers. Rename them only when
downstream scripts and review expectations can tolerate the changed column
prefixes.

## Analysis Method And Saved Settings

`settings.csv` is the active Targeted configuration; `targets.csv` remains the
separate target table. Platform defaults are owned by the application, not by
one custom config file per concern. **自訂參數 · N 項調整** compares only the
11 scalar parameters owned by `xic_extractor.settings_schema.METHOD_KEYS`. RAW/DLL paths,
workers, output options, targets, and external resources do not increase this
count. Gaussian smoothing is fixed information, not a selectable method.

**回到預設** restores those parameters while preserving batch, environment,
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

Daily review should start from the workbook, not the debug CSV files.

| Workbook sheet | Purpose |
| --- | --- |
| `Overview` | Run summary, counts, and review priorities |
| `Review Queue` | Human worklist for sample-target rows needing attention |
| `XIC Results` | Sample-target RT, Area, NL status, confidence, and reason |
| `Summary` | Per-target detection, area, NL, RT, and confidence summaries |
| `Targets` | Snapshot of the target table used for the run |
| `Diagnostics` | Technical issue rows |
| `Run Metadata` | Reproducibility metadata and config hash |
| `Score Breakdown` | Optional technical audit sheet |

Intermediate CSVs are kept only when `keep_intermediate_csv=true` or the CLI
uses `--skip-excel`.

## Detection And Review

The `Overview` sheet reports two key percentages:

- **Detection %** — the count decided by the formal Targeted projection.
  Numeric availability and counted detection are separate: a row can retain
  RT/area without counting. Read `Counted Detection`, `Product State`, and the
  recorded reasons in the advanced columns; NL tokens alone do not decide it.
- **Flagged %** — review workload. A detected row can still be flagged for
  manual inspection (e.g. low confidence, unusual RT shift).

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
| `--skip-excel` | Keep CSV-only output and skip workbook conversion |

Tracked settings currently default to `parallel_mode=process`. Use serial mode
for diagnosis or environment isolation.

## Validation

After a run, review the `Overview` sheet for detection rates and the
`Review Queue` for rows that need attention. If detection rates or RT deltas
look abnormal, check the `Diagnostics` sheet before re-running.

For developer-level validation harness details, see
[docs/agent/parameter-settings.md](../agent/parameter-settings.md).

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `settings.csv` or `targets.csv` format error | Fix the file, row, and column named in the error |
| `data_dir` does not exist | Point Settings to the folder containing `.raw` files |
| DLL load failure | Point Settings to the folder with Thermo RawFileReader DLLs |
| One RAW file fails | Continue the run and inspect the `FILE_ERROR` diagnostic row |
| Need debug CSVs | Enable `keep_intermediate_csv` or use `--skip-excel` |
| Need scoring details | Enable `emit_score_breakdown` and inspect `Score Breakdown` |
