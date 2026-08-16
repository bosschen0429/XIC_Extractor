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
baseline = AsLS(raw; lam=100000, p=0.01, n_iter=10)
smoothed = Gaussian(raw; window_points,
                    sigma=window_points/6,
                    unit_sum=true,
                    edge_policy=zero_pad_same)
positive_signal = max(smoothed - baseline, 0)
```

AsLS estimates background; it is not a smoothing method exposed alongside
Gaussian. Raw vendor intensity remains an input to both fixed transforms and is
retained where required for audit/provenance, but it is not a second covered
peak answer or compatibility area source.

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
relative quantitation the intended use, but it does not prove that bias cancels
for every peak, sample, scan rate, or matrix. Non-default production claims and
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

## Migration Boundary

`smooth_window`, `smooth_polyorder`, and `resolver_mode` belong to legacy
resolver behavior. They are not aliases for the canonical signal profile and
cannot change profile-covered physical facts. The only canonical public method
is `gaussian`; its public point count is
`signal_processing_window_points` (default 15, odd integer 3–999).

Untargeted Owner Backfill was the first bounded consumer of the canonical
profile and shared physical owner. Current normal Untargeted Discovery,
Alignment owner-build, and Owner Backfill routes are now cut over without
waiting for Targeted parity. The former Standard Peak product route and
source-Matrix republisher are retired; retained shape/overlay helpers are
offline diagnostics only. The automatic GUI gallery route is parked. Targeted
and any deliberately retained diagnostic computation remain separately
bounded and cannot supply a second answer for profile-covered Untargeted work.

The physical owner answers a closed stage-supplied peak question from the
canonical signal. It does not choose a `PeakHypothesis`, group, feature row,
Backfill status, activation, or Matrix identity. A question with zero admissible
segments is unavailable; one with multiple admissible segments is ambiguous.
Neither case may be resolved by nearest apex, height, area, or legacy fallback.

## Baseline Estimation Order

The canonical formula deliberately computes `Gaussian(raw)` and `AsLS(raw)`
independently, then subtracts and positive-clips. It is not
`Gaussian(raw - AsLS(raw))` and not `Gaussian(raw) - AsLS(Gaussian(raw))`.
These alternatives are not equivalent because AsLS is nonlinear. The formula
and all fixed mechanism versions are part of profile identity; changing the
order requires a new profile version and evidence contract.

## Parameters

| Parameter | Default | Derived | Scope |
| --- | --- | --- | --- |
| `signal_processing_method` | `gaussian` only | AsLS remains a fixed baseline mechanism | Canonical profile |
| `signal_processing_window_points` | 15 (odd integer, 3–999) | sigma = window / 6.0 | Canonical signal, boundaries, area, shape |
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
