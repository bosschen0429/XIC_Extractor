"""Warning-only reference RT diagnostics over immutable target/peak tables."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import TypedDict

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import isotonic_regression
from scipy.signal import find_peaks

from xic_extractor.configuration.models import Target
from xic_extractor.xic_models import XICTrace


@dataclass(frozen=True)
class ReferencePolicy:
    mapping_residual_min: float = 0.5
    peak_match_min: float = 0.2
    peak_prominence_ratio: float = 0.20
    peak_separation_min: float = 0.35
    peak_min_width_points: float = 6.0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.peak_prominence_ratio >= 1:
            raise ValueError("peak_prominence_ratio must be < 1")


@dataclass(frozen=True)
class ClusterObservation:
    group: str
    sample: str
    label: str
    apex_rt_min: tuple[float, ...]
    extent_rt_min: tuple[tuple[float, float], ...] = ()


class ReferenceRow(TypedDict):
    group: str
    sample: str
    label: str
    mz: float
    reference_rt_min: float
    predicted_rt_min: float | None
    residual_min: float | None
    prediction_count: int
    cluster_apices_min: list[float]
    cluster_extents_min: list[list[float]]
    nearest_ordinal: int | None
    nearest_distance_min: float | None
    raw_status: str
    warnings: list[str]
    severity: str


class MappingError(TypedDict):
    source_group: str
    group: str
    label: str
    reference_rt_min: float
    predicted_rt_min: float
    residual_min: float
    anchor_count: int


class ReferenceReport(TypedDict):
    rows: list[ReferenceRow]
    mapping_errors: list[MappingError]
    summary: dict[str, object]


def reference_rt(target: Target) -> float:
    """Target CSV represents the reference by the search-window midpoint."""
    return (target.rt_min + target.rt_max) / 2


def trace_clusters(
    trace: XICTrace, policy: ReferencePolicy = ReferencePolicy()
) -> tuple[tuple[float, float, float], ...]:
    """Diagnostic apex and half-prominence extents, never selected peaks.

    Smooth with a 15-point Gaussian (sigma=2), retain peaks with at least
    the policy fraction of full-trace maximum prominence and height, and
    require half-prominence width beyond a smoothed single-scan impulse,
    and suppress nearby maxima by RT distance. Separate samples retain their
    own positions; acquisition drift is not pooled into extra clusters.
    """
    rt, intensity = trace.rt, trace.intensity
    if len(rt) < 3:
        return ()
    if not np.all(np.isfinite(rt)) or not np.all(np.isfinite(intensity)):
        raise ValueError("cluster traces must be finite")
    if np.any(np.diff(rt) <= 0):
        raise ValueError("cluster trace RT must be strictly increasing")
    smoothed = gaussian_filter1d(np.maximum(intensity, 0), sigma=2, radius=7)
    maximum = float(np.max(smoothed))
    if maximum <= 0:
        return ()
    peaks, properties = find_peaks(
        smoothed,
        prominence=maximum * policy.peak_prominence_ratio,
        height=maximum * policy.peak_prominence_ratio,
        width=policy.peak_min_width_points,
    )
    retained: list[int] = []
    for index in sorted(peaks, key=lambda i: float(smoothed[i]), reverse=True):
        if all(
            abs(float(rt[index] - rt[p])) >= policy.peak_separation_min
            for p in retained
        ):
            retained.append(int(index))
    scan_index = np.arange(len(rt))
    widths = {
        int(peak): (
            float(np.interp(left, scan_index, rt)),
            float(np.interp(right, scan_index, rt)),
        )
        for peak, left, right in zip(
            peaks, properties["left_ips"], properties["right_ips"], strict=True
        )
    }
    return tuple((float(rt[i]), *widths[i]) for i in sorted(retained))


def trace_cluster_apices(
    trace: XICTrace, policy: ReferencePolicy = ReferencePolicy()
) -> tuple[float, ...]:
    """Return diagnostic apex positions for callers that need positions only."""
    return tuple(apex for apex, _, _ in trace_clusters(trace, policy))


def _same_target(left: Target, right: Target) -> bool:
    return abs(left.mz - right.mz) <= min(left.ppm_tol, right.ppm_tol) * left.mz / 1e6


def _mapping_prediction(
    source: Mapping[str, Target],
    destination: Mapping[str, Target],
    anchors: set[str],
    held: str,
) -> float | None:
    knots: dict[float, list[float]] = defaultdict(list)
    for label in anchors - {held}:
        knots[reference_rt(source[label])].append(reference_rt(destination[label]))
    x = sorted(knots)
    query = reference_rt(source[held])
    if len(x) < 2 or not x[0] <= query <= x[-1]:
        return None  # No extrapolation evidence at unsupported edges.
    y = [float(np.median(knots[value])) for value in x]
    fitted = isotonic_regression(y, increasing=True).x
    return float(np.interp(query, x, fitted))


def _mapping_errors(
    groups: Mapping[str, Sequence[Target]], policy: ReferencePolicy
) -> tuple[list[MappingError], list[dict[str, object]]]:
    tables = {group: {t.label: t for t in targets} for group, targets in groups.items()}
    errors: list[MappingError] = []
    exclusions: list[dict[str, object]] = []
    for destination, targets in tables.items():
        for source, peers in tables.items():
            if source == destination:
                continue
            shared = sorted(
                label
                for label in targets.keys() & peers.keys()
                if _same_target(targets[label], peers[label])
            )
            anchors = set(shared)
            # Remove the largest self-inconsistent anchor, then reassess.
            # Excluded labels remain held-out predictions and warning rows.
            while len(anchors) > 3:
                residuals = [
                    (abs(reference_rt(targets[label]) - predicted), label)
                    for label in sorted(anchors)
                    if (
                        predicted := _mapping_prediction(peers, targets, anchors, label)
                    )
                    is not None
                ]
                if not residuals:
                    break
                residual, label = max(residuals)
                if residual <= policy.mapping_residual_min:
                    break
                anchors.remove(label)
                exclusions.append(
                    {
                        "source_group": source,
                        "group": destination,
                        "label": label,
                        "screening_residual_min": residual,
                    }
                )
            for held in shared:
                predicted = _mapping_prediction(peers, targets, anchors, held)
                if predicted is None:
                    continue
                reference = reference_rt(targets[held])
                errors.append(
                    MappingError(
                        source_group=source,
                        group=destination,
                        label=held,
                        reference_rt_min=reference,
                        predicted_rt_min=predicted,
                        residual_min=reference - predicted,
                        anchor_count=len(anchors - {held}),
                    )
                )
    return errors, exclusions


def _majority(values: Sequence[int], electorate: int | None = None) -> int | None:
    if not values:
        return None
    ordinal, count = Counter(values).most_common(1)[0]
    return (
        ordinal
        if count > (len(values) if electorate is None else electorate) / 2
        else None
    )


def check_reference_consistency(
    groups: Mapping[str, Sequence[Target]],
    observations: Sequence[ClusterObservation] = (),
    policy: ReferencePolicy = ReferencePolicy(),
) -> ReferenceReport:
    """Compare references without modifying targets, selections, or values.

    Group predictions are the median of supported pairwise leave-one-out
    interpolations. Mapping-only alerts are informational even without RAW.
    Ordinals are comparable only for equal peak counts; each group gets one
    vote, so replicate counts cannot dominate a cross-method comparison.
    """
    errors, exclusions = (
        _mapping_errors(groups, policy) if len(groups) > 1 else ([], [])
    )
    predictions: dict[tuple[str, str], list[float]] = defaultdict(list)
    for error in errors:
        predictions[error["group"], error["label"]].append(error["predicted_rt_min"])
    observed: dict[tuple[str, str], list[ClusterObservation]] = defaultdict(list)
    for observation in observations:
        observed[observation.group, observation.label].append(observation)
    rows: list[ReferenceRow] = []
    residuals: list[float] = []
    for group, targets in groups.items():
        for target in targets:
            reference = reference_rt(target)
            values = predictions[group, target.label]
            predicted = float(np.median(values)) if values else None
            residual = reference - predicted if predicted is not None else None
            if residual is not None:
                residuals.append(abs(residual))
            samples = observed[group, target.label] or [
                ClusterObservation(group, "", target.label, ())
            ]
            for sample in samples:
                clusters = (
                    sorted(zip(sample.apex_rt_min, sample.extent_rt_min, strict=True))
                    if sample.extent_rt_min
                    else [(apex, (apex, apex)) for apex in sorted(sample.apex_rt_min)]
                )
                apices = [apex for apex, _ in clusters]
                extents = [bounds for _, bounds in clusters]
                nearest = (
                    min(range(len(apices)), key=lambda i: abs(apices[i] - reference))
                    if apices
                    else None
                )
                distance = (
                    abs(apices[nearest] - reference) if nearest is not None else None
                )
                warnings = []
                if residual is not None and abs(residual) > policy.mapping_residual_min:
                    warnings.append("mapping_residual")
                raw_status = "unavailable" if not sample.sample else "no_peaks"
                if apices:
                    raw_status = "multiple_peaks" if len(apices) > 1 else "single_peak"
                    if (
                        len(apices) == 1
                        and distance is not None
                        and distance <= policy.peak_match_min
                    ):
                        raw_status = "single_peak_matches_reference"
                    if (
                        len(apices) > 1
                        and apices[0] < reference < apices[-1]
                        and distance is not None
                        and distance > policy.peak_match_min
                        and not any(
                            left <= reference <= right for left, right in extents
                        )
                    ):
                        warnings.append("reference_between_clusters")
                    if (
                        len(apices) > 1
                        and predicted is not None
                        and "mapping_residual" in warnings
                    ):
                        predicted_peak = min(
                            range(len(apices)), key=lambda i: abs(apices[i] - predicted)
                        )
                        if (
                            predicted_peak != nearest
                            and abs(apices[predicted_peak] - predicted)
                            <= policy.peak_match_min
                        ):
                            warnings.append("mapping_points_to_different_cluster")
                rows.append(
                    ReferenceRow(
                        group=group,
                        sample=sample.sample,
                        label=target.label,
                        mz=target.mz,
                        reference_rt_min=reference,
                        predicted_rt_min=predicted,
                        residual_min=residual,
                        prediction_count=len(values),
                        cluster_apices_min=apices,
                        cluster_extents_min=[list(bounds) for bounds in extents],
                        nearest_ordinal=nearest + 1 if nearest is not None else None,
                        nearest_distance_min=distance,
                        raw_status=raw_status,
                        warnings=warnings,
                        severity="none",
                    )
                )
    for row in rows:
        if len(row["cluster_apices_min"]) > 1:
            peers: dict[str, list[int]] = defaultdict(list)
            other_groups = {
                other["group"]
                for other in rows
                if other["group"] != row["group"]
                and other["label"] == row["label"]
                and math.isclose(other["mz"], row["mz"], rel_tol=1e-6)
            }
            for other in rows:
                if (
                    other is row
                    or other["label"] != row["label"]
                    or not math.isclose(other["mz"], row["mz"], rel_tol=1e-6)
                    or len(other["cluster_apices_min"])
                    != len(row["cluster_apices_min"])
                ):
                    continue
                ordinal = other["nearest_ordinal"]
                if ordinal is not None:
                    peers[other["group"]].append(ordinal)
            cross_group = [
                vote
                for group, ordinals in peers.items()
                if group != row["group"] and (vote := _majority(ordinals)) is not None
            ]
            # Incomparable groups abstain; they do not turn a lone comparable
            # peer into a majority. With one method, compare its own samples.
            consensus = (
                _majority(cross_group, len(other_groups))
                if other_groups
                else _majority(peers.get(row["group"], []))
            )
            if consensus is not None and consensus != row["nearest_ordinal"]:
                row["warnings"].append("cluster_ordinal_mismatch")
        row["severity"] = (
            "high"
            if any(w != "mapping_residual" for w in row["warnings"])
            else "info"
            if row["warnings"]
            else "none"
        )
    return ReferenceReport(
        rows=rows,
        mapping_errors=errors,
        summary={
            "evidence": "diagnostic_only",
            "policy": asdict(policy),
            "group_count": len(groups),
            "mapping_prediction_count": len(residuals),
            "mapping_unavailable_count": sum(len(ts) for ts in groups.values())
            - len(residuals),
            "pairwise_prediction_count": len(errors),
            "mapping_excluded_anchors": exclusions,
            "raw_unavailable_count": sum(
                row["raw_status"] == "unavailable" for row in rows
            ),
            "loo_absolute_error_median_min": float(np.median(residuals))
            if residuals
            else None,
            "loo_absolute_error_p90_min": float(np.percentile(residuals, 90))
            if residuals
            else None,
            "distribution_unit": (
                "one absolute residual per group/target; median peer prediction"
            ),
            "high_warning_count": sum(row["severity"] == "high" for row in rows),
            "info_warning_count": sum(row["severity"] == "info" for row in rows),
            "mapping_method": (
                "per directed group pair, iteratively exclude the anchor with "
                "largest supported own leave-one-out residual above "
                "mapping_residual_min while at least three anchors remain; "
                "predict every shared target from remaining anchors with "
                "leave-one-out PAVA monotonic piecewise-linear interpolation; "
                "no extrapolation; exclusion is not an error adjudication"
            ),
        },
    )
