# Signal Processing

This page owns the canonical MS1 signal, physical peak qualification,
boundaries and area, and the reasons for them. Targeted measurement policy on
top of these physical answers is in [Targeted Selection](targeted-selection.md).

## At A Glance

| Question | Rule | Detail |
| --- | --- | --- |
| Which signal do boundaries, area and shape use? | One canonical positive signal: Gaussian-smoothed raw minus a clipped RT-aware AsLS baseline. | [Canonical signal](#canonical-signal) |
| When is a peak real enough to measure? | Two adjacent acquired points support its half-prominence top, and its relief is at least four times the local raw-noise MAD. | [Peak qualification](#peak-qualification) |
| Where does a peak end? | At the first observed return to 10% of the apex residual, bounded by separating valleys; a peak reaching the trace edge has no numeric answer. | [Boundaries](#boundaries) |
| When are two lobes measured separately? | Only at a valley the acquired scans resolve; a shoulder stays one envelope, and how many compounds it holds is not decided here. | [Separable valleys](#separable-valleys) |
| Which height and area are reported? | Raw and smoothed height without baseline subtraction; morphology area with it. Raw area is never a fallback. | [Reported height and area](#reported-height-and-area) |
| What does low SNR do? | It is an advisory flag; alone it never blanks, zeroes or authorizes a value. | [Noise and SNR](#noise-and-snr) |
| What does changing the method take? | A new profile version and separated evidence for membership, boundaries, background and area. | [Changing the method](#changing-the-method) |

## Canonical Signal

Within a profile-covered physical evaluation, boundary detection, area
integration, intensity, scan support and relative-shape comparison use one
canonical positive signal:

```text
baseline = max(AsLS_RT(rt_minutes, raw; lam=1 minute^4, p=0.01, n_iter=10), 0)
smoothed = Gaussian(raw; window_points,
                    sigma=window_points/6,
                    unit_sum=true,
                    edge_policy=zero_pad_same)
positive_signal = max(smoothed - baseline, 0)
```

- **Clipped baseline.** The baseline cannot be negative: extrapolated negative
  background must not create positive peak signal.
- **RT penalty.** The AsLS penalty uses retention time in minutes, including
  nonuniform sampling, not scan ordinal, so a change in scan density cannot make
  the baseline follow a broad peak. It does not guarantee unbiased area under
  every curved background or a complete peak in a clipped context.
- **Order.** `Gaussian(raw)` and `AsLS(raw)` are computed independently, then
  subtracted and clipped. This is neither `Gaussian(raw - AsLS(raw))` nor
  `Gaussian(raw) - AsLS(Gaussian(raw))`; they differ because AsLS is nonlinear.
- **Roles.** AsLS estimates background; it is not a smoothing method offered
  beside Gaussian. Raw vendor intensity is an input to both transforms and kept
  where audit or provenance needs it, but it is not a second peak answer or an
  area source.
- **Profile identity.** The formula, RT baseline and envelope parameters and all
  fixed mechanism versions are bound into profile identity; changing them needs
  a new profile version and evidence contract. The strict reader rejects
  retained Discovery facts with unsupported profile semantics, with no
  automatic conversion. Profile-covered scoring and shape comparison consume
  the resolved canonical signal, not a fixed 15-point trace. Physical comparison consumes the capture's explicit
  profile; absent or different profiles leave an overlapping comparison
  unresolved.

## Peak Qualification

### Original sampling support

An ordinary peak needs original sampling support before it exposes numeric
facts: Gaussian spread cannot create acquired evidence.

- The contiguous canonical core around the apex is bounded by half its
  whole-interval prominence. Inside it, an adjacent pair of original XIC values
  minus the owner baseline must reach that half-top level, and at least two
  original observations must support the top. Low neighbors elsewhere in a long
  envelope cannot certify an impulse; constant background is not support.
  Failures give `insufficient_original_xic_support` or
  `insufficient_original_xic_continuity`.
- Ordinary support does not also require an above-baseline run spanning the
  Gaussian window or hit-density enrichment; those must not erase a morphology
  peak with adjacent acquired support and enough local contrast. A compact core
  shorter than the smoothing footprint can still protect its peak.
- The stronger continuous-core check decides only whether a censored cluster
  may replace morphology-owned boundaries and area. Isolated dropouts in an
  ordinary continuous core do not authorize that; repeatedly interrupted cores
  still need sustained support, and a brief continuous patch does not make a
  censored cluster ordinary.

### Censored clusters

For left-censored signals, unsmoothed binary hit density must be enriched
against both local flanks: fourfold, with a trace-length-corrected one-sided
exact test. A cluster replaces fragmented segments only when it contains no
ordinary continuous peak. Qualified clusters use observed-signal trapezoid area
and carry `local_hit_density_enrichment` and `near_detection_limit`; adjacency
and intensity shape cannot be required because censoring destroys them. No
censored intensity is imputed. Ordinary peaks keep their morphology boundaries
and canonical baseline-corrected area.

### Local relief

Sampling support alone accepts shallow ripples on a plateau, so fresh
qualification also requires the peak's relief above its accepted left and
right component boundaries to be at least four times the unscaled MAD of
normalized local three-point differences in the original signal. Failure gives
`insufficient_local_peak_contrast` with no numeric facts or integration.

- **Reference level.** Segmentation's supported lobes and clear-valley cuts own
  the boundaries, so an uncut ripple adds no contrast valley. Each boundary's
  canonical value is the reference, and the higher one sets the level, not the
  deepest minimum in the envelope; an earlier 10% return can be more
  conservative than the valley. Unlike whole-contour prominence, this relief
  cannot borrow height from a distant trough; the half-top core still uses
  whole-interval prominence, and this numerator neither moves that core nor
  changes the noise estimator.
- **Noise.** Each interior point is compared with linear interpolation between
  its neighbors, using actual RT spacing and the coefficient norm, which
  removes constant and linear background; on a uniform grid this is
  `diff(raw, n=2) / sqrt(6)`. A wide smoother would confuse resolved neighbors
  with noise. Fewer than three points or zero MAD adds no rejection. The
  construction relates to the normalized three-point estimators in
  [Brown and Levine (2007)](https://faculty.wharton.upenn.edu/wp-content/uploads/2012/04/2007b-Variance-estimation-in-nonparametric-regression-via-the-difference-sequence-method.pdf);
  the MAD aggregation and factor are product policy that paper does not
  validate.
- **Meaning.** This is a local contrast requirement, not the full residual
  height above AsLS, computed on the prepared arrays. The factor 4 is a
  minimum qualification policy, not a calibrated false-positive probability,
  and retained-input results do not establish cross-dataset accuracy. These
  checks address missing support and low local contrast, not every noise form
  or chemical identity.

Known-reference ISTD localization asks a narrower question: small ripples
should not erase a supported broad contour before the known RT and method
evidence locate it. Its contour policy is in
[Targeted Selection](targeted-selection.md) and does not relax Discovery,
Backfill or non-ISTD Targeted qualification.

Known limits are conservative false negatives, not proof of absence: in very
short peaks the three-point noise estimate can include curvature, and an
acquired dropout can suppress a weaker lobe's seed and merge a resolved pair.
They stay explicit regression cases, not exceptions in the owner.

### Interior maxima

Low-contrast maxima inside a supported envelope must not become artificial
integration boundaries.

- An RT envelope with minimum Gaussian sigma 0.08 minutes identifies supported
  lobes and valleys, applying only the smoothing needed beyond the configured
  Gaussian. Because it can bury a valley the scans still resolve, each coarse
  component is then cut at its [separable valleys](#separable-valleys). The
  envelope is boundary evidence only: boundary walks and the final apex use the
  canonical signal.
- Each interior maximum's topographic prominence over the envelope, measured
  before neighbors truncate its interval, is compared with four times the raw
  three-point noise MAD between its prominence bases. A subordinate lobe also
  uses the contour toward its higher neighbor, so distant zero background
  cannot dilute its noise. There is no seed-height fraction of the envelope or
  context maximum, so a remote strong peak cannot erase a local weak one.
- Subordinate maxima are removed before boundaries are built. A clear valley
  separates groups; an unsupported group stays for the ordinary gates, so weak
  neighbors are not dropped to make a window question unique. Edge maxima stay
  subject to context completeness. The rule removes measurement-noise ripple,
  not every periodic fluctuation or unresolved shoulder.

## Boundaries

Fresh boundaries stop at the first observed return to 10% of the canonical
apex residual, bounded by adjacent separating valleys. A selected interval
reaching an acquired trace edge has no observed boundary context and no numeric
answer (`incomplete_peak_context`); Discovery's bounded reacquisition can
recover it.

- **Edge-reaching intervals.** Outside Discovery, an edge-reaching interval can
  instead use a fully observed return at the greater of 10% and the local
  original-signal noise MAD, keeping the return samples. It must keep the same
  apex, lie within the original interval, and have both ends inside the trace.
  Complete intervals and their values stay exact, and no candidate is dropped
  to resolve ambiguity. The tolerance keeps a small context-dependent AsLS
  change near zero from joining a returned peak to an incomplete neighbor; it
  keeps the Gaussian trace and states the stability/quantitation trade-off in
  the versioned selection policy, but does not guarantee window invariance.
- **Point queries.** An ordinary point query (`baseline_peak_contains_query`)
  shares the selected bounds for membership and integration, inter-scan tails
  included. Discovery instead assigns an NL event to its acquired raw
  valley-separated component, even beyond that component's quantitative
  return; raw valleys bound which component is measured, not the signal. An
  acquired raw apex at a trace edge requests one bounded same-channel
  extension, and an unresolved edge stays non-numeric. Group-guided recovery
  (`unique_baseline_peak_in_context`) uses its center only to locate the
  context; several local peaks stay ambiguous and non-numeric.
- **Envelopes.** Shoulder-connected lobes form one quantitative envelope
  unless a clear envelope valley or a separable acquired valley separates
  them. A single trace cannot tell imperfect chromatography of one component
  from two unresolved components, so no shoulder or overlap flag is emitted.
  Untargeted decides one band or two from cross-sample evidence
  ([Alignment](alignment.md#join-groups-and-consolidate-split-bands));
  Targeted has no such check, so its area can include an unresolved neighbor.
- **NL and context.** NL evidence does not change this physical definition.
  Targeted uses the same owner for NL and no-NL inputs; its acquisition context
  adds one configured window width per side, while only apexes inside the
  original selection window compete, so wider acquisition does not widen the
  selection question.

### Separable valleys

A separable valley answers whether the acquired scans support two regions
measurable separately; it does not claim two compounds. The cut is inside the
valley, each side must still qualify as a peak, and an apex dropout does not
establish a boundary.

- **Gap.** Within one coarse component, at least two consecutive
  non-detections separate two runs of detections when each run holds at least
  two consecutive scans and falls from its maximum toward the gap, the
  bracketing detections are at least one envelope resolution apart (or the
  explicit valley duration if larger), and the Gaussian envelope on the native
  scan grid confirms a clear valley.
- **Measured valley.** Zero scans are censored and neither form nor fill a
  valley. A measured valley is at least two consecutive scans at or below
  `min(minor - 3 sigma, f x minor)`, where `minor` is the smaller side maximum,
  `sigma` the component's short-scale noise and `f` the clear-valley fraction
  (0.5 for the physical owner). Each adjacent lobe holds two scans above that
  level and is itself a peak: beyond it the signal returns to the level for two
  scans, or the component ends. A single scan can stand apart only on the
  higher side and never sets the depth, but an isolated spike then stays out of
  its neighbor's measurement. A policy can also require a minimum valley
  duration in minutes.
- **Order.** The deepest valley cuts first and each side is examined again;
  the result depends only on acquired values, not peak IDs or input order.
- **Discovery basins.** Accepted cuts also delimit Discovery's raw peak basins;
  taking the first raw minimum between distant peaks could cut a lobe at an
  unrelated dropout.
- **Envelope.** A cut leaves its valley scan as the last scan of one segment
  and the first of the next. Segments joined that way form one `peak_envelope`,
  one continuous signal above background; `measure_peak_envelope` integrates it
  on the canonical signal as the sum of segment areas. How many compounds it
  holds is decided by
  [band consolidation](alignment.md#join-groups-and-consolidate-split-bands).

Calibration (#1056) on the 102 reviewed regions of #1049 (8RAW and the enzyme
control) split separable doublets (B) 13/24, noise ripple or zero dropouts (A)
0/13, broad single components including peak plus plateau (W) 0/15, and
unflagged controls 0/27. The "lobe is a peak at its own scale" condition
removes splits of plateau ripple and dense-sampling noise; the missed doublets
are mostly weak isotope or adduct channels whose valley is no deeper than the
noise, and stay one envelope.

## Quantitation Context

The quantitation context is the acquired RT trace available to the shared
physical owner, not the full search window; a proposal interval can be
narrower, and the selected component supplies the integration bounds.

- The physical owner decides whether the context supports complete bounds.
  Missing or clipped support keeps its unavailable reason; no adapter invents
  flanks or integrates a replacement region.
- Lobes stay distinct at a [separable valley](#separable-valleys); brief
  dropouts, ripple and plateau texture do not separate peaks.
- An unresolved envelope keeps overlap review; its area is measured, not
  deconvolved compound-specific area.
- If a focused query clips the peak, Targeted NL acquisition can restore the
  configured target window once, evaluated by the same owner. The writer never
  extends bounds.
- Local-minimum, derivative and alternate boundary proposals stay diagnostic or
  model-selection evidence; boundary review annotations do not replace the
  integration or establish absence.
- Do not integrate every positive residual in a broad RT context.

### Reported height and area

Outputs and review report height without baseline subtraction and area with
it.

- `Int` and `height_raw` are the raw maximum inside the selected segment;
  `IntSmoothed` and `height_smoothed` are the Gaussian trace at the apex.
- Matrix and workbook `Area` are the morphology area. A review curve shows its
  own morphology area, equal to the published value on the curve that gave it.
  Raw area is never a fallback: a peak without a morphology area publishes none
  (Alignment `missing_ms1_morphology_area`; Targeted
  `missing_positive_ms1_peak`).
- `area_raw_counts_seconds` stays internal grouping evidence (edge scoring and
  owner order) and is not shown.

Segmentation, selection, grouping and blank qualification keep their own
signals; this rule does not change which peaks are kept.

## Noise And SNR

The short-scale noise is the RT-weighted three-point residual above:
`noise_sigma = 1.4826 * MAD(residual)`, centered at the residual median, each
residual normalized by its coefficient norm. It assumes independent,
approximately Gaussian noise and describes acquired raw noise, not noise after
smoothing or AsLS subtraction.

- SNR is `max(raw[apex] - baseline[apex], 0) / noise_sigma` over the selected
  interval and apex, using the producer's baseline, not another solve.
  Physical capture and scoring share the clipped physical baseline;
  nonphysical integration keeps AsLS baseline semantics. Prominence and
  nearest-valley relief are separate contrast gates, not SNR numerators.
- Thresholds in sigma units: Discovery relief `4 / 1.4826`, Backfill contrast
  3, provisional SNR below `5 / 1.4826` with low raw contrast, scoring
  transitions `2 / 1.4826` and `3 / 1.4826`, and the advisory
  `low_signal_to_noise` flag below `10 / 1.4826`. These are quality policies,
  not detection probabilities or validated LOD/LOQ.
- The integration result's signal quality carries status, noise sigma, SNR and
  the flag. Fewer than three points, invalid RT or intensity, a missing
  baseline or zero noise cannot give a good-quality or infinite-SNR answer. The
  flag is not part of physical content identity. `baseline_residual_mad` and
  area uncertainty are AsLS-residual quantities.
- A low-SNR hint alone does not blank or zero Matrix values, change headers,
  authorize writing, or change membership, disposition or counting; cell review
  TSVs and Inspector keep its sample/observation attribution. Targeted's
  [quantitative-support policy](decision-policy.md#targeted-quantitative-support)
  combines it with missing and fragmented support before a numeric write.
  Short-peak curvature,
  correlated noise and censoring remain estimator limits.

## Changing The Method

One profile gives boundary, intensity, area and shape consumers one
reproducible definition, so they cannot silently use different preprocessing.
That is an engineering contract, not proof that smoothing is chemically more
accurate than raw integration or that detection and quantitation must share a
signal.

- **Gaussian.** Nonnegative symmetric weights and one public point count
  average scan-to-scan fluctuation without the negative lobes of some
  polynomial filters. Symmetry does not keep the observed apex of an
  asymmetric, overlapping, truncated or irregularly sampled peak, and a fixed
  point count is a different duration when scan spacing changes.
- **Smoothing versus area.** For a linear smoother `S`, preserving the
  unweighted sum requires `1^T S = 1^T`; preserving trapezoid area with weights
  `w` requires `w^T S = w^T`, which a unit-sum scan-index Gaussian does not in
  general satisfy on an irregular RT grid. Truncating at acquired edges can
  lose signal outside the trace or to observed neighbors.
- **Area bias.** Physical area also depends on background estimation, clipping
  and boundaries. Raw area can include baseline or overlapping signal, and
  subtracting a baseline does not guarantee removal; neither a smaller
  canonical nor a larger raw area shows which is closer to the target, and
  comparable processing does not prove the bias cancels in ratios.
- **Evidence.** Compare trace membership, boundaries, background and area
  separately on the same input. Synthetic components can test mechanisms; a
  chemical quantitation claim needs real concentration, spike or dilution
  controls. Uniformly scaling a trace preserves SNR and is only a numerical
  check; one high-concentration standard establishes neither a response curve
  nor low-concentration sample-matrix accuracy.
- **Replacement.** An RT-aware mass-preserving smoother would be a method
  change with a new profile identity and must satisfy the retained-evaluation
  replay contract. Until a replacement has method evidence and reproducible
  persisted answers, the Gaussian/AsLS definition stays the owner.

### Area changes

- Area differences from any other integration are accepted expected
  differences; parity is never manufactured by a second calculation.
- Cross-batch production claims need a review of raw-versus-Gaussian area
  ratios and smoothing duration on retained numbers; the review never changes
  boundaries, area, presence or Matrix values.
- Apex, boundary, height, scan-support and shape differences are qualitative
  and need bounded real-RAW and manual-EIC evidence before activation. Changes
  to the selected hypothesis, ambiguity handling, SUPPORT/HOLD/REJECT,
  Backfill status, identity or write decisions are hard-zero expected
  differences; a qualitative mismatch is HOLD, never a reason to restore
  another computation.
- Focused synthetic/no-RAW tests can prove formula, profile propagation,
  identity invalidation, failure semantics and serial/process parity, not
  production readiness.

## Parameters

| Parameter | Default | Derived | Scope |
| --- | --- | --- | --- |
| `signal_processing_method` | `gaussian` only | AsLS is a fixed baseline mechanism | Canonical profile |
| `signal_processing_window_points` | 9 (odd integer, 3–999) | sigma = window / 6.0 | Canonical signal, area, shape |
| `baseline_lam` | 1.0 minute^4 (fixed) | RT-aware AsLS penalty | Canonical baseline |
| `boundary_min_sigma_rt` | 0.08 minutes (fixed) | Additional Gaussian smoothing only if needed | Lobe and valley evidence |
| `DEFAULT_OWN_MAX_SMOOTH_POINTS` | 15 | sigma = window / 6.0 | Shape-identity comparisons |

Sigma derives from the window size and is not configurable. The 9-point
default is scan-rate sensitive, so other windows are method parameters
([Area changes](#area-changes)).

## Boundary And Area Owners

- Normal Untargeted Discovery, Alignment owner-build and Backfill use the
  canonical profile and the shared physical owner; Backfill has no other
  fallback, so apex, boundaries, height, area, scan support and shape share one
  signal. Retained shape and overlay helpers are offline diagnostics. Targeted
  and retained diagnostic computations are separately bounded and cannot
  supply a second answer for profile-covered Untargeted work.
- The physical owner answers a closed stage-supplied peak question. It does not
  choose a peak hypothesis, group, feature row, Backfill status, activation or
  Matrix identity. Zero admissible segments is unavailable; several is
  ambiguous; neither is resolved by nearest apex, height, area or fallback.
- The accepted segment owns a candidate's boundary when it contains the
  selected apex. Raw trapezoid and standalone AsLS areas can remain audit
  evidence only.
- Physical questions do not cross profile-bound segment universes; a changed
  interpretation mints a new question, and saved answers are never rewritten.
- Canonical region audit projects the selected segment; it never proposes
  merges or grants authority.
- Morphology supplies boundary and shape evidence, not identity. RT, target
  window, MS2/NL opportunity, CID-NL evidence and cross-sample gates decide
  acceptance ([decision policy](decision-policy.md)).

## Where The Code Lives

| Rule | Module |
| --- | --- |
| Segments and accepted boundaries | `ChromPeakSegment` in `peak_detection.chrom_peak_segments` |
| Physical evaluation and envelopes | `peak_detection.physical_peak_evaluation` |
| Point and context questions | `peak_detection.physical_peak_models` |
| Short-scale noise and SNR, carried as `IntegrationResult.signal_quality` | `peak_detection.signal_quality` |
| Profile parameters | `configuration.signal_processing_profile` |
| Shape-identity smoothing | `peak_detection.ms1_shape_identity` |
