from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from xic_extractor.alignment.cell_region_audit import with_region_audit
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.matrix_handoff import integration_from_values
from xic_extractor.alignment.owner_area import median_owner_area, positive_finite
from xic_extractor.alignment.owner_group_delivery import (
    GroupProjection,
    OwnerGroupDeliveryFeature,
    OwnerGroupDeliveryFeatures,
    delivery_cell_projection_from_group,
    delivery_group_projection,
)
from xic_extractor.alignment.ownership_models import AmbiguousOwnerRecord

_BACKFILL_SUPERSEDES_LOCAL_AREA_RATIO = 3.0
_LOCAL_OWNER_LOW_FAMILY_FRACTION = 0.25
_BACKFILL_FAMILY_SUPPORT_FRACTION = 0.5


def build_owner_alignment_matrix(
    features: OwnerGroupDeliveryFeatures,
    *,
    sample_order: tuple[str, ...],
    ambiguous_by_sample: Mapping[str, tuple[AmbiguousOwnerRecord, ...]],
    rescued_cells: tuple[AlignedCell, ...],
) -> AlignmentMatrix:
    builder = OwnerAlignmentMatrixBuilder(
        features,
        sample_order=sample_order,
        ambiguous_by_sample=ambiguous_by_sample,
    )
    builder.consume_rescued_cells(rescued_cells)
    return builder.finish()


class OwnerAlignmentMatrixBuilder:
    """Private all-sample transaction that freezes into owner Matrix authority."""

    def __init__(
        self,
        features: OwnerGroupDeliveryFeatures,
        *,
        sample_order: tuple[str, ...],
        ambiguous_by_sample: Mapping[str, tuple[AmbiguousOwnerRecord, ...]],
    ) -> None:
        self._features = tuple(features)
        self._sample_order = sample_order
        self._feature_by_id = {
            feature.feature_family_id: feature for feature in self._features
        }
        self._feature_rank = {
            feature.feature_family_id: index
            for index, feature in enumerate(self._features)
        }
        self._sample_rank = {
            sample_stem: index for index, sample_stem in enumerate(sample_order)
        }
        if len(self._feature_by_id) != len(self._features):
            raise ValueError("owner Matrix features must have unique identities")
        if len(self._sample_rank) != len(sample_order):
            raise ValueError("owner Matrix sample_order must be unique")
        self._cells: list[AlignedCell] = []
        self._consumed_keys: set[tuple[str, str]] = set()
        for feature in self._features:
            owners_by_sample = {owner.sample_stem: owner for owner in feature.owners}
            group_projection = delivery_group_projection(feature)
            for sample_stem in sample_order:
                if feature.ambiguous_sample_stem == sample_stem:
                    cell = _feature_ambiguous_cell(
                        feature,
                        sample_stem,
                        group_projection=group_projection,
                    )
                elif (owner := owners_by_sample.get(sample_stem)) is not None:
                    cell = _detected_cell(
                        feature,
                        owner,
                        group_projection=group_projection,
                    )
                elif (records := ambiguous_by_sample.get(sample_stem, ())):
                    cell = _ambiguous_cell(
                        feature.feature_family_id,
                        sample_stem,
                        records,
                        group_projection=group_projection,
                    )
                else:
                    cell = _absent_cell(
                        feature.feature_family_id,
                        sample_stem,
                        group_projection=group_projection,
                    )
                self._cells.append(cell)

    def consume_rescued_cells(self, rescued_cells: tuple[AlignedCell, ...]) -> None:
        replacements: list[tuple[int, tuple[str, str], AlignedCell]] = []
        batch_keys: set[tuple[str, str]] = set()
        for rescued in rescued_cells:
            key = (rescued.cluster_id, rescued.sample_stem)
            if key in batch_keys or key in self._consumed_keys:
                raise ValueError("duplicate rescued owner Matrix cell")
            batch_keys.add(key)
            feature = self._feature_by_id.get(rescued.cluster_id)
            sample_index = self._sample_rank.get(rescued.sample_stem)
            if feature is None or sample_index is None:
                raise ValueError("rescued cell falls outside owner Matrix universe")
            feature_index = self._feature_rank[rescued.cluster_id]
            position = feature_index * len(self._sample_order) + sample_index
            current = self._cells[position]
            group_projection = delivery_group_projection(feature)
            if feature.ambiguous_sample_stem == rescued.sample_stem:
                replacement = current
            elif any(
                owner.sample_stem == rescued.sample_stem for owner in feature.owners
            ):
                replacement = _detected_or_confirmed_cell(
                    feature,
                    current,
                    rescued,
                    family_area=median_owner_area(feature),
                    group_projection=group_projection,
                )
            else:
                replacement = _rescued_cell(
                    rescued,
                    group_projection=group_projection,
                )
            replacements.append((position, key, replacement))
        for position, key, replacement in replacements:
            self._cells[position] = replacement
            self._consumed_keys.add(key)

    def finish(self) -> AlignmentMatrix:
        return AlignmentMatrix(
            clusters=self._features,
            cells=tuple(self._cells),
            sample_order=self._sample_order,
        )


def ambiguous_records_by_sample(
    records: tuple[AmbiguousOwnerRecord, ...],
) -> dict[str, tuple[AmbiguousOwnerRecord, ...]]:
    grouped: dict[str, list[AmbiguousOwnerRecord]] = {}
    for record in records:
        grouped.setdefault(record.sample_stem, []).append(record)
    return {sample: tuple(items) for sample, items in grouped.items()}


def _detected_cell(
    feature: OwnerGroupDeliveryFeature,
    owner,
    *,
    group_projection: GroupProjection,
) -> AlignedCell:
    event = owner.primary_identity_event
    cell = AlignedCell(
        sample_stem=owner.sample_stem,
        cluster_id=feature.feature_family_id,
        status="detected",
        area=owner.owner_area,
        apex_rt=owner.owner_apex_rt,
        height=owner.owner_height,
        peak_start_rt=owner.owner_peak_start_rt,
        peak_end_rt=owner.owner_peak_end_rt,
        rt_delta_sec=(owner.owner_apex_rt - feature.family_center_rt) * 60.0,
        trace_quality=owner.assignment_reason,
        scan_support_score=None,
        source_candidate_id=event.candidate_id,
        source_raw_file=None,
        reason="sample-local MS1 owner with original MS2 evidence",
        selected_integration=(
            getattr(owner, "selected_integration", None)
            or integration_from_values(
                area_raw_counts_seconds=owner.owner_area,
                rt_apex_min=owner.owner_apex_rt,
                raw_apex_rt_min=owner.owner_apex_rt,
                height_raw=owner.owner_height,
                height_smoothed=owner.owner_height,
                rt_left_min=owner.owner_peak_start_rt,
                rt_right_min=owner.owner_peak_end_rt,
                boundary_sources=("alignment_owner_scalar_legacy",),
            )
        ),
        **delivery_cell_projection_from_group(
            group_projection,
            gap_fill_state="observed_member",
            gap_fill_reason="local_owner_detected",
            missing_observation_state="observed",
        ),
    )
    return with_region_audit(cell, owner.region_audit)


def _detected_or_confirmed_cell(
    feature: OwnerGroupDeliveryFeature,
    detected: AlignedCell,
    rescued: AlignedCell | None,
    *,
    family_area: float | None,
    group_projection: GroupProjection,
) -> AlignedCell:
    if (
        rescued is not None
        and feature.confirm_local_owners_with_backfill
        and _rescued_supersedes_detected(detected, rescued, family_area=family_area)
    ):
        return replace(
            rescued,
            reason=(
                "owner-centered MS1 backfill superseded low local owner; "
                f"local_owner_area={detected.area}; "
                f"local_owner_rt={detected.apex_rt}; "
                f"source_reason={detected.reason}"
            ),
            **delivery_cell_projection_from_group(
                group_projection,
                gap_fill_state="gap_fill_rescued",
                gap_fill_reason="group_centered_query_detected",
                missing_observation_state="queried_and_detected",
            ),
        )
    return detected


def _rescued_cell(
    rescued: AlignedCell,
    *,
    group_projection: GroupProjection,
) -> AlignedCell:
    if rescued.status != "rescued":
        return replace(
            rescued,
            **delivery_cell_projection_from_group(
                group_projection,
                gap_fill_state="not_filled",
                gap_fill_reason=(
                    rescued.gap_fill_reason or "query_attempt_not_detected"
                ),
                missing_observation_state=(
                    rescued.missing_observation_state or "missing_unchecked"
                ),
            ),
        )
    return replace(
        rescued,
        **delivery_cell_projection_from_group(
            group_projection,
            gap_fill_state="gap_fill_rescued",
            gap_fill_reason="group_centered_query_detected",
            missing_observation_state="queried_and_detected",
        ),
    )


def _rescued_supersedes_detected(
    detected: AlignedCell,
    rescued: AlignedCell,
    *,
    family_area: float | None,
) -> bool:
    detected_area = positive_finite(detected.area)
    rescued_area = positive_finite(rescued.area)
    if detected_area is None or rescued_area is None:
        return False
    if rescued_area < detected_area * _BACKFILL_SUPERSEDES_LOCAL_AREA_RATIO:
        return False

    if family_area is None:
        return True
    return (
        detected_area <= family_area * _LOCAL_OWNER_LOW_FAMILY_FRACTION
        and rescued_area >= family_area * _BACKFILL_FAMILY_SUPPORT_FRACTION
    )


def _ambiguous_cell(
    feature_family_id: str,
    sample_stem: str,
    records: tuple[AmbiguousOwnerRecord, ...],
    *,
    group_projection: GroupProjection,
) -> AlignedCell:
    candidate_ids = sorted(
        candidate_id for record in records for candidate_id in record.candidate_ids
    )
    return AlignedCell(
        sample_stem=sample_stem,
        cluster_id=feature_family_id,
        status="ambiguous_ms1_owner",
        area=None,
        apex_rt=None,
        height=None,
        peak_start_rt=None,
        peak_end_rt=None,
        rt_delta_sec=None,
        trace_quality="ambiguous_ms1_owner",
        scan_support_score=None,
        source_candidate_id=None,
        source_raw_file=None,
        reason=(
            "checked local MS1 region is ambiguous"
            if not candidate_ids
            else f"checked local MS1 region is ambiguous: {';'.join(candidate_ids)}"
        ),
        **delivery_cell_projection_from_group(
            group_projection,
            gap_fill_state="not_filled",
            gap_fill_reason="not_requested_ambiguous_owner",
            missing_observation_state="ambiguous_observation",
        ),
    )


def _feature_ambiguous_cell(
    feature: OwnerGroupDeliveryFeature,
    sample_stem: str,
    *,
    group_projection: GroupProjection,
) -> AlignedCell:
    candidate_ids = ";".join(feature.ambiguous_candidate_ids)
    return AlignedCell(
        sample_stem=sample_stem,
        cluster_id=feature.feature_family_id,
        status="ambiguous_ms1_owner",
        area=None,
        apex_rt=None,
        height=None,
        peak_start_rt=None,
        peak_end_rt=None,
        rt_delta_sec=None,
        trace_quality="ambiguous_ms1_owner",
        scan_support_score=None,
        source_candidate_id=None,
        source_raw_file=None,
        reason=(
            "checked local MS1 region is ambiguous"
            if not candidate_ids
            else f"checked local MS1 region is ambiguous: {candidate_ids}"
        ),
        **delivery_cell_projection_from_group(
            group_projection,
            gap_fill_state="not_filled",
            gap_fill_reason="not_requested_ambiguous_owner",
            missing_observation_state="ambiguous_observation",
        ),
    )


def _absent_cell(
    feature_family_id: str,
    sample_stem: str,
    *,
    group_projection: GroupProjection,
) -> AlignedCell:
    return AlignedCell(
        sample_stem=sample_stem,
        cluster_id=feature_family_id,
        status="absent",
        area=None,
        apex_rt=None,
        height=None,
        peak_start_rt=None,
        peak_end_rt=None,
        rt_delta_sec=None,
        trace_quality="absent",
        scan_support_score=None,
        source_candidate_id=None,
        source_raw_file=None,
        reason="no local MS1 owner",
        **delivery_cell_projection_from_group(
            group_projection,
            gap_fill_state="not_filled",
            gap_fill_reason="not_requested_no_gap_fill",
            missing_observation_state="missing_not_observed",
        ),
    )
