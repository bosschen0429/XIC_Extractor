# Signal Processing

This page owns the reasons behind the canonical MS1 signal, physical peak
qualification, boundaries and area. The code and its tests own the formula,
parameters, thresholds and failure codes. Targeted measurement policy on top of
these physical answers is in [Targeted Selection](targeted-selection.md).

## Where Each Decision Lives

| Decision | Owner | Tests |
| --- | --- | --- |
| Canonical profile and its parameters | `configuration.signal_processing_profile` | [`test_signal_processing_profile.py`](../../tests/test_signal_processing_profile.py) |
| RT-aware AsLS baseline | `peak_detection.baseline` | [`test_baseline.py`](../../tests/test_baseline.py), [`test_baseline_integration.py`](../../tests/test_baseline_integration.py) |
| Segments, separable valleys, envelopes | `peak_detection.chrom_peak_segments` | [`test_chrom_peak_segments.py`](../../tests/test_chrom_peak_segments.py) |
| Qualification, boundaries, censored clusters, local relief, integration | `peak_detection.physical_peak_evaluation` | [`test_physical_peak_evaluation_behavior.py`](../../tests/test_physical_peak_evaluation_behavior.py) |
| Short-scale noise and SNR | `peak_detection.signal_quality` | [`test_signal_quality.py`](../../tests/test_signal_quality.py) |
| Shape-identity smoothing | `peak_detection.ms1_shape_identity` | |

## Canonical Signal

Boundary detection, area, intensity, scan support and relative-shape
comparison share one canonical positive signal: Gaussian-smoothed raw minus a
clipped, RT-aware AsLS baseline. Sharing one definition keeps consumers from
silently using different preprocessing; it is an engineering contract, not
proof that smoothing is chemically more accurate than raw integration.

- **Clipped baseline.** Extrapolated negative background must not create
  positive peak signal.
- **RT penalty.** The AsLS penalty uses retention time, not scan ordinal, so a
  change in scan density cannot make the baseline follow a broad peak. It does
  not guarantee unbiased area under every curved background.
- **Order.** `Gaussian(raw)` and `AsLS(raw)` are computed independently, then
  subtracted and clipped; the alternatives differ because AsLS is nonlinear.
  The formula and parameters are part of profile identity, so changing them
  needs a new profile version and evidence.
- **Raw intensity** is an input and an audit fact, never a second peak answer
  or a fallback area.

## Peak Qualification

Gaussian spread cannot create acquired evidence, so an ordinary peak needs
original sampling support around its top, and its relief above its accepted
boundaries must clear the local raw noise. Sampling support alone would accept
shallow ripples on a plateau; whole-contour prominence would borrow height
from a distant trough. A wide smoother would confuse resolved neighbors with
noise, which is why noise comes from normalized three-point differences,
related to [Brown and Levine (2007)](https://faculty.wharton.upenn.edu/wp-content/uploads/2012/04/2007b-Variance-estimation-in-nonparametric-regression-via-the-difference-sequence-method.pdf);
the MAD aggregation and factor are product policy that paper does not
validate.

- Censored, near-detection-limit clusters qualify by hit-density enrichment
  instead, because censoring destroys adjacency and intensity shape; no
  censored intensity is imputed.
- There is no seed-height fraction of the envelope or context maximum, so a
  remote strong peak cannot erase a local weak one before grouping.
- The qualification factor is a minimum policy, not a calibrated
  false-positive probability; these checks address missing support and low
  contrast, not every noise form or chemical identity.
- Known-reference ISTD localization has its own contour policy
  ([Targeted Selection](targeted-selection.md)) that does not relax Discovery,
  Backfill or non-ISTD Targeted qualification.
- Known limits are conservative false negatives, not proof of absence: in very
  short peaks the three-point noise estimate can include curvature, and an
  acquired dropout can suppress a weaker lobe's seed and merge a resolved pair.

## Boundaries And Envelopes

A selected interval reaching an acquired trace edge has no observed boundary
context and no numeric answer; Discovery's bounded reacquisition can recover
it. The edge tolerance outside Discovery keeps a small context-dependent AsLS
change near zero from joining a returned peak to an incomplete neighbor, but
does not guarantee window invariance.

A single trace cannot tell imperfect chromatography of one component from two
unresolved components, so no shoulder or overlap flag is emitted and
shoulder-connected lobes form one envelope. Untargeted decides one band or two
from cross-sample evidence
([Alignment](alignment.md#join-groups-and-consolidate-split-bands)); Targeted
has no such check, so its area can include an unresolved neighbor. NL evidence
does not change this physical definition, and wider acquisition context does
not widen a Targeted selection question.

### Separable valleys

A separable valley answers whether the acquired scans support two regions that
can be measured separately; it does not claim two compounds, and an apex
dropout does not establish a boundary. The result depends only on acquired
values, not peak IDs or input order.

Calibration (#1056) on the 102 reviewed regions of #1049 (8RAW and the enzyme
control) split separable doublets (B) 13/24, noise ripple or zero dropouts (A)
0/13, broad single components including peak plus plateau (W) 0/15, and
unflagged controls 0/27. The "lobe is a peak at its own scale" condition
removes splits of plateau ripple and dense-sampling noise; the missed doublets
are mostly weak isotope or adduct channels whose valley is no deeper than the
noise, and stay one envelope.

## Quantitation Context

The quantitation context is the acquired RT trace available to the physical
owner, not the full search window. Missing or clipped support keeps its
unavailable reason: no adapter invents flanks, and the writer never extends
bounds. An unresolved envelope's area is measured, not deconvolved
compound-specific area. Alternate boundary proposals stay diagnostic evidence.
Do not integrate every positive residual in a broad RT context.

### Reported height and area

Height is reported without baseline subtraction and area with it. Matrix and
workbook `Area` are the morphology area; raw area is never a fallback, so a
peak without a morphology area publishes none.

## Noise And SNR

The short-scale noise scale assumes independent, approximately Gaussian noise
and describes acquired raw noise, not noise after smoothing. Its thresholds are
quality policies, not detection probabilities or validated LOD/LOQ. A low-SNR
hint alone never blanks, zeroes or authorizes a Matrix value; Targeted's
[quantitative-support policy](decision-policy.md#targeted-quantitative-support)
combines it with missing and fragmented support. Short-peak curvature,
correlated noise and censoring remain estimator limits.

## Changing The Method

- **Smoothing versus area.** Preserving trapezoid area under an irregular RT
  grid requires `w^T S = w^T`, which a unit-sum scan-index Gaussian does not in
  general satisfy; truncating at acquired edges can lose signal. A fixed point
  count is a different duration when scan spacing changes, and symmetry does
  not keep the apex of an asymmetric or overlapping peak.
- **Area bias.** Raw area can include baseline or overlapping signal;
  neither a smaller canonical nor a larger raw area shows which is closer to
  the target, and comparable processing does not prove the bias cancels in
  ratios.
- **Evidence.** Compare trace membership, boundaries, background and area
  separately on the same input. Synthetic components test mechanisms; a
  chemical quantitation claim needs real concentration, spike or dilution
  controls, and one high-concentration standard establishes neither a
  response curve nor low-concentration accuracy.
- **Expected differences.** Area differences from any other integration are
  accepted; parity is never manufactured by a second calculation. Apex,
  boundary, height, scan-support and shape differences need bounded real-RAW
  and manual-EIC evidence before activation; changes to selected hypotheses,
  ambiguity, SUPPORT/HOLD/REJECT, Backfill status, identity or writes are
  hard-zero expected differences.
- The 9-point default is scan-rate sensitive, so other windows are method
  parameters.

## Boundary And Area Owners

The physical owner answers a closed stage-supplied peak question; it never
chooses a hypothesis, group, row, Backfill status or Matrix identity, and it
never resolves an unavailable or ambiguous question by nearest apex, height,
area or fallback. Physical questions do not cross profile-bound segment
universes, and saved answers are never rewritten. Morphology supplies boundary
and shape evidence, not identity.
