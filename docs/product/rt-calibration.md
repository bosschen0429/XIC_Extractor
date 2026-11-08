# Retention Time Calibration

Retention Time (RT) calibration places every injection on a common RT coordinate
frame so cross-sample correspondence, Backfill forecasting and review can compare
observations across runs. It models run-to-run retention drift, not compound
identification.

[`sample_rt_calibration`](../../xic_extractor/alignment/sample_rt_calibration.py)
owns anchor preparation and the fit; downstream consumers access the frozen map
through `SampleRtFrame`. Shared terminology remains owned by the
[Domain Glossary](domain-glossary.md). See [Alignment](alignment.md) for how
grouping consumes this frame.

## Calibration Contract

### Anchor qualification

Bootstrap obtains measured anchors from consistent directional grouping
results; ordinary bootstrap membership is not used directly as the final
answer. Pattern seeds participate in both directions. Extensions that disagree
between directions remain unresolved without downgrading the seed.

Known-ISTD reference acquisition reuses Targeted's authoritative peak selection
and credible-anchor decisions rather than adding a separate rule that makes all
candidates unusable whenever multiple candidates appear. An imported reference
workbook retains its declared measurement contract; the reader does not add MS2
qualification. See the
[Untargeted guide](../development/command-line.md#alignment-cli-inputs) for CLI
input selection.

| Mechanism | Current source and use |
| --- | --- |
| Known-ISTD reference | Exact identity bindings and measured coordinates. Bootstrap comparison uses the shared fitter with ISTDs only; final calibration combines those measurements with ordinary anchors. |
| Sample RT calibration | One frozen map fitted from the complete request's measured ISTDs and ordinary bootstrap correspondences. Final Grouping, Backfill localization/admission, and replay consume this map through `SampleRtFrame`. |
| Peak trajectory | Ordered correspondences and direct/MS1-only positions, used for membership constraints and sample-local localization. |
| Calibration diagnostic | Uses the same fit owner with detected-cell inputs and an interleaved held-out comparison. Its files do not alter production results. |

[`sample_rt_calibration`](../../xic_extractor/alignment/sample_rt_calibration.py)
owns anchor preparation and the fit. A correspondence's reference is its
same-cohort median observed RT. Ordinary correspondences require direct identity
support, no identity conflict or provisional status, and quantification-eligible
observations in at least half the declared cohort (rounded up), with at least
three measured members. Bootstrap takes measured support from the retained
assessment binding, not nominal method-query masses projected into an
observation-only owner. These are anchor qualifications, not feature-retention
rules: other observations remain available to grouping. Exact duplicate
sample/RT patterns contribute only once. Measured ISTDs require at least two
same-cohort measurements, but not ordinary-feature prevalence. Missing ISTDs,
predicted coordinates and Backfill observations are not imputed as anchors.
Declared batch IDs define cohorts; empty IDs share an unspecified cohort and
sample role does not define the cohort.

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
The fit consumes fixed bootstrap anchors once; final regrouping and Backfill do
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
`estimate_support`. The bootstrap `known_istd_reference` retains measured identity
provenance and target snapshots, not a second display calibration. Inspector and
Workbench replay the same estimate owner without refitting: `loess` within the
fit, continuous endpoint `boundary_shift` outside it, and qualified
`median_shift` for samples without a curve. Only samples with no estimate fall
back to raw RT, with an explicit label. This changes display coordinates, not
measured signal, area, grouping or quantification. Injection order is preserved
separately. Obsolete explanation layouts require rerunning Alignment.
