# Matrix Curation

Matrix curation runs independently switchable steps on a published Alignment
Matrix and writes the result to a new folder. The workbench **矩陣整理** page
and `xic-matrix-curation` share the same owner: `xic_extractor.matrix_curation`
holds the steps, export and RAW-header sample reading, and
`xic_workbench.matrix_curation` adds the workbench's own sample sources. Curation reads only the published files. It does not reopen RAW
files, rerun Untargeted analysis, or change Alignment decisions, counted
detection, peak identity, or the source Matrix.

## Steps

Enabled steps run in a fixed sequence: remove supported isotope/adduct and other
derivative ions with `same_compound`, then apply `finite_fraction` to the remaining
features. Removed derivatives are not evaluated by later filters or promoted
when their representative is filtered out. Internal standards remain protected
at every step (see [Internal standards](#internal-standards)). A step that lacks a required input is shown as
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

`same_compound` excludes supported isotope, adduct, salt-cluster and multimer derivatives from
the statistical Matrix. The analysis aims to retain [M+H]⁺, not substitute a
stronger Na/K or other adduct as the quantitative representative. Classifying
a row as a derivative is separate from identifying its element or assigning
it to exactly one parent. An unresolved element or parent does not rescue a
supported derivative; an unclassified feature is not assumed to be an artifact.
It has no parameters; its thresholds are fixed
and recorded in the summary. It needs the run's `alignment_explanation.sqlite` in
the current table layout (compact review or full audit) and is unavailable
without its published cell values.

Evidence counts only directly detected cells (`discovery_support`) and their
observation apex:

- **Co-elution:** a supporting sample has both rows detected directly with
  apices at most 3 s apart. A pair needs at least 3 supporting samples and at
  least 80% of the samples where both rows were detected directly. Row RT is not
  used. Backfilled cells never count; when they alone would complete the
  co-elution, the pair is reviewed. A backfilled cell without a recorded apex is
  counted as unavailable.
- **Mass relation:** `e_ppm = 1e6 × (observed − predicted) / predicted` with
  `|e_ppm| ≤ 10`, where the predicted heavier m/z comes from the lighter row.
  Both use the Review's `family_center_mz` (the group's centre m/z, the mean
  of its members' m/z); this is not per-sample mass evidence. `precursor_mz` is
  never used as MS1 m/z.
- **Ratio screening:** on supporting samples whose published values are finite
  and positive, the median absolute deviation of `log10(light/heavy)` (no scale
  constant) is at most 0.1 for isotopes and 0.15 otherwise, over at least 3
  samples. A variable monomer-adduct ratio can instead use the independently
  supported carrier evidence below; isotope ratio checks are never waived.
- **Isotope abundance:** every supporting sample's heavy/light ratio stays at
  or under `n·(p13/p12)` (13C), `C(n,2)·(p13/p12)²` (13C₂) or `n·(p15/p14)`
  (15N), with `n = floor(M/12)` or `floor(M/14)` and `M` the neutral mass at
  the relation's charge assuming a proton. This is a loose upper bound; a stable
  median does not hide one sample's contradiction.

The single-pair rules remove the heavier row when it relates to the lighter one by:

| Relation | Predicted heavier m/z |
| --- | --- |
| `13C_M+1` | +1.003355 |
| `15N_M+1` | +0.997035 |
| `13C2_M+2` | +2.006710 |
| `13C_z2` | +0.501678 (z=2) |
| `41K` | +1.998119, with light/heavy median 11–18 and ≥80% of samples within 0.5–2× of 13.857 |
| `M+2_unassigned` | +1.997551; intensity-compatible with one/two Mg or two K, without assigning the element |
| `[M+Na]+` | +21.981944 |
| `[M+K]+` | +37.955882 |
| `[M+NH4]+` | +17.026549 |
| `[2M+H]+` | 2x − 1.00727647, x the lighter row's m/z |
| `[2M-H+Mg]+` | 2x + 20.96211518 |
| `[2M-H+Ca]+` | 2x + 36.93966435 |
| `+NaHCOO` | +67.98742355 (neutral sodium formate) |
| `+NaCH3COO` | +82.00307362 (neutral sodium acetate) |
| `[M+Mg+HCOO]+` | +67.974871 |
| `[M+Mg+CH3COO]+` | +81.990521 |
| `[M+Ca+HCOO]+` | +83.952420 |
| `[M+Ca+CH3COO]+` | +97.968070 |

`M+2_unassigned` requires the isotope MAD limit above plus a light/heavy
median within 0.8–1.3× one of 7.1744 (one Mg), 3.5872 (two Mg) or 6.9285
(two K), and ≥80% of ratio samples within 0.5–2× that **same** model.
The ratios use [NIST Mg](https://physics.nist.gov/cgi-bin/Compositions/stand_alone.pl?ele=Mg)
and [K](https://physics.nist.gov/cgi-bin/Compositions/stand_alone.pl?ele=K)
abundances. This is an intensity- and mass-compatible isotope classification,
not an elemental identification; overlapping envelopes or other elements may
give the same evidence. Its relation is always marked undetermined. These
thresholds have local 8RAW evidence, not cross-batch validation. Mg/Ca relation
names likewise describe compatible mass models, not confirmed chemical identities.
Neutral sodium formate/acetate can attach to an already charged ion. Their
derivatives are excluded by the single-pair checks, but they do not establish
the lighter row as [M+H]⁺. They are not interchangeable with the Mg-formate/
acetate mass differences.

#### Variable monomer-adduct ratios

A cross-sample adduct ratio above the MAD limit is not, by itself, evidence
that the rows are different compounds. A Na/K/NH₄/Mg/Ca monomer-adduct pair
that fails only ratio stability can still contribute to exclusion when:

- the same reference has at least two **other, different** monomer carrier
  forms that independently pass the original mass, co-elution and stable-ratio
  checks;
- the reference, candidate and those two witness rows all co-elute directly,
  with at least three jointly supporting samples. Two rows of the same carrier
  do not count as two forms; backfill and transitive co-elution do not count.

The original single-pair outcomes supply the witnesses. A newly corroborated
variable-ratio row cannot bootstrap another candidate. Neutral salt additions,
dimers and isotopes do not use this exception; their existing single-pair or
joint-family rules remain in force. The final direct-co-elution check with an
ultimate root still applies, so corroboration does not rescue a row whose only
connection to that root is through intervening rows.

The relation outcome is `collapse_corroborated_adduct`; its actual ratio MAD
remains in the table. Review kind `variable_ratio_adduct_supported` records
the reference, witness feature IDs, ratio MAD and final step decision, including
`decision=drop` when excluded. No output columns or RAW-reading path are added.
This is multi-ion evidence from the published Matrix, not a claim that normal
curation has read within-peak curves or MS2. Bounded 8RAW trace/mass checks
support the rule, not cross-batch validation.

#### Ion families without a detected [M+H]⁺

A joint neutral-mass hypothesis can exclude non-[M+H]⁺ rows even when the
protonated monomer has no Matrix row. It requires all of:

- at least three distinct monomer forms among H, Na, K, NH₄, Mg-formate,
  Mg-acetate, Ca-formate and Ca-acetate;
- a compatible `[2M+H]+` row, independently constraining the neutral mass;
- at least one Mg-formate/acetate monomer with a supported `M+2_unassigned`
  companion, passing the original isotope-ratio checks;
- the mass tolerance above and direct co-elution of every member with both
  the seed monomer and the protonated dimer. A chain of co-eluting rows is not
  sufficient.

Cross-sample adduct ratios need not be constant for this joint hypothesis;
the ratio screen and isotope thresholds are unchanged. A compatible
metal dimer (`[2M-H+Mg]+`, `[2M-H+Ca]+`, `[2M-2H+Na+Mg]+`,
`[2M-2H+2Mg+HCOO]+` or `[2M-2H+2Mg+CH3COO]+`) can join an established family
only with its own supported `M+2_unassigned` companion and direct co-elution
with the seed and protonated dimer. More elaborate salt combinations are not
inferred by this rule.

When an [M+H]⁺ row is measured, parallel `[nM-H+Mg]+` and `[nM-H+Ca]+` rows
with the same `n` (2 or 3) provide an alternative joint hypothesis. All three
rows must co-elute directly with each other and the Mg-compatible multimer
must have a supported `M+2_unassigned` companion. Both metal forms are
excluded without requiring a stable cross-sample adduct ratio; the [M+H]⁺
row is retained. This is not a general trimer or neutral-loss search. These
joint thresholds have bounded 8RAW diagnostic evidence, not cross-batch validation.

Non-[M+H]⁺ members are excluded with reason `non_protonated_ion_family` and
compatible models prefixed `ion_family:` in their relation field. A single
retained, directly co-eluting [M+H]⁺ member is their representative; otherwise that field
is blank. Their existing isotope/adduct descendants remain excluded, but no
longer point to a removed Na or metal row as a retained [M+H]⁺ representative
(`derived_through_non_protonated_family`). Family models and witness rows are
recorded in the review table; the element/model remains marked undetermined.
A row compatible with [M+H]⁺ in a supported family is not excluded solely by
another family's non-[M+H]⁺ interpretation; this classification conflict is
marked undetermined, not confirmed [M+H]⁺, and reviewed. Family evidence does not merge
distinct neutral-mass hypotheses or invent an unmeasured protonated row.
An earlier adduct exclusion that only expresses these competing family forms
is withdrawn; independent derivative evidence, such as an isotope relation,
still applies. The conflict review records the actual keep/drop decision.

Absence from the Matrix is not proof that an ion was not formed. In particular,
an expected [M+H]⁺ outside the acquisition's MS1 mass range was not measured,
not demonstrated absent. This rule uses the measured family, not the absence
of [M+H]⁺, as its exclusion evidence; curation still does not reopen RAW.

+CH₂, +H₂, +CH₂O, +CH₃OH and H₂O loss are only reviewed; they may be real
chemical differences. +116.04734 (a second dR) is only annotated in the
relation table. ≈1.998 pairs not explained by either isotope rule are reviewed.

Representatives are rows that no folding relation derives from another row. A
row is removed when folding relations, each passing its own checks, reach it
from at least one root and it co-elutes directly with that root by the rule
above. With one directly co-eluting root its representative is recorded. With
several, it is still excluded: its representative field is blank, its reason
is `derived_representative_undetermined`, and its candidate roots are recorded
in the review with `decision=drop`. The candidate roots are not merged or
arbitrarily selected. A sodium adduct's 13C isotope is therefore
removed into the protonated row through the adduct, while a row that co-elutes
with the representative only through other rows (A–B and B–C 2.4 s apart, A–C
4.8 s) is kept and reviewed. When
several relations fit one pair they are all recorded and
`same_compound_relation_undetermined` is TRUE. Results depend on m/z and cell
evidence only, never on row order, `feature_id` or the order of the relation
list.

A representative's ion form is `confirmed_[M+H]+` when an adduct or dimer row
(other than a neutral salt cluster) derived directly from it was removed into it,
or a supported family assigns
it only the [M+H]⁺ form, and `unconfirmed` otherwise.
Isotopes are not evidence of the ion form, and no other form is inferred.
`unconfirmed` is not reviewed. Retained unclassified roots are not claimed to
be known [M+H]⁺; this step does not perform complete ion identification.

A representative removed by a later feature filter is not replaced by one of
its removed derivatives, so the whole ion family may leave the curated Matrix.
The review lists these families; removed derivatives are not evaluated again.

### Internal standards

A row is an internal standard when its `family_center_mz` lies within 10 ppm of
a listed standard and its `family_center_rt` lies in that standard's RT range
(`rt_min`–`rt_max`; without a range, `expected_rt_min` ± 0.75 min). Internal
standards are always kept; their step columns are recorded as judged and the
final `decision_reason` is `istd_protected`. The list comes from a chosen
target list file, or else from the standards the run declared in its
`alignment_explanation.sqlite`. Without either, no row is protected.

## Inputs

The source is an Alignment output folder with `alignment_matrix.tsv` and its
bound `alignment_review.tsv`. The Review supplies `feature_id`; its Matrix rows
must pass the row-index census and row-binding validator, and non-Matrix groups
with blank `matrix_row_index` are ignored. A folder without Review cannot be
curated; rerun Alignment at an output level that emits Review. Identity is never
reconstructed from rounded m/z or RT.

Sample data is chosen in this order:

1. a sample metadata TSV/CSV the user selects;
2. `sample_metadata.tsv` saved with the Untargeted result, including the roles,
   injection order and exclusions used by that run. Statistical grouping is not
   required; curation selects eligible samples by role and exclusion only;
3. the samples carried by the current run's result, when no saved file exists;
4. the workbench's confirmed sample data for the RAW folder named by
   `alignment_run_metadata.json`;
5. the roles read from that RAW folder's headers with the same reader Alignment
   uses when it is given no sample data (Thermo sample type and the QC, blank
   and similar vocabulary in names). This needs the RAW folder and reader
   recorded in the run metadata on this computer.

The CLI uses the explicit file, saved result file, then RAW headers. When none applies, the steps that
need samples are unavailable and a sample file must be selected.

`same_compound` reads per-cell detection, value and apex from the folder's
`alignment_explanation.sqlite`. Direct detections take their apex from its
observations; backfilled cells take theirs from the backfill ledger when the
output level wrote one.

## Outputs

The output folder must be new. The default is `matrix_curation_<time>` inside
the source folder.

| File | Meaning |
| --- | --- |
| `analysis_matrix.tsv` | Kept rows in original order with original tokens and all sample columns |
| `feature_decisions.tsv` | Every source row: `source_matrix_row_index`, `feature_id`, `Mz`, `RT`, `istd_label`, each enabled step's columns, the final `decision` and `decision_reason` (`kept_by_all_steps`, `dropped_by:<steps>` or `istd_protected`) |
| `same_compound_relations.tsv` | Each mass-related row pair that co-elutes in some sample: relation, `e_ppm`, direct, supporting and backfill sample counts, largest apex difference, median light/heavy ratio, log-ratio MAD, outcome and whether it contributed to excluding the heavier row; an excluded row need not have a unique parent |
| `same_compound_review.tsv` | Pairs and groups to review: unstable ratio, ≈1.998 unexplained by the isotope rules, review-only relation, backfill-only co-elution, representative ≥10× weaker than a removed member, excluded derivatives with unassigned parent (`decision=drop`), supported non-[M+H]⁺ families (neutral mass, models and witnesses), no direct co-elution with a root, an ion family removed by a later feature filter, and recurring unexplained mass differences between rows this step keeps, most frequent first. A pair already inside one group, or between two removed ions, is not reviewed again |
| `sample_metadata.tsv` | Matrix column → sample identity, role, group, order and exclusion |
| `matrix_curation_summary.json` | Enabled steps and parameters, input paths, sample data source and counts |

`finite_fraction` contributes `finite_count`, `eligible_sample_count`,
`finite_fraction`, `finite_fraction_decision` and `finite_fraction_reason`.
Rows removed before a later step have `not_evaluated` in that step's decision
column and blank measurement fields. Preview and summary counts describe each
step's actual input, removals and remaining rows, not independent original-Matrix counts.
`same_compound` contributes `same_compound_decision`, `same_compound_reason`,
`same_compound_representative_feature_id` (removed rows),
`same_compound_relation` and `same_compound_via_feature_ids` (the relations
from the rows it derives from, and those rows when they are not the
representative, or all direct parents when attribution is unresolved),
`same_compound_relation_undetermined` (also TRUE for `M+2_unassigned` or an
unassigned parent),
`same_compound_ion_form` and `same_compound_supporting_relations` (kept rows:
how many rows were removed into it, a note rather than a threshold). Its two
tables are written only when it runs. The summary records each step's
parameters and counts, including the `same_compound` thresholds, removals by
relation, removals through the group, `removed_representative_undetermined_count`,
`removed_non_protonated_family_count`, `used_variable_ratio_adduct_pair_count`,
review counts and `empty_group_count` (uniquely assigned groups with no row left), and
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
panel opens with that run's folder, saved samples and internal standards; enter
the threshold; the preview follows the settings, and **產生新矩陣** is enabled
once it matches them. The run's own explanation supplies the evidence and internal
standards for **同化合物收斂**. To curate an earlier result, choose its Alignment output folder on
**改用其他來源（選用）** tools; select a sample data TSV only when the page reports that
none was found, and a target list only when no internal standard list is
found. The page remembers the last enabled steps and thresholds on this
computer. Untargeted runs never start curation automatically.

For batches, `xic-matrix-curation --source-dir <folder> --min-finite-fraction
<value> --same-compound` runs the same owner; a step runs when its parameters
or, for `same_compound`, its flag are given. `--istd-list` selects a target
list; without it the run's own standards are used.
`--sample-metadata` selects a file; without it the saved result's sample data is
used, falling back to RAW headers for older outputs. The module entry is `python -m scripts.run_matrix_curation`.

## Verification

`tests/test_same_compound.py` checks each folding relation, the dr95
counterexamples (15N-like pairs with too strong heavier rows, +116.047, ≈1.998
pairs incompatible with the intensity models), per-sample abundance, direct co-elution with the
representative, removal through the group, ion-form
labels, unassigned isotope and parent exclusion, ion families with/without a
protonated row, missing/indirect family witnesses, metal dimers, rule-order,
row-order and ID invariance, backfill exclusion, internal
standard matching and derivative-before-feature-filter ordering. Its thresholds come from a read-only
dr95 diagnostic, not cross-batch validation.
`tests/test_matrix_curation.py` checks independently calculated fractions and
kept rows, including zero versus missing, role/exclusion denominators, exact
sample attribution, sample data resolution and workbench/CLI agreement. Stale
Review binding, unbound samples and an invalid threshold fail before any output
folder is created. These checks prove engineering behavior, not a
method-independent scientific threshold or biological readiness.
