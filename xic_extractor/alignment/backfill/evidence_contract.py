"""Owner models and claim vocabulary for persisted Backfill evidence."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from xic_extractor.alignment.evidence_claims import (
    EvidenceClaim,
    EvidenceClaimState,
    EvidenceQueryResult,
)
from xic_extractor.alignment.matrix import CellStatus
from xic_extractor.alignment.production_decisions import ProductionStatus

ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION = (
    "alignment_backfill_cell_query_evidence"
)
ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS = (
    "schema_version",
    "feature_id",
    "sample_stem",
    "status",
    "production_cell_status",
    "write_matrix_value",
    "include_in_primary_matrix",
    "identity_decision",
    "row_flags",
    "area",
    "primary_matrix_area",
    "primary_matrix_area_source",
    "primary_matrix_area_reason",
    "apex_rt",
    "height",
    "peak_start_rt",
    "peak_end_rt",
    "rt_delta_sec",
    "trace_quality",
    "scan_support_score",
    "gap_fill_state",
    "gap_fill_reason",
    "source_candidate_id",
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
    "reason",
    "backfill_query_rt_min",
    "backfill_query_rt_max",
)

_CELL_STATUSES = frozenset(
    {
        "detected",
        "backfilled",
        "absent",
        "unchecked",
        "ambiguous_ms1_owner",
    }
)
_PRODUCTION_STATUSES = frozenset({"detected", "accepted_backfill", "blank"})


@dataclass(frozen=True, slots=True)
class BackfillEvidenceOwnerBindings:
    known_feature_ids: frozenset[str]
    known_sample_stems: frozenset[str]
    candidate_samples: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class BackfillEvidenceRecord:
    """Exact compact row; decision and full-cell authority remain unavailable."""

    schema_version: str
    feature_id: str
    sample_stem: str
    status: CellStatus
    production_cell_status: ProductionStatus
    write_matrix_value: bool
    include_in_primary_matrix: bool
    identity_decision: str
    row_flags: tuple[str, ...]
    area: float | None
    primary_matrix_area: float | None
    primary_matrix_area_source: str
    primary_matrix_area_reason: str
    apex_rt: float | None
    height: float | None
    peak_start_rt: float | None
    peak_end_rt: float | None
    rt_delta_sec: float | None
    trace_quality: str
    scan_support_score: float | None
    gap_fill_state: str
    gap_fill_reason: str
    source_candidate_id: str
    neutral_loss_tag: str
    family_center_mz: float
    family_center_rt: float
    reason: str
    backfill_query_rt_min: float | None = None
    backfill_query_rt_max: float | None = None

    def __post_init__(self) -> None:
        if self.schema_version != ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION:
            raise ValueError(
                "schema_version must be "
                f"{ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION!r}"
            )
        for field_name in (
            "feature_id",
            "sample_stem",
            "identity_decision",
            "neutral_loss_tag",
        ):
            _require_nonempty(field_name, getattr(self, field_name))
        if self.status not in _CELL_STATUSES:
            raise ValueError(f"unsupported status {self.status!r}")
        if self.production_cell_status not in _PRODUCTION_STATUSES:
            raise ValueError(
                f"unsupported production_cell_status {self.production_cell_status!r}"
            )
        expected_write = self.production_cell_status in {
            "detected",
            "accepted_backfill",
        }
        if self.write_matrix_value is not expected_write:
            raise ValueError(
                "write_matrix_value must be true only for detected or "
                "accepted_backfill production status"
            )
        _require_finite_fields(
            self,
            (
                "area",
                "primary_matrix_area",
                "apex_rt",
                "height",
                "peak_start_rt",
                "peak_end_rt",
                "rt_delta_sec",
                "backfill_query_rt_min",
                "backfill_query_rt_max",
                "scan_support_score",
                "family_center_mz",
                "family_center_rt",
            ),
        )
        if self.write_matrix_value and (
            self.primary_matrix_area is None or self.primary_matrix_area <= 0
        ):
            raise ValueError(
                "primary_matrix_area must be positive when write_matrix_value is true"
            )
        for field_name in (
            "area",
            "primary_matrix_area",
            "apex_rt",
            "height",
            "peak_start_rt",
            "peak_end_rt",
            "scan_support_score",
        ):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must be non-negative when present")
        if self.family_center_mz <= 0:
            raise ValueError("family_center_mz must be positive")
        if self.family_center_rt < 0:
            raise ValueError("family_center_rt must be non-negative")
        if (self.backfill_query_rt_min is None) != (self.backfill_query_rt_max is None):
            raise ValueError("Backfill query bounds must both be present or absent")
        if self.backfill_query_rt_min is not None and (
            self.backfill_query_rt_max is None
            or not 0 <= self.backfill_query_rt_min < self.backfill_query_rt_max
        ):
            raise ValueError("Backfill query bounds must be non-negative and ordered")
        if (
            self.peak_start_rt is not None
            and self.apex_rt is not None
            and self.peak_end_rt is not None
            and not self.peak_start_rt <= self.apex_rt <= self.peak_end_rt
        ):
            raise ValueError("peak_start_rt, apex_rt, and peak_end_rt are inconsistent")

    @property
    def identity(self) -> tuple[str, str]:
        return (
            self.feature_id,
            self.sample_stem,
        )


@dataclass(frozen=True, slots=True)
class BackfillEvidenceCandidateAssignment:
    sample_stem: str
    source_candidate_id: str
    feature_id: str


@dataclass(frozen=True, slots=True)
class BackfillEvidenceOwnerResult:
    record: BackfillEvidenceRecord
    group_assignment_binding: EvidenceClaimState
    candidate_assignment: BackfillEvidenceCandidateAssignment | None

    @property
    def cross_artifact_binding(self) -> EvidenceClaimState:
        if self.group_assignment_binding is EvidenceClaimState.OWNER_REHYDRATED:
            return EvidenceClaimState.OWNER_REHYDRATED
        return EvidenceClaimState.UNAVAILABLE


def _require_nonempty(field_name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")


def _require_finite_fields(value: object, field_names: tuple[str, ...]) -> None:
    for field_name in field_names:
        field_value = getattr(value, field_name)
        if field_value is not None and not math.isfinite(field_value):
            raise ValueError(f"{field_name} must be finite")


__all__ = (
    "ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS",
    "ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION",
    "BackfillEvidenceCandidateAssignment",
    "BackfillEvidenceOwnerBindings",
    "BackfillEvidenceOwnerResult",
    "BackfillEvidenceRecord",
    "EvidenceClaim",
    "EvidenceClaimState",
    "EvidenceQueryResult",
)
