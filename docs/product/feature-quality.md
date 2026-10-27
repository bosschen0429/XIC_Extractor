# Feature Quality And Selection

The HTML workbench's **Feature Quality** action and `xic-feature-quality` evaluate a finalized
Alignment Matrix and write a separate analysis set. `xic_extractor.feature_quality` owns the in-memory
calculation; `feature_quality_io` owns input binding and bundle serialization.
The command does not reopen RAW files or change Alignment decisions, counted
detection, peak identity, or the source Matrix.

## Finite-value coverage

The first rule is `finite_fraction`: the number of finite published values in
eligible samples divided by the number of eligible samples. Numeric zero is
finite; blank and nonfinite cells are unavailable. This is numeric availability,
not a detection-rate claim. A published censored area is available without
becoming counted detection. Cell states are not inferred from numbers.

Eligible samples have explicit `sample_role=study_sample` and `excluded=FALSE`
in the canonical sample metadata TSV. Sample names and raw-stem aliases must
match the Matrix columns exactly, including the writer's formula escaping.
Roles and groups are not inferred from names. QC, blank, unknown-role and
excluded samples do not enter the denominator, but their Matrix columns remain
in the delivered tables. Missing sample bindings or no eligible study samples
cause an error before any bundle is published.

The caller must supply `--min-finite-fraction` in `[0, 1]`; there is no scientific
default. A feature is kept when its fraction is at least the supplied threshold.
Every feature receives its count, denominator, fraction, keep/drop decision and
reason. This rule does not apply a QC CV threshold or change any value, including
zero or missingness. Group prevalence, QC correction and normalization are not
part of this rule.

## Input and delivery contract

The input is the finalized `alignment_matrix.tsv`, its current bound
`alignment_review.tsv`, and an explicit canonical sample metadata TSV.
The Review Matrix subset must pass the complete row-index census and Matrix
row-binding validator. Review can also contain non-Matrix groups with blank
`matrix_row_index`; its physical row order is not used for the join. A production
export without Review cannot be used; select an output level that emits Review.
This command never reconstructs identity from rounded m/z or RT.

The output directory must be new. The bundle schema is `xic_feature_quality.v2`:

| File | Meaning |
| --- | --- |
| `full_matrix.tsv` | All original rows, sample columns and numeric/missing tokens |
| `full_review.tsv` | Original Review table, including non-Matrix groups, without renumbering |
| `analysis_matrix.tsv` | Kept rows in original order, with all sample columns |
| `analysis_features.tsv` | Selected row position → source row position, original `feature_id` and original row-binding digest |
| `feature_quality.tsv` | Metric and keep/drop reason for every source feature |
| `sample_metadata.tsv` | Matrix column → canonical sample identity, role, group, order and exclusion data |
| `feature_quality_manifest.json` | Schema, stages, exact input snapshot hashes, rule, eligible sample columns, counts and output file hashes |

`feature_id` is scoped by the source Matrix identified in the manifest;
it does not assert identity across independent reruns. The selected Matrix is a
row selection of a finalized product, not a new Alignment finalization. Use
`analysis_features.tsv` to recover the original row position; its source indices
can have gaps. The full Matrix and Review remain independently valid under
the original binding contract. Serialization may change TSV line endings, but
never numeric tokens, row identities, column order or values.

The manifest is written last. A directory without that manifest is incomplete
and must not be consumed. Source files and existing output directories are
never overwritten. Downstream analysis consumes the selected rows and records
its own numerical applicability; it must not silently repeat global feature
filtering. QC drift correction and normalization belong to the analysis backend.

## Usage and verification

In the Untargeted HTML view, export the sample table as TSV with **匯出樣品資料**.
After Alignment finishes, choose **Feature Quality** in the results, select that TSV,
enter an explicit minimum finite-value fraction and choose a new output folder.
The workbench binds the Matrix and Review from the completed result and runs the
same `export_feature_quality` owner in a background thread. It opens the output
folder only after the manifest is published. Export failures remain in the
dialog for correction. No scientific threshold is preselected.

HTML Alignment uses the existing `machine` output level to retain its bound
Review. Older results without Review cannot be filtered through this
action; rerun Alignment rather than reconstructing identity from coordinates.

Run `xic-feature-quality --help` for the command interface. The equivalent
module entry is `python -m scripts.run_feature_quality`; the Python API is
`feature_quality_io.export_feature_quality`.

Verification compares independently calculated fractions and selected original
rows, including zero versus missing, role/exclusion denominators and exact
sample attribution. Stale Review binding, missing metadata and an invalid threshold
must fail before publication. These checks prove engineering behavior, not a
method-independent scientific threshold or biological readiness.
