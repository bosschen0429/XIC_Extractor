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
| `method` | Optional per-target override: a named method or `ms1`, `cid:dR`, `cid:R`, `cid:MeR`; otherwise use the document default |
| `role` | `analyte` or `istd` |
| `istd_pair` | Optional ISTD label for an analyte; blank for an ISTD or an analyte without a paired ISTD |
| `sample_applicability` | Legacy metadata: `all` (default) or `rna_containing`; retained when reading older methods, but does not restrict Targeted analysis |

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

A method `.xlsx` has exactly `Method` and `Targets` worksheets. `Method` uses
`key,value` (optional `description`) and retains the compatibility row
`strategy,cid-nl`. This format marker does not override each target's method.
Custom methods add `identification_methods` (named definitions as JSON) and
`default_method`. Large JSON definitions continue in `identification_methods.2`,
`identification_methods.3`, etc., without adding worksheets or truncating text.
These numbered fragments must be unique and contiguous, starting with the
unnumbered first part. Edit methods in the workbench rather than editing JSON cells.
A target-table `.xlsx` has exactly one `Targets` worksheet with the columns above.
Headers occupy the first row. Use actual values, not formulas or Excel error cells.
Numeric text and descriptive metadata survive round trips without rounding.

Download [method XLSX](../../xic_extractor/configuration/data/targeted-method.xlsx)
or [target-table XLSX](../../xic_extractor/configuration/data/targets.xlsx), or use
the links in the target-list card. Save a copy before editing. These are format
examples, not evidence that the listed method is appropriate for your samples.

### Design and reuse an identification method

1. Open **編輯辨認方法**, name it, and add CID-NL or Delta Mass condition cards.
2. Choose **符合全部條件（AND）** or **符合任一條件（OR）**. Conditions must refer
   to the same quantitation peak, not different peaks in the run. HCD-PI is not
   implemented and is not an available condition.
3. For Delta Mass, the quantitation ion is fixed at `0`; enter positive or negative
   partner `offset_mz` values. The Target's m/z and reported area always belong to
   that zero position. Neutral mass differences require charge conversion first.
4. Leave advanced panels closed for normal use. Open them to adjust m/z/RT
   tolerances, shape thresholds or reference ratios. Shape and ratios reference
   the quantitation ion; ratios only annotate, never reject peaks.
5. **套用為預設方法** sets the default method. Routine analysis only needs the
   **預設辨認方法** selector. In the target editor, enable
   **顯示個別 target 的方法設定** for row overrides. Hiding these controls does not clear saved choices;
   select **沿用整張表的方法** in a row to remove its override. With no default/override, legacy `cid_nl_tag` behavior is preserved.
6. Save the workspace or **另存清單**. XLSX export includes the definitions and
   default, so a later import preserves the complete method, not only target rows.

CLI uses the same method admission and execution:

```powershell
.venv\Scripts\python.exe -m scripts.run_extraction --method C:\methods\my-method.xlsx
```

The `Method` worksheet retains `strategy=cid-nl` as the existing shared-strategy
marker, plus optional `identification_methods` (JSON object) and `default_method`.
A definition uses `combine_mode: "all" | "any"`, `mz_tolerance_ppm`, and
`conditions`: `{ "kind": "cid_nl", "tag": "dR" }` or
`{ "kind": "delta_mass", "positions": [{ "label": "quant", "offset_mz": 0 },
{ "label": "partner", "offset_mz": -2 }] }`. Exactly one position must be zero.
Use the editor or exported workbook rather than manually composing JSON.

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
