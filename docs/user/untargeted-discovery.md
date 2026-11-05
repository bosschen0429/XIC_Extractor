# Untargeted Discovery

Use Untargeted to discover features under a selected method and compare them
across samples. A full run produces a feature × sample area Matrix; candidate
tables and workbench review explain how a feature or missing cell arose.
Aligned features are not automatically identified compounds. The Matrix is an
input to downstream analysis, not a normalized or batch-corrected result.

![Untargeted workspace: run scope, data and output, internal standards, and pre-run checks](assets/untargeted-workspace.png)

## Workflow

1. Open `XIC_Extractor.exe`, or run `uv run xic-workbench` in a developer
   checkout. On first use, follow the [reader setup notice](../../README.md#run-the-html-workbench)
   to install RawFileReader 8.0.42 and, if needed, .NET 8 or later.
2. Select **Untargeted**. The GUI always runs the complete analysis with Backfill.
3. Set the RAW folder. **結果存放位置** has a default; each run creates its own
   `<date>_<time>_untargeted_<RAW folder>` folder there, so earlier results are
   never overwritten. Reader setup supplies the shared DLL directory.
4. Import the Word **上機資訊** to match file names and injection order. Check
   unmatched or ambiguous entries; alphabetical file order is not injection order.
5. Check the **內標清單**. Download the shared XLSX example beside **選擇檔案**,
   then choose or drop an XLSX, CSV or TSV file. Only `role=istd` rows are used;
   the preview shows method-compatible standards and skipped rows. A dR method
   uses dR standards, not R standards such as `[13C,15N2]-8-oxo-Guo`. Incompatible
   standards are marked not applicable, not ND; changing the method refreshes
   this selection.
6. Choose a CID-NL starting method and open **編輯方法…** to select dR, R, MeR,
   or custom NL tags and their combination. Apply the method to the current run.
7. Press **開始 Untargeted 分析**. The finished run opens its result; **返回設定**
   goes back to the setup of the next analysis.

The GUI stores local Untargeted settings in `config/discovery_gui.json`. This is
machine-local state and should stay untracked.

GUI and CLI default to each RAW file's full acquisition RT range, read from
instrument metadata. Optional RT cropping accepts one or both finite,
non-negative bounds; an unspecified end remains automatic. Explicit cropping
is limited to the acquired span. Target-list RT windows remain compound search
windows, not acquisition bounds.

## Modes

| Mode | Use when | Input |
| --- | --- | --- |
| `full` | You want discovery plus cross-sample alignment | RAW directory |
| `discovery_only` | You want to inspect candidates from one RAW file | Single RAW file |
| `align_only` | Discovery was already run and you want to align existing outputs | RAW directory plus `discovery_batch_index.csv` |

For directory-level discovery-only batches, use the CLI discovery workflow. In
the GUI, use `full` when starting from a RAW directory.

## Inputs

| Input | Role |
| --- | --- |
| RAW directory or RAW file | Thermo data to discover or align |
| DLL directory | RawFileReader 8.0.42 folder selected by setup; not the Xcalibur folder |
| Output directory | Where candidate and Alignment artifacts are written |
| Acquisition information | Word sequence table supplying file/sample matching and injection order |
| Internal-standard list | Shared Targeted XLSX, CSV or TSV; Untargeted consumes only `role=istd` rows |
| Preset | CID-NL method with selected tags and combination |
| Advanced overrides | Optional per-run method changes layered on the preset |

The target list uses the same format as Targeted. Save a copy of the bundled
example, edit it in Excel, then choose or drop it in the GUI. Keep the `Targets`
sheet headers and `role=istd` for standards. A Targeted method XLSX is also accepted;
its method settings do not change the Untargeted method. There is no separate
Untargeted list to maintain. A supplied file with no internal standards shows an
error; clearing the field runs without an internal-standard list.

### Alignment CLI inputs

The Alignment CLI resolves ISTD input before RAW acquisition. An explicit
targets CSV or reference workbook takes precedence; otherwise it uses the same
[bundled target template](../../xic_extractor/configuration/data/targets.csv) as the HTML workbench's fresh default and prints the
resolved source and ISTD count. Missing or invalid input fails before RAW work.
An intentional uncorrected CLI run requires `--allow-uncorrected`, which cannot
be combined with either ISTD input. The Python API retains its explicit optional
input contract.

Sample metadata is supplied to Alignment separately from Discovery; see
[Alignment sample input](../product/sample-metadata-qc.md#alignment-input).
Use current CLI help for the complete option list.

## Outputs

GUI Alignment writes `alignment_matrix.tsv` with its bound identity and the
review/provenance tables from the `machine` output level. The identity supports
the results panel's **篩選匯出…** action. The CLI still defaults to the Matrix-only
`production` output level.

Successful retained CID-NL publication also saves `alignment_explanation.sqlite`
alongside its runtime marker for Inspector evidence.

Start with the file that matches your review question:

| Output | Content | Use it for |
| --- | --- | --- |
| `discovery_candidates.csv` | Per-sample candidate features with m/z, RT, area, seed evidence | Detailed candidate inspection per sample |
| `discovery_review.csv` | Compact subset of candidate fields with confidence | Quick review triage |
| `discovery_batch_index.csv` | Links each sample's candidate/review CSVs | Input for alignment (generated automatically) |
| `alignment_matrix.tsv` | Samples as columns, aligned features as rows, area values in cells | Default cross-sample quantitative output |
| `alignment_review.tsv` | Group identity, detection/quantification summaries, warnings, and bound Matrix row references | Investigate a group or trace a Matrix row |
| `alignment_explanation.sqlite` | Completed CID-NL decisions and retained curve evidence | Source for XIC Inspector export |
| `xic_inspector.html` | Offline chromatogram review | Open with **開啟 Inspector** once ready |

## Read And Use The Matrix

1. Open `alignment_matrix.tsv` for the published feature × sample areas. Check
   sample names and roles against the intended batch. Feature correspondence
   does not establish a chemical identity.
2. Investigate an unexpected value or blank in workbench result review. Select
   the feature and sample, then inspect the saved decision and available XIC
   evidence. A candidate table alone is not the final Matrix.
3. Distinguish numeric availability from detection. Zero is a numeric value;
   a blank is not automatically zero or proof that a compound is absent.
   Backfill revisits local evidence, not an interpolation of other samples'
   areas. Use the [processing story](processing-guide.html#backfill) when you
   need to understand that distinction.
4. If you need a curated Matrix, choose **整理矩陣** in the completed results.
   On **矩陣整理**, check the source folder and sample data, choose enabled
   steps and any required thresholds, then start curation. An unavailable step
   reports its missing input; it does not silently establish a decision.
5. Read the curation summary and `feature_decisions.tsv` before using the kept
   Matrix downstream. Curation writes a new `matrix_curation_<time>` folder;
   the original Matrix and Alignment decisions are preserved.

For the finite-value fraction step, zero and finite backfilled values count
as available; blank/nonfinite cells do not. QC, blank, unknown-role and excluded
samples are outside the denominator. This is availability filtering, not a
detection rate. The [Matrix Curation contract](../product/matrix-curation.md)
defines each step, inputs, decisions and output files. Normalization, calibration
and statistical analysis remain separate downstream decisions.

Saved results can be reopened with `uv run xic-workbench "path/to/batch-output"`.
Review and offline reports explain the saved result; they do not change its
peak selection, area, or group membership. Use the
[output reference](untargeted-output-reference.md) to look up a column rather
than guessing its meaning from a number.

## Further Reading

For maintainer-facing product contracts behind this workflow, see:

- [Workbench and review](../product/workbench.md)
- [Discovery](../product/discovery.md)
- [Alignment](../product/alignment.md)
- [Presets](../product/presets.md)

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Run button says settings are incomplete | Fill every visible path field and verify the files/folders exist |
| DLL load failure | Follow the reader setup notice; use RawFileReader 8.0.42 with .NET 8 or later, then restart the workbench |
| Inspector is unavailable | Check the Inspector status below the output buttons; a completed run needs a published evidence source |
| Matrix selection is disabled | The result needs its bound identity; rerun Alignment for older Matrix-only outputs |
| Need only alignment | Use `align_only` with an existing `discovery_batch_index.csv` |
| Results look unexpected | Verify RAW inputs and method settings; use the supported RawFileReader version rather than matching Xcalibur |
