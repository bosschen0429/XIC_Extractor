from __future__ import annotations

import csv
import hashlib
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from xic_extractor.alignment.machine_decision import project_machine_decision
from xic_extractor.alignment.promotion_policy import (
    BACKFILL_OWNER_REHYDRATION_UNAVAILABLE_REASON,
)

CandidateGateStatus = Literal[
    "production_candidate",
    "keep_provisional",
    "audit",
    "excluded",
]
CandidateRecommendedAction = Literal[
    "track_candidate",
    "keep_provisional",
    "review",
    "exclude",
]
CandidateConfidence = Literal["medium", "review", "none"]

PRODUCTION_CANDIDATE_GATE_COLUMNS = (
    "feature_family_id",
    "matrix_role",
    "candidate_gate_status",
    "recommended_action",
    "evidence_tier",
    "support_components",
    "dependent_context",
    "challenge_blockers",
    "tier2_evidence_available",
    "candidate_confidence",
    "source_review_artifact",
    "source_review_sha256",
    "source_cell_artifact",
    "source_cell_sha256",
    "source_matrix_artifact",
    "source_matrix_sha256",
)

MIN_SCAN_SUPPORT_SCORE = 0.5
LOW_SCAN_SUPPORT_MAX = 0.2
MAX_ABS_RT_DELTA_SEC = 180.0
MAX_BACKFILLED_APEX_RT_SPAN_MIN = 0.35

_PROVISIONAL_DECISION = "provisional_discovery"
_REQUIRED_FLAGS = frozenset({"single_detected_seed", "provisional_retention_candidate"})
_STRUCTURAL_EXCLUDE_FLAGS = frozenset(
    {"family_consolidation_loser", "duplicate_only", "rescue_only", "zero_present"}
)
TIER2_SUPPORT_COMPONENT = "validated_tier2_trace_evidence"
TIER2_CRITERIA = "tier2_trace_identity_rescued_coherence"
TIER2_DIAGNOSTIC_CRITERIA = "tier2_trace_identity_diagnostic"
TIER2_DIAGNOSTIC_ONLY_CRITERIA = frozenset({TIER2_DIAGNOSTIC_CRITERIA})
TIER2_ALLOWED_CRITERIA = frozenset(
    {TIER2_CRITERIA, TIER2_DIAGNOSTIC_CRITERIA}
)
TIER2_RECOGNIZED_PRODUCERS = frozenset(
    {"raw_trace_reread_tier2"}
)
TIER2_TRACE_EVIDENCE_COLUMNS = (
    "feature_family_id",
    "evidence_status",
    "support_component",
    "criteria_version",
    "producer_version",
    "raw_trace_reread_status",
    "seed_apex_rt",
    "tier2_apex_rt",
    "apex_delta_sec",
    "scan_support_score",
    "trace_scan_count",
    "boundary_start_rt",
    "boundary_end_rt",
    "boundary_width_sec",
    "neighbor_interference_ratio",
    "rescued_cell_count_checked",
    "rescued_cell_count_supported",
    "rescued_apex_rt_span_sec",
    "rescued_boundary_overlap_min",
    "coherence_status",
    "challenge_blockers",
    "dependent_context",
    "source_alignment_review_sha256",
    "source_alignment_cells_sha256",
    "source_raw_manifest_sha256",
    "source_candidate_subset_sha256",
    "source_candidate_subset_count",
    "source_expected_sample_count",
    "raw_reader_runtime",
    "python_executable",
    "dll_dir",
    "producer_command",
    "generated_at_utc",
    "scan_availability_score",
    "trace_apex_intensity",
    "trace_baseline_noise",
    "trace_signal_to_noise_proxy",
    "trace_apex_prominence_score",
    "scan_support_basis",
    "seed_rescued_boundary_overlap_min",
    "rescued_pairwise_boundary_overlap_min",
    "family_consensus_boundary_overlap_min",
    "seed_rescued_apex_span_sec",
    "rescued_only_apex_span_sec",
    "neighbor_interference_status",
)
TIER2_TRACE_EVIDENCE_REQUIRED_COLUMNS = TIER2_TRACE_EVIDENCE_COLUMNS
TIER2_GATE_JOIN_COLUMNS = (
    "feature_family_id",
    "evidence_status",
    "support_component",
    "criteria_version",
    "producer_version",
    "source_alignment_review_sha256",
    "source_alignment_cells_sha256",
    "source_raw_manifest_sha256",
    "source_candidate_subset_sha256",
    "source_candidate_subset_count",
)
TIER2_RAW_MANIFEST_REQUIRED_COLUMNS = (
    "sample_stem",
    "raw_file_path",
    "raw_file_size_bytes",
    "raw_file_mtime_utc",
    "raw_reader_runtime",
    "python_executable",
    "dll_dir",
)
TIER2_CANDIDATE_SUBSET_FIELDS = (
    "feature_family_id",
    "identity_decision",
    "quantifiable_detected_count",
    "quantifiable_backfill_count",
    "duplicate_assigned_count",
    "ambiguous_ms1_owner_count",
    "row_flags",
)
_INTERFERENCE_MARKERS = ("neighbor", "interference")
_LOW_COVERAGE_MARKERS = (
    "low_scan_support",
    "skipped_low_scan_support",
    "coverage",
    "unassessable",
)
_INTEGER_PATTERN = re.compile(r"^[+-]?\d+$")


@dataclass(frozen=True)
class GateSourceContext:
    review_path: Path
    review_sha256: str
    cell_path: Path
    cell_sha256: str
    matrix_path: Path
    matrix_sha256: str


@dataclass(frozen=True)
class ProductionCandidateGateDecision:
    feature_family_id: str
    matrix_role: str
    candidate_gate_status: CandidateGateStatus
    recommended_action: CandidateRecommendedAction
    evidence_tier: int
    support_components: tuple[str, ...]
    dependent_context: tuple[str, ...]
    challenge_blockers: tuple[str, ...]
    tier2_evidence_available: bool
    candidate_confidence: CandidateConfidence
    source_context: GateSourceContext


@dataclass(frozen=True)
class Tier2CandidateSubsetSignature:
    sha256: str
    count: int


@dataclass(frozen=True)
class Tier2TraceEvidence:
    feature_family_id: str
    evidence_status: str
    support_component: str
    criteria_version: str
    producer_version: str
    raw_trace_reread_status: str
    coherence_status: str
    challenge_blockers: tuple[str, ...]
    dependent_context: tuple[str, ...]
    provenance_blockers: tuple[str, ...]
    metric_blockers: tuple[str, ...]


def source_context_for_artifacts(
    *,
    review_path: Path,
    cell_path: Path,
    matrix_path: Path,
) -> GateSourceContext:
    return GateSourceContext(
        review_path=review_path,
        review_sha256=_sha256_file(review_path),
        cell_path=cell_path,
        cell_sha256=_sha256_file(cell_path),
        matrix_path=matrix_path,
        matrix_sha256=_sha256_file(matrix_path),
    )


def tier2_candidate_subset_signature(
    candidate_rows: Sequence[Mapping[str, object]],
) -> Tier2CandidateSubsetSignature:
    normalized_lines = ["\t".join(TIER2_CANDIDATE_SUBSET_FIELDS)]
    for row in sorted(
        (_string_row(item) for item in candidate_rows),
        key=lambda item: item.get("feature_family_id", ""),
    ):
        normalized_lines.append(
            "\t".join(row.get(field, "") for field in TIER2_CANDIDATE_SUBSET_FIELDS)
        )
    payload = ("\n".join(normalized_lines) + "\n").encode("utf-8")
    return Tier2CandidateSubsetSignature(
        sha256=hashlib.sha256(payload).hexdigest().upper(),
        count=len(candidate_rows),
    )


def load_tier2_trace_evidence(
    *,
    sidecar_path: Path,
    raw_manifest_path: Path,
    candidate_rows: Sequence[Mapping[str, object]],
    source_context: GateSourceContext,
) -> dict[str, Tier2TraceEvidence]:
    sidecar_rows = _read_tsv_tier2_sidecar(sidecar_path)
    _read_tsv_required(raw_manifest_path, TIER2_RAW_MANIFEST_REQUIRED_COLUMNS)
    raw_manifest_sha256 = _sha256_file(raw_manifest_path)
    subset = tier2_candidate_subset_signature(candidate_rows)
    evidence_by_family: dict[str, Tier2TraceEvidence] = {}
    for row in sidecar_rows:
        evidence = _tier2_evidence_from_row(
            row,
            source_context=source_context,
            raw_manifest_sha256=raw_manifest_sha256,
            candidate_subset=subset,
        )
        if evidence.feature_family_id in evidence_by_family:
            raise ValueError(
                "alignment_tier2_trace_evidence.tsv has duplicate "
                f"feature_family_id: {evidence.feature_family_id}"
            )
        evidence_by_family[evidence.feature_family_id] = evidence
    return evidence_by_family


def evaluate_production_candidate_gate(
    review_row: Mapping[str, object],
    cell_rows: Sequence[Mapping[str, object]],
    *,
    source_context: GateSourceContext,
    tier2_evidence: Tier2TraceEvidence | None = None,
) -> ProductionCandidateGateDecision:
    review = _string_row(review_row)
    cells = tuple(_string_row(row) for row in cell_rows)
    machine = project_machine_decision(review, cells)
    flags = _split_tokens(review.get("row_flags"))

    structural_blockers = _structural_blockers(review, flags)
    if structural_blockers:
        return _decision(
            review=review,
            machine_role=machine.matrix_role,
            status=(
                "excluded"
                if set(structural_blockers) & _STRUCTURAL_EXCLUDE_FLAGS
                else "audit"
            ),
            blockers=structural_blockers,
            evidence_tier=1,
            support=(),
            dependent=(),
            tier2_available=False,
            source_context=source_context,
        )

    if not _is_retention_candidate(review, flags):
        return _decision(
            review=review,
            machine_role=machine.matrix_role,
            status=_status_for_non_candidate(machine.matrix_role),
            blockers=("not_retention_candidate",),
            evidence_tier=1,
            support=(),
            dependent=(),
            tier2_available=False,
            source_context=source_context,
        )

    backfilled = tuple(row for row in cells if row.get("status") == "backfilled")
    support = _tier2_positive_support(
        tier2_evidence,
        review_feature_family_id=review.get("feature_family_id", ""),
    )
    tier2_available = bool(support)
    dependent = _ordered_unique(
        (
            *_dependent_context(review, backfilled),
            *_tier2_dependent_context(tier2_evidence),
        )
    )
    blockers = _ordered_unique(
        (
            *(
                blocker
                for blocker in machine.blockers
                if blocker == BACKFILL_OWNER_REHYDRATION_UNAVAILABLE_REASON
            ),
            *_challenge_blockers(backfilled),
            *_tier2_blockers(
                tier2_evidence,
                review_feature_family_id=review.get("feature_family_id", ""),
            ),
        )
    )
    if _int_value(review.get("quantifiable_backfill_count")) and not backfilled:
        blockers = _ordered_unique((*blockers, "missing_rescued_cell_evidence"))
    if not tier2_available and tier2_evidence is None:
        blockers = _ordered_unique((*blockers, "missing_positive_tier2_support"))

    status: CandidateGateStatus
    if tier2_evidence is not None and blockers:
        status = "audit"
    elif any(blocker != "missing_positive_tier2_support" for blocker in blockers):
        status = "audit"
    elif blockers:
        status = "keep_provisional"
    else:
        status = "production_candidate"

    return _decision(
        review=review,
        machine_role=machine.matrix_role,
        status=status,
        blockers=blockers,
        evidence_tier=2 if tier2_available else 1,
        support=support,
        dependent=dependent,
        tier2_available=tier2_available,
        source_context=source_context,
    )


def is_candidate_gate_scope(review_row: Mapping[str, object]) -> bool:
    return "provisional_retention_candidate" in _split_tokens(
        review_row.get("row_flags")
    )


def production_candidate_gate_as_row(
    decision: ProductionCandidateGateDecision,
) -> dict[str, str]:
    return {
        "feature_family_id": decision.feature_family_id,
        "matrix_role": decision.matrix_role,
        "candidate_gate_status": decision.candidate_gate_status,
        "recommended_action": decision.recommended_action,
        "evidence_tier": str(decision.evidence_tier),
        "support_components": ";".join(decision.support_components),
        "dependent_context": ";".join(decision.dependent_context),
        "challenge_blockers": ";".join(decision.challenge_blockers),
        "tier2_evidence_available": (
            "TRUE" if decision.tier2_evidence_available else "FALSE"
        ),
        "candidate_confidence": decision.candidate_confidence,
        "source_review_artifact": str(decision.source_context.review_path),
        "source_review_sha256": decision.source_context.review_sha256,
        "source_cell_artifact": str(decision.source_context.cell_path),
        "source_cell_sha256": decision.source_context.cell_sha256,
        "source_matrix_artifact": str(decision.source_context.matrix_path),
        "source_matrix_sha256": decision.source_context.matrix_sha256,
    }


def summarize_gate_decisions(
    decisions: Sequence[ProductionCandidateGateDecision],
) -> dict[str, object]:
    status_counts = Counter(decision.candidate_gate_status for decision in decisions)
    return {
        "schema_version": "production-candidate-gate",
        "readiness_label": "diagnostic_only",
        "row_count": len(decisions),
        "production_candidate_count": status_counts["production_candidate"],
        "keep_provisional_count": status_counts["keep_provisional"],
        "audit_count": status_counts["audit"],
        "excluded_count": status_counts["excluded"],
        "production_ready": False,
        "matrix_contract_changed": False,
    }


def _decision(
    *,
    review: Mapping[str, str],
    machine_role: str,
    status: CandidateGateStatus,
    blockers: Sequence[str],
    evidence_tier: int,
    support: Sequence[str],
    dependent: Sequence[str],
    tier2_available: bool,
    source_context: GateSourceContext,
) -> ProductionCandidateGateDecision:
    return ProductionCandidateGateDecision(
        feature_family_id=review.get("feature_family_id", ""),
        matrix_role=machine_role,
        candidate_gate_status=status,
        recommended_action=_recommended_action(status),
        evidence_tier=evidence_tier,
        support_components=_ordered_unique(support),
        dependent_context=_ordered_unique(dependent),
        challenge_blockers=_ordered_unique(blockers),
        tier2_evidence_available=tier2_available,
        candidate_confidence=_confidence(status),
        source_context=source_context,
    )


def _status_for_non_candidate(matrix_role: str) -> CandidateGateStatus:
    if matrix_role == "excluded":
        return "excluded"
    if matrix_role == "audit":
        return "audit"
    return "keep_provisional"


def _recommended_action(status: CandidateGateStatus) -> CandidateRecommendedAction:
    if status == "production_candidate":
        return "track_candidate"
    if status == "keep_provisional":
        return "keep_provisional"
    if status == "excluded":
        return "exclude"
    return "review"


def _confidence(status: CandidateGateStatus) -> CandidateConfidence:
    if status == "production_candidate":
        return "medium"
    if status in {"keep_provisional", "audit"}:
        return "review"
    return "none"


def _is_retention_candidate(review: Mapping[str, str], flags: frozenset[str]) -> bool:
    if review.get("identity_decision") != _PROVISIONAL_DECISION:
        return False
    if not _REQUIRED_FLAGS.issubset(flags):
        return False
    detected_count = _int_value(review.get("quantifiable_detected_count"))
    backfill_count = _int_value(review.get("quantifiable_backfill_count"))
    duplicate_count = _int_value(review.get("duplicate_assigned_count"))
    ambiguous_count = _int_value(review.get("ambiguous_ms1_owner_count"))
    if None in {detected_count, backfill_count, duplicate_count, ambiguous_count}:
        return False
    if detected_count != 1:
        return False
    if backfill_count is None or backfill_count <= 0:
        return False
    if duplicate_count != 0:
        return False
    return ambiguous_count == 0


def _structural_blockers(
    review: Mapping[str, str],
    flags: frozenset[str],
) -> tuple[str, ...]:
    blockers: list[str] = []
    if review.get("identity_reason") == "review_only" or "review_only" in flags:
        blockers.append("review_only")
    blockers.extend(flag for flag in sorted(flags & _STRUCTURAL_EXCLUDE_FLAGS))
    return tuple(blockers)


def _dependent_context(
    review: Mapping[str, str],
    backfilled: Sequence[Mapping[str, str]],
) -> tuple[str, ...]:
    context: list[str] = []
    if review.get("primary_evidence") == "owner_complete_link":
        context.append("owner_backfill_context")
    if backfilled:
        context.append("family_ms1_context")
    if backfilled and all(
        (_float(row.get("scan_support_score")) or 0.0) >= MIN_SCAN_SUPPORT_SCORE
        for row in backfilled
    ):
        context.append("rescued_cell_scan_support_distribution")
    if backfilled and all(_local_apex_consistent(row) for row in backfilled):
        context.append("selected_boundary_local_apex_consistency")
    apex_rts = [_float(row.get("apex_rt")) for row in backfilled]
    finite_apex_rts = [value for value in apex_rts if value is not None]
    if (
        len(finite_apex_rts) >= 2
        and max(finite_apex_rts) - min(finite_apex_rts)
        <= MAX_BACKFILLED_APEX_RT_SPAN_MIN
    ):
        context.append("rescued_cell_rt_coherence")
    return tuple(context)


def _tier2_evidence_from_row(
    row: Mapping[str, str],
    *,
    source_context: GateSourceContext,
    raw_manifest_sha256: str,
    candidate_subset: Tier2CandidateSubsetSignature,
) -> Tier2TraceEvidence:
    blockers: list[str] = []
    if (
        row.get("source_alignment_review_sha256") != source_context.review_sha256
        or row.get("source_alignment_cells_sha256") != source_context.cell_sha256
    ):
        blockers.append("source_hash_mismatch")
    if row.get("source_raw_manifest_sha256") != raw_manifest_sha256:
        blockers.append("raw_manifest_hash_mismatch")
    if (
        row.get("source_candidate_subset_sha256") != candidate_subset.sha256
        or _int_value(row.get("source_candidate_subset_count"))
        != candidate_subset.count
    ):
        blockers.append("candidate_subset_hash_mismatch")
    criteria_version = row.get("criteria_version", "")
    producer_version = row.get("producer_version", "")
    if criteria_version not in TIER2_ALLOWED_CRITERIA:
        blockers.append("criteria_version_not_allowlisted")
    if producer_version not in TIER2_RECOGNIZED_PRODUCERS:
        blockers.append("producer_version_not_recognized")
    source_expected_sample_count = _int_value(row.get("source_expected_sample_count"))
    if (
        source_expected_sample_count is None
        or source_expected_sample_count <= 0
        or not row.get("raw_reader_runtime", "").strip()
        or not row.get("python_executable", "").strip()
        or not row.get("dll_dir", "").strip()
        or not row.get("producer_command", "").strip()
        or not row.get("generated_at_utc", "").strip()
    ):
        blockers.append("missing_valid_tier2_provenance")
    return Tier2TraceEvidence(
        feature_family_id=row.get("feature_family_id", ""),
        evidence_status=row.get("evidence_status", ""),
        support_component=row.get("support_component", ""),
        criteria_version=criteria_version,
        producer_version=producer_version,
        raw_trace_reread_status=row.get("raw_trace_reread_status", ""),
        coherence_status=row.get("coherence_status", ""),
        challenge_blockers=tuple(sorted(_split_tokens(row.get("challenge_blockers")))),
        dependent_context=tuple(sorted(_split_tokens(row.get("dependent_context")))),
        provenance_blockers=tuple(blockers),
        metric_blockers=_tier2_metric_blockers(row),
    )


def _tier2_metric_blockers(row: Mapping[str, str]) -> tuple[str, ...]:
    if row.get("evidence_status") != "validated":
        return ()
    blockers: list[str] = []
    trace_scan_count = _int_value(row.get("trace_scan_count"))
    seed_apex_rt = _float(row.get("seed_apex_rt"))
    tier2_apex_rt = _float(row.get("tier2_apex_rt"))
    scan_support_score = _float(row.get("scan_support_score"))
    apex_delta_sec = _float(row.get("apex_delta_sec"))
    boundary_start_rt = _float(row.get("boundary_start_rt"))
    boundary_end_rt = _float(row.get("boundary_end_rt"))
    boundary_width_sec = _float(row.get("boundary_width_sec"))
    neighbor_value = row.get("neighbor_interference_ratio")
    neighbor_interference_ratio = (
        None if neighbor_value in (None, "") else _float(neighbor_value)
    )
    dependent_context = _split_tokens(row.get("dependent_context"))
    backfilled_checked = _int_value(row.get("rescued_cell_count_checked"))
    backfilled_supported = _int_value(row.get("rescued_cell_count_supported"))
    backfilled_apex_span = _float(row.get("rescued_apex_rt_span_sec"))
    backfilled_boundary_overlap_min = _float(row.get("rescued_boundary_overlap_min"))
    if any(
        value is None
        for value in (
            trace_scan_count,
            seed_apex_rt,
            tier2_apex_rt,
            scan_support_score,
            apex_delta_sec,
            boundary_start_rt,
            boundary_end_rt,
            boundary_width_sec,
            backfilled_checked,
            backfilled_supported,
            backfilled_apex_span,
            backfilled_boundary_overlap_min,
        )
    ):
        blockers.append("metric_unavailable")
        return tuple(blockers)
    assert trace_scan_count is not None
    assert scan_support_score is not None
    assert apex_delta_sec is not None
    assert boundary_width_sec is not None
    assert backfilled_checked is not None
    assert backfilled_supported is not None
    assert backfilled_apex_span is not None
    assert backfilled_boundary_overlap_min is not None
    if neighbor_value not in (None, "") and neighbor_interference_ratio is None:
        blockers.append("metric_unavailable")
        return tuple(blockers)
    if (
        neighbor_value in (None, "")
        and "neighbor_interference_not_assessed" not in dependent_context
    ):
        blockers.append("neighbor_interference_unassessed")
    if trace_scan_count < 5:
        blockers.append("metric_unavailable")
    if scan_support_score < 0.20:
        blockers.append("low_scan_support")
    elif scan_support_score < 0.50:
        blockers.append("weak_scan_support")
    if apex_delta_sec > 30.0:
        blockers.append("apex_delta_exceeds_threshold")
    if boundary_width_sec <= 0.0 or boundary_width_sec > 180.0:
        blockers.append("boundary_width_out_of_range")
    if (
        neighbor_interference_ratio is not None
        and neighbor_interference_ratio > 0.33
    ):
        blockers.append("neighbor_interference")
    if backfilled_checked < 1 or backfilled_supported < 1:
        blockers.append("rescued_cell_support_low")
    elif backfilled_supported / backfilled_checked < 0.50:
        blockers.append("rescued_cell_support_low")
    if backfilled_apex_span > 21.0:
        blockers.append("rescued_apex_span_wide")
    if backfilled_boundary_overlap_min < 0.50:
        blockers.append("rescued_boundary_overlap_low")
    return _ordered_unique(blockers)


def _tier2_positive_support(
    evidence: Tier2TraceEvidence | None,
    *,
    review_feature_family_id: str,
) -> tuple[str, ...]:
    if evidence is None:
        return ()
    if _tier2_blockers(evidence, review_feature_family_id=review_feature_family_id):
        return ()
    if evidence.evidence_status != "validated":
        return ()
    if evidence.support_component != TIER2_SUPPORT_COMPONENT:
        return ()
    if evidence.raw_trace_reread_status != "pass":
        return ()
    if evidence.coherence_status != "pass":
        return ()
    return (TIER2_SUPPORT_COMPONENT,)


def _tier2_blockers(
    evidence: Tier2TraceEvidence | None,
    *,
    review_feature_family_id: str,
) -> tuple[str, ...]:
    if evidence is None:
        return ()
    blockers = [
        *evidence.provenance_blockers,
        *evidence.metric_blockers,
        *evidence.challenge_blockers,
    ]
    if evidence.criteria_version in TIER2_DIAGNOSTIC_ONLY_CRITERIA:
        blockers.append("tier2_diagnostic_only")
    if evidence.evidence_status == "validated":
        if evidence.feature_family_id != review_feature_family_id:
            blockers.append("tier2_feature_family_id_mismatch")
        if evidence.support_component != TIER2_SUPPORT_COMPONENT:
            blockers.append("missing_positive_tier2_support")
        if evidence.raw_trace_reread_status != "pass":
            blockers.append("raw_trace_reread_not_pass")
        if evidence.coherence_status != "pass":
            blockers.append("rescued_coherence_not_pass")
    elif evidence.evidence_status in {"blocked", "not_supported", "inconclusive"}:
        if not blockers:
            blockers.append(f"tier2_{evidence.evidence_status}")
    else:
        blockers.append("tier2_evidence_status_unrecognized")
    return _ordered_unique(blockers)


def _tier2_dependent_context(
    evidence: Tier2TraceEvidence | None,
) -> tuple[str, ...]:
    return evidence.dependent_context if evidence is not None else ()


def _challenge_blockers(backfilled: Sequence[Mapping[str, str]]) -> tuple[str, ...]:
    blockers: list[str] = []
    if any(_has_marker(row, _INTERFERENCE_MARKERS) for row in backfilled):
        blockers.append("neighboring_interference_challenge")
    if any(_low_assessable_coverage(row) for row in backfilled):
        blockers.append("low_assessable_coverage_challenge")
    if any(not _local_apex_consistent(row) for row in backfilled):
        blockers.append("selected_boundary_local_apex_inconsistency")
    return tuple(blockers)


def _local_apex_consistent(row: Mapping[str, str]) -> bool:
    apex = _float(row.get("apex_rt"))
    start = _float(row.get("peak_start_rt"))
    end = _float(row.get("peak_end_rt"))
    rt_delta = _float(row.get("rt_delta_sec"))
    if apex is None or start is None or end is None or rt_delta is None:
        return False
    return start <= apex <= end and abs(rt_delta) <= MAX_ABS_RT_DELTA_SEC


def _low_assessable_coverage(row: Mapping[str, str]) -> bool:
    scan_support = _float(row.get("scan_support_score"))
    if scan_support is None:
        return True
    if scan_support <= LOW_SCAN_SUPPORT_MAX:
        return True
    return _has_marker(row, _LOW_COVERAGE_MARKERS)


def _has_marker(row: Mapping[str, str], markers: Sequence[str]) -> bool:
    text = str(row.get("reason", "")).lower()
    return any(marker in text for marker in markers)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _read_tsv_tier2_sidecar(path: Path) -> tuple[dict[str, str], ...]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fieldnames = tuple(reader.fieldnames or ())
        if not fieldnames:
            raise ValueError(f"{path}: missing required columns: feature_family_id")
        fieldname_set = set(fieldnames)
        base_missing = [
            column
            for column in TIER2_TRACE_EVIDENCE_COLUMNS
            if column not in fieldname_set
        ]
        if base_missing:
            raise ValueError(
                f"{path}: missing required columns: {', '.join(base_missing)}"
            )
        rows = tuple({key: value or "" for key, value in row.items()} for row in reader)
    return rows



def _read_tsv_required(
    path: Path,
    required_columns: Sequence[str],
) -> tuple[dict[str, str], ...]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fieldnames = tuple(reader.fieldnames or ())
        missing = [column for column in required_columns if column not in fieldnames]
        if missing:
            raise ValueError(f"{path}: missing required columns: {', '.join(missing)}")
        return tuple(dict(row) for row in reader)


def _string_row(row: Mapping[str, object]) -> dict[str, str]:
    return {
        str(key): "" if value is None else str(value)
        for key, value in row.items()
    }


def _split_tokens(value: object) -> frozenset[str]:
    return frozenset(
        part.strip() for part in str(value or "").split(";") if part.strip()
    )


def _ordered_unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _float(value: object) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip().lstrip("'"))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _int_value(value: object) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        return int(value)
    text = str(value).strip().lstrip("'")
    if not _INTEGER_PATTERN.fullmatch(text):
        return None
    try:
        return int(text)
    except ValueError:
        return None
