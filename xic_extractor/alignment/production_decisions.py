from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Literal, cast

from xic_extractor.alignment.cell_quality import (
    CellQualityDecision,
    build_cell_quality_decisions,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.matrix_identity import (
    MatrixIdentityRowDecision,
    build_matrix_identity_decisions,
)
from xic_extractor.alignment.output_rows import cells_by_cluster, row_id
from xic_extractor.alignment.promotion_policy import (
    BACKFILL_CELL_EVIDENCE_REQUIRED_FLAG,
    LOW_MS1_COVERAGE_BLOCKED_REASON,
    MISSING_BACKFILL_EVIDENCE_BLOCKED_REASON,
    NEIGHBOR_INTERFERENCE_BLOCKED_REASON,
    cell_evidence_from_aligned,
)
from xic_extractor.decision_policy import (
    DECISION_CLASS_RANK,
    DecisionRecord,
    DecisionTerm,
)
from xic_extractor.evidence_semantics import DecisionClass

ProductionStatus = Literal[
    "detected",
    "accepted_rescue",
    "review_rescue",
    "rejected_rescue",
    "blank",
]
RescueTier = Literal["", "accepted_rescue", "review_rescue", "rejected_rescue"]

ALIGNMENT_DECISION_CONFIG_SCHEMA_VERSION = "alignment_decision_config_projection_v1"
PRODUCTION_CELL_DECISION_SCHEMA_VERSION = "production_cell_decision_projection_v1"
ALIGNMENT_DECISION_CONFIG_ID_PREFIX = "alignment-decision-config.v1:"
PRODUCTION_CELL_DECISION_ID_PREFIX = "production-cell-decision.v1:"
OWNER_BACKFILL_AUTHORITY_CELL_ID_PREFIX = "owner-backfill-authority-cell.v1:"

ALIGNMENT_DECISION_CONFIG_FIELDS = (
    "preferred_ppm",
    "max_ppm",
    "preferred_rt_sec",
    "max_rt_sec",
    "product_mz_tolerance_ppm",
    "observed_loss_tolerance_ppm",
    "mz_bucket_neighbor_radius",
    "anchor_priorities",
    "anchor_min_evidence_score",
    "anchor_min_seed_events",
    "anchor_min_scan_support_score",
    "duplicate_fold_ppm",
    "duplicate_fold_rt_sec",
    "duplicate_fold_product_ppm",
    "duplicate_fold_observed_loss_ppm",
    "duplicate_fold_min_detected_overlap",
    "duplicate_fold_min_shared_detected_count",
    "duplicate_fold_min_detected_jaccard",
    "duplicate_fold_min_present_overlap",
    "owner_window_overlap_fraction",
    "owner_apex_close_sec",
    "owner_tail_seed_guard_sec",
    "owner_tail_max_secondary_ratio",
    "owner_backfill_min_detected_samples",
    "identity_rt_candidate_window_sec",
    "rt_unit",
    "fragmentation_model",
)


@dataclass(frozen=True, slots=True)
class AlignmentDecisionConfigProjectionV1:
    preferred_ppm: float
    max_ppm: float
    preferred_rt_sec: float
    max_rt_sec: float
    product_mz_tolerance_ppm: float
    observed_loss_tolerance_ppm: float
    mz_bucket_neighbor_radius: int
    anchor_priorities: tuple[str, ...]
    anchor_min_evidence_score: int
    anchor_min_seed_events: int
    anchor_min_scan_support_score: float
    duplicate_fold_ppm: float
    duplicate_fold_rt_sec: float
    duplicate_fold_product_ppm: float
    duplicate_fold_observed_loss_ppm: float
    duplicate_fold_min_detected_overlap: float
    duplicate_fold_min_shared_detected_count: int
    duplicate_fold_min_detected_jaccard: float
    duplicate_fold_min_present_overlap: float
    owner_window_overlap_fraction: float
    owner_apex_close_sec: float
    owner_tail_seed_guard_sec: float
    owner_tail_max_secondary_ratio: float
    owner_backfill_min_detected_samples: int
    identity_rt_candidate_window_sec: float
    rt_unit: str
    fragmentation_model: str

    @property
    def alignment_decision_config_id(self) -> str:
        return _typed_authority_id(
            ALIGNMENT_DECISION_CONFIG_ID_PREFIX,
            _ordered_authority_json_bytes(
                ALIGNMENT_DECISION_CONFIG_SCHEMA_VERSION,
                tuple(
                    (field, getattr(self, field))
                    for field in ALIGNMENT_DECISION_CONFIG_FIELDS
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class ProductionCellDecisionProjectionV1:
    owner_backfill_cell_content_id: str
    alignment_decision_config_id: str
    feature_family_id: str
    sample_stem: str
    raw_status: str
    production_status: ProductionStatus
    rescue_tier: RescueTier
    write_matrix_value: bool
    matrix_value: float | None
    blank_reason: str
    matrix_value_source: str

    @property
    def production_cell_decision_id(self) -> str:
        return _typed_authority_id(
            PRODUCTION_CELL_DECISION_ID_PREFIX,
            _ordered_authority_json_bytes(
                PRODUCTION_CELL_DECISION_SCHEMA_VERSION,
                (
                    (
                        "owner_backfill_cell_content_id",
                        self.owner_backfill_cell_content_id,
                    ),
                    ("alignment_decision_config_id", self.alignment_decision_config_id),
                    ("feature_family_id", self.feature_family_id),
                    ("sample_stem", self.sample_stem),
                    ("raw_status", self.raw_status),
                    ("production_status", self.production_status),
                    ("rescue_tier", self.rescue_tier),
                    ("write_matrix_value", self.write_matrix_value),
                    ("matrix_value", self.matrix_value),
                    ("blank_reason", self.blank_reason),
                    ("matrix_value_source", self.matrix_value_source),
                ),
            ),
        )


@dataclass(frozen=True)
class ProductionCellDecision:
    feature_family_id: str
    sample_stem: str
    raw_status: str
    production_status: ProductionStatus
    rescue_tier: RescueTier
    write_matrix_value: bool
    matrix_value: float | None
    blank_reason: str

    @property
    def decision_record(self) -> DecisionRecord:
        return production_cell_decision_record(self)


@dataclass(frozen=True)
class ProductionRowDecision:
    feature_family_id: str
    include_in_primary_matrix: bool
    identity_decision: str
    identity_confidence: str
    primary_evidence: str
    identity_reason: str
    quantifiable_detected_count: int
    quantifiable_rescue_count: int
    accepted_cell_count: int
    detected_count: int
    accepted_rescue_count: int
    review_rescue_count: int
    duplicate_assigned_count: int
    ambiguous_ms1_owner_count: int
    row_flags: tuple[str, ...]


@dataclass(frozen=True)
class ProductionDecisionSet:
    cells: dict[tuple[str, str], ProductionCellDecision]
    rows: dict[str, ProductionRowDecision]

    def cell(self, feature_family_id: str, sample_stem: str) -> ProductionCellDecision:
        return self.cells[(feature_family_id, sample_stem)]

    def row(self, feature_family_id: str) -> ProductionRowDecision:
        return self.rows[feature_family_id]


def alignment_decision_config_projection(
    config: AlignmentConfig,
) -> AlignmentDecisionConfigProjectionV1:
    if not isinstance(config, AlignmentConfig):
        raise TypeError("config must be an AlignmentConfig")
    return AlignmentDecisionConfigProjectionV1(
        **{field: getattr(config, field) for field in ALIGNMENT_DECISION_CONFIG_FIELDS}
    )


def production_cell_decision_projection(
    decision: ProductionCellDecision,
    *,
    cell: AlignedCell,
    config: AlignmentConfig,
    owner_backfill_cell_content_id: str,
) -> ProductionCellDecisionProjectionV1:
    """Mint the final cell-decision identity from live owner values.

    ``owner_backfill_cell_content_id`` is an explicit selected-cell binding.  This
    function validates it, but never discovers a selected work item by comparing
    numeric areas.
    """

    if not isinstance(decision, ProductionCellDecision):
        raise TypeError("decision must be a ProductionCellDecision")
    if not isinstance(cell, AlignedCell):
        raise TypeError("cell must be an AlignedCell")
    _require_typed_authority_id(
        owner_backfill_cell_content_id,
        prefix=OWNER_BACKFILL_AUTHORITY_CELL_ID_PREFIX,
        field="owner_backfill_cell_content_id",
    )
    if (decision.feature_family_id, decision.sample_stem, decision.raw_status) != (
        cell.cluster_id,
        cell.sample_stem,
        cell.status,
    ):
        raise ValueError("production decision crosses its live Matrix cell")
    matrix_value_source = cell.matrix_area_source if decision.write_matrix_value else ""
    if decision.write_matrix_value:
        expected_value = cell.matrix_area
        if expected_value is None or decision.matrix_value != expected_value:
            raise ValueError(
                "production decision value does not match its live Matrix cell"
            )
        if not matrix_value_source:
            raise ValueError("written Matrix value requires a live value source")
    elif decision.matrix_value is not None:
        raise ValueError(
            "non-writing production decision must not carry a Matrix value"
        )

    config_projection = alignment_decision_config_projection(config)
    return ProductionCellDecisionProjectionV1(
        owner_backfill_cell_content_id=owner_backfill_cell_content_id,
        alignment_decision_config_id=(config_projection.alignment_decision_config_id),
        feature_family_id=decision.feature_family_id,
        sample_stem=decision.sample_stem,
        raw_status=decision.raw_status,
        production_status=decision.production_status,
        rescue_tier=decision.rescue_tier,
        write_matrix_value=decision.write_matrix_value,
        matrix_value=decision.matrix_value,
        blank_reason=decision.blank_reason,
        matrix_value_source=matrix_value_source,
    )


def production_cell_decision_record(
    decision: ProductionCellDecision,
) -> DecisionRecord:
    decision_class = _production_cell_decision_class(decision)
    blockers = _production_cell_blockers(decision)
    gate: tuple[DecisionTerm, ...] = (
        ("decision_class_rank", float(DECISION_CLASS_RANK[decision_class])),
        (
            "matrix_value_projection_rank",
            0.0 if decision.write_matrix_value else 1.0,
        ),
        ("blocker_count", float(len(blockers))),
    )
    tie_break: tuple[DecisionTerm, ...] = (
        (
            "production_status_rank",
            float(_PRODUCTION_STATUS_RANK[decision.production_status]),
        ),
        ("rescue_tier_rank", float(_RESCUE_TIER_RANK[decision.rescue_tier])),
        (
            "matrix_value_present_rank",
            0.0 if decision.matrix_value is not None else 1.0,
        ),
    )
    return DecisionRecord(
        workflow="alignment_production_cell",
        unit_id=f"{decision.feature_family_id}:{decision.sample_stem}",
        required_evidence=(
            "production_cell_decision",
            "cell_quality_decision",
            "matrix_identity_row_decision",
        ),
        decision_class=decision_class,
        blockers=blockers,
        support=_production_cell_support(decision),
        gate=gate,
        tie_break=tie_break,
        projection_authority="build_production_decisions",
    )


def build_production_decisions(
    matrix: AlignmentMatrix,
    config: AlignmentConfig,
) -> ProductionDecisionSet:
    grouped_cells = cells_by_cluster(matrix)
    quality_decisions = build_cell_quality_decisions(matrix.cells, config)
    identity_decisions = build_matrix_identity_decisions(
        matrix,
        config,
        cell_quality=quality_decisions,
    )
    cell_decisions: dict[tuple[str, str], ProductionCellDecision] = {}
    row_decisions: dict[str, ProductionRowDecision] = {}

    for cluster in matrix.clusters:
        cluster_id = row_id(cluster)
        cluster_cells = grouped_cells.get(cluster_id, ())
        identity_row = identity_decisions.row(cluster_id)

        decisions: list[ProductionCellDecision] = []
        for cell in cluster_cells:
            decision = _cell_decision(
                cell,
                quality_decisions[(cell.cluster_id, cell.sample_stem)],
                identity_row=identity_row,
            )
            cell_decisions[(cell.cluster_id, cell.sample_stem)] = decision
            decisions.append(decision)
        row_decisions[cluster_id] = _row_decision(
            cluster_id,
            cluster_cells,
            tuple(decisions),
            identity_row=identity_row,
        )

    return ProductionDecisionSet(cells=cell_decisions, rows=row_decisions)


_PRODUCTION_STATUS_RANK: dict[ProductionStatus, int] = {
    "detected": 0,
    "accepted_rescue": 1,
    "review_rescue": 2,
    "rejected_rescue": 3,
    "blank": 4,
}
_RESCUE_TIER_RANK: dict[RescueTier, int] = {
    "": 0,
    "accepted_rescue": 1,
    "review_rescue": 2,
    "rejected_rescue": 3,
}


def _production_cell_decision_class(
    decision: ProductionCellDecision,
) -> DecisionClass:
    if decision.write_matrix_value:
        return "accepted"
    if decision.production_status == "review_rescue":
        return "review"
    if decision.blank_reason == "ambiguous_ms1_owner":
        return "ambiguous"
    if decision.production_status == "rejected_rescue":
        return "excluded"
    return "not_counted"


def _production_cell_blockers(
    decision: ProductionCellDecision,
) -> tuple[str, ...]:
    if decision.write_matrix_value:
        return ()
    return tuple(
        dict.fromkeys(
            part
            for part in (
                decision.blank_reason,
                "" if decision.blank_reason else "matrix_value_not_written",
            )
            if part
        )
    )


def _production_cell_support(
    decision: ProductionCellDecision,
) -> tuple[str, ...]:
    support = [
        f"raw_status:{decision.raw_status}",
        f"production_status:{decision.production_status}",
    ]
    if decision.rescue_tier:
        support.append(f"rescue_tier:{decision.rescue_tier}")
    if decision.write_matrix_value:
        support.append("write_matrix_value")
    if decision.matrix_value is not None:
        support.append("matrix_value_present")
    return tuple(dict.fromkeys(support))


def _cell_decision(
    cell: AlignedCell,
    quality: CellQualityDecision,
    *,
    identity_row: MatrixIdentityRowDecision,
) -> ProductionCellDecision:
    if quality.quality_status == "detected_quantifiable":
        if not identity_row.include_in_primary_matrix:
            return _blank(cell, "blank", "", "missing_row_identity_support")
        return ProductionCellDecision(
            feature_family_id=cell.cluster_id,
            sample_stem=cell.sample_stem,
            raw_status=cell.status,
            production_status="detected",
            rescue_tier="",
            write_matrix_value=True,
            matrix_value=quality.matrix_area,
            blank_reason="",
        )
    if quality.quality_status == "rescue_quantifiable":
        if not identity_row.include_in_primary_matrix:
            return _blank(
                cell,
                "review_rescue",
                "review_rescue",
                "missing_row_identity_support",
            )
        if BACKFILL_CELL_EVIDENCE_REQUIRED_FLAG in identity_row.row_flags:
            backfill_evidence = cell_evidence_from_aligned(cell, quality)
            if not backfill_evidence.supported_for_backfill:
                return _blank(
                    cell,
                    "review_rescue",
                    "review_rescue",
                    _backfill_cell_blank_reason(backfill_evidence),
                )
        return ProductionCellDecision(
            feature_family_id=cell.cluster_id,
            sample_stem=cell.sample_stem,
            raw_status=cell.status,
            production_status="accepted_rescue",
            rescue_tier="accepted_rescue",
            write_matrix_value=True,
            matrix_value=quality.matrix_area,
            blank_reason="",
        )
    if quality.quality_status == "review_rescue":
        return _blank(
            cell,
            "review_rescue",
            "review_rescue",
            quality.quality_reason,
        )
    if quality.quality_status == "duplicate_loser":
        return _blank(cell, "blank", "", "duplicate_loser")
    if quality.quality_status == "ambiguous_owner":
        return _blank(cell, "blank", "", "ambiguous_ms1_owner")
    if quality.quality_status == "blank" and cell.status == "absent":
        return _blank(cell, "blank", "", "absent")
    if quality.quality_status == "blank" and cell.status == "unchecked":
        return _blank(cell, "blank", "", "unchecked")
    if quality.quality_status == "invalid" and cell.status == "rescued":
        return _blank(
            cell,
            "rejected_rescue",
            "rejected_rescue",
            quality.quality_reason,
        )
    return _blank(cell, "blank", "", quality.quality_reason)


def _backfill_cell_blank_reason(backfill_evidence: object) -> str:
    if getattr(backfill_evidence, "high_neighbor_interference", False):
        return NEIGHBOR_INTERFERENCE_BLOCKED_REASON
    if getattr(backfill_evidence, "low_assessable_coverage", False):
        return LOW_MS1_COVERAGE_BLOCKED_REASON
    reason = str(getattr(backfill_evidence, "backfill_identity_block_reason", ""))
    return reason or MISSING_BACKFILL_EVIDENCE_BLOCKED_REASON


def _row_decision(
    cluster_id: str,
    cells: tuple[AlignedCell, ...],
    decisions: tuple[ProductionCellDecision, ...],
    *,
    identity_row: MatrixIdentityRowDecision,
) -> ProductionRowDecision:
    detected_count = sum(1 for cell in cells if cell.status == "detected")
    accepted_rescue_count = sum(
        1 for decision in decisions if decision.production_status == "accepted_rescue"
    )
    review_rescue_count = sum(
        1 for decision in decisions if decision.production_status == "review_rescue"
    )
    duplicate_count = identity_row.duplicate_assigned_count
    ambiguous_count = identity_row.ambiguous_ms1_owner_count
    accepted_cell_count = sum(
        1 for decision in decisions if decision.write_matrix_value
    )

    flags: list[str] = list(identity_row.row_flags)
    if accepted_cell_count == 0 and review_rescue_count > 0:
        flags.append("rescue_only_review")

    return ProductionRowDecision(
        feature_family_id=cluster_id,
        include_in_primary_matrix=(
            identity_row.include_in_primary_matrix and accepted_cell_count > 0
        ),
        identity_decision=identity_row.identity_decision,
        identity_confidence=identity_row.identity_confidence,
        primary_evidence=identity_row.primary_evidence,
        identity_reason=identity_row.identity_reason,
        quantifiable_detected_count=identity_row.quantifiable_detected_count,
        quantifiable_rescue_count=identity_row.quantifiable_rescue_count,
        accepted_cell_count=accepted_cell_count,
        detected_count=detected_count,
        accepted_rescue_count=accepted_rescue_count,
        review_rescue_count=review_rescue_count,
        duplicate_assigned_count=duplicate_count,
        ambiguous_ms1_owner_count=ambiguous_count,
        row_flags=tuple(dict.fromkeys(flags)),
    )


def _blank(
    cell: AlignedCell,
    production_status: ProductionStatus,
    rescue_tier: RescueTier,
    blank_reason: str,
) -> ProductionCellDecision:
    return ProductionCellDecision(
        feature_family_id=cell.cluster_id,
        sample_stem=cell.sample_stem,
        raw_status=cell.status,
        production_status=production_status,
        rescue_tier=rescue_tier,
        write_matrix_value=False,
        matrix_value=None,
        blank_reason=blank_reason,
    )


_LOWER_SHA256 = re.compile(r"[0-9a-f]{64}")


def _ordered_authority_json_bytes(
    schema_version: str,
    fields: tuple[tuple[str, object], ...],
) -> bytes:
    payload: dict[str, object] = {"schema_version": schema_version}
    for name, value in fields:
        payload[name] = _canonical_authority_value(value, field=name)
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_authority_value(value: object, *, field: str) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) is int:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field} must be finite")
        return _normalized_float_text(value)
    if isinstance(value, tuple):
        if any(not isinstance(item, str) for item in value):
            raise TypeError(f"{field} tuple must contain only strings")
        return list(cast(tuple[str, ...], value))
    raise TypeError(f"{field} has no canonical authority scalar codec")


def _normalized_float_text(value: float) -> str:
    return (0.0).hex() if value == 0.0 else value.hex()


def _typed_authority_id(prefix: str, payload: bytes) -> str:
    return f"{prefix}{hashlib.sha256(payload).hexdigest()}"


def _require_typed_authority_id(value: str, *, prefix: str, field: str) -> None:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ValueError(f"{field} must use the {prefix!r} prefix")
    if _LOWER_SHA256.fullmatch(value[len(prefix) :]) is None:
        raise ValueError(f"{field} must contain a lowercase SHA-256 digest")
