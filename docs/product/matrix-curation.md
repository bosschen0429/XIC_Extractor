# Matrix Curation

Matrix curation runs independently switchable steps on a published Alignment
Matrix and writes the result to a new folder. The workbench **矩陣整理** page
and `xic-matrix-curation` share the same owner: `xic_extractor.matrix_curation`
holds the steps and export, and `xic_workbench.matrix_curation` finds the
sample data. Curation reads only the published files. It does not reopen RAW
files, rerun Untargeted analysis, or change Alignment decisions, counted
detection, peak identity, or the source Matrix.

## Steps

Each enabled step judges the original Matrix with its own inputs; enabling or
disabling one step never changes another step's decision columns. A feature is
kept when every enabled step keeps it. A step that lacks a required input is
shown as unavailable with the reason. Steps are listed in
`matrix_curation.export.STEPS`; a new step adds itself there and the page, CLI
options and output columns follow.

### Finite-value fraction

`finite_fraction` is the number of finite published values in eligible samples
divided by the number of eligible samples. Numeric zero and finite backfilled
values count as available; blank and nonfinite cells do not. This is numeric
availability, not a detection rate. Cell states are not inferred from numbers.

Eligible samples have explicit `sample_role=study_sample` and `excluded=FALSE`.
Sample names and raw-stem aliases must match the Matrix columns exactly,
including the writer's formula escaping. Roles and groups are not inferred from
names. QC, blank, unknown-role and excluded samples do not enter the
denominator, but their Matrix columns remain in the delivered tables. The step
is unavailable when sample data is missing, does not cover every Matrix column,
or has no eligible study sample.

The user supplies `min_finite_fraction` in `[0, 1]`; there is no scientific
default. A feature is kept when its fraction is at least the threshold. This
step does not apply a QC CV threshold or change any value, including zero or
missingness. Group prevalence, QC correction and normalization are not part of
it.

## Inputs

The source is an Alignment output folder with `alignment_matrix.tsv` and its
bound `alignment_review.tsv`. The Review supplies `feature_id`; its Matrix rows
must pass the row-index census and row-binding validator, and non-Matrix groups
with blank `matrix_row_index` are ignored. A folder without Review cannot be
curated; rerun Alignment at an output level that emits Review. Identity is never
reconstructed from rounded m/z or RT.

Sample data is chosen in this order:

1. a sample metadata TSV/CSV the user selects;
2. the samples of the Untargeted run that produced the folder, when curation
   starts from that run's result;
3. the workbench's confirmed sample data for the RAW folder named by
   `alignment_run_metadata.json`.

## Outputs

The output folder must be new. The default is `matrix_curation_<time>` inside
the source folder.

| File | Meaning |
| --- | --- |
| `analysis_matrix.tsv` | Kept rows in original order with original tokens and all sample columns |
| `feature_decisions.tsv` | Every source row: `source_matrix_row_index`, `feature_id`, `Mz`, `RT`, each enabled step's columns, and the final `decision` |
| `sample_metadata.tsv` | Matrix column → sample identity, role, group, order and exclusion |
| `matrix_curation_summary.json` | Enabled steps and parameters, input paths, sample data source and counts |

`finite_fraction` contributes `finite_count`, `eligible_sample_count`,
`finite_fraction`, `finite_fraction_decision` and `finite_fraction_reason`.
`source_matrix_row_index` is the 1-based row position in the source Matrix;
`feature_id` is scoped by that Matrix and does not assert identity across
reruns. The kept Matrix is a row selection of a finalized product, not a new
Alignment finalization. The summary is written last; a folder without it is an
incomplete export. Downstream analysis consumes the kept rows and must not
silently repeat global feature filtering. QC drift correction and normalization
belong to the analysis backend.

## Usage

After an Untargeted run with Alignment, choose **整理矩陣** in its result. The
page opens with that run's folder and samples; enter the threshold and choose
**開始整理**. To curate an earlier result, choose its Alignment output folder on
the **矩陣整理** page; select a sample data TSV only when the page reports that
none was found. The page remembers the last enabled steps and thresholds on this
computer. Untargeted runs never start curation automatically.

For batches, `xic-matrix-curation --source-dir <folder> --min-finite-fraction
<value>` runs the same owner; a step runs when its parameters are given.
`--sample-metadata` selects a file; without it the workbench's confirmed sample
data is used (`--config-dir` selects the workbench configuration). The module
entry is `python -m scripts.run_matrix_curation`.

## Verification

`tests/test_matrix_curation.py` checks independently calculated fractions and
kept rows, including zero versus missing, role/exclusion denominators, exact
sample attribution, sample data resolution and workbench/CLI agreement. Stale
Review binding, unbound samples and an invalid threshold fail before any output
folder is created. These checks prove engineering behavior, not a
method-independent scientific threshold or biological readiness.
