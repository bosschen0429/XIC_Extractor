"""Stage-local RT answers over shared metadata and the frozen batch calibration.

Coordinate availability is distinct from measured zero drift. Pairwise ISTD
identity evidence and batch coordinate calibration consume the same measured
reference but remain separate answers; calibration does not prove correspondence.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Protocol

from xic_extractor.alignment.drift_evidence import (
    DriftEvidenceLookup,
    DriftPriorSource,
    KnownIstdOwnerLookup,
    KnownIstdReference,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

if TYPE_CHECKING:
    from xic_extractor.alignment.feature_membership_inference import BatchRtWarp
    from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration


class RtFrame(Protocol):
    """The RT questions shared by grouping and localization consumers."""

    @property
    def injection_order(self) -> Mapping[str, int]: ...

    @property
    def sample_cohorts(self) -> Mapping[str, str]: ...

    @property
    def has_coordinates(self) -> bool: ...

    @property
    def has_drift(self) -> bool: ...

    @property
    def drift_source(self) -> DriftPriorSource: ...

    @property
    def known_identity_bindings(self) -> tuple[tuple[str, str], ...]: ...

    def corrected_rt_min(self, owner: SampleLocalMS1Owner) -> float: ...

    def has_support(self, sample_stem: str) -> bool: ...

    def common_rt_min(self, sample_stem: str, observed_rt: float) -> float: ...

    def calibrated_rt_min(
        self, sample_stem: str, observed_rt: float
    ) -> float | None: ...

    def observed_rt_min(self, sample_stem: str, common_rt: float) -> float | None: ...

    def calibrated_peak_region(
        self, owner: SampleLocalMS1Owner
    ) -> tuple[float, float, float] | None: ...

    def offset_min(self, sample_stem: str, rt_min: float) -> float: ...

    def corrected_pair_delta_sec(
        self,
        left: SampleLocalMS1Owner,
        right: SampleLocalMS1Owner,
    ) -> float | None: ...

    def has_direct_reference_pair(
        self,
        left: SampleLocalMS1Owner,
        right: SampleLocalMS1Owner,
    ) -> bool: ...

    def drift_injection_order_gap(self, left: str, right: str) -> int | None: ...


@dataclass(frozen=True)
class SampleRtFrame:
    """One stage's sources; derived stages reuse the same metadata mappings.

    The batch owner freezes calibration. Owner-local ISTD bindings are retained
    when that calibration is attached, rather than rebound to another population.
    Diagnostic coordinate overrides do not provide Backfill calibration support.
    """

    injection_order: Mapping[str, int] = field(default_factory=dict)
    sample_cohorts: Mapping[str, str] = field(default_factory=dict)
    sample_universe: frozenset[str] | None = None
    _drift: DriftEvidenceLookup | KnownIstdOwnerLookup | None = field(
        default=None,
        repr=False,
    )
    _reference: KnownIstdReference | None = field(default=None, repr=False)
    _warp: BatchRtWarp | None = field(default=None, repr=False)
    _sample_calibration: BatchRtCalibration | None = field(default=None, repr=False)
    _coordinate_override: Callable[[SampleLocalMS1Owner], float] | None = field(
        default=None,
        repr=False,
    )

    @classmethod
    def from_samples(
        cls,
        samples: Sequence[ResolvedCanonicalSampleIdentity],
    ) -> SampleRtFrame:
        """Derive metadata once at the boundary that owns the sample universe."""
        return cls(
            injection_order={
                sample.metadata.raw_stem: sample.metadata.injection_order
                for sample in samples
                if sample.metadata.injection_order is not None
            },
            sample_cohorts={
                sample.metadata.raw_stem: sample.metadata.batch_id for sample in samples
            },
            sample_universe=frozenset(sample.metadata.raw_stem for sample in samples),
        )

    def with_sources(
        self,
        *,
        drift_lookup: DriftEvidenceLookup | KnownIstdOwnerLookup | None = None,
        known_istd_reference: KnownIstdReference | None = None,
    ) -> SampleRtFrame:
        if known_istd_reference is not None and drift_lookup is not None:
            raise ValueError(
                "known ISTD reference cannot be combined with generic drift"
            )
        return replace(self, _drift=drift_lookup, _reference=known_istd_reference)

    def bind_owners(
        self,
        owners: Sequence[SampleLocalMS1Owner],
    ) -> SampleRtFrame:
        """Bind the existing reference to this workset's complete owner universe."""
        if self._reference is None:
            return self
        return replace(
            self,
            _drift=self._reference.bind(
                tuple(owners),
                sample_universe=self.sample_universe,
                injection_order=self.injection_order,
            ),
        )

    def with_warp(self, warp: BatchRtWarp | None) -> SampleRtFrame:
        return replace(
            self,
            _warp=warp,
            _sample_calibration=None,
            _coordinate_override=None,
        )

    def with_sample_calibration(self, calibration: BatchRtCalibration) -> SampleRtFrame:
        """Attach the bounded batch map shared by grouping and localization."""
        return replace(
            self,
            _sample_calibration=calibration,
            _warp=None,
            _coordinate_override=None,
        )

    @property
    def measured_istd_coordinates(self) -> tuple[tuple[str, str, float], ...]:
        """Measured anchors, never interpolated per-injection predictions."""
        return (
            tuple(
                (label, sample, rt)
                for label, sample, rt, _ in self._reference.coordinates
            )
            if self._reference is not None
            else ()
        )

    def for_sample_stems(self, samples: frozenset[str]) -> SampleRtFrame:
        """Restrict a consumer's population without reconstructing sample metadata."""
        if samples == self.sample_universe:
            return self
        return replace(
            self,
            sample_universe=samples,
            injection_order={
                k: v for k, v in self.injection_order.items() if k in samples
            },
            sample_cohorts={
                k: v for k, v in self.sample_cohorts.items() if k in samples
            },
        )

    @property
    def warp_for_snapshot(self) -> BatchRtWarp | None:
        """Expose the existing calibration payload only to the snapshot boundary."""
        return self._warp

    @property
    def calibration_for_snapshot(self) -> BatchRtCalibration | None:
        """Retain the same frozen map used by every current RT consumer."""
        return self._sample_calibration

    def with_calibration(self, frozen: SampleRtFrame) -> SampleRtFrame:
        """Attach frozen batch coordinates without changing local identity evidence."""
        return replace(
            self,
            _warp=frozen._warp,
            _sample_calibration=frozen._sample_calibration,
            _coordinate_override=frozen._coordinate_override,
        )

    def with_coordinate_override(
        self,
        corrected_rt_min: Callable[[SampleLocalMS1Owner], float],
    ) -> SampleRtFrame:
        """Keep opt-in diagnostic coordinates separate from production estimators."""
        return replace(self, _coordinate_override=corrected_rt_min)

    @property
    def has_coordinates(self) -> bool:
        return (
            self._warp is not None
            or self._sample_calibration is not None
            or self._coordinate_override is not None
        )

    @property
    def has_drift(self) -> bool:
        return self._sample_calibration is not None or self._drift is not None

    @property
    def drift_source(self) -> DriftPriorSource:
        if self._sample_calibration is not None:
            return "batch_anchor_calibration"
        if self._drift is None:
            return "none"
        source = self._drift.source
        supported = {"targeted_istd_trend", "batch_istd_trend", "none"}
        if source not in supported:
            raise ValueError(
                f"Unsupported drift prior source: {source}. Expected one of: "
                + ", ".join(sorted(supported))
            )
        return source

    @property
    def known_identity_bindings(self) -> tuple[tuple[str, str], ...]:
        return (
            tuple((owner, label) for owner, label, _ in self._drift.bindings)
            if isinstance(self._drift, KnownIstdOwnerLookup)
            else ()
        )

    def corrected_pair_delta_sec(
        self,
        left: SampleLocalMS1Owner,
        right: SampleLocalMS1Owner,
    ) -> float | None:
        if self._sample_calibration is not None:
            a = self.calibrated_rt_min(left.sample_stem, left.owner_apex_rt)
            b = self.calibrated_rt_min(right.sample_stem, right.owner_apex_rt)
            return abs(a - b) * 60.0 if a is not None and b is not None else None
        if self._drift is None:
            return None
        if isinstance(self._drift, KnownIstdOwnerLookup):
            return self._drift.corrected_pair_delta_sec(left, right)
        left_delta = self._drift.sample_delta_min(left.sample_stem)
        right_delta = self._drift.sample_delta_min(right.sample_stem)
        if left_delta is None or right_delta is None:
            return None
        left_corrected_rt = left.owner_apex_rt - left_delta
        right_corrected_rt = right.owner_apex_rt - right_delta
        return abs(left_corrected_rt - right_corrected_rt) * 60.0

    def has_direct_reference_pair(
        self,
        left: SampleLocalMS1Owner,
        right: SampleLocalMS1Owner,
    ) -> bool:
        return isinstance(self._drift, KnownIstdOwnerLookup) and (
            self._drift.has_direct_reference_pair(left, right)
        )

    def drift_injection_order_gap(self, left: str, right: str) -> int | None:
        # Edge evidence uses the drift source's order, not a metadata fallback.
        if self._drift is None:
            return None
        left_order = self._drift.injection_order(left)
        right_order = self._drift.injection_order(right)
        if left_order is None or right_order is None:
            return None
        return abs(left_order - right_order)

    def corrected_rt_min(self, owner: SampleLocalMS1Owner) -> float:
        if self._coordinate_override is not None:
            return self._coordinate_override(owner)
        source = self._sample_calibration or self._warp
        return (
            source.corrected_rt_min(owner)
            if source is not None
            else owner.owner_apex_rt
        )

    def has_support(self, sample_stem: str) -> bool:
        source = self._sample_calibration or self._warp
        return source is not None and source.has_support(sample_stem)

    def common_rt_min(self, sample_stem: str, observed_rt: float) -> float:
        source = self._sample_calibration or self._warp
        return (
            source.common_rt_min(sample_stem, observed_rt)
            if source is not None
            else observed_rt
        )

    def calibrated_rt_min(self, sample_stem: str, observed_rt: float) -> float | None:
        """A bounded common coordinate, distinct from an unsupported raw RT."""
        if self._sample_calibration is not None:
            sample = self._sample_calibration.samples.get(sample_stem)
            return sample.calibrated_rt(observed_rt) if sample is not None else None
        if self._warp is not None and self._warp.has_support(sample_stem):
            return self._warp.common_rt_min(sample_stem, observed_rt)
        return None

    def observed_rt_min(self, sample_stem: str, common_rt: float) -> float | None:
        if self._sample_calibration is not None:
            sample = self._sample_calibration.samples.get(sample_stem)
            if sample is None or not sample.knots:
                return None
            if not sample.knots[0][1] <= common_rt <= sample.knots[-1][1]:
                return None
            return common_rt + self._sample_calibration.offset_min(
                sample_stem, common_rt
            )
        if self._warp is not None and self._warp.has_support(sample_stem):
            return common_rt + self._warp.offset_min(sample_stem, common_rt)
        return None

    def calibrated_peak_region(
        self, owner: SampleLocalMS1Owner
    ) -> tuple[float, float, float] | None:
        # A historical clamped offset map has no measured support bounds.
        if self._sample_calibration is None:
            return None
        start, apex, end = (
            self.calibrated_rt_min(owner.sample_stem, value)
            for value in (
                owner.owner_peak_start_rt,
                owner.owner_apex_rt,
                owner.owner_peak_end_rt,
            )
        )
        if start is None or apex is None or end is None:
            return None
        return start, apex, end

    def offset_min(self, sample_stem: str, rt_min: float) -> float:
        if self._sample_calibration is not None:
            return self._sample_calibration.offset_min(sample_stem, rt_min)
        return (
            self._warp.offset_min(sample_stem, rt_min)
            if self._warp is not None
            else 0.0
        )
