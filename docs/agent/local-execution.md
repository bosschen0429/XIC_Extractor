# Local Execution

This file owns stable XIC runner, local-input, and expensive-run constraints.
CLI help owns flag inventories; Issues and validation owners own task-specific
values and artifacts.

Before repeating expensive validation, check [the diagnostic ledger](diagnostic-ledger.md) and the
active validation owner. Rerun only when current code, inputs, freshness, or the
decision oracle changed.

## Runner And Environment

Use the current checkout's .venv\Scripts\python.exe on Windows, or
.venv/bin/python on Linux, for project tests and scripts. Formal RAW validation
uses Windows Native. Each worktree owns its .venv; linked worktrees share only
.uv-cache. Do not borrow another checkout's interpreter.

A .venv belongs to one OS. When checking another Python or OS against the same
checkout, use a separate, checkout-owned environment and keep the normal .venv
intact. Set UV_PROJECT_ENVIRONMENT when syncing it. On WSL, keep that environment
and pytest temporary files on the Linux filesystem rather than a Windows mount;
keep source changes and evidence in this checkout and ignored output/.

Check a worktree after setup changes or an environment failure with:

    uv run --cache-dir .uv-cache --no-project python scripts/bootstrap_worktree.py --check --local --json

Bootstrap a new worktree once with:

    uv run --cache-dir .uv-cache --no-project python scripts/bootstrap_worktree.py --local

The bootstrap uses locked dev dependencies and creates a worktree-local copy of
the ignored local env. Treat a failed runtime check as environment uncertainty,
not a product finding.

## Local Inputs

Machine-specific values live in ignored .env.xic-local; config/env.example is
the public template.

| Environment name | Owns |
| --- | --- |
| XIC_RAW_ROOT | 85RAW tissue root |
| XIC_RAW_VALIDATION_DIR | lane-specified validation subset |
| THERMO_RAWFILE_READER_DLL_DIR | RawFileReader 8.0.42 DLL directory (not Xcalibur) |
| XIC_MANUAL_2RAW_ROOT | manual-truth calibration inputs |

Stable accepted-input paths and hashes live in
[Stable Inputs](diagnostic-ledger.md#stable-inputs). The runner validates the selected inputs;
do not silently substitute another dataset when the configured input is missing.

Reader setup is documented in [the application guide](../../README.md#run-the-html-workbench).
Use .NET 8 or later and the installed RawFileReader 8.0.42 folder. For a checkout,
run `.venv\Scripts\python.exe -m scripts.install_thermo_reader --accept-thermo-license`
after license acceptance. Set the local environment variable above to the printed
path; existing local overrides are not changed by the CLI installer.

## Validation Scale

Validation data is method-specific, not a universal ladder.

| Scale | Meaning |
| --- | --- |
| synthetic/unit | engineering regression only |
| manual-2RAW | manual-truth or resolver calibration |
| lane subset | smoke, parity, or benchmark evidence named by the owner |
| 85RAW | expensive full-tissue evidence explicitly required by the owner |

Select the data and comparison that answer the current task. Reuse existing
evidence when its producing state and inputs support that decision; a historical
run does not by itself validate changed code. Do not enlarge a run merely because
a smaller one was inconclusive.

For current CID-NL tissue validation, use the shared maintained entry point:

    .venv\Scripts\python.exe -m scripts.run_tissue_validation --dataset 8

Use `--dataset 85` from this worktree’s virtual environment for formal full-cohort
validation. A sample count on the standalone Alignment CLI checks only the index
size. Normal tissue execution validates local inputs,
prints and saves the effective plan, then runs Discovery followed by Alignment.
Add `--preflight-only` to inspect that plan without running analysis; it is not
a required extra invocation. `--help` owns runtime overrides; the plan owns the
actual paths and effective settings.

Both datasets use the same scientific preset, background library, ISTD input,
and shared [execution profile](../../xic_extractor/configuration/execution_profile.py).
`--help` lists the worker-budget options, and the preflight plan prints the
effective worker counts. The runner reads the ignored local environment file;
explicit CLI arguments and process environment take precedence. It creates a
fresh run directory and does not select historical Discovery indexes or
workbooks implicitly. The older targeted validation harness remains a separate
method, not this mainline's launch recipe. Scripts under `output/` and old
command transcripts are historical evidence, not defaults.

## Alignment And Handoff

The current command surface is:

    .venv\Scripts\python.exe -m scripts.run_alignment --help

Output semantics live in docs/product/quant-matrix.md; cell decisions live in
docs/product/decision-policy.md; preset
compatibility lives in docs/product/presets.md. Task-specific paths and accepted
parameter values stay in the Issue or validation owner.

### Replay only the stage you changed

Add only the capture flags you need to the usual full Alignment command.
This leaves its formal output as the baseline and retains the selected inputs
from the same run. Use a fresh output directory for each trial. If you only
want initial grouping results, `--stop-after grouping` still works with the
first three flags below.

| Stage | Input to retain | What to compare |
| --- | --- | --- |
| `regroup` | `--save-grouping-inputs` → `grouping-inputs.json.gz` | Initial groups, members and reassignments, after refitting batch RT calibration and drift from the retained anchors and voting the sample shift with the snapshot's direct-evidence rows; `--compare-to <capture output dir>` keeps a variant to one replay when that capture ran the current grouping code |
| `backfill` | `--save-backfill-inputs` → `backfill-inputs/` | `backfill-cells.tsv`: competing rows, sample, area, decision and status |
| `pair-batches` | `--save-pairing-inputs` → one file per declared batch | `batch_correspondence.tsv`: partners, competitors and unpaired reasons; `summary.json`: reference and RT map diagnostics |
| `matrix` | `--save-matrix-inputs` → `matrix-cells.pickle.gz` and, for multiple batches, `matrix-join.pickle.gz` | Only `alignment_matrix.tsv`, from the formal writer |

The Backfill, pairing and Matrix inputs are the compressed pickle of the formal
objects that stage consumes, so replay sees exactly what the full run handed it.
The grouping input is JSON with selected peak subtraces, not the original
acquisition traces or method assessments. Replay covers band linking, not
envelope consolidation or shape/contrast checks. Each matches only the code that captured it, and a pickle
runs code when loaded: replay captures you made yourself, and capture again
after upstream code changes. A capture in an earlier format is rejected with a
request to capture it again.

**Regroup without RAW, a preset or a DLL.** `--set` changes an `AlignmentConfig`
value; `members.tsv` shows the new membership and `changes.tsv` the
reassignments. See the
[grouping diagnostic](../../tools/diagnostics/grouping_experiment.py) for
baseline comparisons and which RT model changes replay covers.

    .venv\Scripts\python.exe -m scripts.run_alignment regroup --inputs output/baseline/grouping-inputs.json.gz --set precursor_mz_tolerance_ppm=20 --output-dir output/regroup-trial

**Replay Backfill or pairing independently.** These examples use captures in
`output/baseline`. Only Backfill may read RAW: it reuses initial assessments,
grouping and the Backfill questions planned with it (band RT locations), not
the measurements needed for new Backfill queries; recapture after changing how
those questions are planned. Pairing accepts batches captured together or
separately, in any order.

    .venv\Scripts\python.exe -m scripts.run_alignment backfill --inputs output/baseline/backfill-inputs --output-dir output/backfill-trial
    .venv\Scripts\python.exe -m scripts.run_alignment pair-batches --inputs output/baseline/pairing-inputs/batch-1.pickle.gz output/baseline/pairing-inputs/batch-2.pickle.gz --output-dir output/pairing-trial

A Backfill capture is usable once its run completes and writes the sample
roster beside the partitions.

**After a Matrix writer edit, run only this command.** No RAW, grouping,
Backfill or pair fitting is needed. For a single batch, omit `--pairing`
even when a batch ID is declared. Multiple batches require the matching join
and a common canonical injection order. The inputs retain unrounded values,
samples with no candidates, batch-qualified partners and fitted RT maps;
old report TSVs cannot substitute for them.

    .venv\Scripts\python.exe -m scripts.run_alignment matrix --backfill output/baseline/matrix-cells.pickle.gz --pairing output/baseline/matrix-join.pickle.gz --output-dir output/matrix-trial

To render a changed Backfill or pairing result, use the cells exported by
`backfill --save-matrix-inputs` or the join exported by `pair-batches`,
respectively.

RAW-backed Alignment requires a bound pure CID-NL preset. Its retained runtime owns
acquisition and scheduling; CLI help defines supported execution options.
The command validates its inputs before acquisition. Use its preflight option
when settings need inspection, and keep timing outputs in the task-specific
output directory.

The live timing file is the heartbeat for interrupted runs; the final timing
file is the completed history. Use `xic-performance-diagnosis` to interpret it.

On Windows the cache path budget is measured below the run directory: a
141-character prefix plus a 48-character cache path leaves 70 of the 259
usable characters for longer installations and batch names. Paths distribute
entries; the stored namespace, key and numeric content identify the fact.
