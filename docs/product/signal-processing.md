# Signal Processing

This page owns the canonical MS1 signal, physical peak qualification,
boundaries and area, and the reasons for them. Targeted measurement policy on top
of these physical answers is in [Targeted Selection](targeted-selection.md).

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
trace edge lacks observed boundary context and has no numeric physical answer
(`incomplete_peak_context`). Discovery measures each located peak in a window
centred on its raw apex, which usually holds both returns.

### Edge-reaching intervals

Fresh boundaries stop at the first observed return to 10% of the canonical
apex residual, bounded by adjacent separating valleys. Outside Discovery, an edge-reaching interval
can instead use a fully observed return at the greater of that same 10% level
and the local original-signal noise MAD, with the return samples retained. This
alternative must keep the same apex and lie within the original interval;
both ends must be inside the acquired trace. Already complete intervals and
their values remain exact, and no candidate is dropped to resolve ambiguity.
The bounded tolerance prevents a small context-dependent AsLS change near zero
from necessarily joining a returned peak to an incomplete neighboring lobe.
The bounded tolerance preserves the Gaussian trace and makes the
stability/quantitation tradeoff explicit in the versioned selection policy. A
context window can still contain a different candidate population. The
bounded tolerance is not a guarantee of window invariance.

Ordinary point queries use `baseline_peak_contains_query`; membership and integration
share the selected bounds, including inter-scan tails. Discovery instead assigns
an NL event to its acquired raw valley-separated component, including an event
beyond that component's canonical quantitative return. Raw valleys bound which
component is measured; AsLS, Gaussian and canonical integration remain unchanged.
An acquired raw apex at a trace edge requests one bounded same-channel extension;
an unresolved edge still has no numeric answer. Group-guided recovery
uses `unique_baseline_peak_in_context`: its center locates the search context
and need not fall inside the peak, and multiple local peaks remain ambiguous and
non-numeric.

### Baseline and raw intensity

AsLS estimates background. AsLS is not a smoothing method exposed alongside
Gaussian. Raw vendor intensity remains an input to both fixed transforms and is
retained where required for audit/provenance. Raw vendor intensity is not a
second covered peak answer or a compatibility area source.

### Original sampling support

Ordinary physical peaks require original sampling support before exposing
numeric facts: Gaussian spread cannot create independent acquired evidence.
The contiguous canonical core around the selected apex is bounded by half of
its whole-interval prominence. Within that core, an adjacent pair of original
XIC values after subtracting the owner baseline must reach the same half-top
level, and at least two original observations must support the top. Low
neighbors elsewhere in a long envelope cannot certify an impulse; constant
background is not peak support. Failures give
`insufficient_original_xic_support` or `insufficient_original_xic_continuity`.
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
clusters use observed-signal trapezoid area and carry
`local_hit_density_enrichment` and `near_detection_limit`;
adjacency and intensity shape cannot be additional requirements because
censoring destroys them. No censored intensity is imputed. Ordinary peaks retain
their morphology boundaries and canonical baseline-corrected area.

### Interior maxima

Low-contrast maxima inside a supported envelope must not first become artificial
integration boundaries. The physical owner uses an RT envelope with a minimum
Gaussian sigma of 0.08 minutes to identify supported lobes and valleys. The
envelope can bury a valley that the acquired scans still resolve, so each
coarse component is then cut at its [separable acquired valleys](#separable-valleys).
Only the
additional smoothing needed beyond the configured scan-point Gaussian is
applied. This envelope is boundary evidence: it does not replace the canonical
signal for apex intensity or integration. Boundary walks use the canonical
signal, and the final apex is its maximum within the supported interval.

The physical owner measures each interior maximum's
topographic prominence over the RT envelope, before adjacent
maxima truncate its interval. It compares that prominence with four times the raw
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

Shoulder-connected lobes form one quantitative envelope unless a clear envelope
valley or a separable acquired valley separates their area. Canonical contour
maxima reaching the same 4% prominence floor remain review evidence even when
they do not qualify as independent seeds. The complete envelope does not
establish one chemical component or individual component areas.

An unresolved shoulder remains a review condition (`physical_shoulder_overlap`).
Targeted carries it into the product review reasons; Untargeted retains it as
provisional evidence and excludes it from independent owner quantification.
NL evidence does not change this physical definition. Targeted uses the same
owner for NL and no-NL inputs; its acquisition context includes one configured
window width on each side, while only apexes inside the original selection
window may compete. Wider acquisition must not silently widen the target's
selection question.

### Separable valleys

A separable valley answers one question: do the acquired scans support two
regions that can be measured separately? It does not claim two compounds.
Within one coarse component, zero scans are censored (they neither form nor
fill a valley). A valley is at least two consecutive scans at or below
`min(minor - 3 sigma, f x minor)`, where `minor` is the smaller of the two side
maxima, `sigma` the short-scale noise of the component's scans and `f` the
policy's clear-valley fraction (0.5 for the physical owner). Each adjacent lobe
holds two scans above that level and is itself a peak: beyond it the signal
returns to the level for two scans, or the component ends. A single scan can
stand apart only on the higher side; it never sets the valley depth, but an
isolated spike then stays out of its neighbor's measurement. A policy can also
require a minimum valley duration in minutes. The deepest valley cuts first and
each side is examined again; the result depends only on the acquired values,
not on peak IDs or input order.

Calibration (#1056) used the 102 reviewed regions of #1049 on 8RAW and the
enzyme control: separable doublets (B) 13/24 split; noise ripple or zero
dropouts (A) 0/13, broad single components including peak plus plateau (W)
0/15 and unflagged controls 0/27 split. The "lobe is a peak at its own scale"
condition removes splits of plateau ripples and dense-sampling noise; the
missed doublets are mostly weak isotope or adduct channels whose valley is not
deeper than the noise, which stay one unresolved envelope.

### Local relief

Sampling support alone also accepts shallow ripples on a background plateau.
Fresh qualification therefore compares the selected canonical peak's relief
above its accepted left and right component boundaries with fluctuations in the
same original interval. Segmentation's supported lobes and clear-valley cuts own
these boundaries: an uncut surface ripple cannot introduce another contrast
valley. Each boundary's actual canonical value supplies the reference; an earlier
10% return can be more conservative than the separating valley itself. The higher
boundary supplies the reference level, not the deepest minimum anywhere in the
selected envelope.
The relief must be at least four times the unscaled MAD of normalized local
three-point differences in the original signal. Each interior point is compared
with linear interpolation between its immediate neighbors, using actual RT
spacing and the coefficient norm. This normalized difference removes constant
and linear background. On a uniform grid the difference is
`diff(raw, n=2) / sqrt(6)`. Comparing raw values with a wide smoother instead
would confuse resolved neighboring peaks with noise. Fewer than three points or
zero MAD adds no rejection. Failure gives `insufficient_local_peak_contrast`
without numeric facts or integration.

The local-relief rule is a local contrast requirement, not the full residual
height above an AsLS floor. The rule uses the existing prepared arrays without
recomputation.
Unlike whole-contour topographic prominence, this boundary relief cannot
borrow height from a distant trough. The half-top sampling core still uses
whole-interval prominence; replacing the final contrast numerator does not
move that core or change the noise estimator. The factor 4 is an explicit
minimum qualification policy, not a calibrated false-positive probability.
Retained-input results alone do not establish cross-dataset detection accuracy.
The difference construction is related to the normalized three-point
estimators discussed by [Brown and Levine (2007)](https://faculty.wharton.upenn.edu/wp-content/uploads/2012/04/2007b-Variance-estimation-in-nonparametric-regression-via-the-difference-sequence-method.pdf).
The robust MAD aggregation and qualification factor here are product policy;
that paper does not validate this chromatographic gate or its threshold.
These checks address missing acquired support and insufficient local contrast,
not every form of noise or chemical identity.

Known-reference ISTD localization answers a narrower question than discovering
local maxima: small surface ripples should not erase a supported broad contour
before the known RT and method evidence can locate it. The reference-specific
contour policy is in [Targeted Selection](targeted-selection.md); it does not
relax Discovery, Backfill or non-ISTD Targeted qualification.

Known limits are conservative false negatives, not proof of signal absence.
In very short peaks the three-point noise estimate can include peak curvature;
an acquired dropout can suppress a weaker lobe's seed and merge an otherwise
resolved pair. These limitations remain explicit regression cases, not
exceptions or alternate calculations in the owner.

## Short-Scale Signal Quality

`peak_detection/signal_quality.py` owns the RT-weighted three-point residual
estimator described above. It returns `noise_sigma = 1.4826 * MAD(residual)`;
the MAD is centered at the residual median, and each residual is normalized
by its interpolation-coefficient norm. This scale assumes independent,
approximately Gaussian measurement noise. It describes acquired raw noise,
not noise after Gaussian smoothing or AsLS peak subtraction.

For a selected peak, SNR is `max(raw[apex] - baseline[apex], 0) / noise_sigma`.
Both noise and signal use its selected integration interval and apex; the
baseline is the producer's existing baseline, not another solve. Physical
capture and scoring share the clipped physical baseline. Legacy nonphysical
integration retains its existing AsLS baseline semantics. Physical prominence
and nearest-valley relief remain distinct contrast gates, not SNR numerators.

Thresholds retain their previous units-equivalent values: Discovery relief is
`4 / 1.4826` sigma, Backfill contrast is 3 sigma, provisional SNR is below
`5 / 1.4826` together with low raw contrast, and scoring transitions are
`2 / 1.4826` and `3 / 1.4826`. These are quality/qualification policies,
not calibrated detection probabilities or validated LOD/LOQ.

`IntegrationResult.signal_quality` carries status, noise sigma, SNR and the
advisory `low_signal_to_noise` flag (SNR below `10 / 1.4826`). Fewer than three
points, invalid RT/intensity, missing baseline, and zero noise cannot produce
a good-quality or infinite-SNR answer. Old artifacts lacking the field decode
as not recorded. The advisory fact is not
part of physical content identity. Existing `baseline_residual_mad` and area
uncertainty retain their original AsLS-residual meaning.

Cell review TSVs and Inspector retain the sample/observation attribution.
A low-SNR hint alone does not blank or zero Matrix values, change headers,
authorize writing, or change membership/disposition/counting. Targeted's
[quantitative-support policy](decision-policy.md#targeted-quantitative-support)
combines it with missing and fragmented acquired support before allowing a
numeric write; Untargeted group-cell decisions are unchanged. A short peak's curvature,
correlated noise and censored observations remain estimator limitations.

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

## Quantitation Context

The quantitation context is the acquired RT trace available to the shared
physical owner. A candidate proposal interval can be narrower than that
context, and the full search window is not the quantitation context. The
selected physical component supplies the integration bounds.

- The shared physical owner decides whether the observed context supports
  complete peak bounds. Missing or clipped support keeps its explicit
  unavailable reason; no downstream adapter invents flanks or integrates a
  replacement region.
- Lobes stay distinct at a [separable valley](#separable-valleys). Brief
  dropouts, unsupported ripple and plateau texture do not separate peaks.
- An unresolved envelope keeps overlap review. Its area is measured, not
  deconvolved compound-specific area.
- If a focused query clips the peak, Targeted NL acquisition can restore the
  configured target window once; the same physical owner evaluates the new
  trace. The writer never extends bounds.
- Local-minimum, derivative and alternate boundary proposals remain
  diagnostic or model-selection evidence. Boundary review annotations do not
  replace the retained integration or establish chemical absence.
- Do not integrate every positive residual in a broad RT context.

## Consumer Boundary

`smooth_window`, `smooth_polyorder`, and `resolver_mode` belong to legacy
resolver behavior. They are not aliases for the canonical signal profile and
cannot change profile-covered physical facts. The only canonical public method
is `gaussian`; its public point count is
`signal_processing_window_points` (default 9, odd integer 3–999).

Normal Untargeted Discovery, Alignment owner-build and Backfill use the
canonical profile and shared physical owner. Retained shape and overlay helpers
are offline diagnostics only. Targeted and any deliberately retained diagnostic
computation remain separately bounded and cannot supply a second answer for
profile-covered Untargeted work.

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
Retained Discovery facts with unsupported profile semantics cannot be used as
current physical answers. The strict reader rejects them; there is no old/new
algorithm dispatcher or automatic result conversion. Existing files remain
unchanged and new work uses the current owner. Physical comparison consumes
the capture's explicit profile, not an implicit current default; absent or
different profiles leave an otherwise overlapping comparison unresolved.

## Parameters

| Parameter | Default | Derived | Scope |
| --- | --- | --- | --- |
| `signal_processing_method` | `gaussian` only | AsLS remains a fixed baseline mechanism | Canonical profile |
| `signal_processing_window_points` | 9 (odd integer, 3–999) | sigma = window / 6.0 | Canonical signal, area, shape |
| `baseline_lam` | 1.0 minute^4 (fixed) | RT-aware AsLS penalty | Canonical baseline |
| `boundary_min_sigma_rt` | 0.08 minutes (fixed) | Additional Gaussian smoothing only if needed | Lobe and valley evidence |
| `segment_min_prominence_fraction` | 0.04 (fixed) | Fraction of shoulder-connected envelope maximum | Independent seed evidence |
| `DEFAULT_OWN_MAX_SMOOTH_POINTS` | 15 | sigma = window / 6.0 | Shape-identity comparisons |

Sigma is not independently configurable; it is derived from window size. The
legacy settings key `ms1_morphology_smoothing_window_points` is accepted only
as a one-way load-time alias to `signal_processing_window_points`. Canonical
and legacy values may coexist only when equal. Savitzky–Golay and resolver
settings are not aliases. The 9-point default is scan-rate sensitive, so other
windows are method parameters (see [Area changes](#area-changes)).

## Boundary And Area Owners

- `ChromPeakSegment` owns the boundary of an accepted candidate when a canonical
  segment contains the selected apex.
- Raw trapezoid and standalone AsLS areas may remain audit evidence for excluded
  legacy consumers. Owner Backfill has no legacy fallback: apex, boundaries,
  height, area, scan support and shape share one canonical signal.
- Physical questions do not cross profile-bound segment universes. A changed
  interpretation mints a new question; saved answers are never rewritten.
- Canonical region audit projects the selected segment; it never proposes
  merges or grants authority.
- Morphology supplies boundary and shape evidence, not identity. RT, target
  window, MS2/NL opportunity, CID-NL evidence and cross-sample gates decide
  acceptance ([decision policy](decision-policy.md)).

### Area changes

- Area differences from legacy integration are accepted expected differences;
  parity is never manufactured by a second calculation.
- Cross-batch production claims need a review of raw-versus-Gaussian area ratios
  and smoothing duration on retained numbers. The review never changes selected
  boundaries, area, presence or Matrix values.
- Apex, boundary, height, scan-support and shape differences are qualitative and
  need bounded real-RAW and manual-EIC evidence before activation. Changes to
  the selected hypothesis, ambiguity handling, SUPPORT/HOLD/REJECT, Backfill
  status, identity or write decisions are hard-zero expected differences; a
  qualitative mismatch is HOLD, never a reason to restore legacy computation.

## Evidence Status

- Focused synthetic/no-RAW tests can prove formula, profile propagation,
  identity invalidation, failure semantics, and serial/process parity. They do
  not prove production readiness.
- PT-pattern discovery: explicit `TRACE_DECISION_BASIS =
  "gaussian15_smoothed_trace"` constant.
- Profile-covered scoring and relative-shape comparison consume the resolved
  canonical signal, rather than assuming a fixed 15-point trace.
