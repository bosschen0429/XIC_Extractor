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

Shared physical and selection policy is platform-owned. The normal GUI and
Targeted CLI do not offer smoothing, anchor-window, Dirty Matrix or backend
controls. Custom Delta Mass methods own their mass and co-elution tolerances.
The GUI execution-resources section can save the shared worker count. The run metadata records the effective
policy and actual resource allocation.

## Target intent (`Targets` worksheet)

| Column | Meaning |
| --- | --- |
| `label` | Unique target name; becomes a public output identifier |
| `mz` | Positive precursor m/z |
| `expected_rt_min` | Expected retention time **in minutes**, not a lower RT bound |
| `method` | Blank selects MS1 only with the shared 20 ppm precursor tolerance; `dR`, `R`, or `MeR` selects a shared built-in CID-NL recipe; any other value names a custom identification method. Older tables call this column `cid_nl_tag`; it is read as `method` |
| `role` | `analyte` or `istd` |
| `istd_pair` | Optional ISTD label for an analyte; blank for an ISTD or an analyte without a paired ISTD |

Additional descriptive metadata is retained. Retired scientific fields such as
`rt_min`, `rt_max`, `ppm_tol`, `neutral_loss_da`, `nl_ppm_warn`, `nl_ppm_max`, and
`is_istd`, `delta_mass_method` and `quant_position` are not metadata and are rejected. There is no automatic conversion.
Expected RT guides identity, including the declared analyte–ISTD offset; it does
not crop the acquired candidate universe.
MS1-only rows retain their declared name, m/z and expected RT. They do not
require an ISTD pair and do not acquire CID-NL support from their label or a
missing tag. A table may mix MS1-only and tagged targets.
Selection compares retained regions across the batch, using the reference RT,
supported sample drift, same-ion target order, and acquired signal. There is no
fixed 0.75-minute acceptance window. A plausible but uncertain correspondence
can retain a provisional area with review; an incompatible correspondence is
ND. Touching lobes can propose a total area alongside the original regions,
without erasing the valleys needed to distinguish other targets. Shoulders and
double tops can require review without suppressing the measured total.

## Portable method

A method `.xlsx` has `Method` and `Targets` worksheets, plus an
`Identification Methods` worksheet when it defines custom methods. `Method` uses
`key,value` (optional `description`) and has exactly one row: `strategy,cid-nl`.
This format marker does not override each target's method.
A target-table `.xlsx` has exactly one `Targets` worksheet with the columns above.
Headers occupy the first row. Use actual values, not formulas or Excel error cells.
Numeric text and descriptive metadata survive round trips without rounding.

Download [method XLSX](../../xic_extractor/configuration/data/targeted-method.xlsx)
or [target-table XLSX](../../xic_extractor/configuration/data/targets.xlsx), or use
the links in the target-list card. Save a copy before editing. These are format
examples, not evidence that the listed method is appropriate for your samples.

### Design and reuse an identification method

1. Open **管理辨認方法**, choose **＋ 新增方法**, name it, and add
   **＋ CID-NL 條件** or **＋ Delta Mass 條件**.
2. Choose **全部符合（AND）** or **任一符合（OR）**. Conditions must refer to the
   same quantitation peak, not different peaks in the run. HCD-PI is not
   implemented and is not an available condition.
3. For Delta Mass, the quantitation ion is fixed at `0`; enter positive or negative
   partner Δ m/z values. The Target's m/z and reported area always belong to that
   zero position. Neutral mass differences require charge conversion first.
   Ratio limits and shape comparison sit on each partner's row; ratios only
   annotate, never reject peaks. Co-elution and shape thresholds stay in the
   collapsed **進階：共洗脫與峰形** panel.
4. **儲存方法** keeps the definition. It does not change which targets use it.
5. In the target editor, pick the method in each row's **辨認方法** column, or use
   **全部目標改用** to fill every row. Blank (MS1 only) means no identification
   condition. A method still used by targets cannot be deleted.
6. Save the workspace or **另存清單**. XLSX export includes the definitions, so a
   later import preserves the complete method, not only target rows.

CLI uses the same method admission and execution:

```powershell
.venv\Scripts\python.exe -m scripts.run_extraction --method C:\methods\my-method.xlsx
```

The `Identification Methods` worksheet has one row per CID-NL condition and
one row per Delta Mass ion:

| Method | Combine | m/z Tolerance ppm | Condition | Kind | CID-NL Tag | Position | Delta m/z | Ratio Min | Ratio Max | Compare Shape |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Pt-pair | all | 20 | 1 | cid_nl | dR | | | | | |
| Pt-pair | all | 20 | 2 | delta_mass | | quant | 0 | | | FALSE |
| Pt-pair | all | 20 | 2 | delta_mass | | partner_1 | -1.9981 | 0.1 | 10 | TRUE |

Optional `Apex Floor sec`, `Scan Interval Multiplier`, `Shape Window sec` and
`Min Shape r` columns hold Delta Mass co-elution/shape thresholds; blank uses the
defaults. Method- and condition-level values must agree on every row of that
method or condition. Each Delta Mass condition needs exactly one ion at `0`.
Every method needs at least one condition; use a blank target `method` for MS1 only.

Overlapping ion windows are rejected. Two-position methods require a measured
partner. Unmet methods do not fall back to MS1-only. `Method Evidence` explains
matched/not-matched/not-acquired conditions for each candidate; missing MS2 is
not counted as a chemical failure, but cannot fulfill CID-NL. Inspector reads saved
method/evidence rows without rerunning analysis. Synthetic tests provide
`diagnostic_only` evidence, not scientific validation of a user-designed method.

Drag a file into the target-list import area or use **選擇檔案**. XLSX content
automatically distinguishes a method from a target table; CSV/TSV are target tables.
Uploads accept one nonempty file up to 1 MiB. Review the filename, detected type,
targets and ISTD pairs, then choose **確認匯入並取代**; cancellation changes nothing.

Loading a method replaces the complete target list while retaining RAW/DLL paths,
injection metadata and worker settings. **另存清單** creates a named XLSX under
`config/methods/`; **保存目前工作** saves JSON without overwriting that named method.
CSV/TSV target tables remain explicit draft imports only, not a second startup
configuration. DNA/RNA workbook detection and `.xls` conversion are not offered.

Workbench theme/output preferences stay in `workbench.json`; Untargeted controls
stay in `discovery_gui.json`. Neither is a portable Targeted method. Private local
files and existing backups are not migrated or removed by the application.

## Evidence and outputs

A missing MS2 event does not suppress exact-mass MS1 search and does not create
CID-NL support. Reported value, counted detection and review are separate outputs.
Read [Targeted extraction](targeted-extraction.md) for the workbook workflow.
