# Retention Time Calibration

Retention Time (RT) calibration places every injection on a common RT coordinate
frame so cross-sample correspondence, Backfill forecasting and review can compare
observations across runs. It models run-to-run retention drift, not compound
identification.

[`sample_rt_calibration`](../../xic_extractor/alignment/sample_rt_calibration.py)
owns the fit and [`rt_drift`](../../xic_extractor/alignment/rt_drift.py) the
anchor observations and drift model; downstream consumers access the frozen map
through `SampleRtFrame`. Shared terminology remains owned by the
[Domain Glossary](domain-glossary.md). See [Alignment](alignment.md) for how
grouping consumes this frame.

## Calibration Contract

### Anchor qualification

General anchors come from method channels, not from initial grouping. A
channel is one CID-NL method subject (precursor m/z and neutral loss). When a
sample has exactly one owner in that channel with direct method support,
quantifiable integration and a primary identity event, its apex RT is that
channel's observation in that sample. Several such owners leave the sample
unknown for the channel; no peak is chosen among them. A unique peak is a
low-ambiguity candidate, not an identity confirmation. Initial grouping
membership is not consulted, so channels that drift too far to group before
calibration still contribute.

Known-ISTD reference acquisition reuses Targeted's authoritative peak selection
and credible-anchor decisions rather than adding a separate rule that makes all
candidates unusable whenever multiple candidates appear. An imported reference
workbook retains its declared measurement contract; the reader does not add MS2
qualification. See the
[Untargeted guide](../development/command-line.md#alignment-cli-inputs) for CLI
input selection.

| Mechanism | Current source and use |
| --- | --- |
| Known-ISTD reference | Exact identity bindings and measured coordinates. Bootstrap comparison uses the shared fitter with ISTDs only; final calibration combines those measurements with channel anchors. |
| Sample RT calibration | One frozen map fitted from the complete request's measured ISTDs and channel anchors. Final Grouping, Backfill localization/admission, and replay consume this map through `SampleRtFrame`. |
| Batch drift model | Frozen batch state (B), sample shift (M) and drift anchors; see [Drift beyond the map](#drift-beyond-the-map). |
| Peak trajectory | Ordered correspondences and direct/MS1-only positions, used for membership constraints and sample-local localization. |
| Calibration diagnostic | Uses the same fit owner with detected-cell inputs and an interleaved held-out comparison. Its files do not alter production results. |

[`rt_drift`](../../xic_extractor/alignment/rt_drift.py) owns the channel
observations and the batch model; [`sample_rt_calibration`](../../xic_extractor/alignment/sample_rt_calibration.py)
owns the fit. A channel's reference is its same-cohort median observed RT.
Channels need observations in at least half the declared cohort (rounded up),
with at least three measured members. These are anchor qualifications, not
feature-retention rules: other observations remain available to grouping.
Exact duplicate sample/RT patterns contribute only once. An ordinary
observation whose offset differs from its sample's median offset by more than
one minute is removed before the fit: it is another peak or a gross error, and
at a sparsely supported edge it can bend the map. Measured ISTDs require at
least two same-cohort measurements, but not ordinary-feature prevalence.
Missing ISTDs, predicted coordinates and Backfill observations are not imputed
as anchors. Declared batch IDs define cohorts; empty IDs share an unspecified
cohort and sample role does not define the cohort.

The map is fitted twice. After the first fit, a channel whose calibrated RT
still has a robust spread of at least 12 seconds is left out of the second fit:
it either drifts beyond what compounds at its RT share, or mixes peaks across
samples. Neither can anchor the shared map.

### Robust LOESS fit

Each sample uses robust local-linear regression of reference-minus-observed RT,
with a 0.3 neighborhood fraction and three residual reweightings. Repeated
observed RT positions receive one median reference vote. A new fit requires at
least eight distinct measured RT positions, and at least eight positions must
retain nonzero robust weight in the final fit. Local neighborhoods include at
least eight positions; they expand when rejected points would otherwise fill
the final neighborhood. Distance weights remain unequal: eight positions are
not a guarantee of precision or dense coverage. A deficient strong fit is
unavailable, not replaced by a two-point line or interpolated pseudo-anchors.
Non-monotone strong fits also remain unavailable. With only ISTDs, contradictory
landmark order cannot establish strong calibration from the same
measurements. Ordinary corroboration can support a fit despite a conflicting
standard; fitted standard residuals remain visible and do not reselect that
standard's peak.

Eight is a conservative application support floor, not a universal LOESS
theorem. Local support, polynomial degree, bandwidth and sampling density still
matter; see [Cleveland (1979)](https://doi.org/10.1080/01621459.1979.10481038)
and the [NIST LOESS guidance](https://www.itl.nist.gov/div898/handbook/pmd/section1/pmd144.htm).
It counts RT positions per injection, not the number of samples in the batch.
The fit consumes fixed channel anchors once; final regrouping and Backfill do
not feed their own results back into that fit. Saved frozen maps keep their
coordinates on replay and are not refitted under this floor.

### ISTD landmark bounded influence

Within a usable fit, a measured ISTD also supplies a bounded local landmark.
Its fitted coordinate can move toward its measured cohort reference by at most
six times the median absolute residual of the final nonzero-weight positions,
using the same residual multiplier as the robust fit. Rejected correspondences
do not enlarge this influence limit. The adjustment must preserve order against
both the original neighboring fitted coordinates and neighboring landmark
proposals; conflicting proposals keep their original fitted values. It changes
only an existing measured knot, not neighboring knots, the observed support
range, or the count of supported positions. The frozen interpolation therefore
carries this local correction without a second ISTD override. Large or
order-conflicting standard residuals remain visible rather than forcing the
curve through every standard. This bound limits influence; it is not a
statistical confidence interval or independent identity verification.

### Graded query support

The strong-only map and inverse remain bounded by the supported anchor range.
Final membership and localization explicitly use a graded query instead:

- Inside a usable curve, preserve its LOESS coordinate exactly.
- Outside it, use the nearest endpoint's constant shift (`boundary_shift`).
  This weak continuation joins continuously and preserves order and a unique
  inverse; it does not extend strong calibration support.
- With at least two qualified distinct positions but no usable strong fit,
  use their median reference-minus-observed shift (`median_shift`). Zero or
  one position does not establish a batch shift. Failed strong-fit status is
  retained, not relabelled as strong support.

The frozen measured support is prepared once. Each answer carries its method,
qualified position count, measured range, model residual spread, nearest-anchor
distance and distance outside model support. These are descriptive facts, not
probabilities or bounds on true error. Weak estimates multiply RT costs by 0.5,
in addition to signal-quality weighting; mass and independent shape costs are
unchanged. Accepted membership risks retain the applied query facts in the
review outputs. Weak localization uses an `estimated_` basis and remains a
forecast, not a physical observation. Neither query changes observed RT, peak
boundaries, area, identity, or Backfill admission rules.

Calibration schema v3 retains the measured support for these queries. Legacy
v1/v2 coordinates remain exact and bounded, with quality unrecorded; old knots
do not create weak estimates. Bootstrap's strong-only APIs and ISTD-only unique
same-target fallback retain their existing meaning.

### Persisted RT replay

Explanation summaries retain the final frozen batch `sample_rt_calibration`,
including fitted knots and distinct qualified observation positions with their
`estimate_support`. The bootstrap ISTD reference's measured identity provenance
and target snapshot are recorded once, in the run metadata
`known_istd_reference_*` keys, not as a second display calibration. Inspector and
Workbench replay the same estimate owner without refitting: `loess` within the
fit, continuous endpoint `boundary_shift` outside it, and qualified
`median_shift` for samples without a curve. Only samples with no estimate fall
back to raw RT, with an explicit label. This changes display coordinates, not
measured signal, area, grouping or quantification. Injection order is preserved
separately. Obsolete explanation layouts require rerunning Alignment.

Optional grouping snapshots (`--stop-after grouping --save-grouping-inputs`) keep the
frozen drift model, including the owners each drift anchor observed, so replay
reuses its B shapes and M. Explanation summaries keep only the calibration.

## Drift beyond the map

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

[`rt_drift`](../../xic_extractor/alignment/rt_drift.py) freezes B and M once,
after the general fit and before final grouping:

- **Drift anchors** are the channels left out of the second fit whose residuals
  correlate (Spearman ≥ 0.75 over at least six common samples) with a channel
  of another elution family. Channels whose apices coincide within 0.05 min
  (median over the samples both observe) form one family (isotopes, adducts,
  co-eluting ions) and vote once.
- **M**: each drift anchor's residual is detrended by its pooled-QC trend (the
  samples' own running median along injection order when fewer than two QCs
  carry it). Anchors whose scatter around their own running trend has a robust
  spread of at least 8 seconds, and that correlate with another family's
  scatter, define M. M(s) is the median over families of each family's median
  offset. A sample without such an offset has unknown M, not zero. The zero of
  M is arbitrary: a constant is absorbed by the group center.
- **B shapes**: each drift anchor freezes its own shape, the running median
  along injection order of its residual after removing its own M response
  (a Theil–Sen slope against M without its own family), scaled to a unit
  5–95% range and oriented to rise with injection order. Shapes are not one
  batch-wide curve: early and late RT drift differently.
- Without pooled QC, M is measured against the samples' running trend; when
  samples are injected in tissue blocks, time and tissue then mix.

Final grouping uses them as follows:

- A group overlapping a drift anchor's observations uses that anchor's shape;
  other groups borrow shapes from anchors weighted by a Gaussian of RT distance
  (σ = 1 min). No group fits a free curve during grouping.
- Each group may fit time only, sample shift only, or both, by intercept-bearing
  Theil–Sen (alternating for both). A coefficient is estimated only with at
  least six members having that regressor and a regressor range of at least a
  quarter of the shape range (B) or 10 seconds (M); otherwise it stays zero.
  Each nonzero coefficient costs what a 10-second RT difference costs a pair.
  A group keeps the cheapest feasible choice, zero included.
- A member with unknown B or M does not enter the fit; it keeps its general
  coordinate with half the RT weight and is not placed in the drift-adjusted
  frame for the population-gap constraint. It does not force the group to
  zero coefficients.
- Once chosen, pair costs, population gap, local trend, mean peer fit and
  contradiction flags all use the group's adjusted coordinate. Backfill
  admission keeps the group's coefficients and B, and a Backfill forecast is
  fitted in the adjusted coordinate with the target's drift added back.
- Pattern correspondence between two calibrated samples also tries each nearby
  drift anchor's own predicted displacement; see [Alignment](alignment.md).
- Observed RT, peak boundaries and area are unchanged. Review rows record
  `rt_time_coefficient` and `rt_sample_shift_coefficient`; run metadata records
  `rt_drift_summary` (drift anchors, unknown-M samples, knots per RT segment,
  failed calibrations, groups with nonzero coefficients).

Known limits: a non-anchor drifting compound near the RT boundary between two
shape types can borrow the wrong shape; isomers that occupy different injection
segments can be explained by a segment-aligned M only when the shift within
each segment does not contradict it.
