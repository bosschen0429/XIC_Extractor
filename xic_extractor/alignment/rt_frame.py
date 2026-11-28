"""Stage-local RT answers over shared metadata and the frozen batch calibration.

Coordinate availability is distinct from measured zero drift. Pairwise ISTD
identity evidence and batch coordinate calibration consume the same measured
reference but remain separate answers; calibration does not prove correspondence.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from statistics import median
from typing import TYPE_CHECKING, Protocol

from xic_extractor.alignment.drift_evidence import (
    KnownIstdOwnerLookup,
    KnownIstdReference,
)
from xic_extractor.alignment.early_segment_shift import (
    EarlySegmentShift,
    measure_early_segment_shift,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.sample_rt_calibration import RtEstimate
from xic_extractor.injection_timeline import InjectionSequence
from xic_extractor.sample_metadata import SampleMetadata

if TYPE_CHECKING:
    from xic_extractor.alignment.rt_drift import RtDriftModel
    from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration


class RtFrame(Protocol):
    """The RT questions shared by grouping and localization consumers."""

    @property
    def injections(self) -> InjectionSequence: ...

    @property
    def sample_cohorts(self) -> Mapping[str, str]: ...

    @property
    def known_identity_bindings(self) -> tuple[tuple[str, str], ...]: ...

    @property
    def drift_model(self) -> RtDriftModel | None: ...

    def corrected_rt_min(self, owner: SampleLocalMS1Owner) -> float: ...

    def common_rt_min(self, sample_stem: str, observed_rt: float) -> float: ...

    def calibrated_rt_min(
        self, sample_stem: str, observed_rt: float
    ) -> float | None: ...

    def early_segment_rt_min(
        self, sample_stem: str, observed_rt: float
    ) -> float | None: ...

    def observed_rt_min(self, sample_stem: str, common_rt: float) -> float | None: ...

    def estimate_rt(
        self, sample_stem: str, observed_rt: float
    ) -> RtEstimate | None: ...

    def estimate_observed_rt(
        self, sample_stem: str, common_rt: float
    ) -> RtEstimate | None: ...

    def offset_min(self, sample_stem: str, rt_min: float) -> float: ...


@dataclass(frozen=True)
class SampleRtFrame:
    """One stage's sources; derived stages reuse the sample injection sequence.

    The batch owner freezes calibration. Owner-local ISTD bindings are retained
    when that calibration is attached, rather than rebound to another population.
    Diagnostic coordinate overrides do not provide Backfill calibration support.
    """

    injections: InjectionSequence = field(default_factory=InjectionSequence)
    sample_cohorts: Mapping[str, str] = field(default_factory=dict)
    sample_universe: frozenset[str] | None = None
    qc_samples: frozenset[str] = frozenset()
    _istd_bindings: KnownIstdOwnerLookup | None = field(
        default=None,
        repr=False,
    )
    _reference: KnownIstdReference | None = field(default=None, repr=False)
    _sample_calibration: BatchRtCalibration | None = field(default=None, repr=False)
    _drift_model: RtDriftModel | None = field(default=None, repr=False)
    _early_segment: EarlySegmentShift = field(
        default_factory=EarlySegmentShift, repr=False
    )
    _coordinate_override: Callable[[SampleLocalMS1Owner], float] | None = field(
        default=None,
        repr=False,
    )

    @classmethod
    def from_samples(
        cls,
        samples: Sequence[SampleMetadata],
        *,
        injections: InjectionSequence | None = None,
    ) -> SampleRtFrame:
        """Derive metadata once at the boundary that owns the sample universe."""
        return cls(
            injections=InjectionSequence.from_samples(samples)
            if injections is None
            else injections,
            sample_cohorts={sample.raw_stem: sample.batch_id for sample in samples},
            sample_universe=frozenset(sample.raw_stem for sample in samples),
            qc_samples=frozenset(
                sample.raw_stem for sample in samples if sample.sample_role == "qc"
            ),
        )

    def with_sources(
        self,
        *,
        known_istd_reference: KnownIstdReference | None = None,
    ) -> SampleRtFrame:
        return replace(self, _istd_bindings=None, _reference=known_istd_reference)

    def bind_owners(
        self,
        owners: Sequence[SampleLocalMS1Owner],
    ) -> SampleRtFrame:
        """Bind the existing reference to this workset's complete owner universe."""
        if self._reference is None:
            return self
        return replace(
            self,
            _istd_bindings=self._reference.bind(tuple(owners)),
        )

    def with_sample_calibration(
        self,
        calibration: BatchRtCalibration,
        drift_model: RtDriftModel | None = None,
    ) -> SampleRtFrame:
        """Attach the bounded batch map shared by grouping and localization."""
        return replace(
            self,
            _sample_calibration=calibration,
            _drift_model=drift_model,
            _early_segment=measure_early_segment_shift(calibration, self.injections),
            _coordinate_override=None,
        )

    @property
    def drift_model(self) -> RtDriftModel | None:
        """Frozen batch state and sample shift beyond the general map."""
        return self._drift_model

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
            injections=self.injections.restrict(samples),
            sample_cohorts={
                k: v for k, v in self.sample_cohorts.items() if k in samples
            },
        )

    @property
    def calibration_for_snapshot(self) -> BatchRtCalibration | None:
        """Retain the same frozen map used by every current RT consumer."""
        return self._sample_calibration

    def with_calibration(self, frozen: SampleRtFrame) -> SampleRtFrame:
        """Attach frozen batch coordinates without changing local identity evidence."""
        return replace(
            self,
            _sample_calibration=frozen._sample_calibration,
            _drift_model=frozen._drift_model,
            _early_segment=frozen._early_segment,
            _coordinate_override=frozen._coordinate_override,
        )

    def with_coordinate_override(
        self,
        corrected_rt_min: Callable[[SampleLocalMS1Owner], float],
    ) -> SampleRtFrame:
        """Keep opt-in diagnostic coordinates separate from production estimators."""
        return replace(self, _coordinate_override=corrected_rt_min)

    @property
    def known_identity_bindings(self) -> tuple[tuple[str, str], ...]:
        return self._istd_bindings.bindings if self._istd_bindings is not None else ()

    def corrected_rt_min(self, owner: SampleLocalMS1Owner) -> float:
        if self._coordinate_override is not None:
            return self._coordinate_override(owner)
        source = self._sample_calibration
        return (
            source.corrected_rt_min(owner)
            if source is not None
            else owner.peak.rt_apex_min
        )

    def common_rt_min(self, sample_stem: str, observed_rt: float) -> float:
        source = self._sample_calibration
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
        return None

    def early_segment_rt_min(
        self, sample_stem: str, observed_rt: float
    ) -> float | None:
        """An under-equilibrated injection's early peak, in its neighbours' maps.

        The peak is first moved to where it would elute without the shift, then
        read through the maps of the unaffected injections around it; without
        their answer it keeps that unshifted RT. None where no shift applies.
        """
        elution = self._early_segment.elution_rt_min(sample_stem, observed_rt)
        if elution is None:
            return None
        mapped = [
            rt
            for other in self._early_segment.neighbours[sample_stem]
            if (rt := self.calibrated_rt_min(other, elution)) is not None
        ]
        return median(mapped) if mapped else elution

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
        return None

    def estimate_rt(self, sample_stem: str, observed_rt: float) -> RtEstimate | None:
        if self._sample_calibration is not None:
            sample = self._sample_calibration.samples.get(sample_stem)
            return sample.estimate_rt(observed_rt) if sample is not None else None
        calibrated = self.calibrated_rt_min(sample_stem, observed_rt)
        return (
            RtEstimate(calibrated, "retained_map") if calibrated is not None else None
        )

    def estimate_observed_rt(
        self, sample_stem: str, common_rt: float
    ) -> RtEstimate | None:
        if self._sample_calibration is not None:
            sample = self._sample_calibration.samples.get(sample_stem)
            return (
                sample.estimate_observed_rt(common_rt) if sample is not None else None
            )
        observed = self.observed_rt_min(sample_stem, common_rt)
        return RtEstimate(observed, "retained_map") if observed is not None else None

    def offset_min(self, sample_stem: str, rt_min: float) -> float:
        if self._sample_calibration is not None:
            return self._sample_calibration.offset_min(sample_stem, rt_min)
        return 0.0
