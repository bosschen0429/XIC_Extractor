# CID-NL / NL116 ML Validation Research

Date: 2026-07-24
Issue: [#125](https://github.com/Chao-hu-Lab/XIC_Extractor/issues/125)
Status: `diagnostic_only`

## Decision Summary

ML is a plausible long-term direction for reducing repeated human review, but
the current evidence does not support one row-wise random train/test split or
one universal accuracy number.

The first ML-ready oracle contract should distinguish two deployment
questions:

1. **Operational generalization**: can a known `PeakHypothesis` be judged
   correctly in a new sample, acquisition batch, or study?
2. **Discovery generalization**: can the method handle a feature or related
   identity family that was not represented during development?

These questions require different holdout boundaries. A model may succeed at
the first while failing at the second.

The minimum defensible contract is:

- group related observations instead of randomly splitting rows;
- fit every learned preprocessing, feature-selection, calibration, threshold,
  and model-selection step inside the training fold;
- separate calibration/development data from a sealed blinded holdout;
- retire a holdout from confirmatory use after its labels influence a design
  change;
- test at least one acquisition-batch or study shift before claiming routine
  cross-batch automation;
- report `inconclusive` when independent groups or precision are insufficient;
- keep peak/integration truth separate from compound-identification truth.

This research does not choose the exact group key, number of folds, sample
count, tolerance, model class, or promotion threshold.

## What Primary Sources Establish

### Related rows must not cross validation boundaries

`GroupKFold` is explicitly designed so a group does not appear in both training
and test folds. More generally, blocked validation is required when observations
have spatial, temporal, hierarchical, or other dependence; ordinary random
cross-validation can otherwise underestimate prediction error.

For XIC, technical replicates, reinjections, repeated observations from the
same biological source, and sample-cells belonging to the same identity family
must be treated as potentially dependent until characterization proves
otherwise.

Sources:

- [scikit-learn `GroupKFold`](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.GroupKFold.html)
- [Roberts et al. 2017, cross-validation for structured data](https://doi.org/10.1111/ecog.02881)
- [Metabolomics study that consolidates related observations and replicates](https://doi.org/10.1038/s41598-020-74823-1)

### The whole learned pipeline must stay inside the fold

Normalization, imputation, feature selection, calibration, threshold selection,
and similar data-dependent steps can leak test information even when the final
model never directly trains on test labels. They must be fitted using training
data only and then applied unchanged to the held-out data.

Nested cross-validation separates model and hyperparameter selection in an
inner loop from evaluation of that selection procedure in an outer loop.

Sources:

- [scikit-learn data-leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html)
- [scikit-learn nested cross-validation example](https://scikit-learn.org/stable/auto_examples/model_selection/plot_nested_cross_validation_iris.html)
- [Cawley and Talbot 2010, selection bias in model evaluation](https://jmlr.org/papers/v11/cawley10a.html)
- [FDA, Health Canada, and MHRA Good Machine Learning Practice](https://www.fda.gov/media/153486/download)

### LC-MS batch and platform shifts are part of the problem

LC-MS batch differences can affect retention time, feature alignment,
merge/split behavior, and weak-signal recovery. Post-hoc intensity correction
cannot be assumed to repair upstream peak or identity errors.

`MetaClean`, which is directly relevant to LC-MS peak-quality classification,
used development data for model construction and evaluated independent data,
including different platform or laboratory conditions. The reported
cross-platform reduction in performance illustrates why a same-dataset score
cannot stand in for transportability.

Sources:

- [Liu et al. 2020, LC/MS batch effects during preprocessing](https://doi.org/10.1038/s41598-020-70850-0)
- [`MetaClean`, ML classification of untargeted LC-MS peak quality](https://doi.org/10.1007/s11306-020-01738-3)
- [Multi-batch LCHRMS fit-for-purpose validation](https://doi.org/10.1016/j.chroma.2024.465230)

### Human EIC review does not establish compound identity

Human EIC review can adjudicate background, peak shape, apex, integration
boundary, and whether a usable single peak is present. It does not by itself
prove chemical identity.

Compound-identification confidence requires a separate claim and stronger
evidence. The Metabolomics Standards Initiative separates preprocessing and QC
from metabolite identification; authentic standards and orthogonal evidence are
needed for the strongest identification claim. Exact mass, neutral loss, or a
library-like profile alone must not be silently promoted to confirmed compound
identity.

Sources:

- [`MetaClean` peak-quality ground truth](https://doi.org/10.1007/s11306-020-01738-3)
- [MSI minimum reporting standards for chemical analysis](https://doi.org/10.1007/s11306-007-0082-2)
- [Schymanski et al. 2014 HRMS identification-confidence levels](https://doi.org/10.1021/es5002105)

### A repeatedly inspected holdout stops being independent

The test set must not guide model, feature, threshold, or calibration choices.
Once holdout labels have influenced a change, the old set may remain a
regression fixture but no longer independently confirms the revised system.

The size of a validation set should be driven by the target metric, event
frequency, and required precision rather than a universal train/test
percentage. If the available independent units cannot support the needed
precision, the honest result is `inconclusive`.

Sources:

- [Gossmann et al. 2021, overfitting to reused holdout data](https://doi.org/10.1137/20M1333110)
- [Riley et al. 2020, sample size for external validation](https://doi.org/10.1136/bmj.m441)
- [BMJ guidance on external validation](https://www.bmj.com/content/384/bmj-2023-074820)

## XIC-Specific Inferences

The following are design inferences from the sources and current XIC domain
model. They are not direct claims made by the cited publications.

### Two validation estimands

#### Operational generalization

Question: given a hypothesis already known to the system, can it correctly judge
new samples and new acquisition conditions?

Candidate isolation boundaries:

- biological source/specimen;
- technical replicate and reinjection;
- acquisition sequence or batch;
- study/site;
- instrument or acquisition-method version.

This estimand is closest to the stated operating goal: after method development,
routine batches should be machine-decided without full manual re-review.

#### Discovery generalization

Question: can the method correctly generate and judge a previously unseen
feature or related identity family?

Candidate isolation boundaries:

- `PeakHypothesis`;
- isotope/adduct/in-source-fragment family;
- known compound/profile family when that relationship is available;
- acquisition batch/study as an additional block.

Whether every family relation can be defined reliably is still unknown. The
contract should fail closed on known relations and record unresolved grouping
debt rather than claiming full unseen-family generalization.

### Deployment-matched QC is not automatically leakage

If the production protocol legitimately uses unlabeled QC samples from the new
batch for calibration, a validation run may reproduce that protocol. It must be
reported separately from a stricter external holdout that receives no new-batch
adaptation. The two scores answer different questions and must not be merged.

## Proposed Contract for Issue #125

### Dataset roles

1. **Calibration set**: human and machine workflow calibration; not scored as
   final evidence.
2. **Development set**: rule/feature/model/threshold development using grouped
   nested validation.
3. **Blinded confirmatory holdout**: sealed labels, fixed pipeline, one
   confirmatory evaluation.
4. **Shifted external slice**: a different batch, study, period, platform, or
   instrument condition chosen to match the intended deployment claim.
5. **Retired holdout fixtures**: previously unblinded cases retained for
   regression and failure-class coverage, with no independent-confirmation
   claim.

### Split and leakage rules

- Row-wise random splitting is not an accepted default.
- Related sample-cells must share a group.
- The manifest records the intended estimand and every grouping key used.
- All learned preprocessing and selection steps are fitted inside the fold.
- Threshold and probability calibration count as learned steps.
- The confirmatory holdout is sealed before final pipeline selection.
- Any post-unblinding adaptive change downgrades that holdout to
  `development_exposed`.

### Oracle fields

Keep separate fields for:

- feature/`PeakHypothesis` support;
- sample-local peak, apex, boundary, and integration quality;
- Targeted and Untargeted policy outcome;
- compound annotation/identification evidence and confidence;
- reviewer verdict, `unassessable`, repeatability, and adjudication history.

The first three may support tracer acceptance. Compound identification remains a
separate evidence claim.

### Reporting

Report at both `PeakHypothesis` and sample-cell levels, including:

- number of independent hypotheses/families, batches, studies, and instruments;
- positive, negative, ambiguous, and `unassessable` counts;
- paired Targeted/Untargeted results;
- confidence intervals or another preregistered uncertainty summary;
- same-condition and shifted-condition performance;
- all model/feature/threshold searches performed;
- holdout lifecycle and whether labels ever influenced development.

Insufficient independent groups, excessive uncertainty, or missing shifted-data
evidence produces `inconclusive` or a bounded `NARROW`, not `GO`.

## What This Evidence Does Not Decide

The literature does not determine:

- the final XIC group-key implementation;
- whether a known `PeakHypothesis` may be reused across every future study;
- the number of folds or repeated splits;
- the minimum number of hypotheses, batches, positive cases, or negative cases;
- acceptable recovery, false-positive, boundary, area, calibration, or
  performance tolerances;
- the ML model class or feature representation;
- the promotion threshold from `diagnostic_only`;
- Matrix, counted-detection, selected-area, workbook, activation, export, or
  writer authority.

Those decisions require XIC-specific calibration data, intended-deployment
scope, and a preregistered acceptance packet.

## Current Recommendation

For #125, accept the principle of group/batch-aware validation, but do not yet
freeze one universal split key. First define which claim is being tested:
operational generalization, discovery generalization, or both. Then choose the
smallest grouping boundary that prevents related evidence from crossing the
development/holdout boundary for that claim.

The current status remains `diagnostic_only`.
