from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.diagnostics.diagnostic_io import format_diagnostic_value, write_tsv
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
)
from xic_extractor.extraction.targeted_projection_reasons import (
    OWN_MAX_SAME_PEAK_SUPPORT_REASON,
)
from xic_extractor.peak_detection.ms1_shape_identity import (
    DEFAULT_COMPETING_EXCLUSION_WINDOW_MIN,
    DEFAULT_COMPETING_MIN_RATIO,
    DEFAULT_LOCAL_GRID_SIZE,
    DEFAULT_LOCAL_HALF_WINDOW_MIN,
    DEFAULT_OWN_MAX_SMOOTH_POINTS,
    competing_peak_summary,
    local_own_max_shape_similarity,
)
from xic_extractor.tabular_io import render_delimited_rows

SCHEMA_VERSION = "targeted_ms1_shape_identity_v0"
VALIDATION_LABEL = "diagnostic_only"
DECISION_AUTHORITY = "diagnostic_only_no_product_write"
DEFAULT_MIN_OWN_MAX_SIMILARITY = 0.80
DEFAULT_STRONG_COMPETING_PEAK_RATIO = 0.65
ARTIFACT_OWNER_NAMESPACE = "xic_extractor.diagnostics.targeted_ms1_shape_identity"
ARTIFACT_SEMANTIC_ROLE = "targeted_ms1_shape_identity_evidence"
ARTIFACT_MEDIA_TYPE = "text/tab-separated-values; charset=utf-8"
ARTIFACT_SERIALIZATION_FORMAT = "targeted_ms1_shape_identity_tsv_v0"

TARGETED_MS1_SHAPE_IDENTITY_COLUMNS = (
    "schema_version",
    "validation_label",
    "decision_authority",
    "sample_name",
    "target_name",
    "target_role",
    "paired_istd",
    "source_row_id",
    "candidate_state",
    "reference_source",
    "candidate_rt_min",
    "reference_rt_min",
    "candidate_anchor_rt_delta_min",
    "paired_istd_rt_min",
    "candidate_pair_rt_delta_min",
    "target_window_status",
    "own_max_same_peak_status",
    "own_max_same_peak_supported",
    "own_max_same_peak_support_reason",
    "own_max_same_peak_similarity",
    "own_max_compared_point_count",
    "strongest_peak_rt_min",
    "strongest_peak_own_max_ratio",
    "strongest_competing_peak_rt_min",
    "strongest_competing_peak_own_max_ratio",
    "competing_peak_status",
    "reason",
)

_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace=ARTIFACT_OWNER_NAMESPACE,
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=SCHEMA_VERSION,
    validation_status=VALIDATION_LABEL,
    media_type=ARTIFACT_MEDIA_TYPE,
    serialization_format=ARTIFACT_SERIALIZATION_FORMAT,
)


@dataclass(frozen=True)
class TargetedMs1ShapeCandidate:
    sample_name: str
    target_name: str
    candidate_rt_min: float | None
    candidate_rt: Sequence[float | int | None]
    candidate_intensity: Sequence[float | int | None]
    reference_rt_min: float | None
    reference_rt: Sequence[float | int | None]
    reference_intensity: Sequence[float | int | None]
    target_role: str = "analyte"
    paired_istd: str = ""
    source_row_id: str = ""
    candidate_state: str = ""
    reference_source: str = ""
    paired_istd_rt_min: float | None = None
    target_window_start_min: float | None = None
    target_window_end_min: float | None = None


def build_targeted_ms1_shape_identity_rows(
    candidates: Sequence[TargetedMs1ShapeCandidate],
    *,
    min_own_max_similarity: float = DEFAULT_MIN_OWN_MAX_SIMILARITY,
    strong_competing_peak_ratio: float = DEFAULT_STRONG_COMPETING_PEAK_RATIO,
    half_window_min: float = DEFAULT_LOCAL_HALF_WINDOW_MIN,
    grid_size: int = DEFAULT_LOCAL_GRID_SIZE,
    smooth_points: int = DEFAULT_OWN_MAX_SMOOTH_POINTS,
    competing_exclusion_window_min: float = DEFAULT_COMPETING_EXCLUSION_WINDOW_MIN,
    competing_min_peak_ratio: float = DEFAULT_COMPETING_MIN_RATIO,
) -> tuple[dict[str, str], ...]:
    return tuple(
        _format_row(
            _identity_row(
                candidate,
                min_own_max_similarity=min_own_max_similarity,
                strong_competing_peak_ratio=strong_competing_peak_ratio,
                half_window_min=half_window_min,
                grid_size=grid_size,
                smooth_points=smooth_points,
                competing_exclusion_window_min=competing_exclusion_window_min,
                competing_min_peak_ratio=competing_min_peak_ratio,
            )
        )
        for candidate in candidates
    )


def write_targeted_ms1_shape_identity_tsv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    write_tsv(
        path,
        rows,
        TARGETED_MS1_SHAPE_IDENTITY_COLUMNS,
        lineterminator="\n",
    )


def canonical_targeted_ms1_shape_identity_bytes(
    rows: Sequence[Mapping[str, Any]],
) -> bytes:
    """Render the exact UTF-8 TSV bytes owned by this diagnostic contract."""

    canonical_rows = tuple(rows)
    for row_index, row in enumerate(canonical_rows, start=2):
        if not isinstance(row, Mapping):
            raise ValueError(f"artifact row {row_index} must be a mapping")
        if row.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"artifact row {row_index} schema_version must be {SCHEMA_VERSION!r}"
            )
        if row.get("validation_label") != VALIDATION_LABEL:
            raise ValueError(
                f"artifact row {row_index} validation_label must be "
                f"{VALIDATION_LABEL!r}"
            )
        if row.get("decision_authority") != DECISION_AUTHORITY:
            raise ValueError(
                f"artifact row {row_index} decision_authority must be "
                f"{DECISION_AUTHORITY!r}"
            )
    return render_delimited_rows(
        canonical_rows,
        TARGETED_MS1_SHAPE_IDENTITY_COLUMNS,
        delimiter="\t",
        lineterminator="\n",
    ).encode("utf-8")


def mint_targeted_ms1_shape_identity_artifact_ref(
    *,
    processing_attempt: object,
    rows: Sequence[Mapping[str, Any]],
    upstream_artifacts: Sequence[EvidenceArtifactRef] = (),
) -> EvidenceArtifactRef:
    """Mint this owner's fixed-role artifact reference without writing output."""

    from xic_extractor.processing_identity import ProcessingAttemptIdentity

    if not isinstance(processing_attempt, ProcessingAttemptIdentity):
        raise ValueError(
            "processing_attempt must be an owner-issued ProcessingAttemptIdentity"
        )
    return _mint_evidence_artifact_ref(
        producer_processing_request_id=processing_attempt.processing_request_id,
        producer_processing_attempt_id=processing_attempt.processing_attempt_id,
        semantic_contract=_ARTIFACT_SEMANTIC_CONTRACT,
        payload_bytes=canonical_targeted_ms1_shape_identity_bytes(rows),
        upstream_artifacts=upstream_artifacts,
    )


def _identity_row(
    candidate: TargetedMs1ShapeCandidate,
    *,
    min_own_max_similarity: float,
    strong_competing_peak_ratio: float,
    half_window_min: float,
    grid_size: int,
    smooth_points: int,
    competing_exclusion_window_min: float,
    competing_min_peak_ratio: float,
) -> dict[str, Any]:
    reasons: list[str] = [VALIDATION_LABEL]
    anchor_delta = _delta(candidate.candidate_rt_min, candidate.reference_rt_min)
    pair_delta = _delta(candidate.candidate_rt_min, candidate.paired_istd_rt_min)
    target_window_status = _target_window_status(candidate)
    reasons.append(target_window_status)

    if candidate.candidate_rt_min is None or candidate.reference_rt_min is None:
        similarity_status = "own_max_same_peak_inconclusive"
        similarity_supported = False
        similarity = None
        compared_count = 0
        reasons.append("missing_candidate_or_reference_rt")
    else:
        similarity_result = local_own_max_shape_similarity(
            candidate_rt_min=candidate.candidate_rt_min,
            candidate_rt=candidate.candidate_rt,
            candidate_intensity=candidate.candidate_intensity,
            reference_rt_min=candidate.reference_rt_min,
            reference_rt=candidate.reference_rt,
            reference_intensity=candidate.reference_intensity,
            half_window_min=half_window_min,
            grid_size=grid_size,
            smooth_points=smooth_points,
        )
        similarity = similarity_result.similarity
        compared_count = similarity_result.compared_point_count
        similarity_supported = (
            similarity_result.is_usable
            and similarity is not None
            and similarity >= min_own_max_similarity
        )
        similarity_status = _same_peak_status(
            similarity_result.status,
            supported=similarity_supported,
        )
        if similarity_result.status != "ok":
            reasons.append(similarity_result.reason)

    competing = competing_peak_summary(
        candidate.candidate_rt,
        candidate.candidate_intensity,
        candidate_rt_min=_candidate_rt_for_competing_summary(candidate),
        exclusion_window_min=competing_exclusion_window_min,
        min_peak_ratio=competing_min_peak_ratio,
        smooth_points=smooth_points,
    )
    competing_status = _competing_status(
        competing.strongest_competing_peak_own_max_ratio,
        status=competing.status,
        strong_competing_peak_ratio=strong_competing_peak_ratio,
    )
    if competing_status == "strong_competing_peak_observed_diagnostic":
        similarity_supported = False
        similarity_status = "own_max_same_peak_not_supported"
    reasons.append(similarity_status)
    reasons.append(competing_status)
    if competing.status != "ok":
        reasons.append(competing.reason)
    if pair_delta is not None:
        reasons.append("paired_istd_rt_delta_available")

    return {
        "schema_version": SCHEMA_VERSION,
        "validation_label": VALIDATION_LABEL,
        "decision_authority": DECISION_AUTHORITY,
        "sample_name": candidate.sample_name,
        "target_name": candidate.target_name,
        "target_role": candidate.target_role,
        "paired_istd": candidate.paired_istd,
        "source_row_id": candidate.source_row_id,
        "candidate_state": candidate.candidate_state,
        "reference_source": candidate.reference_source,
        "candidate_rt_min": candidate.candidate_rt_min,
        "reference_rt_min": candidate.reference_rt_min,
        "candidate_anchor_rt_delta_min": anchor_delta,
        "paired_istd_rt_min": candidate.paired_istd_rt_min,
        "candidate_pair_rt_delta_min": pair_delta,
        "target_window_status": target_window_status,
        "own_max_same_peak_status": similarity_status,
        "own_max_same_peak_supported": similarity_supported,
        "own_max_same_peak_support_reason": (
            OWN_MAX_SAME_PEAK_SUPPORT_REASON if similarity_supported else ""
        ),
        "own_max_same_peak_similarity": similarity,
        "own_max_compared_point_count": compared_count,
        "strongest_peak_rt_min": competing.strongest_peak_rt_min,
        "strongest_peak_own_max_ratio": competing.strongest_peak_own_max_ratio,
        "strongest_competing_peak_rt_min": (competing.strongest_competing_peak_rt_min),
        "strongest_competing_peak_own_max_ratio": (
            competing.strongest_competing_peak_own_max_ratio
        ),
        "competing_peak_status": competing_status,
        "reason": ";".join(_dedupe_reasons(reasons)),
    }


def _same_peak_status(status: str, *, supported: bool) -> str:
    if status != "ok":
        return "own_max_same_peak_inconclusive"
    if supported:
        return "own_max_same_peak_supported"
    return "own_max_same_peak_not_supported"


def _competing_status(
    ratio: float | None,
    *,
    status: str,
    strong_competing_peak_ratio: float,
) -> str:
    if status != "ok":
        return "competing_peak_inconclusive"
    if ratio is None:
        return "no_competing_peak_observed"
    if ratio >= strong_competing_peak_ratio:
        return "strong_competing_peak_observed_diagnostic"
    return "competing_peak_observed_below_blocker_threshold"


def _target_window_status(candidate: TargetedMs1ShapeCandidate) -> str:
    if (
        candidate.target_window_start_min is None
        or candidate.target_window_end_min is None
    ):
        return "target_window_not_provided"
    if candidate.candidate_rt_min is None:
        return "target_window_inconclusive"
    if (
        candidate.target_window_start_min
        <= candidate.candidate_rt_min
        <= candidate.target_window_end_min
    ):
        return "candidate_inside_target_window"
    return "candidate_outside_target_window"


def _candidate_rt_for_competing_summary(candidate: TargetedMs1ShapeCandidate) -> float:
    if candidate.candidate_rt_min is not None:
        return candidate.candidate_rt_min
    return 0.0


def _delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _format_row(row: Mapping[str, Any]) -> dict[str, str]:
    return {
        field: format_diagnostic_value(row.get(field))
        for field in TARGETED_MS1_SHAPE_IDENTITY_COLUMNS
    }


def _dedupe_reasons(reasons: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for reason in reasons:
        if not reason or reason in seen:
            continue
        seen.add(reason)
        result.append(reason)
    return tuple(result)
