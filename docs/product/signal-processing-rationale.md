# Signal Processing Rationale

Doc placement: formal_repo_doc
Doc kind: product_doc
Doc lifecycle: active
Repo owner: docs/product/signal-processing-rationale.md
Doc exit rule: Keep active as the durable design rationale for trace smoothing
and area integration choices; retire only if the pipeline abandons the
smoothed-everywhere principle.

This document records **why** the canonical signal profile uses a
Gaussian-smoothed raw trace together with an independently estimated AsLS raw
baseline for judgment, scoring, boundary, and area decisions. For the operational rules
themselves, see [`lc-msms-evidence-rules.md`](../lc-msms-evidence-rules.md)
§MS1 Morphology And Area Owner.

## Design Principle

Within a profile-covered physical evaluation, boundary detection, area
integration, intensity measurement, scan support, and relative-shape comparison
operate on one canonical positive signal:

```text
baseline = max(AsLS_RT(rt_minutes, raw; lam=1 minute^4, p=0.01, n_iter=10), 0)
smoothed = Gaussian(raw; window_points,
                    sigma=window_points/6,
                    unit_sum=true,
                    edge_policy=zero_pad_same)
positive_signal = max(smoothed - baseline, 0)
```

The physical baseline cannot be negative: extrapolated negative background
must not create positive peak signal. A selected interval reaching an acquired
trace edge lacks observed boundary context and has no numeric physical answer.

### Edge-reaching intervals

For fresh selection, an edge-reaching interval can instead use a fully observed
return at the greater of 0.1% of its apex residual and the local original-signal
noise MAD, with the return samples retained. This
alternative must keep the same apex and lie within the original interval;
both ends must be inside the acquired trace. Already complete intervals and
their values remain exact, and no candidate is dropped to resolve ambiguity.
The bounded tolerance prevents a small context-dependent AsLS change near zero
from necessarily joining a returned peak to an incomplete neighboring lobe.
The bounded tolerance preserves the Gaussian trace and makes the
stability/quantitation tradeoff explicit in the versioned selection policy. A
context window can still contain a different candidate population. The
bounded tolerance is not a guarantee of window invariance.

### Baseline and raw intensity

AsLS estimates background. AsLS is not a smoothing method exposed alongside
Gaussian. Raw vendor intensity remains an input to both fixed transforms and is
retained where required for audit/provenance. Raw vendor intensity is not a
second covered peak answer or a compatibility area source.

### Original sampling support

Ordinary physical peaks require original sampling support before exposing
numeric facts: Gaussian spread cannot create independent acquired evidence.
The selected interval needs an adjacent pair of original XIC values above the
owner baseline; constant background is not peak support.

For left-censored signals, unsmoothed binary hit density must be enriched against
both local flanks. The shared physical owner uses a fourfold enrichment and a
trace-length-corrected one-sided exact test. It replaces fragmented segments only
when the enriched cluster contains no ordinary continuous peak. Qualified
clusters use observed-signal trapezoid area and carry `near_detection_limit`;
adjacency and intensity shape cannot be additional requirements because
censoring destroys them. No censored intensity is imputed. Ordinary peaks retain
their morphology boundaries and canonical baseline-corrected area.

### Interior maxima

Low-contrast maxima inside a supported envelope must not first become artificial
integration boundaries. The physical owner uses an RT envelope with a minimum
Gaussian sigma of 0.08 minutes to identify supported lobes and valleys, while
preserving supported clear valleys already visible on the canonical signal.
To split a coarse envelope, the canonical valley must remain below 30% of the
smaller lobe for at least one configured Gaussian support footprint. A short
dropout notch cannot create separate quantitative peaks. Only the
additional smoothing needed beyond the configured scan-point Gaussian is
applied. This envelope is boundary evidence: it does not replace the canonical
signal for apex intensity or integration. Boundary walks use the canonical
signal, and the final apex is its maximum within the supported interval.

The physical owner measures each interior maximum's
topographic prominence over the RT envelope, before adjacent
maxima truncate its interval. It compares that prominence with twice the raw
three-point noise MAD between the prominence bases. A subordinate lobe also
uses the contour toward its adjacent higher lobe, so distant zero background
cannot dilute its noise estimate. Within a group connected
by shoulder valleys, maxima must also reach 4% of the group's maximum to remain
independent seeds. There is no seed-height fraction relative to the maximum
of the entire acquired context: a remote strong peak cannot erase a local
weak component before grouping. Subordinate maxima are removed before boundaries are built. A clear valley
separates groups; an entirely unsupported group remains for the ordinary
qualification gates, so weak neighboring peaks cannot be dropped merely to
make a window question unique. Edge maxima also remain subject to the existing
context-completeness rule. The interior-maxima rule removes measurement-noise
ripple, not every periodic fluctuation or unresolved chemical shoulder.

Shoulder-connected lobes form one complete quantitative envelope unless a
supported clear valley separates its area, or independently supported coarse
lobes retain a shallow valley across one configured smoothing footprint. The
latter remain separate intervals at the observed valley. Canonical contour maxima reaching
the same 4% prominence floor remain review evidence even when they do not
qualify as independent seeds. The complete envelope does not establish one
chemical component or individual component areas.

An unresolved shoulder remains a review condition (`physical_shoulder_overlap`).
Targeted carries it into the product review reasons; Untargeted retains it as
provisional evidence and excludes it from independent owner quantification.
NL evidence does not change this physical definition. Targeted uses the same
owner for NL and no-NL inputs; its acquisition context includes one configured
window width on each side, while only apexes inside the original selection
window may compete. Wider acquisition must not silently widen the target's
selection question.

### Local relief

Sampling support alone also accepts shallow ripples on a background plateau.
Fresh qualification therefore compares the selected canonical peak's relief
above its two local valleys with fluctuations in the same original interval.
The relief must be at least twice the unscaled MAD of normalized local
three-point differences in the original signal. Each interior point is compared
with linear interpolation between its immediate neighbors, using actual RT
spacing and the coefficient norm. This normalized difference removes constant
and linear background. On a uniform grid the difference is
`diff(raw, n=2) / sqrt(6)`. Comparing raw values with a wide smoother instead
would confuse resolved neighboring peaks with noise.

The local-relief rule is a local contrast requirement, not the full residual
height above an AsLS floor. The rule uses the existing prepared arrays without
recomputation.
The local-relief construction follows the surrounding-contour interpretation
of [peak prominence](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.peak_prominences.html),
bounded here by the selected interval. The factor 2 is an explicit minimum
qualification policy, not a calibrated false-positive probability. Broad,
narrow, background-shifted and jagged positive controls guard retention;
retained-input results alone do not establish cross-dataset detection accuracy.
The difference construction is related to the normalized three-point
estimators discussed by [Brown and Levine (2007)](https://faculty.wharton.upenn.edu/wp-content/uploads/2012/04/2007b-Variance-estimation-in-nonparametric-regression-via-the-difference-sequence-method.pdf).
The robust MAD aggregation and qualification factor here are product policy;
that paper does not validate this chromatographic gate or its threshold.
The versioned selection policy in
[`LC-MS/MS evidence rules`](../lc-msms-evidence-rules.md#ms1-morphology-and-area-owner)
owns that eligibility check; the profile continues to own the quantitative
signal and its scan-support count.
These checks address missing acquired support and insufficient local contrast,
not every form of noise or
chemical identity. Historical policies keep their recorded interpretation.

## Why Smoothed-Everywhere

### The noise regime is different from pharma quantitation

Mature LC-MS software (Skyline, Analyst, MassHunter) typically uses a hybrid
approach: smooth for peak detection, integrate the raw signal within those
boundaries. This design was developed for triple-quadrupole MRM data where
signal-to-noise ratios routinely exceed 100:1. In that regime the difference
between raw and smoothed integration is negligible, so the instability cost of
raw integration is near zero.

Full-scan HRMS metabolomics operates in a fundamentally lower S/N regime. Raw
traces carry substantial high-frequency noise, and boundary placement on raw
data is poorly reproducible. The hybrid approach's assumption — that raw
integration adds precision — breaks down when the raw signal itself is noisy.

### Regulatory context does not apply

The pharma preference for raw-trace integration is driven by regulatory
compliance (FDA/EMA bioanalytical method validation guidelines), which treats
smoothing as a data transformation requiring justification. This constraint
presupposes that raw data is clean enough to be directly usable — a premise
that holds for MRM but not for full-scan HRMS. Metabolomics pipelines are not
subject to these validation guidelines and should not inherit their design
trade-offs without re-examining the premises.

### Systematic underestimation is acceptable for relative quantitation

Gaussian smoothing can systematically reduce absolute area and intensity. This
quantitative difference is accepted for the canonical direction because stable
same-feature interpretation is the higher-level requirement; the pipeline does
not manufacture parity with legacy raw integration. Comparable processing makes
relative quantitation the intended use. Comparable processing does not prove
that bias cancels for every peak, sample, scan rate, or sample matrix. Non-default production claims and
external absolute-value comparisons therefore require method-appropriate
evidence.

### Stability enables automated rule-based interpretation

The pipeline processes hundreds to thousands of features per sample without
manual review. Every downstream rule — confidence scoring, noise-shape
severity, boundary promotion, shape similarity, pattern discovery — depends on
trace values being stable and reproducible. Raw traces are too sensitive to
high-frequency noise, causing over-interpretation: false boundary splits,
unstable severity scores, and irreproducible shape comparisons. The stability
gained from smoothing makes the entire rule chain tractable.

### One trace for all decisions eliminates internal contradictions

Using the same smoothed trace for boundary detection and area integration
avoids the pathological case where smoothed boundaries land on raw-signal
local maxima rather than true valley points. In the hybrid approach, a
boundary that is optimal for the smoothed curve may be suboptimal for the raw
curve, producing areas that are neither the best smoothed estimate nor the best
raw estimate.

## Why Gaussian Smoothing Specifically

The pipeline uses a Gaussian kernel (symmetric, sigma = window / 6.0) rather
than other smoothing methods. The original choice was inherited from Xcalibur
(Thermo vendor software), but the choice is independently justified for this
pipeline's design goals.

### Alternatives considered

| Method | Theoretical advantage | Why it conflicts with smoothed-everywhere |
| --- | --- | --- |
| Savitzky-Golay | Preserves peak height/width better via local polynomial fit | Introduces oscillation (ringing) near sharp features; adds a polynomial-order parameter; retired from this pipeline (`legacy_savgol`) |
| Wavelet denoising | Scale-adaptive; preserves sharp features while removing noise | Behavior varies with peak shape, reducing cross-sample predictability — directly opposes the stability goal |
| Whittaker smoother | Continuous smoothness/fidelity trade-off; mathematically elegant | Additional parameters; higher computational cost; tunable fidelity is a liability when the design goal is uniform smoothing |
| Median filter | Extremely robust to spikes and outliers | Distorts continuous peak shapes; not suitable for chromatographic signals |
| LOESS / LOWESS | Local regression; adapts to varying point density | Computationally expensive; at fixed scan rates offers no advantage over Gaussian |

### Why Gaussian wins in this design space

Most advanced smoothing methods optimize for preserving original signal detail.
The smoothed-everywhere principle explicitly does not want to preserve
high-frequency detail, because in the full-scan HRMS noise regime that detail
is predominantly noise. Gaussian's uniform peak-height suppression and uniform
broadening — typically cited as its weaknesses — are advantages here. Uniform
suppression and broadening make smoothing behavior fully predictable across
samples and features.

Key properties that align with the design goals:

- **Symmetric kernel**: no apex shift, so all RT-dependent rules (identity,
  alignment, boundary) are unaffected by the smoothing step.
- **Smooth frequency response**: no ringing or oscillation artifacts that could
  trigger false boundary splits or shape-similarity mismatches.
- **Single effective parameter** (window size; sigma derived): minimal
  configuration surface, reproducible behavior.
- **Vendor familiarity**: Xcalibur users see comparable smoothed traces,
  reducing cognitive disconnect when visually comparing pipeline output against
  vendor software.

## Migration Boundary

`smooth_window`, `smooth_polyorder`, and `resolver_mode` belong to legacy
resolver behavior. They are not aliases for the canonical signal profile and
cannot change profile-covered physical facts. The only canonical public method
is `gaussian`; its public point count is
`signal_processing_window_points` (default 15, odd integer 3–999).

Untargeted Backfill was the first bounded consumer of the canonical profile
and shared physical owner. Current normal Untargeted Discovery, Alignment
owner-build, and Backfill routes are now cut over without waiting for
Targeted parity. The former Standard Peak product route and
source-Matrix republisher are retired; retained shape/overlay helpers are
offline diagnostics only. The automatic GUI gallery route is parked. Targeted
and any deliberately retained diagnostic computation remain separately
bounded and cannot supply a second answer for profile-covered Untargeted work.

The physical owner answers a closed stage-supplied peak question from the
canonical signal. It does not choose a `PeakHypothesis`, group, feature row,
Backfill status, activation, or Matrix identity. A question with zero
admissible segments is unavailable. A question with multiple admissible
segments is ambiguous. The physical owner must not resolve either case by
nearest apex, height, area, or legacy fallback.

## Baseline Estimation Order

The baseline penalty uses retention time in minutes, including nonuniform
sampling, rather than scan ordinal. Its fixed regularization is 1 minute^4.
This prevents a change in scan density from making the baseline follow a broad
chromatographic peak. It does not guarantee an unbiased area for every curved
background or a complete peak when the acquired context is clipped.

The canonical formula deliberately computes `Gaussian(raw)` and `AsLS(raw)`
independently, then subtracts and positive-clips. It is not
`Gaussian(raw - AsLS(raw))` and not `Gaussian(raw) - AsLS(Gaussian(raw))`.
These alternatives are not equivalent because AsLS is nonlinear. The formula
and all fixed mechanism versions are part of profile identity; changing the
order requires a new profile version and evidence contract.
The RT baseline and envelope parameters are bound into profile identity.
Retained Discovery facts with a different profile cannot be used as current
physical answers; they must be regenerated through the existing acquisition
route.

## Parameters

| Parameter | Default | Derived | Scope |
| --- | --- | --- | --- |
| `signal_processing_method` | `gaussian` only | AsLS remains a fixed baseline mechanism | Canonical profile |
| `signal_processing_window_points` | 15 (odd integer, 3–999) | sigma = window / 6.0 | Canonical signal, area, shape |
| `baseline_lam` | 1.0 minute^4 (fixed) | RT-aware AsLS penalty | Canonical baseline |
| `boundary_min_sigma_rt` | 0.08 minutes (fixed) | Additional Gaussian smoothing only if needed | Lobe and valley evidence |
| `segment_min_prominence_fraction` | 0.04 (fixed) | Fraction of shoulder-connected envelope maximum | Independent seed evidence |
| `DEFAULT_OWN_MAX_SMOOTH_POINTS` | 15 | sigma = window / 6.0 | Shape-identity comparisons |

Sigma is not independently configurable; it is derived from window size. The
legacy settings key `ms1_morphology_smoothing_window_points` is accepted only
as a one-way load-time alias to `signal_processing_window_points`. Canonical
and legacy values may coexist only when equal. Savitzky–Golay and resolver
settings are not aliases. The 15-point default is scan-rate sensitive — non-default windows require
`gaussian15_area_pressure_audit.py` review per
[`lc-msms-evidence-rules.md`](../lc-msms-evidence-rules.md).

## Evidence Status And Activation

- Focused synthetic/no-RAW tests can prove formula, profile propagation,
  identity invalidation, failure semantics, and serial/process parity. They do
  not prove production readiness.
- Historical 85-sample evidence that raw and standalone AsLS areas did not reach
  the product matrix predates this exact canonical formula and cannot validate
  its qualitative peak choice.
- PT-pattern discovery: explicit `TRACE_DECISION_BASIS =
  "gaussian15_smoothed_trace"` constant.
- Scoring layer: `ScoringContext.intensity_array` is Gaussian15-smoothed; all
  severity and confidence computations use smoothed values.
- Canonical area differences are an authorized quantitative expected diff.
  Apex, boundary, height, scan-support, and relative-shape differences are
  qualitative and require the bounded real-RAW/manual EIC gate. Selected
  hypothesis, stage status, activation, row/cell identity, and write/blank
  decisions must remain unchanged. Until that gate and the registered
  selected-area authority scope pass, construction is not production-ready.
