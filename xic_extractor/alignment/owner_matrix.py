from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from xic_extractor.alignment.cell_region_audit import with_region_audit
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.matrix_handoff import integration_from_values
from xic_extractor.alignment.owner_area import (
    median_owner_area,
    owner_quantification_eligible,
    positive_finite,
)
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
        self._owners_by_feature_id = {
            feature.feature_family_id: {
                owner.sample_stem: owner for owner in feature.owners
            }
            for feature in self._features
        }
        self._group_projection_by_feature_id = {
            feature.feature_family_id: delivery_group_projection(feature)
            for feature in self._features
        }
        self._sample_rank = {
            sample_stem: index for index, sample_stem in enumerate(sample_order)
        }
        if len(self._feature_by_id) != len(self._features):
            raise ValueError("owner Matrix features must have unique identities")
        if len(self._sample_rank) != len(sample_order):
            raise ValueError("owner Matrix sample_order must be unique")
        self._ambiguous_by_sample = {
            sample_stem: tuple(records)
            for sample_stem, records in ambiguous_by_sample.items()
        }
        self._cells_by_sample: dict[str, tuple[AlignedCell, ...]] = {}

    def consume_rescued_cells(self, rescued_cells: tuple[AlignedCell, ...]) -> None:
        batch_keys: set[tuple[str, str]] = set()
        rescued_by_sample: dict[str, list[AlignedCell]] = {}
        for rescued in rescued_cells:
            key = (rescued.cluster_id, rescued.sample_stem)
            if key in batch_keys:
                raise ValueError("duplicate rescued owner Matrix cell")
            batch_keys.add(key)
            if (
                rescued.cluster_id not in self._feature_by_id
                or rescued.sample_stem not in self._sample_rank
            ):
                raise ValueError("rescued cell falls outside owner Matrix universe")
            rescued_by_sample.setdefault(rescued.sample_stem, []).append(rescued)
        for sample_stem, sample_cells in rescued_by_sample.items():
            self.consume_sample_rescued_cells(sample_stem, tuple(sample_cells))

    def consume_sample_rescued_cells(
        self,
        sample_stem: str,
        rescued_cells: tuple[AlignedCell, ...],
    ) -> None:
        if sample_stem not in self._sample_rank:
            raise ValueError("rescued sample falls outside owner Matrix universe")
        if sample_stem in self._cells_by_sample:
            raise ValueError("owner Matrix sample was consumed more than once")
        rescued_by_feature: dict[str, AlignedCell] = {}
        for rescued in rescued_cells:
            if rescued.sample_stem != sample_stem:
                raise ValueError("rescued cells cross owner Matrix sample identity")
            if rescued.cluster_id not in self._feature_by_id:
                raise ValueError("rescued cell falls outside owner Matrix universe")
            if rescued.cluster_id in rescued_by_feature:
                raise ValueError("duplicate rescued owner Matrix cell")
            rescued_by_feature[rescued.cluster_id] = rescued

        sample_cells: list[AlignedCell] = []
        for feature in self._features:
            rescued_cell = rescued_by_feature.get(feature.feature_family_id)
            group_projection = self._group_projection_by_feature_id[
                feature.feature_family_id
            ]
            owner = self._owners_by_feature_id[feature.feature_family_id].get(
                sample_stem
            )
            if feature.ambiguous_sample_stem == sample_stem:
                replacement = _feature_ambiguous_cell(
                    feature,
                    sample_stem,
                    group_projection=group_projection,
                )
            elif owner is not None:
                detected = _detected_cell(
                    feature,
                    owner,
                    group_projection=group_projection,
                )
                replacement = _detected_or_confirmed_cell(
                    feature,
                    detected,
                    rescued_cell,
                    family_area=(
                        median_owner_area(feature)
                        if rescued_cell is not None
                        and feature.confirm_local_owners_with_backfill
                        else None
                    ),
                    group_projection=group_projection,
                )
            elif rescued_cell is not None:
                replacement = _rescued_cell(
                    rescued_cell,
                    group_projection=group_projection,
                )
            elif records := self._ambiguous_by_sample.get(sample_stem, ()):
                replacement = _ambiguous_cell(
                    feature.feature_family_id,
                    sample_stem,
                    records,
                    group_projection=group_projection,
                )
            else:
                replacement = _absent_cell(
                    feature.feature_family_id,
                    sample_stem,
                    group_projection=group_projection,
                )
            sample_cells.append(replacement)
        self._cells_by_sample[sample_stem] = tuple(sample_cells)

    def finish(self) -> AlignmentMatrix:
        for sample_stem in self._sample_order:
            if sample_stem not in self._cells_by_sample:
                self.consume_sample_rescued_cells(sample_stem, ())
        return AlignmentMatrix(
            clusters=self._features,
            cells=tuple(
                self._cells_by_sample[sample_stem][feature_index]
                for feature_index in range(len(self._features))
                for sample_stem in self._sample_order
            ),
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
    if not owner_quantification_eligible(owner):
        exclusion_reason = (
            getattr(owner, "quantification_exclusion_reason", "")
            or "owner_quantification_excluded"
        )
        return AlignedCell(
            sample_stem=owner.sample_stem,
            cluster_id=feature.feature_family_id,
            status="unchecked",
            area=None,
            apex_rt=owner.owner_apex_rt,
            height=None,
            peak_start_rt=owner.owner_peak_start_rt,
            peak_end_rt=owner.owner_peak_end_rt,
            rt_delta_sec=(owner.owner_apex_rt - feature.family_center_rt) * 60.0,
            trace_quality=exclusion_reason,
            scan_support_score=None,
            source_candidate_id=event.candidate_id,
            source_raw_file=None,
            reason=f"local owner excluded from quantification: {exclusion_reason}",
            selected_integration=None,
            **delivery_cell_projection_from_group(
                group_projection,
                gap_fill_state="not_filled",
                gap_fill_reason="owner_quantification_excluded",
                missing_observation_state="missing_unchecked",
            ),
        )
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
    rescued_exclusion_reason = (
        _rescued_quantification_exclusion_reason(rescued)
        if rescued is not None and rescued.status == "rescued"
        else None
    )
    if (
        rescued is not None
        and feature.confirm_local_owners_with_backfill
        and detected.status == "unchecked"
        and rescued.status == "rescued"
        and rescued_exclusion_reason is None
    ):
        return replace(
            rescued,
            reason=(
                "owner-centered MS1 backfill replaced a local owner excluded "
                f"from quantification; source_reason={detected.reason}"
            ),
            **delivery_cell_projection_from_group(
                group_projection,
                gap_fill_state="gap_fill_rescued",
                gap_fill_reason="group_centered_query_detected",
                missing_observation_state="queried_and_detected",
            ),
        )
    if (
        rescued is not None
        and feature.confirm_local_owners_with_backfill
        and rescued_exclusion_reason is None
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
    exclusion_reason = _rescued_quantification_exclusion_reason(rescued)
    if exclusion_reason is not None:
        return _censored_rescued_cell(
            rescued,
            exclusion_reason=exclusion_reason,
            group_projection=group_projection,
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


def _rescued_quantification_exclusion_reason(
    rescued: AlignedCell,
) -> str | None:
    if not _strictly_interior(
        rescued.apex_rt,
        rescued.peak_start_rt,
        rescued.peak_end_rt,
    ):
        return "owner_apex_on_integration_boundary"
    integration = rescued.selected_integration
    if integration is None:
        return "owner_backfill_missing_selected_integration"
    if not _strictly_interior(
        integration.rt_apex_min,
        integration.rt_left_min,
        integration.rt_right_min,
    ):
        return "owner_apex_on_integration_boundary"
    if positive_finite(rescued.area) is None:
        return "owner_backfill_invalid_area"
    if positive_finite(rescued.matrix_area) is None:
        return "owner_backfill_invalid_matrix_area"
    return None


def _strictly_interior(
    apex: float | None,
    left: float | None,
    right: float | None,
) -> bool:
    return (
        apex is not None
        and left is not None
        and right is not None
        and left < apex < right
    )


def _censored_rescued_cell(
    rescued: AlignedCell,
    *,
    exclusion_reason: str,
    group_projection: GroupProjection,
) -> AlignedCell:
    return replace(
        rescued,
        status="unchecked",
        area=None,
        height=None,
        trace_quality=exclusion_reason,
        selected_integration=None,
        reason=(
            "owner backfill excluded from quantification: "
            f"{exclusion_reason}"
        ),
        **delivery_cell_projection_from_group(
            group_projection,
            gap_fill_state="not_filled",
            gap_fill_reason="owner_quantification_excluded",
            missing_observation_state="missing_unchecked",
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
