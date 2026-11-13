# Targeted Config Reference

Targeted uses **XLSX for reusable user documents** and **JSON for program-managed
current work**. No legacy settings/targets CSV is discovered, copied, or converted.
Unsupported settings and retired scientific columns are rejected.

## Batch and machine work state (`config/workspace.json`)

The application saves batch inputs and targets together by atomic replacement.
`application` contains DLL and worker settings; `batch` contains RAW and injection-order
inputs; `targets` contains the current target rows. A new workspace uses default
machine values and an empty target list. Read operations do not create files.

| Key | Meaning |
| --- | --- |
| `data_dir` | Thermo RAW batch directory |
| `dll_dir` | Thermo RawFileReader DLL directory, shared with Untargeted |
| `injection_order_source` | Optional sample metadata (Word, Excel, CSV or TSV); RAW headers own injection order and sample names |
| `parallel_workers` | Local worker count; blank uses the shared execution-profile default |

The workflows share physical-peak and selection rules. Execution resources
control the worker count, not the scientific method. Custom Delta Mass methods
store mass and co-elution tolerances in their method definitions. Run metadata
records the effective settings and resource allocation.

## Target intent (`Targets` worksheet)

| Column | Meaning |
| --- | --- |
| `label` | Unique target name; becomes a public output identifier |
| `mz` | Positive precursor m/z |
| `expected_rt_min` | Expected retention time **in minutes**, not a lower RT bound |
| `role` | `analyte` or `istd` |
| `istd_pair` | Optional ISTD label for an analyte; blank for an ISTD or an analyte without a paired ISTD |
| `cid_nl_loss_da` | Optional CID-NL neutral loss in Da, below `mz`, for example `116.0474` (dR), `132.0423` (R) or `146.0579` (MeR); any value is used as written |
| `delta_mass_mz` | Optional comma-separated Delta Mass Δm/z of companion ions, relative to `mz`, for example `+1.0021, +4.0052`; positive or negative |

Both blank means MS1 only with the shared 20 ppm precursor tolerance. With both
filled, both conditions must match the same peak. Older tables with a single
`method` (or `cid_nl_tag`) column are read and converted; see
[Earlier target tables](#earlier-target-tables).

Additional descriptive metadata is retained. Retired scientific fields such as
`rt_min`, `rt_max`, `ppm_tol`, `neutral_loss_da`, `nl_ppm_warn`, `nl_ppm_max`, and
`is_istd`, `delta_mass_method` and `quant_position` are not metadata and are rejected. There is no automatic conversion.
Expected RT guides identity, including the declared analyte–ISTD offset; it does
not crop the acquired candidate universe.
MS1-only rows retain their declared name, m/z and expected RT. They do not
require an ISTD pair and do not acquire CID-NL support from their label or a
missing loss. A table may mix MS1-only, CID-NL and Delta Mass targets.
Selection compares retained regions across the batch, using the reference RT,
supported sample drift, same-ion target order, and acquired signal. There is no
fixed 0.75-minute acceptance window. A plausible but uncertain correspondence
can retain a provisional area with review; an incompatible correspondence is
ND. Touching lobes can propose a total area alongside the original regions,
without erasing the valleys needed to distinguish other targets. Shoulders and
double tops can require review without suppressing the measured total.

## Target-list XLSX

A target-list `.xlsx` has a `Targets` worksheet with the columns above. When
targets use Delta Mass it may add a `Delta Mass` worksheet (one row per ion,
below) and a `Delta Mass Settings` worksheet whose `key,value` rows (optional
`description`) hold the list-shared settings: `delta_mass_mz_tolerance_ppm`,
`delta_mass_apex_floor_sec`, `delta_mass_scan_interval_multiplier`,
`delta_mass_shape_window_sec` and `delta_mass_min_shape_r`. No other worksheet
is accepted. An earlier workbook with a `Method` or `Identification Methods`
worksheet is rejected: delete those sheets and move any `delta_mass_*` rows to
`Delta Mass Settings`. Headers occupy the first row. Use actual values, not formulas or Excel error cells.
Numeric text and descriptive metadata survive round trips without rounding.

Download the [example list](../../xic_extractor/configuration/data/targets.xlsx),
or press **從範例開始** while the list is empty to edit it in the workbench.
Save a copy before editing a file. These are format
examples, not evidence that the listed specificity suits your samples.

### Set specificity in the target editor

1. Open **檢視與編輯**. Select targets the same way as samples: drag over the row
   numbers, Ctrl adds, Shift extends, and a search selects what it finds.
2. Fill **CID-NL 中性丟失（Da）** and/or **Delta Mass Δm/z** in the toolbar and
   choose **套用**; only filled fields are written. **設為 MS1 only** clears both.
3. Click a target's **Delta Mass Δm/z** cell to edit its companion ions: Δm/z,
   intensity-ratio range (for example `0.2–0.8`; recorded, never used to reject)
   and shape comparison. The preview plots the ions at the target's actual m/z.
   A new ion is named by its Δm/z with four decimals, for example `+4.0052`; two
   ions that round to the same name cannot be saved.
4. Co-elution and shape thresholds are in the collapsed
   **進階：Delta Mass 判斷設定（整份清單共用）** panel.
5. Press **完成**, then **保存批次設定**, or export the list with **匯出清單 XLSX**.
   The export includes the Delta Mass details.

The `Delta Mass` worksheet has one row per target ion:

| Target | Position | Delta m/z | Ratio Min | Ratio Max | Compare Shape |
| --- | --- | --- | --- | --- | --- |
| Pt-GG | +1.0021 | 1.002111 | 0.514 | 2.056 | TRUE |
| Pt-GG | +4.0052 | 4.005213 | 0.11 | 0.45 | FALSE |

Each `Delta m/z` must appear in that target's `delta_mass_mz` cell. An ion listed
in the cell without a row uses its offset as its name, ratio 0.1–10 and shape
comparison on.

Overlapping ion windows are rejected. A Delta Mass target requires its companion
ions; it does not fall back to MS1-only. `Method Evidence` explains
matched/not-matched/not-acquired conditions for each candidate; missing MS2 is
not counted as a chemical failure, but cannot fulfill CID-NL. Inspector reads saved
rows without rerunning analysis. A configurable specificity is not automatically
scientifically validated; evaluate its suitability for your samples separately.

### Earlier target tables

A `method` (or `cid_nl_tag`) column in a CSV/TSV table or a saved workspace is
converted when read: `dR`, `R` and `MeR` become their losses; a workspace's named
method becomes its CID-NL loss and Delta Mass ions (names, ratios and shape
comparison kept). Workbooks with an `Identification Methods` worksheet are
rejected, as above. A method the new columns cannot express is rejected with the affected
targets: a method with only a CID-NL condition (it required the loss; the
CID-NL column alone only ranks peaks), any-of combination, two conditions of
one kind, or tolerance and co-elution settings that differ between methods. Saving writes the new format.

Drag a file into the target-list import area or use **瀏覽**. Uploads accept one
nonempty XLSX, CSV or TSV file up to 1 MiB. Review the filename, targets and ISTD
pairs, then choose **匯入** (**取代目前清單** when a list is loaded); cancellation
changes nothing.

Importing replaces the complete target list while retaining RAW/DLL paths,
injection metadata and worker settings. **匯出清單 XLSX** writes the list to a
file you choose; **保存批次設定** saves the current setup locally.
CSV/TSV target tables remain explicit draft imports only, not a second startup
configuration. DNA/RNA workbook detection and `.xls` conversion are not offered.

Workbench theme/output preferences stay in `workbench.json`; Untargeted controls
stay in `discovery_gui.json`. Neither is a portable Targeted method. Private local
files and existing backups are not migrated or removed by the application.

## Evidence and outputs

A missing MS2 event does not suppress exact-mass MS1 search and does not create
CID-NL support. Reported value, counted detection and review are separate outputs.
Read [Targeted extraction](targeted-extraction.md) for the workbook workflow.
