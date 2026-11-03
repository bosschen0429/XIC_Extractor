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
| `cid_nl_tag` | `dR`, `R`, or `MeR` selects a shared built-in Discovery recipe; blank without `delta_mass_method` selects MS1-only with the shared 20 ppm precursor tolerance |
| `delta_mass_method` | Optional named custom Delta Mass method; requires a blank `cid_nl_tag` and an accompanying method definition |
| `quant_position` | Required with Delta Mass: the position represented by `mz`, whose selected XIC supplies the area |
| `role` | `analyte` or `istd` |
| `istd_pair` | Optional ISTD label for an analyte; blank for an ISTD or an analyte without a paired ISTD |
| `sample_applicability` | Legacy metadata: `all` (default) or `rna_containing`; retained when reading older methods, but does not restrict Targeted analysis |

Additional descriptive metadata is retained. Retired scientific fields such as
`rt_min`, `rt_max`, `ppm_tol`, `neutral_loss_da`, `nl_ppm_warn`, `nl_ppm_max`, and
`is_istd` are not metadata and are rejected. There is no automatic conversion.
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

A method `.xlsx` has exactly `Method` and `Targets` worksheets. `Method` uses
`key,value` (optional `description`) and retains the compatibility row
`strategy,cid-nl`. This format marker does not override each target's method.
Custom methods add a `delta_mass_methods` row containing the named definitions
as JSON. Edit these definitions in the workbench, rather than editing JSON cells.
A target-table `.xlsx` has exactly one `Targets` worksheet with the columns above.
Headers occupy the first row. Use actual values, not formulas or Excel error cells.
Numeric text and descriptive metadata survive round trips without rounding.

Download [method XLSX](../../xic_extractor/configuration/data/targeted-method.xlsx)
or [target-table XLSX](../../xic_extractor/configuration/data/targets.xlsx), or use
the links in the target-list card. Save a copy before editing. These are format
examples, not evidence that the listed method is appropriate for your samples.

### Design a Delta Mass method

1. Select **設計 Delta Mass 方法** in Targeted's target-list card.
2. Name the method. Add two or more uniquely named positions, with increasing
   relative m/z offsets starting at zero. Ratios are relative to the zero position.
3. Set ppm, co-elution and shape conditions. Check the positions requiring shape
   comparison against the lowest-mass position. Ratio ranges do not reject peaks.
4. Apply the method. In **檢視與編輯**, select that method and a quantitation
   position for each target. The Target's m/z belongs to that position.
5. Save the workspace or save a named XLSX method. Exporting targets that use
   Delta Mass includes `Method` definitions, so reimport does not lose the method.

Delta Mass offsets are m/z differences, not charge-independent neutral masses.
The quantitation position may be any configured position. Overlapping position
windows are rejected because one ion cannot establish two distinct positions.
Two-position methods require a measured partner; a solitary peak is insufficient.
Unmatched patterns do not fall back to MS1-only. Different targets in one list
can use MS1-only, CID-NL or different custom Delta Mass methods.

The workspace and queued run receipts retain method definitions alongside target
bindings. Results preserve method definitions and per-position evidence in the
XLSX; the Inspector exposes the saved evidence without rerunning the matcher.

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
