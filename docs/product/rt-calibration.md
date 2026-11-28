# Retention Time Calibration

Retention Time (RT) calibration places every injection on a common RT
coordinate frame so cross-sample correspondence, Backfill forecasting and
review can compare observations across runs. It models run-to-run retention
drift, not compound identification. The code and its tests own the fits,
thresholds, voting rules and output columns; this page keeps the boundaries,
reasons, evidence and limits. [Alignment](alignment.md) describes how grouping
consumes the frame.

## Where Each Decision Lives

| Decision | Owner | Tests |
| --- | --- | --- |
| Anchor observations both models read | `alignment.rt_anchors` | [`test_rt_anchors.py`](../../tests/test_rt_anchors.py) |
| Knots, robust LOESS fit, ISTD landmark bound, graded query, RT anchor table | `alignment.sample_rt_calibration` | [`test_sample_rt_calibration.py`](../../tests/test_sample_rt_calibration.py) |
| Known-ISTD RT correspondence | `alignment.drift_evidence` | [`test_alignment_drift_evidence.py`](../../tests/test_alignment_drift_evidence.py) |
| Early segment of an under-equilibrated injection | `alignment.early_segment_shift` | [`test_early_segment_shift.py`](../../tests/test_early_segment_shift.py) |
| Batch drift model (B, M, coefficients) | `alignment.rt_drift` | [`test_rt_drift.py`](../../tests/test_rt_drift.py) |
| Declared batches and their pairing | `alignment.batch_correspondence` | [`test_batch_correspondence.py`](../../tests/test_batch_correspondence.py) |
| Frozen map access for consumers | `SampleRtFrame` | |

## Boundaries

- **Frozen once.** Each batch gets one frozen map from fixed bootstrap anchors;
  final regrouping and Backfill never feed their results back into it, and
  replay refits only from the same retained anchors.
- **Measured anchors only.** Missing ISTDs, predicted coordinates and Backfill
  observations are never imputed as anchors; anchor quorums qualify anchors,
  not features.
- **Display changes nothing.** Replayed coordinates change display, not
  measured signal, area, grouping or quantification; display alignment cannot
  repair grouping built without the intended ISTD input.
- **Batches stay apart.** A declared batch aligns alone; pairing relates
  finished Matrix groups and never changes a batch's membership. There is no
  cross-batch Backfill.
- **Calibration is not identity.** Unsupported calibration is not measured zero
  drift; a fitted ISTD residual does not reselect that standard's peak.

## Why It Works This Way

- **Removing a peak only after grouping is not equivalent** to excluding it
  from anchors: it may have set a channel center or changed which bootstrap
  bands formed. Final rows and coefficients are therefore inferred on the full
  population.
- **Eight positions** is a conservative application support floor per
  injection, not a universal LOESS theorem; local support, degree, bandwidth
  and sampling density still matter
  ([Cleveland (1979)](https://doi.org/10.1080/01621459.1979.10481038),
  [NIST LOESS guidance](https://www.itl.nist.gov/div898/handbook/pmd/section1/pmd144.htm)).
  A deficient fit is unavailable rather than replaced by a two-point line.
- **Folds are merged locally.** In 85RAW two Tumor injections folded at one
  pair 0.04 min apart, where the offset drops from about +40 s to about 0 s.
- **ISTDs are bounded landmarks.** Large or order-conflicting standard
  residuals stay visible rather than forcing the curve through every standard;
  the bound limits influence and is not a confidence interval.
- **Graded queries** let Backfill forecast outside the strong fit while
  keeping order and a unique inverse; a weak answer is a forecast, not
  extended strong support.
- **Under-equilibrated injections.** After a short method (a 15 min Blank or
  an 8 min SDOLEK) the next injection's early compounds elute early (#786):
  shift = A × g(RT). The map absorbs it only from about 9 min, where anchors
  exist, so early peaks kept shifts of up to two minutes and fell out of their
  bands. g was measured on the 60 min dna_dr gradient; another gradient needs
  it measured again, and single compounds at 4–7 min spread from 1.2 to 3.1
  times A. Only band linking reads the correction.
- **Batch pairing windows** follow the ISTD leave-one-out error, so a region
  the ISTDs measure well keeps a narrow window; the multiplier 2 and 0.2 min
  floor were fixed before results were read.

## Limits

- Batch maps extrapolate from the endpoint, and leave-one-out errors measure
  only between ISTDs: drift bending beyond the outermost ISTD is invisible, so
  the method needs ISTDs at both ends of the gradient or nonlinear drift there
  can pair a feature with a neighboring same-channel feature.
- Graded-query support facts are descriptive, not probabilities or bounds on
  true error.

## Drift beyond the map

The user guide's [RT 漂移模型](../user/rt-drift.md) page explains this section
with figures; keep it consistent when the model changes.

The general map moves every compound at one RT together. A few compounds move
more, and differently: after general calibration,

> RT(c, s) = center(c) + a(c) × B(s) + b(c) × M(s) + error

- **Batch state B** is the batch's slow chromatographic change along injection
  order. Pooled QC, a fixed matrix, carries it alone.
- **Sample shift M** is each sample's own retention offset, felt only by some
  compounds (a matrix effect is the likely cause; tissue type explains little of
  it, so it is measured per sample).
- **a, b** are one group's two coefficients; ordinary compounds have both near
  zero.

B and M are frozen once, after the general fit and before final grouping, from
method channels' only direct-supported peaks, so compounds that drift too far
to link before calibration still contribute; channels measure drift only and do
not anchor the general map. Unknown M is not zero, and the zero of M is
arbitrary because a constant is absorbed by the group center. Shapes are
frozen per drift anchor rather than as one batch-wide curve because early and
late RT drift differently. No group fits a free curve during grouping, and
Backfill reads the accepted band's coefficients without refitting them.
Observed RT, peak boundaries and area are unchanged.

Known limits: without pooled QC, M is measured against the samples' running
trend, so injecting samples in tissue blocks mixes time and tissue. A
non-anchor drifting compound near the RT boundary between two shape types can
borrow the wrong shape; isomers in different injection segments can be
explained by a segment-aligned M only when the shift within each segment does
not contradict it. A seed's coefficients come only from seed members: a seed
holding two lines in some injections cannot merge with the rest of either, and
an earlier seed can take peaks a later seed's fit would have joined. Pieces of
one line more than eight injections apart, with no detected peak between them,
stay separate rows.
