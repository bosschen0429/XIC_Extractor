"""Read-only Pt envelope comparison on synchronous, in-memory XIC observations.

The score ranks fixed singly charged position hypotheses; it never decides
candidate status, chemical identity, window membership or RT attribution.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from math import comb, isfinite
from statistics import median

POSITION_LABELS = ("base", "plus_1", "plus_2", "plus_4")
_POSITIONS = (0, 1, 2, 4)
# NIST Atomic Weights and Isotopic Compositions, nominal offsets from 194Pt,
# 35Cl and 32S. https://physics.nist.gov/PhysRefData/Compositions/index.html
_PT = {-4: 0.00012, -2: 0.00782, 0: 0.3286, 1: 0.3378, 2: 0.2521, 4: 0.07356}
_CL = {0: 0.7576, 2: 0.2424}
_S = {0: 0.9499, 1: 0.0075, 2: 0.0425, 4: 0.0001}
_MODELS = (("Pt+C", 0, 0), ("Pt+C+Cl", 1, 0), ("Pt+C+S", 0, 1), ("Pt+C+2S", 0, 2))


@dataclass(frozen=True)
class EnvelopeTrace:
    label: str
    rt: tuple[float, ...]
    intensity: tuple[float, ...]


@dataclass(frozen=True)
class PtEnvelopeComparison:
    status: str
    reason: str = ""
    model: str | None = None
    carbon_count: int | None = None
    spread: float | None = None
    core_scan_count: int = 0
    core_rt_window: tuple[float, float] | None = None
    position_labels: tuple[str, ...] = POSITION_LABELS
    observed_relative: tuple[float, ...] = ()
    expected_relative: tuple[float, ...] = ()


def _convolve(a: dict[int, float], b: dict[int, float]) -> dict[int, float]:
    result: dict[int, float] = {}
    for i, p in a.items():
        for j, q in b.items():
            result[i + j] = result.get(i + j, 0.0) + p * q
    return result


@lru_cache(maxsize=512)
def _expected(carbon: int, chlorine: int, sulfur: int) -> tuple[float, ...]:
    distribution = _PT
    for _ in range(chlorine):
        distribution = _convolve(distribution, _CL)
    for _ in range(sulfur):
        distribution = _convolve(distribution, _S)
    # At most eight 13C atoms can reach the observed +4 position from 190Pt.
    carbon_distribution = {
        k: comb(carbon, k) * 0.0107**k * 0.9893 ** (carbon - k)
        for k in range(min(carbon, 8) + 1)
    }
    distribution = _convolve(distribution, carbon_distribution)
    base = distribution[0]
    return tuple(distribution[k] / base for k in _POSITIONS)


def compare_pt_envelope(
    traces: tuple[EnvelopeTrace, ...], *, base_mz: float, rt_apex_min: float
) -> PtEnvelopeComparison:
    """Compare four nominal positions using one base-defined half-height core.

    The local apex search is +/-10 seconds and background is the lower median
    of the available +/-20..30 second flanks (at least three scans per flank).
    Carbon count uses the diagnostic's coarse (M - Pt - Cl - S)/14 bound.
    These are fixed review measurement conventions, not detection thresholds.
    """
    by_label = {trace.label: trace for trace in traces}
    missing = [label for label in POSITION_LABELS if label not in by_label]
    if missing:
        return PtEnvelopeComparison(
            "unavailable", "missing_position:" + ",".join(missing)
        )
    ordered = [by_label[label] for label in POSITION_LABELS]
    rt = ordered[0].rt
    if not rt:
        return PtEnvelopeComparison("unavailable", "no_trace")
    if any(trace.rt != rt or len(trace.intensity) != len(rt) for trace in ordered):
        return PtEnvelopeComparison("unavailable", "rt_grid_mismatch")
    if (
        not isfinite(base_mz)
        or not isfinite(rt_apex_min)
        or any(not isfinite(t) for t in rt)
        or any(b <= a for a, b in zip(rt, rt[1:], strict=False))
        or any(not isfinite(v) or v < 0 for trace in ordered for v in trace.intensity)
    ):
        return PtEnvelopeComparison("unavailable", "invalid_observations")
    seconds = [(t - rt_apex_min) * 60 for t in rt]
    flanks = [
        [i for i, t in enumerate(seconds) if lo <= t <= hi]
        for lo, hi in ((-30, -20), (20, 30))
    ]
    flanks = [indices for indices in flanks if len(indices) >= 3]
    if not flanks:
        return PtEnvelopeComparison("unavailable", "insufficient_background")
    background = [
        min(median(trace.intensity[i] for i in indices) for indices in flanks)
        for trace in ordered
    ]
    base_net = [v - background[0] for v in ordered[0].intensity]
    near = [i for i, t in enumerate(seconds) if abs(t) <= 10]
    if not near:
        return PtEnvelopeComparison("unavailable", "no_base_signal")
    apex = max(near, key=base_net.__getitem__)
    if base_net[apex] <= 0:
        return PtEnvelopeComparison("unavailable", "no_base_signal")
    lo = hi = apex
    half_height = base_net[apex] / 2
    while lo > 0 and base_net[lo - 1] >= half_height:
        lo -= 1
    while hi + 1 < len(rt) and base_net[hi + 1] >= half_height:
        hi += 1
    count = hi - lo + 1
    net = tuple(
        sum(trace.intensity[lo : hi + 1]) / count - bg
        for trace, bg in zip(ordered, background, strict=True)
    )
    core_rt_window = (rt[lo], rt[hi])
    nonpositive = [
        label for label, value in zip(POSITION_LABELS, net, strict=True) if value <= 0
    ]
    if nonpositive:
        return PtEnvelopeComparison(
            "unavailable",
            "nonpositive_core:" + ",".join(nonpositive),
            core_scan_count=count,
            core_rt_window=core_rt_window,
        )
    observed = tuple(value / net[0] for value in net)
    best: tuple[float, str, int, tuple[float, ...]] | None = None
    for name, chlorine, sulfur in _MODELS:
        remaining_mass = (
            base_mz - 195 - chlorine * 34.968852682 - sulfur * 31.9720711744
        )
        if remaining_mass < 0:
            continue
        for carbon in range(int(remaining_mass / 14) + 1):
            expected = _expected(carbon, chlorine, sulfur)
            amounts = [
                value / ratio for value, ratio in zip(observed, expected, strict=True)
            ]
            spread = max(amounts) / min(amounts)
            if best is None or spread < best[0]:
                best = (spread, name, carbon, expected)
    if best is None:
        return PtEnvelopeComparison(
            "unavailable",
            "mass_below_model",
            core_scan_count=count,
            core_rt_window=core_rt_window,
        )
    spread, name, carbon, expected = best
    return PtEnvelopeComparison(
        "available",
        model=name,
        carbon_count=carbon,
        spread=spread,
        observed_relative=observed,
        expected_relative=expected,
        core_scan_count=count,
        core_rt_window=core_rt_window,
    )
