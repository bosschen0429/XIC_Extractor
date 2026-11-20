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
    .venv\Scripts\python.exe -m scripts.agent_sandbox_doctor --strict --probe-write

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
| XIC_ACQUISITION_CACHE_DIR | shared Alignment RAW acquisition cache; keep it on a fast local disk |

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

For a grouping-only code or parameter change, replay measured observations
instead of launching Alignment. From a bootstrapped worktree, this synthetic
two-sample case needs no local data, preset or DLL:

    .venv\Scripts\python.exe -m scripts.run_alignment regroup --inputs tests/fixtures/synthetic-grouping.json.gz --set max_ppm=20 --output-dir output/regroup-smoke

The frozen masses (500.000 and 500.015 at the same apex) fit the snapshot's
50 ppm window but not 20 ppm: inspect `members.tsv` for the two singleton
groups and `changes.tsv` for both reassignments. This is an engineering check,
not tissue validation.

For your own data, add `--stop-after grouping --save-grouping-inputs` to the
usual Alignment command once, then pass its `grouping-inputs.json.gz` to
`regroup` across edits or worktrees. A Discovery index or Matrix alone does
not retain those observed owners. Replay tests initial membership, not new
peak detection, batch-to-batch matching, Backfill or published quantities.
See the [grouping diagnostic](../../tools/diagnostics/INDEX.md#grouping_experimentpy)
for baseline comparisons and calibration options.

To change **cross-batch pairing** without repeating within-batch grouping or
Backfill, add `--stop-after grouping --save-pairing-inputs` to the usual
Alignment command, with a declared `batch_id` in sample metadata. Capture
each batch independently, or take the separate files from one multi-batch
run. Pass one JSON per distinct batch, in any order:

    .venv\Scripts\python.exe -m scripts.run_alignment pair-batches --inputs output/batch-A/pairing-inputs/batch-1.json output/batch-B/pairing-inputs/batch-1.json --output-dir output/pairing-trial

Replay needs no RAW, DLL, Discovery index or previous pairing result. It
chooses the reference using batch sizes and the saved latest acquisition
times, then writes `batch_correspondence.tsv` (partners, unpaired reasons and
competitors) and `summary.json` (reference and map diagnostics). It does not
publish a Matrix. Group tables alone lack the calibrated feature coordinates
and measured ISTD knots: capture once if these inputs were not retained.

To change **Backfill** without repeating initial acquisition or grouping, add
`--stop-after grouping --save-backfill-inputs` to the usual scalar CID-NL
Alignment command once. Point the replay at that run's directory:

    .venv\Scripts\python.exe -m scripts.run_alignment backfill --inputs output/grouping/backfill-inputs --output-dir output/backfill-trial

The diagnostic runs the current Backfill and cell-decision code with the
captured settings and all competitors. `backfill-cells.tsv` identifies each
group/sample, area (empty for no write), decision and status. No Matrix or
cross-batch correspondence is published. Only Backfill can request RAW data;
original RAW paths, the reader and source checks still apply. The acquisition
cache is not a grouping checkpoint. Old group tables and membership snapshots
cannot supply the initial assessments.

Capture is opt-in. Keep the checkpoint at its original absolute path on the
same machine/user. Its partition files are private Python execution objects,
not exchange files: replay verifies the actual bytes against a separate
user-local receipt under `~/.xic-extractor/backfill-v1/` before decoding.
Other worktrees can read the original directory; capture again if upstream
inputs or the stored Python models change.

RAW-backed Alignment requires a bound pure CID-NL preset. Its retained runtime owns
acquisition and scheduling; CLI help defines supported execution options.
The command validates its inputs before acquisition. Use its preflight option
when settings need inspection, and keep timing outputs in the task-specific
output directory.

The live timing file is the heartbeat for interrupted runs; the final timing
file is the completed history. Use `xic-performance-diagnosis` to interpret it.

## Shell Traps

The Windows examples above use PowerShell. Match the syntax to the active
shell; formal RAW validation still requires Windows Native. Keep run outputs
under ignored `output/` and local inputs under ignored `local_validation_artifacts/`.
