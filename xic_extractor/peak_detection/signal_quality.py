"""Short-scale noise and peak S/N facts, independent of identity and write policy.

The RT-weighted three-point residual removes a local linear trend. Unit-variance
normalization and the Gaussian MAD factor express the estimate in sigma units.
This describes short-scale measurement quality, not a validated LOD or LOQ.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import numpy as np

    from xic_extractor.peak_detection.models import PeakCandidate

MAD_TO_SIGMA = 1.4826
DISCOVERY_MIN_CONTRAST_SIGMA = 4.0 / MAD_TO_SIGMA
BACKFILL_MIN_CONTRAST_SIGMA = 3.0
PROVISIONAL_MAX_SNR = 5.0 / MAD_TO_SIGMA
LOW_SIGNAL_TO_NOISE_THRESHOLD = 10.0 / MAD_TO_SIGMA
_SCORING_HARD_SNR = 2.0 / MAD_TO_SIGMA
_SCORING_SOFT_SNR = 3.0 / MAD_TO_SIGMA

SignalQualityStatus = Literal[
    "available",
    "insufficient_points",
    "invalid_trace",
    "missing_baseline",
    "zero_noise",
]


@dataclass(frozen=True)
class SignalQuality:
    """Completed measurement facts; None at the carrier means not recorded."""

    status: SignalQualityStatus
    noise_sigma: float | None = None
    signal_to_noise: float | None = None
    low_signal_to_noise: bool | None = None

    @property
    def scoring_quality(self) -> Literal["strong", "borderline", "poor", "unknown"]:
        ratio = self.signal_to_noise
        if ratio is None:
            return "unknown"
        if ratio < _SCORING_HARD_SNR:
            return "poor"
        if ratio < _SCORING_SOFT_SNR:
            return "borderline"
        return "strong"

    @property
    def scoring_severity(self) -> int:
        return {"strong": 0, "borderline": 1, "poor": 2, "unknown": 2}[
            self.scoring_quality
        ]


def short_scale_noise_sigma(rt: np.ndarray, intensity: np.ndarray) -> float | None:
    """Estimate acquired noise, not noise after smoothing or peak subtraction.

    Independent, approximately Gaussian noise and a sufficiently sampled signal
    motivate the scale conversion. Correlated noise and unresolved curvature
    remain limitations; an all-zero estimate is not proof of infinite S/N.
    """
    import numpy as np

    if (
        rt.ndim != 1
        or intensity.ndim != 1
        or len(rt) != len(intensity)
        or len(rt) < 3
        or not np.all(np.isfinite(rt))
        or not np.all(np.isfinite(intensity))
        or not np.all(np.diff(rt) > 0.0)
    ):
        return None
    weight = (rt[2:] - rt[1:-1]) / (rt[2:] - rt[:-2])
    residual = (
        weight * intensity[:-2] + (1.0 - weight) * intensity[2:] - intensity[1:-1]
    ) / np.sqrt(1.0 + weight**2 + (1.0 - weight) ** 2)
    mad = float(np.median(np.abs(residual - np.median(residual))))
    sigma = MAD_TO_SIGMA * mad
    return sigma if np.isfinite(sigma) else None


def measure_signal_quality(
    rt: np.ndarray,
    raw: np.ndarray,
    *,
    baseline: np.ndarray | None,
    apex_index: int,
) -> SignalQuality:
    """Describe an already selected peak interval without changing its values."""
    import numpy as np

    if len(rt) == len(raw) and len(rt) < 3:
        return SignalQuality("insufficient_points")
    sigma = short_scale_noise_sigma(rt, raw)
    if sigma is None or not 0 <= apex_index < len(raw):
        return SignalQuality("invalid_trace")
    if baseline is None:
        return SignalQuality("missing_baseline", noise_sigma=sigma)
    if baseline.shape != raw.shape or not np.all(np.isfinite(baseline)):
        return SignalQuality("invalid_trace", noise_sigma=sigma)
    if sigma <= 0.0:
        return SignalQuality("zero_noise", noise_sigma=sigma)
    signal = max(0.0, float(raw[apex_index] - baseline[apex_index]))
    ratio = signal / sigma
    if not np.isfinite(ratio):
        return SignalQuality("invalid_trace", noise_sigma=sigma)
    return SignalQuality(
        "available",
        noise_sigma=sigma,
        signal_to_noise=ratio,
        low_signal_to_noise=ratio < LOW_SIGNAL_TO_NOISE_THRESHOLD,
    )


def candidate_signal_quality(
    candidate: PeakCandidate,
    rt: np.ndarray,
    raw: np.ndarray,
    baseline: np.ndarray | None,
) -> SignalQuality:
    """Use the candidate's integration interval and selected apex, not its tail."""
    import numpy as np

    if len(rt) != len(raw) or not len(rt):
        return SignalQuality("invalid_trace")
    segment = candidate.physical_segment
    if segment is not None:
        left, right = segment.start_index, segment.end_index
    else:
        left = int(np.argmin(np.abs(rt - candidate.peak.peak_start)))
        right = int(np.argmin(np.abs(rt - candidate.peak.peak_end))) + 1
    return measure_signal_quality(
        rt[left:right],
        raw[left:right],
        baseline=baseline[left:right] if baseline is not None else None,
        apex_index=candidate.selection_apex_index - left,
    )
