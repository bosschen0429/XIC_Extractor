"""Discovery-facing Pt pattern evidence rows.

This module bridges feature-level Pt candidates and optional trace-summary
evidence. It does not read RAW files or grant matrix/export authority.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.discovery.delta_mass_patterns import PtPatternCandidate
from xic_extractor.discovery.pt_pattern_artifact_contract import (
    PT_PATTERN_CANDIDATE_ASSIGNMENT_COLUMNS,
    PT_PATTERN_EVIDENCE_CONTRACT_COLUMNS,
    PT_PATTERN_EVIDENCE_SCHEMA_VERSION,
    PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
    PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSION,
    pt_pattern_candidate_assignment_fingerprint,
    validate_pt_candidate_trace_summary_identity,
    validate_pt_pattern_evidence_row_contract,
)
from xic_extractor.discovery.pt_pattern_evidence_rule import (
    DEFAULT_THRESHOLDS,
    EVIDENCE_RULE_OWNER,
    PtPatternEvidenceThresholds,
    effective_max_abs_apex_delta_sec,
    evaluate_pt_pattern_evidence,
    pt_pattern_rule_id,
    pt_pattern_scan_interval_sec,
)
from xic_extractor.discovery.pt_pattern_trace_bundle_reader import (
    VerifiedTraceSummaryBundle,
    load_verified_trace_summary_bundle,
)
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
)
from xic_extractor.tabular_io import (
    escape_excel_formula,
    format_machine_float,
    render_delimited_rows,
)

SCHEMA_VERSION = PT_PATTERN_EVIDENCE_SCHEMA_VERSION
TRACE_EVIDENCE_NOT_EVALUATED = "not_evaluated"
TRACE_EVIDENCE_EVALUATED = "evaluated"
NO_WRITE_AUTHORITY = False
ARTIFACT_OWNER_NAMESPACE = "xic_extractor.discovery.pt_pattern_discovery_evidence"
ARTIFACT_SEMANTIC_ROLE = "pt_pattern_discovery_evidence"
ARTIFACT_VALIDATION_STATUS = "diagnostic_only"
ARTIFACT_MEDIA_TYPE = "text/csv; charset=utf-8"
ARTIFACT_SERIALIZATION_FORMAT = "pt_pattern_discovery_evidence_csv"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_ARTIFACT_ESCAPED_FIELDS = frozenset({"trace_rule_reason"})

_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace=ARTIFACT_OWNER_NAMESPACE,
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=SCHEMA_VERSION,
    validation_status=ARTIFACT_VALIDATION_STATUS,
    media_type=ARTIFACT_MEDIA_TYPE,
    serialization_format=ARTIFACT_SERIALIZATION_FORMAT,
)


def load_pt_discovery_trace_bundle(
    summary_csv: Path,
    *,
    metrics_csv: Path | None = None,
    manifest_json: Path | None = None,
) -> VerifiedTraceSummaryBundle:
    """Load the full-context trace bundle permitted as Discovery evidence."""

    return load_verified_trace_summary_bundle(
        summary_csv,
        expected_artifact_role=PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
        metrics_csv=metrics_csv,
        manifest_json=manifest_json,
    )


def build_pt_pattern_evidence_rows(
    candidates: Sequence[PtPatternCandidate],
    *,
    trace_summary_rows: Sequence[Mapping[str, object]] | None = None,
    trace_manifest_sha256: str | None = None,
    thresholds: PtPatternEvidenceThresholds = DEFAULT_THRESHOLDS,
) -> tuple[dict[str, object], ...]:
    """Build one Discovery evidence row per Pt candidate.

    Feature-level Pt matching creates the seed candidate. Trace-level
    detected/non-detected status is only evaluated when a matching trace
    summary row is supplied.
    """

    normalized_candidate_ids = _normalized_candidate_ids(candidates)
    trace_source_fields = _trace_source_fields(
        trace_summary_rows,
        trace_manifest_sha256=trace_manifest_sha256,
    )
    trace_by_candidate_id = (
        _trace_rows_by_candidate_id(trace_summary_rows)
        if trace_summary_rows is not None
        else {}
    )
    trace_summary_provided = trace_summary_rows is not None
    return tuple(
        _evidence_row(
            candidate,
            trace_row=trace_by_candidate_id.get(normalized_candidate_id),
            trace_summary_provided=trace_summary_provided,
            trace_source_fields=trace_source_fields,
            thresholds=thresholds,
        )
        for candidate, normalized_candidate_id in zip(
            candidates,
            normalized_candidate_ids,
            strict=True,
        )
    )


def canonical_pt_pattern_evidence_csv_bytes(
    evidence_rows: Sequence[Mapping[str, object]],
) -> bytes:
    """Render exact CSV bytes owned by the Pt Discovery evidence contract."""

    canonical_rows = tuple(evidence_rows)
    for row_index, row in enumerate(canonical_rows, start=2):
        missing = [
            column
            for column in PT_PATTERN_EVIDENCE_CONTRACT_COLUMNS
            if column not in row
        ]
        if missing:
            raise ValueError(
                f"evidence row {row_index} is missing required fields: "
                + ", ".join(missing)
            )
        validate_pt_pattern_evidence_row_contract(
            row,
            side=f"evidence row {row_index}",
        )
        for authority_field in (
            "matrix_write_authority",
            "counted_detection_authority",
            "export_activation_authority",
        ):
            if row[authority_field] is not False:
                raise ValueError(
                    f"evidence row {row_index} {authority_field} must be False"
                )
    formatted_rows = tuple(
        {
            column: _format_pt_evidence_value(
                value,
                escape_formula=column in _ARTIFACT_ESCAPED_FIELDS,
            )
            for column, value in row.items()
        }
        for row in canonical_rows
    )
    return render_delimited_rows(
        formatted_rows,
        PT_PATTERN_EVIDENCE_CONTRACT_COLUMNS,
        extrasaction="raise",
    ).encode("utf-8")


def mint_pt_pattern_discovery_evidence_artifact_ref(
    *,
    processing_attempt: object,
    evidence_rows: Sequence[Mapping[str, object]],
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
        payload_bytes=canonical_pt_pattern_evidence_csv_bytes(evidence_rows),
        upstream_artifacts=upstream_artifacts,
    )


def _format_pt_evidence_value(
    value: object,
    *,
    escape_formula: bool,
) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return format_machine_float(value)
    if isinstance(value, int):
        return str(value)
    text = str(value)
    return escape_excel_formula(text) if escape_formula else text


def _trace_source_fields(
    rows: Sequence[Mapping[str, object]] | None,
    *,
    trace_manifest_sha256: str | None,
) -> dict[str, str]:
    if rows is None:
        if trace_manifest_sha256 is not None:
            raise ValueError("trace_manifest_sha256 requires trace_summary_rows")
        return {}
    if not rows:
        raise ValueError("trace summary rows must not be empty")
    normalized_manifest_sha256 = str(trace_manifest_sha256 or "").strip()
    if _SHA256_PATTERN.fullmatch(normalized_manifest_sha256) is None:
        raise ValueError(
            "trace_manifest_sha256 must be a lowercase SHA-256 digest when "
            "trace summary rows are provided"
        )
    schemas = {str(row.get("schema_version", "")).strip() for row in rows}
    roles = {str(row.get("trace_artifact_role", "")).strip() for row in rows}
    extraction_sha256_values = {
        str(row.get("trace_extraction_sha256", "")).strip() for row in rows
    }
    if schemas != {PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSION}:
        raise ValueError(
            "Discovery evidence requires one current full-context trace schema; "
            f"observed={sorted(schemas)!r}"
        )
    if roles != {PT_PATTERN_TRACE_FULL_CONTEXT_ROLE}:
        raise ValueError(
            "Discovery evidence requires trace_artifact_role='full_context'; "
            f"observed={sorted(roles)!r}"
        )
    if (
        len(extraction_sha256_values) != 1
        or _SHA256_PATTERN.fullmatch(next(iter(extraction_sha256_values))) is None
    ):
        raise ValueError(
            "Discovery trace rows must use one lowercase trace_extraction_sha256"
        )
    return {
        "trace_schema_version": PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSION,
        "trace_artifact_role": PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
        "trace_extraction_sha256": next(iter(extraction_sha256_values)),
        "trace_manifest_sha256": normalized_manifest_sha256,
    }


def _normalized_candidate_ids(
    candidates: Sequence[PtPatternCandidate],
) -> tuple[str, ...]:
    normalized_ids: list[str] = []
    seen: set[str] = set()
    duplicates: set[str] = set()
    for candidate in candidates:
        candidate_id = _normalized_candidate_id(
            candidate.candidate_id,
            side="candidate",
        )
        if candidate_id in seen:
            duplicates.add(candidate_id)
        seen.add(candidate_id)
        normalized_ids.append(candidate_id)
    if duplicates:
        joined = ", ".join(sorted(duplicates))
        raise ValueError(f"duplicate candidate candidate_id values: {joined}")
    return tuple(normalized_ids)


def _trace_rows_by_candidate_id(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, Mapping[str, object]]:
    by_id: dict[str, Mapping[str, object]] = {}
    for row in rows:
        candidate_id = _normalized_candidate_id(
            row.get("candidate_id"),
            side="trace summary row",
        )
        if candidate_id in by_id:
            raise ValueError(f"duplicate trace summary candidate_id: {candidate_id}")
        by_id[candidate_id] = row
    return by_id


def _normalized_candidate_id(value: object, *, side: str) -> str:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{side} is missing candidate_id")
    candidate_id = str(value).strip()
    if not candidate_id:
        raise ValueError(f"{side} is missing candidate_id")
    return candidate_id


def _evidence_row(
    candidate: PtPatternCandidate,
    *,
    trace_row: Mapping[str, object] | None,
    trace_summary_provided: bool,
    trace_source_fields: Mapping[str, str],
    thresholds: PtPatternEvidenceThresholds,
) -> dict[str, object]:
    row = _base_evidence_row(candidate, thresholds=thresholds)
    row.update(trace_source_fields)
    if trace_row is None:
        row.update(
            {
                "trace_evidence_status": TRACE_EVIDENCE_NOT_EVALUATED,
                "trace_rule_call": TRACE_EVIDENCE_NOT_EVALUATED,
                "trace_detection_status": TRACE_EVIDENCE_NOT_EVALUATED,
                "trace_rule_reason": (
                    "trace_summary_missing_for_candidate"
                    if trace_summary_provided
                    else "trace_summary_not_provided"
                ),
            }
        )
        return row

    validate_pt_candidate_trace_summary_identity(
        _candidate_assignment_row(candidate),
        trace_row,
    )
    scan_interval_sec = pt_pattern_scan_interval_sec(trace_row)
    decision = evaluate_pt_pattern_evidence(trace_row, thresholds=thresholds)
    row.update(
        {
            "scan_pattern_seed_status": _trace_value(
                trace_row, "scan_pattern_seed_status", default="full_pattern_seeded"
            ),
            "trace_evidence_status": TRACE_EVIDENCE_EVALUATED,
            "trace_rule_call": decision.call,
            "trace_detection_status": decision.detection_status,
            "trace_rule_reason": decision.reason,
            "min_median_rt_step_sec": (
                scan_interval_sec if scan_interval_sec is not None else ""
            ),
            "effective_max_abs_apex_delta_threshold_sec": (
                effective_max_abs_apex_delta_sec(
                    trace_row,
                    thresholds=thresholds,
                )
            ),
            "combined_pt_pattern_status": _trace_value(
                trace_row, "combined_pt_pattern_status"
            ),
            "non_base_positions_supported": _trace_value(
                trace_row, "non_base_positions_supported"
            ),
            "max_abs_apex_delta_sec": _trace_value(trace_row, "max_abs_apex_delta_sec"),
            "min_pearson_r_to_base": _trace_value(trace_row, "min_pearson_r_to_base"),
            "min_active_overlap_fraction": _trace_value(
                trace_row, "min_active_overlap_fraction"
            ),
            "min_shifted_pearson_r_to_base": _trace_value(
                trace_row, "min_shifted_pearson_r_to_base"
            ),
            "max_abs_shape_shift_sec_to_base": _trace_value(
                trace_row, "max_abs_shape_shift_sec_to_base"
            ),
            "trace_ambiguity_status": _trace_value(trace_row, "trace_ambiguity_status"),
            "trace_ambiguity_flags": _trace_value(trace_row, "trace_ambiguity_flags"),
            "shifted_only_position_labels": _trace_value(
                trace_row, "shifted_only_position_labels"
            ),
        }
    )
    return row


def _base_evidence_row(
    candidate: PtPatternCandidate,
    *,
    thresholds: PtPatternEvidenceThresholds,
) -> dict[str, object]:
    candidate_assignment = _candidate_assignment_row(candidate)
    return {
        "schema_version": SCHEMA_VERSION,
        "sample_stem": candidate.sample_stem,
        "method_label": candidate.method_label,
        "pattern_id": candidate.pattern_id,
        "candidate_id": candidate.candidate_id,
        "candidate_assignment_sha256": (
            pt_pattern_candidate_assignment_fingerprint(candidate_assignment)
        ),
        "trace_schema_version": "",
        "trace_artifact_role": "",
        "trace_extraction_sha256": "",
        "trace_manifest_sha256": "",
        "base_mz": candidate.base_mz,
        "base_rt_apex_min": candidate.base_rt_apex_min,
        "candidate_state": candidate.candidate_state,
        "scan_pattern_seed_status": "full_pattern_seeded",
        "trace_evidence_status": "",
        "trace_rule_call": "",
        "trace_detection_status": "",
        "trace_rule_reason": "",
        "evidence_rule_id": pt_pattern_rule_id(thresholds),
        "evidence_rule_owner": EVIDENCE_RULE_OWNER,
        "required_combined_status": thresholds.required_combined_status,
        "min_non_base_positions_supported": (
            thresholds.min_non_base_positions_supported
        ),
        "configured_max_abs_apex_delta_threshold_sec": (
            thresholds.max_abs_apex_delta_sec
        ),
        "max_abs_apex_delta_scan_step_multiplier": (
            thresholds.max_abs_apex_delta_scan_step_multiplier
        ),
        "min_median_rt_step_sec": "",
        "effective_max_abs_apex_delta_threshold_sec": "",
        "min_pearson_r_to_base_threshold": thresholds.min_pearson_r_to_base,
        "min_active_overlap_fraction_threshold": (
            thresholds.min_active_overlap_fraction
        ),
        "min_shifted_pearson_r_to_base_threshold": (
            thresholds.min_shifted_pearson_r_to_base
        ),
        "max_abs_shape_shift_sec_to_base_threshold": (
            thresholds.max_abs_shape_shift_sec_to_base
        ),
        "combined_pt_pattern_status": "",
        "non_base_positions_supported": "",
        "max_abs_apex_delta_sec": "",
        "min_pearson_r_to_base": "",
        "min_active_overlap_fraction": "",
        "min_shifted_pearson_r_to_base": "",
        "max_abs_shape_shift_sec_to_base": "",
        "trace_ambiguity_status": "",
        "trace_ambiguity_flags": "",
        "shifted_only_position_labels": "",
        "matrix_write_authority": NO_WRITE_AUTHORITY,
        "counted_detection_authority": NO_WRITE_AUTHORITY,
        "export_activation_authority": NO_WRITE_AUTHORITY,
    }


def _candidate_assignment_row(
    candidate: PtPatternCandidate,
) -> dict[str, object]:
    return {
        field: getattr(candidate, field)
        for field in PT_PATTERN_CANDIDATE_ASSIGNMENT_COLUMNS
    }


def _trace_value(
    row: Mapping[str, object],
    field: str,
    *,
    default: object = "",
) -> object:
    value = row.get(field, default)
    if value is None:
        return ""
    return value
