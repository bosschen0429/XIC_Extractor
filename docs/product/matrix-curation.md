# Matrix Curation

Matrix curation runs independently switchable steps on a published Alignment
Matrix and writes the result to a new folder. It reads only the published
files: it does not reopen RAW, rerun Untargeted analysis, or change Alignment
decisions, counted detection, peak identity or the source Matrix. The
Workbench **整理矩陣** page and `xic-matrix-curation` run the same owner;
the [Untargeted guide](../user/untargeted.md#整理矩陣) owns how to use the page.

## At A Glance

| Question | Rule | Detail |
| --- | --- | --- |
| In which order do steps run? | Same-compound collapse, then known nucleoside windows, then finite-value fraction; only enabled steps run. | [Steps](#steps) |
| Which rows does same-compound collapse remove? | Isotope, adduct, salt-cluster and multimer derivatives that co-elute, match a named mass relation within 10 ppm and pass the ratio and abundance checks; [M+H]⁺ is kept. | [Same-compound collapse](#same-compound-collapse) |
| Which rows do nucleoside windows remove? | Rows within 10 ppm of a listed reference whose RT lies in its window, and rows folded into them. | [Known nucleoside windows](#known-nucleoside-windows) |
| What does the fraction step keep? | Rows whose finite-value share over eligible study samples reaches the user's threshold. | [Finite-value fraction](#finite-value-fraction) |
| Can a step remove an internal standard? | No; listed standards are always kept. | [Internal standards](#internal-standards) |
| What is written? | A new folder with the kept Matrix, a decision for every source row, and a summary written last. | [Outputs](#outputs) |

## Steps

Enabled steps run in a fixed sequence: `same_compound`, `known_nucleosides`,
then `finite_fraction` on the remaining features. Removed derivatives are not
evaluated by later steps and are not promoted when their representative is
filtered out. Internal standards stay protected at every step. A step that
lacks a required input is shown as unavailable with the reason.

### Same-compound collapse

`same_compound` excludes supported isotope, adduct, salt-cluster and multimer
derivatives from the statistical Matrix, so the quantitative representative
stays [M+H]⁺ rather than a stronger Na, K or other adduct. Classifying a row
as a derivative is separate from identifying its element or assigning it one
parent: an unresolved element or parent does not rescue a supported
derivative, and an unclassified feature is not assumed to be an artifact. The
step has no parameters; its fixed thresholds are recorded in the summary. It
needs the run's `alignment_explanation.sqlite` in the current evidence layout
and is unavailable without its published cell values.

#### Pair evidence

Evidence uses each published cell's recorded apex. Directly detected cells
(`detected`) come first; backfilled cells only complete evidence that direct
detections leave insufficient.

- **Co-elution.** A supporting sample has both apices at most 5.5 s apart: two
  MS1 scans of the ~2.5 s CID-NL cycle. In 8RAW, mass-related pairs are as
  enriched over shifted-mass decoys at 3–5.5 s as at 1–3 s (local evidence,
  not cross-batch validation). A pair needs at least 3 supporting samples, at
  least one with both rows detected directly, and support in at least 80% of
  the samples where both were detected directly. Row RT is not used.
- **Backfilled cells.** When direct samples alone meet those conditions they
  are the pair's whole evidence. Otherwise a backfilled cell with a recorded
  apex counts like a direct one, and one without counts as unavailable.
  Backfill searches near each sample's expected RT, so its apex is weaker
  co-elution evidence; the ratio and abundance checks include those samples
  and guard it, but never overturn sufficient direct evidence.
- **Mass relation.** `e_ppm = 1e6 × (observed − predicted) / predicted` with
  `|e_ppm| ≤ 10`, where the predicted heavier m/z comes from the lighter row.
  Both use the published full-precision centre m/z (the mean of the group's
  members' m/z), not per-sample mass evidence; `precursor_mz` is never used as
  MS1 m/z. The [relation table](#relation-table) lists every relation.
- **Isotope ratio.** On all supporting samples with finite positive values,
  the median absolute deviation of `log10(light/heavy)` (no scale constant) is
  at most 0.1, over at least 3 samples. Adducts, salt clusters and multimers
  have no ratio check: their yield follows each sample's salt content and
  concentration, so a varying ratio is no evidence of different compounds. In
  8RAW, dropping the former adduct limit (0.15) added about one shifted-mass
  decoy pair per twenty new real pairs.
- **Isotope abundance.** Every supporting sample's heavy/light ratio stays at
  or under `n·(p13/p12)` (13C), `C(n,2)·(p13/p12)²` (13C₂) or `n·(p15/p14)`
  (15N), with `n = floor(M/12)` or `floor(M/14)` and `M` the neutral mass at
  the relation's charge assuming a proton. When the lighter row has a
  supported `M+2_unassigned` companion, a single-heavy-atom ceiling also
  admits 25Mg from two Mg atoms (`+2·(p25/p24)`, NIST Mg), the most those
  models allow. This is a loose upper bound; a stable median does not hide one
  sample's contradiction.

+CH₂, +H₂, +CH₂O, +CH₃OH and H₂O loss are only reviewed, since they can be
real chemical differences. +116.04734 (a second dR) is only annotated in the
relation table. ≈1.998 pairs not explained by either isotope rule are
reviewed.

#### Representatives

Representatives are rows that no folding relation derives from another row.
A row is removed when folding relations, each passing its own checks, reach it
from a root.

- Its representative is the one root whose direct detections do not
  contradict co-elution with it: at least one sample has both detected directly
  and co-eluting, and at least 80% of their both-direct samples co-elute. A
  root sharing fewer than 3 samples still qualifies.
- With several such roots, or none, the row is still excluded with a blank
  representative, reason `derived_representative_undetermined`, and its
  candidate roots (the qualifying ones, else every root reaching it) recorded
  for review with `decision=drop`; roots are never merged or picked
  arbitrarily. A sodium adduct's 13C isotope is therefore removed into the
  protonated row through the adduct. A row co-eluting with the root only
  through other rows (A–B and B–C 4.4 s apart, A–C 8.8 s) is removed without a
  representative. In 8RAW, shifted-mass decoys mixed with real relations
  removed the same rows with or without root co-elution, while the requirement
  kept derivatives of saturated peaks whose apices scatter.
- When several relations fit one pair, all are recorded and
  `same_compound_relation_undetermined` is TRUE. Results depend on m/z and
  cell evidence only, never on row order, `feature_id` or relation-list order.
- A representative's ion form is `confirmed_[M+H]+` when an adduct or dimer
  row (other than a neutral salt cluster) derives directly from it and is
  assigned to it after family resolution, or a supported family assigns it
  only the [M+H]⁺ form; otherwise `unconfirmed`, which is not reviewed.
  Isotopes are not ion-form evidence, no other form is inferred, and retained
  unclassified roots are not claimed to be [M+H]⁺; the step does not perform
  complete ion identification.
- A representative removed by a later step is not replaced by a removed
  derivative, so a whole ion family can leave the curated Matrix; the review
  lists these families.

#### Ion families without a detected [M+H]⁺

A joint neutral-mass hypothesis can exclude non-[M+H]⁺ rows even when the
protonated monomer has no Matrix row. It requires all of:

- at least three distinct monomer forms among H, Na, K, NH₄, Mg-formate,
  Mg-acetate, Ca-formate and Ca-acetate;
- a compatible `[2M+H]+` row, independently constraining the neutral mass;
- at least one Mg-formate/acetate monomer with a supported `M+2_unassigned`
  companion, passing the isotope-ratio checks;
- the mass tolerance above and direct co-elution of every member with both the
  seed monomer and the protonated dimer; a chain of co-eluting rows is not
  sufficient.

An `M+2_unassigned` witness must pass co-elution and isotope-ratio checks on
direct detections alone; Backfill can still support single-pair collapse. A
metal dimer (`[2M-H+Mg]+`, `[2M-H+Ca]+`, `[2M-2H+Na+Mg]+`,
`[2M-2H+2Mg+HCOO]+` or `[2M-2H+2Mg+CH3COO]+`) joins an established family only
with its own supported `M+2_unassigned` companion and direct co-elution with
the seed and protonated dimer. More elaborate salt combinations are not
inferred.

When an [M+H]⁺ row is measured, parallel `[nM-H+Mg]+` and `[nM-H+Ca]+` rows
with the same `n` (2 or 3) give an alternative joint hypothesis: all three
rows co-elute directly and the Mg-compatible multimer has a supported
`M+2_unassigned` companion. Both metal forms are excluded without a stable
adduct ratio, and the [M+H]⁺ row is kept. This is not a general trimer or
neutral-loss search; these joint thresholds have bounded 8RAW diagnostic
evidence, not cross-batch validation.

- Non-[M+H]⁺ members are excluded with reason `non_protonated_ion_family` and
  models prefixed `ion_family:` in their relation field. A single retained,
  directly co-eluting [M+H]⁺ member is their representative; otherwise the
  field is blank.
- Their isotope/adduct descendants stay excluded but do not point to a removed
  Na or metal row as an [M+H]⁺ representative
  (`derived_through_non_protonated_family`). Family models and witnesses are
  in the review table; the element/model stays undetermined.
- A row compatible with [M+H]⁺ in a supported family is not excluded solely by
  another family's non-[M+H]⁺ interpretation; the conflict is marked
  undetermined, not confirmed [M+H]⁺, and reviewed with its actual keep/drop
  decision. An adduct exclusion that only expresses these competing family
  forms is withdrawn; independent derivative evidence such as an isotope
  relation still applies. Family evidence does not merge distinct neutral-mass
  hypotheses or invent an unmeasured protonated row.

Absence from the Matrix is not proof an ion was not formed: an expected [M+H]⁺
outside the MS1 mass range was not measured, not shown absent. This rule uses
the measured family, not the absence of [M+H]⁺, as its evidence.

### Known nucleoside windows

`known_nucleosides` (opt-in) removes rows whose published full-precision centre
m/z is within 10 ppm of a listed reference **and** whose `group_center_rt` is
inside its inclusive window. A remaining row also leaves when a row
`same_compound` folded into it lies in a window (`member_window_match`), so a
listed nucleoside cannot stay under another row; with `same_compound` off,
nothing is removed by association. No prevalence or sample metadata is
required. A label means a window match, not confirmed identity or ISTD status.

- **Built-in list.** The [DNA dR TSV](../../xic_extractor/configuration/data/dna_dr_nucleosides.tsv)
  holds protonated dG, dT, dA and dC reference masses and RT windows for one
  DNA-digest LC method. It is offered only when the published explanation
  matches the Matrix digest and declares the single DNA_dR (116.0474 Da)
  channel, which identifies the channel, not the LC gradient. RNA ribose, RNA
  MeR, mixed and unidentified methods do **not** inherit it.
- **Custom list.** For other chromatography, a custom TSV replaces, not
  extends, the method default and needs no explanation. Each row needs a
  nonblank `label`, positive finite `mz`, and either `rt_min < rt_max` in
  minutes or, with both bounds absent, `expected_rt_min` (±0.75 min); a
  complete range takes precedence. An enabled step with a missing, empty or
  invalid list cannot export, and a list alone does not enable the CLI step.
- **Columns.** `known_nucleosides_label` (sorted distinct matches joined by
  semicolons), `known_nucleosides_decision` and `known_nucleosides_reason`.
  Previously removed rows are `not_evaluated`. A match records `drop` /
  `window_match` even when ISTD protection keeps the row; otherwise its final
  reason is `dropped_by:known_nucleosides`, even if the later fraction step
  would have removed it. The summary records the list path and actual stage
  counts, not the number of matching labels; with the step off there are no
  list outputs. To undo it, disable it and export the source to a new folder.

### Finite-value fraction

`finite_fraction` keeps a feature when its finite published values in eligible
samples, divided by the number of eligible samples, reach the user's
`min_finite_fraction` in `[0, 1]`; there is no scientific default. Numeric
zero and finite backfilled values count as available; blank and nonfinite
cells do not. This is numeric availability, not a detection rate, and cell
states are not inferred from numbers.

- Eligible samples have `sample_role=sample` and `excluded=FALSE` in the sample
  data ([Inputs](#inputs)); the step assigns no roles. Sample names and
  raw-stem aliases must match Matrix columns exactly, including the writer's
  formula escaping. QC, STD, blank, other, unspecified-role and excluded
  samples are not in the denominator, but their columns stay in the tables.
- The step is unavailable when sample data is missing, does not cover every
  Matrix column, or has no eligible study sample.
- It applies no QC CV threshold and changes no value, zero and missingness
  included. Group prevalence, QC correction and normalization are not part of
  it.
- It writes `finite_count`, `eligible_sample_count`, `finite_fraction`,
  `finite_fraction_decision` and `finite_fraction_reason`.

### Internal standards

A row is an internal standard when its full-precision centre m/z lies within
10 ppm of a listed standard and its `group_center_rt` lies in that standard's
RT range (`rt_min`–`rt_max`; without a range, `expected_rt_min` ± 0.75 min).
Internal standards are always kept; their step columns are recorded as judged
and the final `decision_reason` is `istd_protected`. The list comes from a
chosen target list, else from the standards declared in the run's
`alignment_explanation.sqlite`; without either, no row is protected.

## Inputs

The source is an Alignment folder with `alignment_matrix.tsv` and
`alignment_run_metadata.json`. Curation checks the Matrix SHA-256 and uses the
published feature IDs, full-precision masses and RT in Matrix row order. If a
file changed, open the original output instead of joining on rounded m/z.

Sample data is chosen in this order:

1. a sample metadata TSV/CSV the user selects;
2. `sample_metadata.tsv` saved with the Untargeted result, with the roles,
   injection order and exclusions that run used; statistical grouping is not
   required;
3. the samples carried by the current run's result, when no saved file exists;
4. the Workbench's confirmed sample data for the RAW folder named by
   `alignment_run_metadata.json`.

The Workbench does not open RAW or load DLLs while inspecting an imported
result; when no source applies, steps that need samples are unavailable until a
file is selected. The CLI uses the explicit file, then the saved result file;
for an older output, an explicit trusted `--dll-dir` lets it read roles from
the RAW folder in run metadata.

`same_compound` reads per-cell detection, value and apex from
`alignment_explanation.sqlite`: direct detections from its observations,
backfilled cells from the backfill ledger when the output level wrote one.

## Outputs

The output folder must be new; the default is `matrix_curation_<time>` inside
the source folder. The summary is written last, so a folder without it is an
incomplete export.

| File | Meaning |
| --- | --- |
| `analysis_matrix.tsv` | Kept rows in original order with original tokens and all sample columns |
| `feature_decisions.tsv` | Every source row: `source_matrix_row_index`, `feature_id`, `Mz`, `RT`, `istd_label`, each enabled step's columns, the final `decision` and `decision_reason` (`kept_by_all_steps`, `dropped_by:<steps>` or `istd_protected`) |
| `same_compound_relations.tsv` | Each mass-related row pair that co-elutes in some sample: relation, `e_ppm`, both-direct samples, supporting samples (backfilled ones only when direct co-elution is insufficient), the backfilled share, backfilled samples without an apex, largest apex difference, median light/heavy ratio, log-ratio MAD, outcome and whether it contributed to excluding the heavier row; an excluded row need not have a unique parent |
| `same_compound_review.tsv` | Pairs and groups to review: unstable isotope ratio, ≈1.998 unexplained by the isotope rules, review-only relation, representative ≥10× weaker than a removed member, excluded derivatives with unassigned parent (`decision=drop`), supported non-[M+H]⁺ families (neutral mass, models and witnesses), and an ion family removed by a later step. A pair already inside one group, or between two removed ions, is not reviewed again |
| `sample_metadata.tsv` | Matrix column → sample identity, role, group, order and exclusion |
| `matrix_curation_summary.json` | Enabled steps and parameters, input paths, sample data source and counts |

- Rows removed before a later step have `not_evaluated` in that step's
  decision column and blank measurement fields. Preview and summary counts
  describe each step's actual input, removals and remaining rows.
- `same_compound` writes `same_compound_decision`, `same_compound_reason`,
  `same_compound_representative_feature_id` (removed rows),
  `same_compound_relation` and `same_compound_via_feature_ids` (the relations
  from the rows it derives from, and those rows when they are not the
  representative, or all direct parents when attribution is unresolved),
  `same_compound_relation_undetermined` (also TRUE for `M+2_unassigned` or an
  unassigned parent), `same_compound_ion_form` and
  `same_compound_supporting_relations` (kept rows: how many rows were removed
  into it, a note rather than a threshold). Its two tables are written only
  when it runs.
- The summary records each step's parameters and counts, including the
  `same_compound` thresholds, removals by relation and through the group,
  `removed_by_cause`, `removed_representative_undetermined_count`,
  `removed_non_protonated_family_count`, review counts, `empty_group_count`
  (uniquely assigned groups with no row left), and the ISTD source and
  protected count.
- `source_matrix_row_index` is the 1-based row in the source Matrix;
  `feature_id` is scoped by that Matrix and asserts no identity across reruns.
- The kept Matrix is a row selection of a finalized product, not a new
  Alignment finalization. Downstream analysis consumes the kept rows and must
  not silently repeat global feature filtering; QC drift correction and
  normalization belong to the analysis backend.

## Running It

Untargeted runs never start curation automatically. In the Workbench, **整理矩陣**
opens with the run's folder, saved samples and internal standards; the preview
follows the settings, **產生新矩陣** is enabled once it matches them, and the
page remembers the last steps and thresholds on this computer. For batches:

```console
xic-matrix-curation --source-dir <alignment> --same-compound --known-nucleosides
```

A step runs when its parameters or flag are given; `--help` owns the options
and defaults. The module entry is `python -m scripts.run_matrix_curation`.

## Relation Table

The single-pair rules remove the heavier row when it relates to the lighter one
by:

| Relation | Predicted heavier m/z |
| --- | --- |
| `13C_M+1` | +1.003355 |
| `15N_M+1` | +0.997035 |
| `13C2_M+2` | +2.006710 |
| `13C_z2` | +0.501678 (z=2) |
| `41K` | +1.998119, with light/heavy median 11–18 and ≥80% of samples within 0.5–2× of 13.857 |
| `M+2_unassigned` | +1.997551; intensity-compatible with one/two Mg, two K or Zn, without assigning the element |
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
| `[M+Fe+CH3COO]+` | +113.940417 |
| `+CH2` | +14.015650 |
| `+H2` | +2.015650 |
| `+CH2O` | +30.010565 |
| `+CH3OH` | +32.026215 (methanol, mobile phase B) |
| `+H2O2` | +34.005479 |
| `delta_mz=<value>` | A difference no relation names that recurs in at least 3 co-eluting pairs of kept rows (within 0.005 Da); the weaker row of each pair folds into the stronger one |
| `[M+K]+/[M+Na]+` | +15.973938; the lighter row is itself an adduct |
| `Ca/Mg` | +15.977549; the same complex with Ca in place of Mg |
| `[M+Mg+CH3COO]+/[M+Na]+` | +60.008577 |

`M+2_unassigned` requires the isotope MAD limit plus a light/heavy median
within 0.8–1.3× one of 7.1744 (one Mg), 3.5872 (two Mg), 6.9285 (two K),
1.7732 (64Zn/66Zn) or 1.5030 (66Zn/68Zn), and ≥80% of ratio samples within
0.5–2× that **same** model. The ratios use
[NIST Mg](https://physics.nist.gov/cgi-bin/Compositions/stand_alone.pl?ele=Mg)
and [K](https://physics.nist.gov/cgi-bin/Compositions/stand_alone.pl?ele=K)
abundances. This is an intensity- and mass-compatible isotope classification,
not an elemental identification; overlapping envelopes or other elements can
give the same evidence, so its relation is always marked undetermined. These
thresholds have local 8RAW evidence, not cross-batch validation; the Zn ratios
come from one 85RAW batch whose removed rows were checked against RAW. Mg/Ca
relation names likewise describe compatible mass models, not confirmed
identities. Neutral sodium formate/acetate can attach to an already charged
ion: their derivatives are excluded by the single-pair checks, but they do not
establish the lighter row as [M+H]⁺, and they are not interchangeable with the
Mg-formate/acetate mass differences.

## Verification

- [`test_same_compound.py`](../../tests/test_same_compound.py): every folding
  relation, the dr95 counterexamples, abundance, representative co-elution,
  ion families, metal dimers, order and ID invariance, backfilled evidence
  limits, ISTD matching and step order. Its thresholds come from a read-only
  dr95 diagnostic, not cross-batch validation.
- [`test_matrix_curation.py`](../../tests/test_matrix_curation.py):
  independently calculated fractions and kept rows, zero versus missing,
  denominators, sample attribution and resolution, Workbench/CLI agreement,
  and failure before any output folder for unbound samples or an invalid
  threshold.

These prove engineering behavior, not a method-independent threshold or
biological readiness.

## Where The Code Lives

| Rule | Module |
| --- | --- |
| Steps, export and RAW-header sample reading | `xic_extractor.matrix_curation` |
| Step registry; page, CLI options and output columns follow it | `matrix_curation.export.STEPS` |
| Workbench sample sources | `xic_workbench.matrix_curation` |
