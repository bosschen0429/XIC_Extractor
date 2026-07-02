# Signal Processing Rationale

Doc placement: formal_repo_doc
Doc kind: product_doc
Doc lifecycle: active
Repo owner: docs/product/signal-processing-rationale.md
Doc exit rule: Keep active as the durable design rationale for trace smoothing
and area integration choices; retire only if the pipeline abandons the
smoothed-everywhere principle.

This document records **why** the pipeline uses Gaussian-smoothed traces for all
judgment, scoring, boundary, and area decisions. For the operational rules
themselves, see [`lc-msms-evidence-rules.md`](../lc-msms-evidence-rules.md)
§MS1 Morphology And Area Owner.

## Design Principle

All rule-based decisions — boundary detection, area integration, intensity
measurement, confidence scoring, shape comparison, and pattern discovery — operate
on the same Gaussian15-smoothed trace. Raw vendor trace is retained for audit and
export only; it never participates in machine judgment.

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

Gaussian smoothing systematically underestimates absolute area and intensity.
This is a known, consistent bias. For relative quantitation (cross-sample
comparison), which is the primary use case of this pipeline, a consistent bias
cancels out — every sample is underestimated by the same mechanism. The bias
only matters when comparing absolute values against external tools that use
raw integration; this is documented and expected.

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
broadening — typically cited as its weaknesses — are advantages here: they make
smoothing behavior fully predictable across samples and features.

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

## Raw Resolver And The Smoothed-Everywhere Boundary

The `local_minimum` resolver performs initial candidate region detection on
the raw trace (valley detection, threshold crossing, trace-continuity scoring).
Its role differs by pipeline path:

### Targeted path — ChromPeakSegment as primary, raw resolver supplementary

In the targeted extraction path, `ChromPeakSegment` independently detects
peaks on the Gaussian15 morphology trace and merges into the candidate pool.
The raw resolver's candidates are augmented/overridden by ChromPeakSegment
boundaries and Gaussian15 area. The raw resolver is architectural debt in
this path and is planned for removal — ChromPeakSegment will become the sole
detection source.

### Discovery and alignment paths — raw resolver is sole detector (to be migrated)

Discovery (`resolver_mode="local_minimum"`) and alignment/backfill (forced
downgrade to `"local_minimum"`) do not pass `scoring_context_builder`, so
`ChromPeakSegment` augmentation never triggers. The raw resolver is the only
detection mechanism in these paths.

The same trace producing different boundaries and areas depending on which
pipeline path processes it is a design defect, not a deliberate trade-off.
These paths should converge to ChromPeakSegment after the targeted path
migration is validated. The migration sequence is: targeted first (accumulate
production evidence) → standardize ChromPeakSegment as a standalone detection
API → connect to discovery/alignment using targeted parity evidence as the
acceptance baseline.

### Layered design rationale

Where both paths coexist (targeted), the design is layered:

1. Raw resolver proposes candidate regions (coarse detection).
2. `ChromPeakSegment` re-determines boundaries on the Gaussian15 morphology
   trace (fine boundary ownership).
3. `area_ms1_morphology` integrates the Gaussian15 trace within segment
   boundaries (area ownership).

The raw resolver layer operates at a coarse granularity where noise sensitivity
is less critical (region-level accept/reject, not point-level boundary
placement). The subsequent Gaussian15 layers override all fine-grained boundary
and area decisions before values reach the product matrix.

## Open Question: Baseline Estimation Order

The current morphology trace pipeline is:

    raw intensity → AsLS baseline → residual → Gaussian15 smooth → positive clip

An alternative order would be:

    raw intensity → Gaussian15 smooth → AsLS baseline → residual → positive clip

These are **not mathematically equivalent**. Gaussian convolution is linear
(`smooth(raw - baseline) = smooth(raw) - smooth(baseline)`), but AsLS is
nonlinear (iteratively reweighted least squares), so
`AsLS(raw) ≠ AsLS(smooth(raw))`.

The practical difference: AsLS with `p=0.01` gives weight 0.99 to points below
the current baseline estimate. In raw data, a single noisy low-intensity sample
in a valley can act as a "pin" that pulls the baseline down locally. This can
cause the baseline to locally overestimate the true background near weak peaks,
driving the residual negative, which positive clipping then zeros out
irreversibly — before smoothing has a chance to recover the information.
Pre-smoothing the input would average out single-sample noise leverage, making
the baseline estimate more stable in valleys.

With `lam=1e5` (heavy smoothness penalty), the effect is damped but not
eliminated. No empirical comparison between the two orders exists in the
current test suite (all morphology tests use noiseless synthetic baselines).
The current order is an implementation artifact, not a deliberated choice.

**Status**: pending empirical comparison on 8RAW data to quantify the
practical impact before deciding whether to change the order.

## Parameters

| Parameter | Default | Derived | Scope |
| --- | --- | --- | --- |
| `ms1_morphology_smoothing_window_points` | 15 (odd, ≥3) | sigma = window / 6.0 | Morphology trace, area, scoring |
| `DEFAULT_OWN_MAX_SMOOTH_POINTS` | 15 | sigma = window / 6.0 | Shape-identity comparisons |

Sigma is not independently configurable; it is derived from window size. The
15-point default is scan-rate sensitive — non-default windows require
`gaussian15_area_pressure_audit.py` review per
[`lc-msms-evidence-rules.md`](../lc-msms-evidence-rules.md).

## Production Evidence

- 85-sample validation: `raw_source_with_area=0`, `asls_source_with_area=0` —
  raw and AsLS area never reach the product matrix (see `diagnostic-ledger.md`).
- PT-pattern discovery: explicit `TRACE_DECISION_BASIS =
  "gaussian15_smoothed_trace"` constant.
- Scoring layer: `ScoringContext.intensity_array` is Gaussian15-smoothed; all
  severity and confidence computations use smoothed values.
