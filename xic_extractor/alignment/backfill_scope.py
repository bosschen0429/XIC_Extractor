from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from xic_extractor.alignment.backfill_evidence_contract import (
    PREDICATE_VERSION,
    SKIPPED_EVIDENCE_LEDGER_COLUMNS,
    SkippedEvidenceRecord,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.family_compatibility import (
    family_center_mz,
    family_center_rt,
    loose_compatible_primary_family,
)
from xic_extractor.alignment.owner_area import positive_finite
from xic_extractor.alignment.owner_backfill_runtime_projection import (
    BackfillRuntimeFeature,
    backfill_runtime_owner_eligibility,
    median_backfill_runtime_owner_area,
)
from xic_extractor.alignment.owner_group_delivery import (
    OwnerGroupDeliveryFeature,
    OwnerGroupDeliveryFeatures,
)
from xic_extractor.tabular_io import write_tsv

BackfillScope = Literal["full-audit", "production-equivalent", "selected-families"]
BackfillScopeExclusionReason = Literal[
    "production_equivalent_excluded",
    "selected_family_excluded",
]
BackfillRequestPlanningReason = Literal[
    "planned",
    "review_only",
    "insufficient_detected_samples",
    "raw_source_unavailable",
    "retained_local_owner",
    "local_owner_not_supersedable",
]
REQUEST_PLAN_VERSION = "alignment-parity-scoped-owner-backfill-request-plan-v1"


@dataclass(frozen=True)
class BackfillScopeSelection:
    scope: BackfillScope
    features: OwnerGroupDeliveryFeatures
    skipped: tuple[SkippedEvidenceRecord, ...]
    selected_family_ids: frozenset[str]


def select_backfill_features(
    features: OwnerGroupDeliveryFeatures,
    *,
    sample_order: tuple[str, ...],
    raw_sample_stems: frozenset[str],
    alignment_config: AlignmentConfig,
    scope: BackfillScope,
    selected_family_ids: frozenset[str] = frozenset(),
) -> BackfillScopeSelection:
    if scope == "full-audit":
        return BackfillScopeSelection(
            scope=scope,
            features=features,
            skipped=(),
            selected_family_ids=frozenset(),
        )
    if scope == "selected-families" and not selected_family_ids:
        raise ValueError("selected-families backfill scope requires selected families")
    if scope not in {"production-equivalent", "selected-families"}:
        raise ValueError(
            "backfill scope must be full-audit, production-equivalent, "
            "or selected-families"
        )

    exclusion_reasons = backfill_scope_exclusion_reasons(
        features,
        alignment_config=alignment_config,
        scope=scope,
        selected_family_ids=selected_family_ids,
    )
    selected: list[OwnerGroupDeliveryFeature] = []
    skipped: list[SkippedEvidenceRecord] = []
    for feature in features:
        exclusion_reason = exclusion_reasons.get(id(feature))
        if exclusion_reason == "selected_family_excluded":
            skipped.extend(
                _skipped_records(
                    feature,
                    sample_order=sample_order,
                    raw_sample_stems=raw_sample_stems,
                    alignment_config=alignment_config,
                    scope=scope,
                    category="selected_family_excluded",
                    reason="not_in_selected_family_allowlist",
                )
            )
            continue
        if exclusion_reason == "production_equivalent_excluded":
            skipped.extend(
                _skipped_records(
                    feature,
                    sample_order=sample_order,
                    raw_sample_stems=raw_sample_stems,
                    alignment_config=alignment_config,
                    scope=scope,
                    category="single_detected_no_consolidation_candidate",
                    reason="single_detected_no_consolidation_candidate",
                )
            )
            continue
        selected.append(feature)

    return BackfillScopeSelection(
        scope=scope,
        features=tuple(selected),
        skipped=tuple(skipped),
        selected_family_ids=frozenset(selected_family_ids),
    )


def backfill_scope_exclusion_reasons(
    features: OwnerGroupDeliveryFeatures,
    *,
    alignment_config: AlignmentConfig,
    scope: BackfillScope,
    selected_family_ids: frozenset[str] = frozenset(),
) -> Mapping[int, BackfillScopeExclusionReason]:
    if scope == "full-audit":
        return {}
    if scope == "selected-families":
        if not selected_family_ids:
            raise ValueError(
                "selected-families backfill scope requires selected families"
            )
        return {
            id(feature): "selected_family_excluded"
            for feature in features
            if feature.feature_family_id not in selected_family_ids
        }
    if scope != "production-equivalent":
        raise ValueError(
            "backfill scope must be full-audit, production-equivalent, "
            "or selected-families"
        )
    compatible_neighbor_ids = _loose_compatible_neighbor_ids(
        features,
        alignment_config=alignment_config,
    )
    return {
        id(feature): "production_equivalent_excluded"
        for feature in features
        if _can_skip_for_production_equivalence(
            feature,
            compatible_neighbor_ids=compatible_neighbor_ids,
        )
    }


def backfill_request_sample_stems(
    feature: BackfillRuntimeFeature,
    *,
    sample_order: tuple[str, ...],
    raw_sample_stems: frozenset[str],
    alignment_config: AlignmentConfig,
) -> tuple[str, ...]:
    return tuple(
        sample_stem
        for sample_stem in sample_order
        if backfill_request_planning_reason(
            feature,
            sample_stem=sample_stem,
            raw_sample_stems=raw_sample_stems,
            alignment_config=alignment_config,
        )
        == "planned"
    )


def backfill_request_planning_reason(
    feature: BackfillRuntimeFeature,
    *,
    sample_stem: str,
    raw_sample_stems: frozenset[str],
    alignment_config: AlignmentConfig,
) -> BackfillRequestPlanningReason:
    if bool(getattr(feature, "review_only", False)):
        return "review_only"
    owner_eligibility = backfill_runtime_owner_eligibility(feature)
    detected_samples = {
        owner.sample_stem
        for owner in owner_eligibility
        if owner.quantification_eligible
    }
    if len(detected_samples) < alignment_config.owner_backfill_min_detected_samples:
        return "insufficient_detected_samples"
    if sample_stem not in raw_sample_stems:
        return "raw_source_unavailable"
    owners_by_sample: dict[str, list[object]] = defaultdict(list)
    for owner in owner_eligibility:
        owners_by_sample[owner.sample_stem].append(owner)

    family_area = (
        median_backfill_runtime_owner_area(feature)
        if bool(getattr(feature, "confirm_local_owners_with_backfill", False))
        else None
    )
    if (
        sample_stem in detected_samples
        and not feature.confirm_local_owners_with_backfill
    ):
        return "retained_local_owner"
    if sample_stem in detected_samples and not any_detected_owner_can_be_superseded(
            feature,
            owners_by_sample.get(sample_stem),
            family_area=family_area,
    ):
        return "local_owner_not_supersedable"
    return "planned"


def backfill_features_for_sample(
    features: OwnerGroupDeliveryFeatures,
    *,
    sample_stem: str,
    sample_order: tuple[str, ...],
    raw_sample_stems: frozenset[str],
    alignment_config: AlignmentConfig,
) -> OwnerGroupDeliveryFeatures:
    return tuple(
        feature
        for feature in features
        if sample_stem
        in backfill_request_sample_stems(
            feature,
            sample_order=sample_order,
            raw_sample_stems=raw_sample_stems,
            alignment_config=alignment_config,
        )
    )


def backfill_seed_centers(
    feature: BackfillRuntimeFeature,
) -> tuple[tuple[float, float], ...]:
    return feature.backfill_seed_centers or (
        (feature.family_center_mz, feature.family_center_rt),
    )


def any_detected_owner_can_be_superseded(
    feature: BackfillRuntimeFeature,
    owners: Sequence[object] | None,
    *,
    family_area: float | None = None,
) -> bool:
    return any(
        detected_owner_can_be_superseded(feature, owner, family_area=family_area)
        for owner in owners or ()
    )


def detected_owner_can_be_superseded(
    feature: BackfillRuntimeFeature,
    owner: object,
    *,
    family_area: float | None = None,
) -> bool:
    detected_area = positive_finite(getattr(owner, "owner_area", None))
    if detected_area is None:
        return False
    if family_area is None:
        family_area = median_backfill_runtime_owner_area(feature)
    if family_area is None:
        return False
    return detected_area <= family_area * 0.25


def _loose_compatible_neighbor_ids(
    features: OwnerGroupDeliveryFeatures,
    *,
    alignment_config: AlignmentConfig,
) -> frozenset[int]:
    by_tag: dict[str, list[OwnerGroupDeliveryFeature]] = defaultdict(list)
    for feature in features:
        if bool(getattr(feature, "review_only", False)):
            continue
        by_tag[str(feature.neutral_loss_tag)].append(feature)

    neighbor_ids: set[int] = set()
    rt_window_min = alignment_config.identity_rt_candidate_window_sec / 60.0
    for tag_features in by_tag.values():
        ordered = sorted(tag_features, key=family_center_rt)
        for index, feature in enumerate(ordered):
            feature_rt = family_center_rt(feature)
            for other in _rt_window_neighbors(
                ordered,
                index=index,
                center_rt=feature_rt,
                rt_window_min=rt_window_min,
            ):
                if loose_compatible_primary_family(
                    feature,
                    other,
                    alignment_config,
                ):
                    neighbor_ids.add(id(feature))
                    neighbor_ids.add(id(other))
                    break
    return frozenset(neighbor_ids)


def _rt_window_neighbors(
    features: Sequence[OwnerGroupDeliveryFeature],
    *,
    index: int,
    center_rt: float,
    rt_window_min: float,
) -> tuple[OwnerGroupDeliveryFeature, ...]:
    neighbors: list[OwnerGroupDeliveryFeature] = []
    for cursor in range(index - 1, -1, -1):
        other = features[cursor]
        if center_rt - family_center_rt(other) > rt_window_min:
            break
        neighbors.append(other)
    for cursor in range(index + 1, len(features)):
        other = features[cursor]
        if family_center_rt(other) - center_rt > rt_window_min:
            break
        neighbors.append(other)
    return tuple(neighbors)


def read_family_allowlist_tsv(
    path: Path,
    *,
    family_id_column: str,
) -> frozenset[str]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None or family_id_column not in reader.fieldnames:
            raise ValueError(f"{path}: missing required column {family_id_column!r}")
        family_ids = frozenset(
            str(row.get(family_id_column, "")).strip()
            for row in reader
            if str(row.get(family_id_column, "")).strip()
        )
    if not family_ids:
        raise ValueError(f"{path}: no selected family ids found")
    return family_ids


def write_skipped_evidence_ledger_tsv(
    path: Path,
    rows: Iterable[SkippedEvidenceRecord],
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(
        path,
        (asdict(row) for row in rows),
        SKIPPED_EVIDENCE_LEDGER_COLUMNS,
        extrasaction="raise",
        formatter=_format_skipped_evidence_ledger_value,
        lineterminator="\n",
    )
    return path


def _format_skipped_evidence_ledger_value(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def skipped_evidence_summary(rows: Sequence[SkippedEvidenceRecord]) -> dict[str, int]:
    return {
        "skipped_evidence_row_count": len(rows),
        "raw_xic_requests_skipped": sum(row.raw_xic_requests_skipped for row in rows),
    }


def _can_skip_for_production_equivalence(
    feature: OwnerGroupDeliveryFeature,
    *,
    compatible_neighbor_ids: frozenset[int],
) -> bool:
    if bool(getattr(feature, "review_only", False)):
        return True
    if bool(getattr(feature, "confirm_local_owners_with_backfill", False)):
        return False
    if len({owner.sample_stem for owner in feature.owners}) != 1:
        return False
    return id(feature) not in compatible_neighbor_ids


def _skipped_records(
    feature: OwnerGroupDeliveryFeature,
    *,
    sample_order: tuple[str, ...],
    raw_sample_stems: frozenset[str],
    alignment_config: AlignmentConfig,
    scope: BackfillScope,
    category: str,
    reason: str,
) -> tuple[SkippedEvidenceRecord, ...]:
    request_samples = backfill_request_sample_stems(
        feature,
        sample_order=sample_order,
        raw_sample_stems=raw_sample_stems,
        alignment_config=alignment_config,
    )
    if not request_samples:
        return ()
    seed_centers = backfill_seed_centers(feature)
    seed_rt_values = [seed_rt for _seed_mz, seed_rt in seed_centers]
    rt_window_min = alignment_config.max_rt_sec / 60.0
    rt_window_start = min(seed_rt_values) - rt_window_min
    rt_window_end = max(seed_rt_values) + rt_window_min
    return tuple(
        SkippedEvidenceRecord(
            feature_family_id=feature.feature_family_id,
            sample_stem=sample_stem,
            family_center_mz=family_center_mz(feature),
            family_center_rt=family_center_rt(feature),
            rt_window_start=rt_window_start,
            rt_window_end=rt_window_end,
            pre_backfill_category=category,
            skipped_stage="owner_backfill",
            skip_reason=reason,
            backfill_scope=scope,
            predicate_version=PREDICATE_VERSION,
            raw_xic_requests_skipped=len(seed_centers),
            would_emit_in_full_audit=True,
            full_audit_available=True,
            source_artifact="owner_backfill",
        )
        for sample_stem in request_samples
    )
