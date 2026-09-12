# Untargeted Discovery

Use this workflow when you want to discover candidate features from RAW files,
align them across samples, and open reviewable outputs from the GUI.

![Untargeted Discovery workspace](../../assets/screenshots/untargeted-workspace.png)

## Workflow

1. Open `XIC_Extractor.exe`, or run `uv run python -m gui.main` in a developer
   checkout.
2. Select **Untargeted Discovery**.
3. Choose a run mode.
4. Set RAW input, Thermo DLL directory, and output directory.
5. Import the Word **上機資訊** to match file names and injection order. Check
   unmatched or ambiguous entries; alphabetical file order is not injection order.
6. Check the **內標清單**. The bundled example is selected by default; only
   `is_istd=TRUE` rows are used. Other analyte rows are counted as skipped.
7. Choose a CID-NL starting method and open **編輯方法…** to select dR, R, MeR,
   or custom NL tags and their combination. Apply the method to the current run.
8. Press **開始執行** and start review from the output directory.

The GUI stores local Untargeted settings in `config/discovery_gui.json`. This is
machine-local state and should stay untracked.

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
| DLL directory | Folder containing Thermo RawFileReader DLLs |
| Output directory | Where candidate and Alignment artifacts are written |
| Acquisition information | Word sequence table supplying file/sample matching and injection order |
| Internal-standard list | Shared targets CSV; Untargeted consumes only `is_istd=TRUE` rows |
| Preset | CID-NL method with selected tags and combination |
| Advanced overrides | Optional per-run method changes layered on the preset |

The target list uses the same format as Targeted. Save a copy of the bundled
example, edit it in Excel or the system's associated application, then reload
it in the GUI. Keep the CSV headers and the `is_istd` flag. There is no separate
Untargeted list to maintain. A supplied file with no internal standards shows an
error; clearing the field runs without an internal-standard list.

## Outputs

The default Alignment run writes only `alignment_matrix.tsv`. Other Alignment
TSV, HTML, and XLSX artifacts remain available through explicit machine,
debug, validation, or artifact options when a review or diagnostic workflow
needs them.

Start with the file that matches your review question:

| Output | Content | Use it for |
| --- | --- | --- |
| `discovery_candidates.csv` | Per-sample candidate features with m/z, RT, area, seed evidence | Detailed candidate inspection per sample |
| `discovery_review.csv` | Compact subset of candidate fields with confidence | Quick review triage |
| `discovery_batch_index.csv` | Links each sample's candidate/review CSVs | Input for alignment (generated automatically) |
| `alignment_matrix.tsv` | Samples as columns, aligned features as rows, area values in cells | Default cross-sample quantitative output |
| `alignment_review.tsv` | Alignment quality, owner identity, backfill status per cell | Explicit review/diagnostic output |
| Review / gallery HTML | Visual plots of candidate peaks and backfill context | Explicit human inspection output |

The GUI surfaces the matrix by default. Explicit review or diagnostic runs may
also produce the corresponding output files.

## Further Reading

For maintainer-facing product contracts behind this workflow, see:

- [Untargeted GUI contract](../product/untargeted-gui.md)
- [Discovery](../product/discovery.md)
- [Alignment](../product/alignment.md)
- [Presets](../product/presets.md)

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Run button says settings are incomplete | Fill every visible path field and verify the files/folders exist |
| DLL load failure | Point the DLL directory to the Thermo RawFileReader install folder |
| No gallery opens | Check primary discovery/alignment outputs first; gallery generation is best-effort |
| Need only alignment | Use `align_only` with an existing `discovery_batch_index.csv` |
| Results look unexpected | Verify RAW files are valid and DLL version matches your Xcalibur install |
