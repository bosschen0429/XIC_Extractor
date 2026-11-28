# tools/diagnostics/ — Diagnostic Tool Index

Each maintained diagnostic is one entry-point module in this directory, and its
module docstring owns its purpose, inputs, outputs and authority. List them
with:

    .venv\Scripts\python.exe -m tools.diagnostics

Read a tool's docstring or `--help` before creating a new one; most needs are
already covered. Shared delimited I/O lives in `xic_extractor/tabular_io.py`.
[Script and diagnostic lifecycle](../../docs/agent/script-tool-lifecycle.md)
owns adding and retiring tools.

## Choose the Workflow

`scripts/` and `tools/` contain different kinds of adapters; their directory
does not determine whether an operation is a user feature. Start with the
workflow owner rather than chaining similarly named commands.

| Task | Entry and owner |
| --- | --- |
| Compare two saved `members.tsv` tables from the same observations | `tools.diagnostics.grouping_experiment compare` — no snapshot or regrouping; [details](grouping_experiment.py) |
| Regenerate only the Matrix from completed Backfill and cross-batch pairing | `scripts.run_alignment matrix` from finalized cells and the saved batch join; [capture and replay](../../docs/agent/local-execution.md#alignment-and-handoff) |
| Match finished batches without RAW, within-batch grouping or Backfill | `scripts.run_alignment pair-batches` from per-batch `--save-pairing-inputs` files; [capture and replay](../../docs/agent/local-execution.md#alignment-and-handoff) |
| Rerun Backfill after a completed grouping, keeping competing rows together | `scripts.run_alignment backfill` from `--save-backfill-inputs`; [capture and replay](../../docs/agent/local-execution.md#alignment-and-handoff) |
| Regroup from a saved `grouping-inputs.json.gz` snapshot | `scripts.run_alignment regroup` — no RAW or Backfill; [details](grouping_experiment.py) |
| Produce initial groups from a Discovery batch index and matching RAW files | `scripts.run_alignment --stop-after grouping`; add `--save-grouping-inputs` only to retain a replay snapshot; [details](grouping_experiment.py) |
| Query measured candidates in a saved Discovery batch | `tools.diagnostics.grouping_experiment query` — no RAW or grouping; [details](grouping_experiment.py) |
| Extract, discover, or align samples | [GUI and CLI user guides](../../docs/user/README.md) |
| Inspect chromatograms | GUI **開啟 Inspector** or `xic-inspector`; [Workbench and review](../../docs/product/workbench.md) owns what review may mean |
| Select Matrix features for downstream analysis | GUI **矩陣整理** or `xic-matrix-curation`; `xic_extractor.matrix_curation` owns the steps and output; [Matrix Curation](../../docs/product/matrix-curation.md) keeps their boundaries |
| Build a solvent-blank background library | `scripts.extract_blank_library`; `xic_extractor.discovery.blank_qualification` owns extraction and serialization |
| Run tissue validation | `scripts.run_tissue_validation`; [execution settings](../../docs/agent/local-execution.md#validation-scale) own prerequisites |
| Score one peak quantified in several Matrix rows | `tools.diagnostics.duplicate_quantification --alignment-dir <validation output>`; [Duplicate quantification](duplicate_quantification.py) |
| Maintain CI or command safety | `tools.testing.test_selection` and `tools.command_policy` serve CI/hooks; they have no GUI workflow |
