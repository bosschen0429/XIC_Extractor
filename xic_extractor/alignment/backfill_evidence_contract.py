"""Owner models and claim vocabulary for persisted Backfill evidence."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from xic_extractor.alignment.evidence_claims import (
    EvidenceClaim,
    EvidenceClaimState,
    EvidenceQueryResult,
)
from xic_extractor.alignment.matrix import CellStatus
from xic_extractor.alignment.production_decisions import (
    BackfillTier,
    ProductionStatus,
)

ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION = (
    "alignment_backfill_cell_evidence"
)
ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS = (
    "schema_version",
    "feature_family_id",
    "group_hypothesis_id",
    "public_family_id",
    "sample_stem",
    "status",
    "production_cell_status",
    "backfill_tier",
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
    "backfill_evidence_reason",
    "source_candidate_id",
    "source_raw_file",
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
    "region_shadow_verdict",
    "reason",
)

SKIPPED_EVIDENCE_LEDGER_SCHEMA_VERSION = "skipped_evidence_ledger"
SKIPPED_EVIDENCE_LEDGER_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "family_center_mz",
    "family_center_rt",
    "rt_window_start",
    "rt_window_end",
    "pre_backfill_category",
    "skipped_stage",
    "skip_reason",
    "backfill_scope",
    "predicate_version",
    "raw_xic_requests_skipped",
    "would_emit_in_full_audit",
    "full_audit_available",
    "source_artifact",
)

PREDICATE_VERSION = "alignment-parity-scoped-backfill-scope"

_CELL_STATUSES = frozenset(
    {
        "detected",
        "backfilled",
        "absent",
        "unchecked",
        "ambiguous_ms1_owner",
        "duplicate_assigned",
    }
)
_PRODUCTION_STATUSES = frozenset(
    {"detected", "accepted_backfill", "review_backfill", "rejected_backfill", "blank"}
)
_BACKFILL_TIERS = frozenset(
    {"", "accepted_backfill", "review_backfill", "rejected_backfill"}
)
_SKIPPED_SCOPE_REASON = {
    "production-equivalent": (
        "single_detected_no_consolidation_candidate",
        "single_detected_no_consolidation_candidate",
    ),
    "selected-families": (
        "selected_family_excluded",
        "not_in_selected_family_allowlist",
    ),
}


@dataclass(frozen=True, slots=True)
class BackfillEvidenceOwnerBindings:
    known_group_ids: frozenset[str]
    known_sample_stems: frozenset[str]
    candidate_samples: Mapping[str, str]
    group_feature_family_ids: Mapping[str, str] = field(default_factory=dict)
    group_public_family_ids: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SkippedEvidenceOwnerBindings:
    known_feature_family_ids: frozenset[str]
    known_sample_stems: frozenset[str]
    expected_backfill_scope: str
    expected_predicate_version: str = PREDICATE_VERSION
    expected_source_artifact: str = "owner_backfill"


@dataclass(frozen=True, slots=True)
class BackfillEvidenceRecord:
    """Exact compact row; decision and full-cell authority remain unavailable."""

    schema_version: str
    feature_family_id: str
    group_hypothesis_id: str
    public_family_id: str
    sample_stem: str
    status: CellStatus
    production_cell_status: ProductionStatus
    backfill_tier: BackfillTier
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
    backfill_evidence_reason: str
    source_candidate_id: str
    source_raw_file: str
    neutral_loss_tag: str
    family_center_mz: float
    family_center_rt: float
    region_shadow_verdict: str
    reason: str

    def __post_init__(self) -> None:
        if self.schema_version != ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION:
            raise ValueError(
                "schema_version must be "
                f"{ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION!r}"
            )
        for field_name in (
            "feature_family_id",
            "sample_stem",
            "identity_decision",
            "neutral_loss_tag",
        ):
            _require_nonempty(field_name, getattr(self, field_name))
        if self.status not in _CELL_STATUSES:
            raise ValueError(f"unsupported status {self.status!r}")
        if self.production_cell_status not in _PRODUCTION_STATUSES:
            raise ValueError(
                "unsupported production_cell_status "
                f"{self.production_cell_status!r}"
            )
        if self.backfill_tier not in _BACKFILL_TIERS:
            raise ValueError(f"unsupported backfill_tier {self.backfill_tier!r}")
        expected_tier = (
            self.production_cell_status
            if self.production_cell_status
            in {"accepted_backfill", "review_backfill", "rejected_backfill"}
            else ""
        )
        if self.backfill_tier != expected_tier:
            raise ValueError(
                "backfill_tier must match the production_cell_status rescue tier"
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
        if (
            self.peak_start_rt is not None
            and self.apex_rt is not None
            and self.peak_end_rt is not None
            and not self.peak_start_rt <= self.apex_rt <= self.peak_end_rt
        ):
            raise ValueError("peak_start_rt, apex_rt, and peak_end_rt are inconsistent")

    @property
    def identity(self) -> tuple[str, str, str]:
        return (
            self.feature_family_id,
            self.group_hypothesis_id,
            self.sample_stem,
        )



@dataclass(frozen=True, slots=True)
class BackfillEvidenceCandidateAssignment:
    sample_stem: str
    source_candidate_id: str
    group_hypothesis_id: str


@dataclass(frozen=True, slots=True)
class BackfillEvidenceOwnerResult:
    record: BackfillEvidenceRecord
    group_assignment_binding: EvidenceClaimState
    public_family_binding: EvidenceClaimState
    candidate_assignment: BackfillEvidenceCandidateAssignment | None

    @property
    def cross_artifact_binding(self) -> EvidenceClaimState:
        if (
            self.group_assignment_binding is EvidenceClaimState.OWNER_REHYDRATED
            and self.public_family_binding is EvidenceClaimState.OWNER_REHYDRATED
        ):
            return EvidenceClaimState.OWNER_REHYDRATED
        return EvidenceClaimState.UNAVAILABLE


@dataclass(frozen=True, slots=True)
class SkippedEvidenceRecord:
    """Complete persisted form of the Backfill-scope producer record."""

    feature_family_id: str
    sample_stem: str
    family_center_mz: float
    family_center_rt: float
    rt_window_start: float
    rt_window_end: float
    pre_backfill_category: str
    skipped_stage: str
    skip_reason: str
    backfill_scope: str
    predicate_version: str
    raw_xic_requests_skipped: int
    would_emit_in_full_audit: bool
    full_audit_available: bool
    source_artifact: str

    def __post_init__(self) -> None:
        for field_name in (
            "feature_family_id",
            "sample_stem",
            "pre_backfill_category",
            "skipped_stage",
            "skip_reason",
            "backfill_scope",
            "predicate_version",
            "source_artifact",
        ):
            _require_nonempty(field_name, getattr(self, field_name))
        _require_finite_fields(
            self,
            (
                "family_center_mz",
                "family_center_rt",
                "rt_window_start",
                "rt_window_end",
            ),
        )
        if self.family_center_mz <= 0:
            raise ValueError("family_center_mz must be positive")
        if not self.rt_window_start <= self.family_center_rt <= self.rt_window_end:
            raise ValueError(
                "rt_window_start, family_center_rt, and rt_window_end are inconsistent"
            )
        expected_disposition = _SKIPPED_SCOPE_REASON.get(self.backfill_scope)
        if expected_disposition is None:
            raise ValueError(f"unsupported backfill_scope {self.backfill_scope!r}")
        if (
            self.pre_backfill_category,
            self.skip_reason,
        ) != expected_disposition:
            raise ValueError(
                "pre_backfill_category and skip_reason must match backfill_scope"
            )
        if self.skipped_stage != "owner_backfill":
            raise ValueError("skipped_stage must be 'owner_backfill'")
        if self.predicate_version != PREDICATE_VERSION:
            raise ValueError(f"predicate_version must be {PREDICATE_VERSION!r}")
        if self.raw_xic_requests_skipped <= 0:
            raise ValueError("raw_xic_requests_skipped must be positive")
        if not self.would_emit_in_full_audit:
            raise ValueError("would_emit_in_full_audit must be true")
        if not self.full_audit_available:
            raise ValueError("full_audit_available must be true")
        if self.source_artifact != "owner_backfill":
            raise ValueError("source_artifact must be 'owner_backfill'")

    @property
    def identity(self) -> tuple[str, str, str, str, str]:
        return (
            self.feature_family_id,
            self.sample_stem,
            self.skipped_stage,
            self.backfill_scope,
            self.predicate_version,
        )


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
    "PREDICATE_VERSION",
    "SKIPPED_EVIDENCE_LEDGER_COLUMNS",
    "SKIPPED_EVIDENCE_LEDGER_SCHEMA_VERSION",
    "BackfillEvidenceCandidateAssignment",
    "BackfillEvidenceOwnerBindings",
    "BackfillEvidenceOwnerResult",
    "BackfillEvidenceRecord",
    "EvidenceClaim",
    "EvidenceClaimState",
    "EvidenceQueryResult",
    "SkippedEvidenceOwnerBindings",
    "SkippedEvidenceRecord",
)
