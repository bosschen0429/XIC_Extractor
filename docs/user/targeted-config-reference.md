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

Scientific policy is platform-owned. The normal GUI and Targeted CLI do not
offer smoothing, tolerance, anchor-window, Dirty Matrix or backend controls.
The GUI execution-resources section can save the shared worker count. The run metadata records the effective
policy and actual resource allocation.

## Target intent (`Targets` worksheet)

| Column | Meaning |
| --- | --- |
| `label` | Unique target name; becomes a public output identifier |
| `mz` | Positive precursor m/z |
| `expected_rt_min` | Expected retention time **in minutes**, not a lower RT bound |
| `cid_nl_tag` | `dR`, `R`, or `MeR`; chemistry and tolerances resolve from the corresponding shared built-in Discovery recipe |
| `role` | `analyte` or `istd` |
| `istd_pair` | Required ISTD label for an analyte; blank for an ISTD |
| `sample_applicability` | Optional `all` (default) or `rna_containing` |

Additional descriptive metadata is retained. Retired scientific fields such as
`rt_min`, `rt_max`, `ppm_tol`, `neutral_loss_da`, `nl_ppm_warn`, `nl_ppm_max`, and
`is_istd` are not metadata and are rejected. There is no automatic conversion.
Expected RT guides identity, including the declared analyte–ISTD offset; it does
not crop the acquired candidate universe.

## Portable method

A method `.xlsx` has exactly `Method` and `Targets` worksheets. `Method` uses
`key,value` (optional `description`) and has exactly one row: `strategy,cid-nl`.
The strategy is fixed; it is not an editable science override.
A target-table `.xlsx` has exactly one `Targets` worksheet with the columns above.
Headers occupy the first row. Use actual values, not formulas or Excel error cells.
Numeric text and descriptive metadata survive round trips without rounding.

Download [method XLSX](../../xic_extractor/configuration/data/targeted-method.xlsx)
or [target-table XLSX](../../xic_extractor/configuration/data/targets.xlsx), or use
the links in the target-list card. Save a copy before editing. These are format
examples, not evidence that the listed method is appropriate for your samples.

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
