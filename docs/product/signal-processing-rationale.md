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
Ordinary support does not additionally require an above-baseline run spanning
the configured Gaussian window or hit-density enrichment. Those conditions
must not erase a morphology peak with adjacent acquired support and sufficient
local contrast. The stronger continuous-core check instead decides whether a
censored cluster may replace morphology-owned boundaries and area; isolated
dropouts in an ordinary continuous core do not authorize that replacement.
A compact core with adjacent acquired support can protect its morphology peak
even when it is shorter than the smoothing footprint. Repeatedly interrupted
cores still require sustained acquired support; a brief continuous patch inside
an otherwise censored cluster does not make the whole cluster ordinary.

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
latter remain separate intervals at the envelope valley. A brief canonical
notch on a descending shoulder cannot move this boundary; sustained canonical
clear splits retain their canonical valley. Canonical contour maxima reaching
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

## What The Canonical Signal Establishes

One profile gives boundary, intensity, area, and shape consumers a reproducible
numerical definition. Sharing that definition prevents those consumers from
silently using different preprocessing. It is an engineering contract, not
proof that smoothing is chemically more accurate than raw integration or that
detection and quantitation must always use the same signal.

The fixed Gaussian has nonnegative symmetric weights and a single public point
count. It averages local scan-to-scan fluctuations without the negative kernel
lobes of some polynomial filters. Symmetry does not guarantee an unchanged
observed apex for an asymmetric, overlapping, truncated, or irregularly sampled
peak. A fixed point count also corresponds to different durations when scan
spacing changes.

### Smoothing And Area Are Different Questions

For a linear smoother `S`, preserving the unweighted sum requires
`1^T S = 1^T`. Preserving an area computed with trapezoid weights `w` instead
requires `w^T S = w^T`. A unit-sum scan-index Gaussian does not in general
satisfy the second condition on an irregular RT grid. Truncating the convolved
trace at acquired edges can also lose signal that spreads outside that trace;
a selected interval can lose signal to its observed neighbors.

The physical area additionally depends on background estimation, positive
clipping, and selected boundaries. Raw area can include baseline or overlapping
chemical signal; subtracting an estimated baseline does not guarantee their
removal. Neither a smaller canonical area nor a larger raw area establishes
which is closer to the target contribution. Comparable processing across
samples alone does not prove that the resulting bias cancels in area ratios.

### Evidence Required To Change The Method

Compare trace membership, peak boundaries, background handling, and area
calculation separately, using the same observed input when attributing a
numerical difference. Known synthetic components can test those mechanisms;
real independent concentration, spike, or dilution controls are needed for a
chemical quantitation claim. Uniformly scaling a saved trace preserves its SNR
and is only a numerical scaling check. A single high-concentration standard
injection establishes neither a response curve nor low-concentration matrix
accuracy.

An RT-aware mass-preserving smoother is a possible method change, not a
correction that can silently reuse the current profile identity. It must also
satisfy the retained-evaluation replay contract. Until a replacement has both
appropriate method evidence and reproducible persisted answers, the current
Gaussian/AsLS definition remains the operational owner.

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
offline diagnostics only. The automatic gallery route is parked. Targeted
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
a method-appropriate area-ratio and smoothing-duration review per
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
- Canonical signal migration follows the
  [quantitative, qualitative, and required non-change gates](../lc-msms-evidence-rules.md#ms1-morphology-and-area-owner).
  Until the required evidence gate and the registered selected-area authority
  scope pass, construction is not production-ready.
