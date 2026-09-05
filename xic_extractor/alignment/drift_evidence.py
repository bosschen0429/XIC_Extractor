from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Iterable
from dataclasses import InitVar, dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Literal

from openpyxl import load_workbook

from xic_extractor.alignment.identity_gates import is_dr_neutral_loss_tag
from xic_extractor.configuration.models import Target
from xic_extractor.configuration.target_identity import TargetListSnapshot
from xic_extractor.injection_rolling import read_injection_order, rolling_median_rt

DriftPriorSource = Literal["targeted_istd_trend", "batch_istd_trend", "none"]
LocalLandmarkQuality = Literal["accepted", "poor"]
LocalTrajectoryStatus = Literal[
    "assessable",
    "missing_landmarks",
    "poor_landmarks",
    "conflict",
]

LOCAL_TRAJECTORY_VALIDATION_STATUS = "shadow_ready"
LOCAL_TRAJECTORY_PRODUCT_AUTHORITY = "none"
_LOCAL_TRAJECTORY_ASSESSMENT_MINT_TOKEN = object()
_TARGETED_ISTD_ANCHOR_MINT_TOKEN = object()
_DNA_DR_NEUTRAL_LOSS_DA = 116.0474


@dataclass(frozen=True)
class SampleDriftEvidence:
    sample_stem: str
    injection_order: int
    trend_id: str
    istd_rt_min: float
    local_trend_rt_min: float
    rt_drift_delta_min: float
    source: Literal["targeted_istd_trend", "batch_istd_trend"]


@dataclass(frozen=True, slots=True)
class TargetedIstdIdentityAnchor:
    """Run-local target anchor for the known DNA dR ISTD lane.

    The anchor is derived from the owner-validated target definitions and the
    existing targeted ISTD workbook labels.  It is an internal grouping
    context, not a second grouping owner or a persisted Matrix identity.
    """

    target_snapshot: TargetListSnapshot
    observed_labels: tuple[str, ...]
    _mint_token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._mint_token is not _TARGETED_ISTD_ANCHOR_MINT_TOKEN:
            raise ValueError(
                "targeted ISTD identity anchors must be minted by the owner"
            )
        if not isinstance(self.target_snapshot, TargetListSnapshot):
            raise TypeError("target_snapshot must be an owner-minted snapshot")
        labels = tuple(
            definition.target.label
            for definition in self.target_snapshot.definitions
        )
        if len(labels) != len(set(labels)):
            raise ValueError("targeted ISTD anchor targets must have unique labels")
        if not isinstance(self.observed_labels, tuple):
            raise TypeError("observed_labels must be a tuple")
        if (
            any(
                not isinstance(label, str) or not label.strip()
                for label in self.observed_labels
            )
            or self.observed_labels != tuple(sorted(set(self.observed_labels)))
        ):
            raise ValueError("observed_labels must be canonical sorted text")

    def matching_target(self, family: object) -> Target | None:
        """Return a unique configured ISTD match for one aligned family."""

        if not is_dr_neutral_loss_tag(str(getattr(family, "neutral_loss_tag", ""))):
            return None
        family_mz = _finite_float(getattr(family, "family_center_mz", None))
        if family_mz is None:
            family_mz = _finite_float(getattr(family, "cluster_center_mz", None))
        if family_mz is None:
            return None
        family_rt = _finite_float(getattr(family, "family_center_rt", None))
        if family_rt is None:
            family_rt = _finite_float(getattr(family, "cluster_center_rt", None))
        if family_rt is None:
            return None
        candidates = tuple(
            target
            for target in self.targets
            if target.label in self.observed_labels
            and self.matches_observation(
                target,
                neutral_loss_tag=str(getattr(family, "neutral_loss_tag", "")),
                precursor_mz=family_mz,
                apex_rt=family_rt,
            )
        )
        return candidates[0] if len(candidates) == 1 else None

    def matches_observation(
        self,
        target: Target,
        *,
        neutral_loss_tag: str,
        precursor_mz: float,
        apex_rt: float,
    ) -> bool:
        """Check one owner observation against the matched target envelope."""

        return (
            target.is_istd
            and target.label in self.observed_labels
            and target.neutral_loss_da is not None
            and math.isclose(
                target.neutral_loss_da,
                _DNA_DR_NEUTRAL_LOSS_DA,
                rel_tol=0.0,
                abs_tol=1e-6,
            )
            and target.rt_min <= apex_rt <= target.rt_max
            and abs(precursor_mz - target.mz)
            <= target.mz * target.ppm_tol / 1_000_000.0
            and is_dr_neutral_loss_tag(neutral_loss_tag)
        )

    def matching_family_ids(self, families: Iterable[object]) -> frozenset[str]:
        return frozenset(
            family_id
            for family in families
            if (family_id := _family_id(family))
            and self.matching_target(family) is not None
        )

    @property
    def target_config_identity(self) -> str:
        """Stable identity of the owner-validated target snapshot."""

        payload = {
            "snapshot_id": self.target_snapshot.snapshot_id,
            "observed_labels": list(self.observed_labels),
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @property
    def targets(self) -> tuple[Target, ...]:
        return tuple(
            definition.target for definition in self.target_snapshot.definitions
        )


def build_targeted_istd_identity_anchor(
    target_snapshot: TargetListSnapshot,
    *,
    observed_labels: Iterable[str],
) -> TargetedIstdIdentityAnchor:
    """Mint a bounded target anchor from existing owner inputs."""

    if not isinstance(target_snapshot, TargetListSnapshot):
        raise TypeError("target_snapshot must be an owner-minted snapshot")
    labels = tuple(
        sorted(
            {
                str(label).strip()
                for label in observed_labels
                if str(label).strip()
            }
        )
    )
    if not target_snapshot.definitions or not labels:
        raise ValueError("ISTD identity anchor requires targets and observed labels")
    return TargetedIstdIdentityAnchor(
        target_snapshot=target_snapshot,
        observed_labels=labels,
        _mint_token=_TARGETED_ISTD_ANCHOR_MINT_TOKEN,
    )


@dataclass(frozen=True, slots=True)
class LocalFeatureLandmarkEvidence:
    """One preregistered local landmark retained on its injection trajectory."""

    sample_stem: str
    injection_order: int
    acquisition_source_id: str
    processing_request_id: str
    processing_attempt_id: str
    landmark_id: str
    observed_rt_min: float
    local_trajectory_rt_min: float
    rt_drift_delta_min: float
    quality: LocalLandmarkQuality
    evidence_ref: str
    exclusion_reason: str = ""
    validation_status: str = LOCAL_TRAJECTORY_VALIDATION_STATUS
    product_authority: str = LOCAL_TRAJECTORY_PRODUCT_AUTHORITY

    def __post_init__(self) -> None:
        for field_name in (
            "sample_stem",
            "acquisition_source_id",
            "processing_request_id",
            "processing_attempt_id",
            "landmark_id",
            "evidence_ref",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-empty text")
        if (
            isinstance(self.injection_order, bool)
            or not isinstance(self.injection_order, int)
            or self.injection_order < 1
        ):
            raise ValueError("injection_order must be a positive integer")
        for field_name in (
            "observed_rt_min",
            "local_trajectory_rt_min",
            "rt_drift_delta_min",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"{field_name} must be numeric")
            if not math.isfinite(float(value)):
                raise ValueError(f"{field_name} must be finite")
        expected_delta = self.observed_rt_min - self.local_trajectory_rt_min
        if not math.isclose(
            self.rt_drift_delta_min,
            expected_delta,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "rt_drift_delta_min must equal observed minus local trajectory RT"
            )
        if self.quality not in {"accepted", "poor"}:
            raise ValueError("quality must be accepted or poor")
        if not isinstance(self.exclusion_reason, str):
            raise ValueError("exclusion_reason must be text")
        if self.quality == "accepted" and self.exclusion_reason:
            raise ValueError("accepted landmarks cannot carry an exclusion reason")
        if self.quality == "poor" and not self.exclusion_reason.strip():
            raise ValueError("poor landmarks require an exclusion reason")
        _validate_local_trajectory_authority(
            validation_status=self.validation_status,
            product_authority=self.product_authority,
        )


@dataclass(frozen=True, slots=True)
class LocalTrajectoryAssessment:
    """Shadow-only assessability/conflict result; never a SUPPORT vote."""

    status: LocalTrajectoryStatus
    sample_stems: tuple[str, ...]
    landmark_evidence_refs: tuple[str, ...]
    reason_codes: tuple[str, ...]
    landmark_points: tuple[LocalFeatureLandmarkEvidence, ...] = ()
    conflict_evidence_refs: tuple[str, ...] = ()
    validation_status: str = LOCAL_TRAJECTORY_VALIDATION_STATUS
    product_authority: str = LOCAL_TRAJECTORY_PRODUCT_AUTHORITY
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _LOCAL_TRAJECTORY_ASSESSMENT_MINT_TOKEN:
            raise ValueError(
                "local trajectory assessments must be minted by their owner"
            )
        if self.status not in {
            "assessable",
            "missing_landmarks",
            "poor_landmarks",
            "conflict",
        }:
            raise ValueError("unsupported local trajectory status")
        _require_canonical_text(self.sample_stems, field="sample_stems")
        if self.landmark_evidence_refs:
            _require_canonical_text(
                self.landmark_evidence_refs,
                field="landmark_evidence_refs",
            )
        _require_canonical_text(self.reason_codes, field="reason_codes")
        if any(
            not isinstance(point, LocalFeatureLandmarkEvidence)
            for point in self.landmark_points
        ):
            raise ValueError("landmark_points must retain typed local evidence")
        if any(
            point.sample_stem not in self.sample_stems
            for point in self.landmark_points
        ):
            raise ValueError("landmark_points must stay inside assessed samples")
        point_refs = tuple(
            sorted({point.evidence_ref for point in self.landmark_points})
        )
        if point_refs != self.landmark_evidence_refs:
            raise ValueError(
                "landmark evidence refs must close the exact retained local points"
            )
        if self.conflict_evidence_refs:
            _require_canonical_text(
                self.conflict_evidence_refs,
                field="conflict_evidence_refs",
            )
        accepted_refs = {
            point.evidence_ref
            for point in self.landmark_points
            if point.quality == "accepted"
        }
        if self.status == "conflict":
            if not self.conflict_evidence_refs:
                raise ValueError("trajectory conflict requires retained evidence refs")
            if not set(self.conflict_evidence_refs).issubset(accepted_refs):
                raise ValueError(
                    "trajectory conflict evidence must close to accepted landmarks"
                )
        elif self.conflict_evidence_refs:
            raise ValueError("only conflict assessments may retain conflict evidence")
        _validate_local_trajectory_authority(
            validation_status=self.validation_status,
            product_authority=self.product_authority,
        )


@dataclass(frozen=True)
class LocalTrajectoryEvidenceLookup:
    """Full local-landmark set for identity-trajectory shadow assessment only."""

    points: tuple[LocalFeatureLandmarkEvidence, ...]
    validation_status: str = LOCAL_TRAJECTORY_VALIDATION_STATUS
    product_authority: str = LOCAL_TRAJECTORY_PRODUCT_AUTHORITY

    def __post_init__(self) -> None:
        if not isinstance(self.points, tuple) or any(
            not isinstance(point, LocalFeatureLandmarkEvidence)
            for point in self.points
        ):
            raise ValueError("points must be LocalFeatureLandmarkEvidence refs")
        keys = tuple((point.sample_stem, point.landmark_id) for point in self.points)
        if len(keys) != len(set(keys)):
            raise ValueError("local landmark evidence must be unique")
        evidence_refs = tuple(point.evidence_ref for point in self.points)
        if len(evidence_refs) != len(set(evidence_refs)):
            raise ValueError(
                "local landmark physical evidence refs must be unique"
            )
        injection_orders_by_sample: dict[str, set[int]] = {}
        acquisition_sources_by_sample: dict[str, set[str]] = {}
        processing_requests_by_sample: dict[str, set[str]] = {}
        processing_attempts_by_sample: dict[str, set[str]] = {}
        for point in self.points:
            injection_orders_by_sample.setdefault(point.sample_stem, set()).add(
                point.injection_order
            )
            acquisition_sources_by_sample.setdefault(point.sample_stem, set()).add(
                point.acquisition_source_id
            )
            processing_requests_by_sample.setdefault(point.sample_stem, set()).add(
                point.processing_request_id
            )
            processing_attempts_by_sample.setdefault(point.sample_stem, set()).add(
                point.processing_attempt_id
            )
        if any(len(orders) != 1 for orders in injection_orders_by_sample.values()):
            raise ValueError(
                "local landmarks for one sample require one injection order"
            )
        if any(len(sources) != 1 for sources in acquisition_sources_by_sample.values()):
            raise ValueError(
                "local landmarks for one sample require one acquisition source"
            )
        if any(
            len(requests) != 1
            for requests in processing_requests_by_sample.values()
        ):
            raise ValueError(
                "local landmarks for one sample require one processing request"
            )
        if any(
            len(attempts) != 1
            for attempts in processing_attempts_by_sample.values()
        ):
            raise ValueError(
                "local landmarks for one sample require one processing attempt"
            )
        _validate_local_trajectory_authority(
            validation_status=self.validation_status,
            product_authority=self.product_authority,
        )

    def sample_injection_order(self, sample_stem: str) -> int | None:
        orders = self._injection_orders_by_sample.get(sample_stem)
        if not orders:
            return None
        if len(orders) == 1:
            return orders[0]
        raise ValueError(
            "conflicting injection order for "
            f"sample {sample_stem!r}: {list(orders)}"
        )

    def landmark_status(self, sample_stem: str) -> LocalTrajectoryStatus:
        sample_points = tuple(
            point for point in self.points if point.sample_stem == sample_stem
        )
        if not sample_points:
            return "missing_landmarks"
        if not any(point.quality == "accepted" for point in sample_points):
            return "poor_landmarks"
        return "assessable"

    def evidence_refs(self, sample_stem: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                point.evidence_ref
                for point in self.points
                if point.sample_stem == sample_stem
            )
        )

    @cached_property
    def _injection_orders_by_sample(self) -> dict[str, tuple[int, ...]]:
        by_sample: dict[str, set[int]] = {}
        for point in self.points:
            by_sample.setdefault(point.sample_stem, set()).add(
                point.injection_order
            )
        return {
            sample_stem: tuple(sorted(orders))
            for sample_stem, orders in by_sample.items()
        }


def assess_local_trajectory(
    lookup: LocalTrajectoryEvidenceLookup,
    sample_stems: tuple[str, ...],
    *,
    conflict_evidence_refs: tuple[str, ...] = (),
) -> LocalTrajectoryAssessment:
    """Classify availability only; conflict must arrive as explicit evidence."""

    if not isinstance(lookup, LocalTrajectoryEvidenceLookup):
        raise TypeError("local trajectory assessment requires local feature landmarks")
    ordered_samples = _canonicalize_input_texts(
        sample_stems,
        field="sample_stems",
    )
    statuses = tuple(lookup.landmark_status(sample) for sample in ordered_samples)
    if "missing_landmarks" in statuses:
        status: LocalTrajectoryStatus = "missing_landmarks"
    elif "poor_landmarks" in statuses:
        status = "poor_landmarks"
    else:
        status = "assessable"
    evidence_refs = tuple(
        sorted(
            {
                ref
                for sample in ordered_samples
                for ref in lookup.evidence_refs(sample)
            }
        )
    )
    landmark_points = tuple(
        point for point in lookup.points if point.sample_stem in ordered_samples
    )
    ordered_conflicts = _canonicalize_input_texts(
        conflict_evidence_refs,
        field="conflict_evidence_refs",
        allow_empty=True,
    )
    if status == "assessable" and ordered_conflicts:
        accepted_refs = {
            point.evidence_ref
            for point in landmark_points
            if point.quality == "accepted"
        }
        if not set(ordered_conflicts).issubset(accepted_refs):
            raise ValueError(
                "trajectory conflict refs must come from retained accepted landmarks"
            )
        return LocalTrajectoryAssessment(
            status="conflict",
            sample_stems=ordered_samples,
            landmark_evidence_refs=evidence_refs,
            reason_codes=("trajectory_conflict",),
            landmark_points=landmark_points,
            conflict_evidence_refs=ordered_conflicts,
            _mint_token=_LOCAL_TRAJECTORY_ASSESSMENT_MINT_TOKEN,
        )
    return LocalTrajectoryAssessment(
        status=status,
        sample_stems=ordered_samples,
        landmark_evidence_refs=evidence_refs,
        reason_codes=(f"trajectory_{status}",),
        landmark_points=landmark_points,
        _mint_token=_LOCAL_TRAJECTORY_ASSESSMENT_MINT_TOKEN,
    )


def _validate_local_trajectory_authority(
    *,
    validation_status: str,
    product_authority: str,
) -> None:
    if validation_status != LOCAL_TRAJECTORY_VALIDATION_STATUS:
        raise ValueError(
            "local trajectory validation_status must remain shadow_ready"
        )
    if product_authority != LOCAL_TRAJECTORY_PRODUCT_AUTHORITY:
        raise ValueError("local trajectory evidence cannot grant product authority")


@dataclass(frozen=True)
class DriftEvidenceLookup:
    points: tuple[SampleDriftEvidence, ...]
    istd_identity_anchor: TargetedIstdIdentityAnchor | None = None

    @property
    def source(self) -> DriftPriorSource:
        if not self.points:
            return "none"
        if any(point.source == "targeted_istd_trend" for point in self.points):
            return "targeted_istd_trend"
        return "batch_istd_trend"

    def sample_delta_min(self, sample_stem: str) -> float | None:
        return self._sample_delta_medians.get(sample_stem)

    def injection_order(self, sample_stem: str) -> int | None:
        orders = self._injection_orders_by_sample.get(sample_stem)
        if not orders:
            return None
        if len(orders) == 1:
            return orders[0]
        raise ValueError(
            "conflicting injection order for "
            f"sample {sample_stem!r}: {list(orders)}"
        )

    @cached_property
    def _sample_delta_medians(self) -> dict[str, float]:
        by_sample: dict[str, list[float]] = {}
        for point in self.points:
            by_sample.setdefault(point.sample_stem, []).append(point.rt_drift_delta_min)
        return {
            sample_stem: float(statistics.median(deltas))
            for sample_stem, deltas in by_sample.items()
        }

    @cached_property
    def _injection_orders_by_sample(self) -> dict[str, tuple[int, ...]]:
        by_sample: dict[str, set[int]] = {}
        for point in self.points:
            by_sample.setdefault(point.sample_stem, set()).add(point.injection_order)
        return {
            sample_stem: tuple(sorted(orders))
            for sample_stem, orders in by_sample.items()
        }


def read_targeted_istd_drift_evidence(
    targeted_workbook: Path,
    sample_info: Path,
    local_window: int = 4,
    *,
    target_snapshot: TargetListSnapshot | None = None,
) -> DriftEvidenceLookup:
    injection_order = read_injection_order(sample_info)
    rt_by_label = _read_istd_rt_by_label(targeted_workbook)
    ordered_labels = tuple(sorted(rt_by_label))
    trend_ids = {
        label: f"trend-{index:03d}"
        for index, label in enumerate(ordered_labels, start=1)
    }
    points: list[SampleDriftEvidence] = []
    for label in ordered_labels:
        rt_by_sample = rt_by_label[label]
        for sample_stem in sorted(
            rt_by_sample,
            key=lambda sample: (injection_order.get(sample, 10**12), sample),
        ):
            local_median = rolling_median_rt(
                label,
                sample_stem,
                rt_by_sample,
                injection_order,
                window=local_window,
            )
            sample_order = injection_order.get(sample_stem)
            if local_median is None or sample_order is None:
                continue
            istd_rt = rt_by_sample[sample_stem]
            points.append(
                SampleDriftEvidence(
                    sample_stem=sample_stem,
                    injection_order=sample_order,
                    trend_id=trend_ids[label],
                    istd_rt_min=istd_rt,
                    local_trend_rt_min=local_median,
                    rt_drift_delta_min=istd_rt - local_median,
                    source="targeted_istd_trend",
                )
            )
    identity_anchor = _default_targeted_istd_identity_anchor(
        observed_labels=tuple(rt_by_label),
        target_snapshot=target_snapshot,
    )
    return DriftEvidenceLookup(
        points=tuple(points),
        istd_identity_anchor=identity_anchor,
    )


def _read_istd_rt_by_label(targeted_workbook: Path) -> dict[str, dict[str, float]]:
    wb = load_workbook(targeted_workbook, read_only=True, data_only=True)
    try:
        ws = wb["XIC Results"]
        rows = ws.iter_rows(values_only=True)
        header = next(rows)
        cols = _header_indexes(header)
        sample_name = ""
        out: dict[str, dict[str, float]] = {}
        for row in rows:
            raw_sample = row[cols["SampleName"]]
            if raw_sample is not None:
                candidate_sample = str(raw_sample).strip()
                if candidate_sample:
                    sample_name = candidate_sample
            if not sample_name:
                continue
            role = row[cols["Role"]]
            if str(role).strip() != "ISTD":
                continue
            rt = _to_float(row[cols["RT"]])
            if rt is None:
                continue
            label = str(row[cols["Target"]]).strip()
            if not label:
                continue
            out.setdefault(label, {})[sample_name] = rt
        return out
    finally:
        wb.close()


def _default_targeted_istd_identity_anchor(
    *,
    observed_labels: Iterable[str],
    target_snapshot: TargetListSnapshot | None,
) -> TargetedIstdIdentityAnchor | None:
    if target_snapshot is None:
        return None
    targets = tuple(
        definition.target for definition in target_snapshot.definitions
    )
    known_labels = {target.label for target in targets}
    labels = tuple(
        sorted(
            {
                str(label).strip()
                for label in observed_labels
                if str(label).strip() in known_labels
            }
        )
    )
    if not targets or not labels:
        return None
    return build_targeted_istd_identity_anchor(
        target_snapshot,
        observed_labels=labels,
    )


def _family_id(family: object) -> str:
    return str(
        getattr(family, "feature_family_id", "")
        or getattr(family, "cluster_id", "")
    ).strip()


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _header_indexes(header: tuple[object, ...]) -> dict[str, int]:
    cols = {
        str(value).strip(): i
        for i, value in enumerate(header)
        if value is not None
    }
    required = ("SampleName", "Target", "Role", "RT")
    missing = [name for name in required if name not in cols]
    if missing:
        raise ValueError(f"XIC Results sheet missing required columns: {missing}")
    return cols


def _to_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _require_canonical_text(values: tuple[str, ...], *, field: str) -> None:
    if (
        not isinstance(values, tuple)
        or not values
        or any(not isinstance(value, str) or not value.strip() for value in values)
        or values != tuple(sorted(set(values)))
    ):
        raise ValueError(f"{field} must be non-empty, unique, and canonical-sorted")


def _canonicalize_input_texts(
    values: tuple[str, ...],
    *,
    field: str,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if (
        not isinstance(values, tuple)
        or (not allow_empty and not values)
        or any(not isinstance(value, str) or not value.strip() for value in values)
    ):
        raise ValueError(f"{field} must contain canonical non-empty text")
    if len(values) != len(set(values)):
        raise ValueError(f"{field} must not contain duplicates")
    return tuple(sorted(values))
