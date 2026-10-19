from __future__ import annotations

import hashlib
import json
import math
import statistics
from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import Literal

from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.configuration.models import Target
from xic_extractor.configuration.target_identity import (
    TargetListSnapshot,
    mint_target_list_snapshot,
)
from xic_extractor.mass_accuracy import neutral_loss_match

DriftPriorSource = Literal["targeted_istd_trend", "batch_istd_trend", "none"]


@dataclass(frozen=True)
class KnownIstdReference:
    """Declared ISTD definitions and measured reference coordinates, never areas."""

    workbook_path: str
    workbook_sha256: str
    anchor: TargetedIstdIdentityAnchor
    coordinates: tuple[tuple[str, str, float, float], ...]
    _batch_context: tuple[tuple[str, ...], tuple[tuple[str, int], ...]] | None = field(
        default=None, repr=False
    )
    _batch_knots: tuple[tuple[str, tuple[tuple[float, float], ...]], ...] = field(
        default=(), repr=False
    )
    _prepared_batch: KnownIstdReference | None = field(
        default=None, init=False, repr=False, compare=False
    )

    @cached_property
    def content_sha256(self) -> str:
        """Identify declared targets and measured coordinates, not source bytes."""
        payload = {
            "schema_version": "known_istd_reference_content",
            "target_snapshot_id": self.anchor.target_snapshot.snapshot_id,
            "observed_labels": self.anchor.observed_labels,
            "coordinates": tuple(
                (label, sample, float(rt), float(delta))
                for label, sample, rt, delta in self.coordinates
            ),
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()

    @property
    def provenance(self) -> dict[str, str]:
        return {
            "workbook_path": self.workbook_path,
            "workbook_sha256": self.workbook_sha256,
            "content_sha256": self.content_sha256,
            "target_snapshot_id": self.anchor.target_snapshot.snapshot_id,
            "policy": "known_istd_batch_monotone_rt_map_same_target_fallback",
        }

    @property
    def explanation_provenance(self) -> dict[str, object]:
        """Project the batch map already used by grouping, without preparing it."""
        result: dict[str, object] = dict(self.provenance)
        prepared = self if self._batch_context is not None else self._prepared_batch
        if prepared is None or prepared._batch_context is None:
            return result
        samples, orders = prepared._batch_context
        knots_by_sample = prepared._knots_by_sample
        result["calibration"] = {
            "schema_version": "known_istd_calibration",
            "rt_unit": "minute",
            "interpolation": "piecewise_linear",
            "extrapolation": "unavailable",
            "sample_universe": list(samples),
            "injection_order": dict(orders),
            "measurements": [
                {"label": label, "sample_stem": sample, "observed_rt_min": rt}
                for label, sample, rt, _delta in prepared.coordinates
                if sample in samples
            ],
            "samples": [
                {
                    "sample_stem": sample,
                    "knots": [list(knot) for knot in knots_by_sample.get(sample, ())],
                    "domain_rt_min": (
                        [knots_by_sample[sample][0][0], knots_by_sample[sample][-1][0]]
                        if sample in knots_by_sample
                        else None
                    ),
                    "status": (
                        "available" if sample in knots_by_sample else "unavailable"
                    ),
                }
                for sample in samples
            ],
        }
        return result

    def prepare_batch(
        self,
        *,
        sample_universe: frozenset[str],
        injection_order: Mapping[str, int] | None,
    ) -> KnownIstdReference:
        """Prepare immutable RT knots once for this canonical batch context."""
        samples = tuple(sorted(sample_universe))
        orders = tuple(
            sorted(
                (sample, order)
                for sample, order in (injection_order or {}).items()
                if sample in sample_universe
            )
        )
        context = samples, orders
        if self._batch_context == context:
            return self
        if (
            self._prepared_batch is not None
            and self._prepared_batch._batch_context == context
        ):
            return self._prepared_batch
        measured: dict[str, dict[str, float]] = {}
        for label, sample, rt, _delta in self.coordinates:
            if sample in sample_universe:
                measured.setdefault(label, {})[sample] = rt
        centers = {
            label: statistics.median(values.values())
            for label, values in measured.items()
        }
        order_by_sample = dict(orders)
        if len(set(order_by_sample.values())) != len(order_by_sample):
            order_by_sample = {}
        observed_by_order = {
            label: tuple(
                sorted(
                    (float(order_by_sample[sample]), rt)
                    for sample, rt in values.items()
                    if sample in order_by_sample
                )
            )
            for label, values in measured.items()
        }
        sample_knots = []
        for sample in samples:
            knots = []
            for label in sorted(centers, key=centers.__getitem__):
                observed = measured[label].get(sample)
                if observed is None and sample in order_by_sample:
                    observed = interpolate_calibrated_rt_min(
                        float(order_by_sample[sample]), observed_by_order[label]
                    )
                if observed is not None:
                    knots.append((observed, centers[label]))
            if len(knots) >= 2 and all(
                left[0] < right[0] and left[1] < right[1]
                for left, right in zip(knots, knots[1:])
            ):
                sample_knots.append((sample, tuple(knots)))
        prepared = replace(
            self, _batch_context=context, _batch_knots=tuple(sample_knots)
        )
        # Materialize the shared lookup before method/owner loops begin. The
        # single memo is only for unchanged input, not another lookup registry.
        _ = prepared._knots_by_sample
        object.__setattr__(self, "_prepared_batch", prepared)
        return prepared

    @cached_property
    def _knots_by_sample(self) -> dict[str, tuple[tuple[float, float], ...]]:
        return dict(self._batch_knots)

    def _corrected_rt_min(self, owner: SampleLocalMS1Owner) -> float | None:
        return interpolate_calibrated_rt_min(
            owner.owner_apex_rt, self._knots_by_sample.get(owner.sample_stem, ())
        )

    def bind(
        self,
        owners: tuple[SampleLocalMS1Owner, ...],
        *,
        sample_universe: frozenset[str] | None = None,
        injection_order: Mapping[str, int] | None = None,
    ) -> KnownIstdOwnerLookup:
        """Bind measured ISTD coordinates to every eligible physical owner."""
        reference = (
            self
            if sample_universe is None
            else self.prepare_batch(
                sample_universe=sample_universe, injection_order=injection_order
            )
        )
        targets = {target.label: target for target in self.anchor.targets}
        owners_by_sample: dict[str, list[SampleLocalMS1Owner]] = {}
        for owner in owners:
            owners_by_sample.setdefault(owner.sample_stem, []).append(owner)
        candidates: list[tuple[str, str, float]] = []
        counts: dict[str, int] = {}
        for label, sample, rt, delta in self.coordinates:
            target = targets[label]
            matches = tuple(
                owner
                for owner in owners_by_sample.get(sample, ())
                if owner.owner_peak_start_rt <= rt <= owner.owner_peak_end_rt
                and owner.primary_identity_event is not None
                and target.neutral_loss_da is not None
                and target.nl_ppm_max is not None
                and neutral_loss_match(
                    owner.primary_identity_event.precursor_mz,
                    owner.primary_identity_event.product_mz,
                    target.neutral_loss_da,
                    target.nl_ppm_max,
                    observed_loss_da=(
                        owner.primary_identity_event.observed_neutral_loss_da
                    ),
                )[2]
                # The measured coordinate above identifies the physical peak;
                # expected RT must not clip a shifted, already measured ISTD.
                and abs(owner.precursor_mz - target.mz)
                <= target.mz * target.ppm_tol / 1e6
            )
            for owner in matches:
                counts[owner.owner_id] = counts.get(owner.owner_id, 0) + 1
            if len(matches) == 1:
                candidates.append((matches[0].owner_id, label, delta))
        return KnownIstdOwnerLookup(
            reference, tuple(item for item in candidates if counts[item[0]] == 1)
        )


@dataclass(frozen=True)
class KnownIstdOwnerLookup:
    """Immutable full-population bindings reused by grouping and cell proof."""

    reference: KnownIstdReference
    bindings: tuple[tuple[str, str, float], ...]
    istd_identity_anchor: None = None

    @property
    def source(self) -> Literal["targeted_istd_trend"]:
        return "targeted_istd_trend"

    def sample_delta_min(self, sample_stem: str) -> None:
        return None

    def injection_order(self, sample_stem: str) -> None:
        return None

    @cached_property
    def _by_owner(self) -> dict[str, tuple[str, float]]:
        return {owner: (label, delta) for owner, label, delta in self.bindings}

    def corrected_pair_delta_sec(
        self,
        left: SampleLocalMS1Owner,
        right: SampleLocalMS1Owner,
    ) -> float | None:
        left_rt = self.reference._corrected_rt_min(left)
        right_rt = self.reference._corrected_rt_min(right)
        if left_rt is not None and right_rt is not None:
            return abs(left_rt - right_rt) * 60.0
        a, b = self._by_owner.get(left.owner_id), self._by_owner.get(right.owner_id)
        if a is None or b is None or a[0] != b[0]:
            return None
        return abs((left.owner_apex_rt - a[1]) - (right.owner_apex_rt - b[1])) * 60.0

    def has_direct_reference_pair(
        self,
        left: SampleLocalMS1Owner,
        right: SampleLocalMS1Owner,
    ) -> bool:
        """A measured same-target binding must survive a closer unbound peak."""
        a, b = self._by_owner.get(left.owner_id), self._by_owner.get(right.owner_id)
        return a is not None and b is not None and a[0] == b[0]


def interpolate_calibrated_rt_min(
    value: float, knots: Sequence[Sequence[float]]
) -> float | None:
    """Apply validated monotone owner knots; coordinates outside are unavailable."""
    if len(knots) < 2 or not knots[0][0] <= value <= knots[-1][0]:
        return None
    right = min(bisect_right(knots, value, key=lambda knot: knot[0]), len(knots) - 1)
    left_x, left_y = knots[right - 1]
    right_x, right_y = knots[right]
    return left_y + (value - left_x) / (right_x - left_x) * (right_y - left_y)


def mint_known_istd_reference_from_measurements(
    targets: Sequence[Target],
    coordinates: Mapping[tuple[str, str], float],
    *,
    source_identifier: str = "<in-memory>",
    source_sha256: str = "",
) -> KnownIstdReference:
    """Mint a KnownIstdReference from declared targets and measured apex RT.

    Directly populates the node map from coordinates without reading Excel.
    """
    istd_targets = tuple(t for t in targets if t.is_istd)
    if not istd_targets:
        raise ValueError("known ISTD reference requires at least one ISTD target")
    if len({t.label for t in istd_targets}) != len(istd_targets):
        raise ValueError("known ISTD reference requires unique ISTD target labels")

    snapshot = mint_target_list_snapshot(
        istd_targets, identity_namespace="known-istd-reference"
    )
    labels = {t.label for t in istd_targets}

    for (label, _sample), rt in coordinates.items():
        if label not in labels:
            raise ValueError(f"undeclared ISTD target {label!r}")
        if not math.isfinite(rt) or rt < 0:
            raise ValueError(f"reference RT must be nonnegative finite float: {rt!r}")

    centers = {
        label: statistics.median(
            rt for (target, _), rt in coordinates.items() if target == label
        )
        for label in labels
        if any(target == label for target, _ in coordinates)
    }
    coords = tuple(
        (label, sample, rt, rt - centers[label])
        for (label, sample), rt in sorted(coordinates.items())
        if label in centers
    )
    sha = (
        source_sha256
        if source_sha256
        else hashlib.sha256(source_identifier.encode("utf-8")).hexdigest()
    )
    return KnownIstdReference(
        workbook_path=source_identifier,
        workbook_sha256=sha,
        anchor=build_targeted_istd_identity_anchor(snapshot, observed_labels=labels),
        coordinates=coords,
    )


@dataclass(frozen=True)
class SampleDriftEvidence:
    sample_stem: str
    injection_order: int
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

    labels = tuple(
        sorted({str(label).strip() for label in observed_labels if str(label).strip()})
    )
    if not target_snapshot.definitions or not labels:
        raise ValueError("ISTD identity anchor requires targets and observed labels")
    return TargetedIstdIdentityAnchor(
        target_snapshot=target_snapshot,
        observed_labels=labels,
    )


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
            f"conflicting injection order for sample {sample_stem!r}: {list(orders)}"
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
