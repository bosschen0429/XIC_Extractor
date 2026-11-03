# Matrix Curation

Matrix curation runs independently switchable steps on a published Alignment
Matrix and writes the result to a new folder. The workbench **矩陣整理** page
and `xic-matrix-curation` share the same owner: `xic_extractor.matrix_curation`
holds the steps, export and RAW-header sample reading, and
`xic_workbench.matrix_curation` adds the workbench's own sample sources. Curation reads only the published files. It does not reopen RAW
files, rerun Untargeted analysis, or change Alignment decisions, counted
detection, peak identity, or the source Matrix.

## Steps

Each enabled step judges the original Matrix with its own inputs; enabling or
disabling one step never changes another step's decision columns. A feature is
kept when it is an internal standard (see [Internal standards](#internal-standards))
or every enabled step keeps it. A step that lacks a required input is shown as
unavailable with the reason. Steps are listed in
`matrix_curation.export.STEPS`; a new step adds itself there and the page, CLI
options and output columns follow.

### Finite-value fraction

`finite_fraction` is the number of finite published values in eligible samples
divided by the number of eligible samples. Numeric zero and finite backfilled
values count as available; blank and nonfinite cells do not. This is numeric
availability, not a detection rate. Cell states are not inferred from numbers.

Eligible samples have `sample_role=study_sample` and `excluded=FALSE` in the
sample data (see [Inputs](#inputs)). Sample names and raw-stem aliases must
match the Matrix columns exactly, including the writer's formula escaping. The
step itself assigns no roles; it uses the roles its sample data carries. QC, blank, unknown-role and excluded samples do not enter the
denominator, but their Matrix columns remain in the delivered tables. The step
is unavailable when sample data is missing, does not cover every Matrix column,
or has no eligible study sample.

The user supplies `min_finite_fraction` in `[0, 1]`; there is no scientific
default. A feature is kept when its fraction is at least the threshold. This
step does not apply a QC CV threshold or change any value, including zero or
missingness. Group prevalence, QC correction and normalization are not part of
it.

### Same-compound collapse

`same_compound` keeps one row per compound when isotope, adduct and dimer rows
of it are separate Matrix rows. It has no parameters; its thresholds are fixed
and recorded in the summary. It needs the run's `cid_nl_explanation.sqlite` in
the current compact review format and is unavailable without it.

Evidence counts only directly detected cells (`discovery_support` or
`reacquired_support`) and their observation apex:

- **Co-elution:** a supporting sample has both rows detected directly with
  apices at most 3 s apart. A pair needs at least 3 supporting samples and at
  least 80% of the samples where both rows were detected directly. Row RT is not
  used. Backfilled cells never count; when they alone would complete the
  co-elution, the pair is reviewed. A backfilled cell without a recorded apex is
  counted as unavailable.
- **Mass relation:** `e_ppm = 1e6 × (observed − predicted) / predicted` with
  `|e_ppm| ≤ 10`, where the predicted heavier m/z comes from the lighter row.
  Both use the Review's `matrix_mz` (row-center m/z, recorded with
  `center_mz_basis`); this is not per-sample mass evidence. `precursor_mz` is
  never used as MS1 m/z.
- **Stable ratio:** on supporting samples whose published values are finite
  and positive, the median absolute deviation of `log10(light/heavy)` (no scale
  constant) is at most 0.1 for isotopes and 0.15 otherwise, over at least 3
  samples.
- **Isotope abundance:** every supporting sample's heavy/light ratio stays at
  or under `n·(p13/p12)` (13C), `C(n,2)·(p13/p12)²` (13C₂) or `n·(p15/p14)`
  (15N), with `n = floor(M/12)` or `floor(M/14)` and `M` the neutral mass at
  the relation's charge assuming a proton. This is a loose upper bound; a stable
  median does not hide one sample's contradiction.

The heavier row is removed when it relates to the lighter one by:

| Relation | Predicted heavier m/z |
| --- | --- |
| `13C_M+1` | +1.003355 |
| `15N_M+1` | +0.997035 |
| `13C2_M+2` | +2.006710 |
| `13C_z2` | +0.501678 (z=2) |
| `41K` | +1.998119, with light/heavy median 11–18 and ≥80% of samples within 0.5–2× of 13.857 |
| `[M+Na]+` | +21.981944 |
| `[M+K]+` | +37.955882 |
| `[M+NH4]+` | +17.026549 |
| `[2M+H]+` | 2x − 1.00727647, x the lighter row's m/z |

+CH₂, +H₂, +CH₂O, +CH₃OH and H₂O loss are only reviewed; they may be real
chemical differences. +116.04734 (a second dR) is only annotated in the
relation table. Other ≈1.998 pairs (65Cu, 37Cl or unknown) are reviewed.

Representatives are rows that no folding relation derives from another row. A
row is removed when folding relations, each passing its own checks, reach it
from exactly one representative and it co-elutes directly with that
representative by the rule above. A sodium adduct's 13C isotope is therefore
removed into the protonated row through the adduct, while a row that co-elutes
with the representative only through other rows (A–B and B–C 2.4 s apart, A–C
4.8 s) is kept and reviewed, as is a row reached from two representatives. When
several relations fit one pair they are all recorded and
`same_compound_relation_undetermined` is TRUE. Results depend on m/z and cell
evidence only, never on row order, `feature_id` or the order of the relation
list.

A representative's ion form is `confirmed_[M+H]+` when an adduct or dimer row
derived directly from it was removed into it, and `unconfirmed` otherwise.
Isotopes are not evidence of the ion form, and no other form is inferred.
`unconfirmed` is not reviewed.

A representative removed by another enabled step is not replaced by one of its
members, so the whole group may leave the curated Matrix. The review lists each
such group whose removed member passes the other steps.

### Internal standards

A row is an internal standard when its `matrix_mz` lies within 10 ppm of a
listed standard and its `matrix_rt` lies in that standard's RT range
(`rt_min`–`rt_max`; without a range, `expected_rt_min` ± 0.75 min). Internal
standards are always kept; their step columns are recorded as judged and the
final `decision_reason` is `istd_protected`. The list comes from a chosen
target list file, or else from the standards the run declared in its
`cid_nl_explanation.sqlite`. Without either, no row is protected.

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
   `alignment_run_metadata.json`;
4. the roles read from that RAW folder's headers with the same reader Alignment
   uses when it is given no sample data (Thermo sample type and the QC, blank
   and similar vocabulary in names). This needs the RAW folder and reader
   recorded in the run metadata on this computer.

The CLI uses only the first and last sources. When none applies, the steps that
need samples are unavailable and a sample file must be selected.

`same_compound` reads per-cell detection, value and apex from the folder's
`cid_nl_explanation.sqlite`. Direct detections take their apex from its
observations; backfilled cells take theirs from the backfill ledger when the
output level wrote one.

## Outputs

The output folder must be new. The default is `matrix_curation_<time>` inside
the source folder.

| File | Meaning |
| --- | --- |
| `analysis_matrix.tsv` | Kept rows in original order with original tokens and all sample columns |
| `feature_decisions.tsv` | Every source row: `source_matrix_row_index`, `feature_id`, `Mz`, `RT`, `istd_label`, each enabled step's columns, the final `decision` and `decision_reason` (`kept_by_all_steps`, `dropped_by:<steps>` or `istd_protected`) |
| `same_compound_relations.tsv` | Each mass-related row pair that co-elutes in some sample: relation, `e_ppm`, `center_mz_basis`, direct, supporting and backfill sample counts, largest apex difference, median light/heavy ratio, log-ratio MAD, outcome and whether it removed a row |
| `same_compound_review.tsv` | Pairs and groups to review: unstable ratio, ≈1.998 not 41K, review-only relation, backfill-only co-elution, representative ≥10× weaker than a removed member, competing representatives, no direct co-elution with the representative, representative dropped while a member passes, and recurring unexplained mass differences, most frequent first |
| `sample_metadata.tsv` | Matrix column → sample identity, role, group, order and exclusion |
| `matrix_curation_summary.json` | Enabled steps and parameters, input paths, sample data source and counts |

`finite_fraction` contributes `finite_count`, `eligible_sample_count`,
`finite_fraction`, `finite_fraction_decision` and `finite_fraction_reason`.
`same_compound` contributes `same_compound_decision`, `same_compound_reason`,
`same_compound_representative_feature_id` (removed rows),
`same_compound_relation` and `same_compound_via_feature_ids` (the relations
from the rows it derives from, and those rows when they are not the
representative), `same_compound_relation_undetermined`,
`same_compound_ion_form` and `same_compound_supporting_relations` (kept rows:
how many rows were removed into it, a note rather than a threshold). Its two
tables are written only when it runs. The summary records each step's
parameters and counts, including the `same_compound` thresholds, removals by
relation, removals through the group, review counts and `empty_group_count` (groups with no row left), and
the internal standard source and protected count.
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
**開始整理**. The run's own explanation supplies the evidence and internal
standards for **同化合物收斂**. To curate an earlier result, choose its Alignment output folder on
the **矩陣整理** page; select a sample data TSV only when the page reports that
none was found, and a target list only when no internal standard list is
found. The page remembers the last enabled steps and thresholds on this
computer. Untargeted runs never start curation automatically.

For batches, `xic-matrix-curation --source-dir <folder> --min-finite-fraction
<value> --same-compound` runs the same owner; a step runs when its parameters
or, for `same_compound`, its flag are given. `--istd-list` selects a target
list; without it the run's own standards are used.
`--sample-metadata` selects a file; without it roles are read from the run's RAW
headers. The module entry is `python -m scripts.run_matrix_curation`.

## Verification

`tests/test_same_compound.py` checks each folding relation, the dr95
counterexamples (15N-like pairs with too strong heavier rows, +116.047, ≈1.998
pairs at ratio 7–10), per-sample abundance, direct co-elution with the
representative, removal through the group, ion-form
labels, rule-order, row-order and ID invariance, backfill exclusion, internal
standard matching and step independence. Its thresholds come from a read-only
dr95 diagnostic, not cross-batch validation.
`tests/test_matrix_curation.py` checks independently calculated fractions and
kept rows, including zero versus missing, role/exclusion denominators, exact
sample attribution, sample data resolution and workbench/CLI agreement. Stale
Review binding, unbound samples and an invalid threshold fail before any output
folder is created. These checks prove engineering behavior, not a
method-independent scientific threshold or biological readiness.
