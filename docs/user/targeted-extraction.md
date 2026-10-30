# Targeted Extraction

Use this workflow when you already know the compounds, m/z values, expected RT, CID-NL tags,
and internal-standard relationships you want to review.

![Targeted Extraction workspace](../../assets/screenshots/targeted-workspace.png)

## Workflow

1. Open `XIC_Extractor.exe`, or run `uv run xic-workbench` in a developer
   checkout. On first use, follow the [reader setup notice](../../README.md#run-the-html-workbench)
   to install RawFileReader 8.0.42 and, if needed, .NET 8 or later.
2. Select **Targeted Extraction**.
3. Select a saved method and press **載入清單**, or configure a method
   for the first time. Set the new RAW data directory; the local DLL setting is retained.
4. For a new method, import targets or choose **檢視與編輯** to open the full-height target table.
   **返回批次設定** (`Alt+Left`) preserves edits and returns to the batch summary.
5. Press **開始 Targeted 分析** in the run panel.
6. Start review from the generated workbook in `output/`.

**開始新批次** clears the RAW directory, injection-order source and displayed results while retaining
targets, DLL, and the selected strategy. The environment section remains available
for machine-path corrections. Editing settings after a
completed run labels the displayed results as previous results.

The GUI and CLI use the same Targeted pipeline and `config/workspace.json` current
work state. A fresh workspace has no selected targets. Download the method or
target-table XLSX example from the target-list card and save a copy. Drag the file
onto the import area or click **選擇檔案** (no pasted path). Workbench detects the
file type and previews targets and ISTD pairs; **確認匯入並取代** applies the list,
while **取消** leaves current work unchanged. **保存目前工作** writes local JSON; **另存清單** creates a reusable XLSX.
Reading settings never creates or imports old CSV working copies.

Keep real machine paths in ignored runtime files. Do not commit local RAW, DLL,
or private lab paths. The [field reference](targeted-config-reference.md) owns
workbook sheet names and the exact editable fields.

## Inputs

| Input | Role |
| --- | --- |
| RAW directory | Batch of Thermo `.raw` files to process |
| DLL directory | RawFileReader 8.0.42 folder selected by setup; not the Xcalibur folder |
| Target table | Names, m/z, expected RT, CID-NL tag, role, and ISTD pairing |
| Reusable analysis method | Processing strategy plus the complete targets/ISTD table |
| Environment and output | DLL and output location; shared worker count in execution resources |

Target labels become public workbook and CSV identifiers. Rename them only when
downstream scripts and review expectations can tolerate the changed column
prefixes.

## Analysis Method And Saved Settings

The normal flow is **select samples → choose method/targets → run → review**.
The target table specifies name, m/z, expected RT in minutes, CID-NL tag
(`dR`, `R`, `MeR`), role and an optional ISTD relationship. An analyte may reference
an ISTD row or leave the pair blank; an ISTD does not reference another row. The tag is the single chemistry input.
Leave the tag blank for MS1-only extraction; no neutral-loss evidence is required
and the shared 20 ppm precursor tolerance applies. Names such as `D1` or `R1`
are identifiers, not chemistry tags. Tagged and MS1-only rows may share a batch.
MS1-only first localizes a continuous envelope at the expected RT (or within
0.75 minutes of its boundary), then integrates its complete area. Unresolved
shoulders and double tops are retained with review notes. No nearby envelope
means ND; a cleaner peak at another RT is not a replacement.

Use **另存清單** to save the complete target table plus the scientific strategy.
Load it for another batch without changing that batch's RAW/DLL paths
or injection order. There is no advanced method panel or method reset: scientific
processing belongs to the platform, not hidden settings. Local worker count is
an execution resource, never part of a portable method.

The current sample's credible paired ISTD supplies the RT shift while preserving
the declared expected analyte–ISTD offset. Expected RT is an identity hint, not an
acquisition crop. Targeted enumerates exact-mass MS1 physical candidates across
the sample acquisition span, even without MS2, and uses the shared Discovery
CID-NL event and physical-peak owners. Missing NL does not count as direct support.
Conservative, confirmed-peer ISTD Backfill keeps its separate provenance.

The CLI accepts `--method <file.xlsx>` and `--data-dir <new-batch>`; execution
inputs are frozen at start. Local settings contain RAW/DLL paths, sample metadata and worker count. The [field reference](targeted-config-reference.md) is the input contract.
Old target/scientific-settings formats are rejected, not silently converted.

## Main Output

```text
output/xic_results_YYYYMMDD_HHMM.xlsx
```

Open it from the workspace result actions. Cancellation or failure publishes no
completed workbook.

| Workbook sheet | Purpose |
| --- | --- |
| `摘要` | First view: batch conclusion and one table of targets with what to look at first |
| `需檢查` (when needed) | The results to look at first (a target's minority outcome, or its review notes when there is no clear majority), with reason and suggested check |
| `面積` | Saved Area per sample and target; analytes sharing an ISTD sit together, followed by that ISTD |
| `XIC Results` | Sample-target Area, RT, counted status and review reason; expand secondary columns for evidence |
| `Targets` | Supporting reference: snapshot of the target table used for the run |
| `Run Metadata` | Supporting reference: method settings and provenance |
| `欄位說明` | Meaning and unit of every column |

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
| `--method` | Saved target-intent XLSX (`Method` + `Targets`) |
| `--injection-order-source` | Explicit injection order for this batch |

Execution scheduling is automatic; actual workers are recorded in run metadata.

## Validation

After a run, start with `摘要` or the workbench's batch overview. Follow 先看
into `需檢查` or a sample detail; inspect the XIC and saved peak/NL evidence in
the workbench before rerunning.
See the [output reference](targeted-output-reference.md) for column meanings.

For developer-level validation harness details, see
[docs/agent/parameter-settings.md](../agent/parameter-settings.md).

## Troubleshooting

| Symptom | Action |
| --- | --- |
| XLSX or current-work JSON format error | Fix the file, row, and column named in the error |
| `data_dir` does not exist | Point Settings to the folder containing `.raw` files |
| DLL load failure | Follow the reader setup notice; use RawFileReader 8.0.42 with .NET 8 or later, then restart the workbench |
| One RAW file fails | Continue the run and inspect the `FILE_ERROR` diagnostic row |
