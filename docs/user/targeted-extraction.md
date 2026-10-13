# Targeted Extraction

Use this workflow when you already know the compounds, m/z values, expected RT, CID-NL tags,
and internal-standard relationships you want to review.

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
targets, DLL, and the selected strategy. The environment section remains available
for machine-path corrections. Editing settings after a
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
| Target table | Names, m/z, expected RT, CID-NL tag, role, and ISTD pairing |
| Reusable analysis method | Processing strategy plus the complete targets/ISTD table |
| Environment and output | DLL and output location; resource scheduling is automatic |

Target labels become public workbook and CSV identifiers. Rename them only when
downstream scripts and review expectations can tolerate the changed column
prefixes.

## Analysis Method And Saved Settings

The normal flow is **select samples → choose method/targets → run → review**.
The target table specifies name, m/z, expected RT in minutes, CID-NL tag
(`dR`, `R`, `MeR`), role and ISTD relationship. An analyte references an ISTD row;
an ISTD does not reference another row. The tag is the single chemistry input.

Use **另存為方法** to save the complete target table plus the scientific strategy.
Load it for another batch without changing that batch's RAW/DLL paths
or injection order. There is no advanced method panel or method reset: scientific
processing and resource scheduling belong to the platform, not hidden settings.

The current sample's credible paired ISTD supplies the RT shift while preserving
the declared expected analyte–ISTD offset. Expected RT is an identity hint, not an
acquisition crop. Targeted enumerates exact-mass MS1 physical candidates across
the sample acquisition span, even without MS2, and uses the shared Discovery
CID-NL event and physical-peak owners. Missing NL does not count as direct support.
Conservative, confirmed-peer ISTD Backfill keeps its separate provenance.

The CLI accepts `--method-dir <folder>` and `--data-dir <new-batch>`; execution
inputs are frozen at start. Settings contain only RAW/DLL paths and injection
order. The [field reference](targeted-config-reference.md) is the input contract.
Old target/scientific-settings formats are rejected, not silently converted.

## Main Output

```text
output/xic_results_YYYYMMDD_HHMM.xlsx
```

Open it from the workspace result actions. Cancellation or failure publishes no
completed workbook.

| Workbook sheet | Purpose |
| --- | --- |
| `Overview` | First view: batch outcome, review priorities and links to detailed results |
| `XIC Results` | Sample-target Area, RT, counted status and review reason; expand secondary columns for evidence |
| `Targets` | Supporting reference: snapshot of the target table used for the run |
| `Run Metadata` | Supporting reference: method settings and provenance |
| `Diagnostics` (when nonempty) | Supporting reference: complete supplied diagnostic text |

The normal output directory contains one XLSX only. A nonempty supplied sample
context table remains in a hidden sheet so it can be inspected without sidecar
files.

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
support policy. Missing MS2 is not direct CID-NL support; there is no detection
override. The [Decision Policy](../product/decision-policy.md) owns these rules.

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
| `--method-dir` | Saved target-intent method folder |
| `--injection-order-source` | Explicit injection order for this batch |

Execution scheduling is automatic; actual workers are recorded in run metadata.

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
