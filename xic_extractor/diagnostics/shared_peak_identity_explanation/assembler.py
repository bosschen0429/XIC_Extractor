from __future__ import annotations

from collections.abc import Mapping, Sequence

from .machine_artifacts import MachineMatch
from .oracle import ManualOracleRow
from .schema import (
    EVIDENCE_SCHEMA_VERSION,
    EVIDENCE_VECTOR_COLUMNS,
    validate_row_tokens,
)


def assemble_evidence_vectors(
    oracle_rows: Sequence[ManualOracleRow],
    machine_matches: Mapping[str, Sequence[MachineMatch]],
) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    for oracle in oracle_rows:
        matches = tuple(machine_matches.get(oracle.oracle_row_id, ()))
        if not matches:
            rows.append(_manual_only_row(oracle))
            continue
        for index, match in enumerate(matches, start=1):
            rows.append(_match_row(oracle, match, index))
    return tuple(rows)


def _manual_only_row(oracle: ManualOracleRow) -> dict[str, str]:
    row = _base_row(oracle)
    row.update(
        {
            "evidence_record_id": f"{oracle.oracle_row_id}|manual",
            "evidence_source": "manual_oracle",
            "source_role": "manual_oracle",
            "source_artifact": "",
            "source_artifact_sha256": "",
            "source_row_id": oracle.oracle_row_id,
            "machine_current_label": "not_applicable"
            if oracle.is_sentinel
            else "not_available",
            "machine_reason": "",
            "machine_blockers": "",
            "metric_availability_status": "not_assessed",
        }
    )
    validate_row_tokens(row)
    return row


def _match_row(
    oracle: ManualOracleRow,
    match: MachineMatch,
    index: int,
) -> dict[str, str]:
    row = _base_row(oracle)
    row.update(
        {
            "evidence_record_id": f"{oracle.oracle_row_id}|{index}",
            "evidence_source": match.evidence_source,
            "source_role": match.source_role,
            "source_artifact": str(match.source_artifact),
            "source_artifact_sha256": match.source_artifact_sha256,
            "source_row_id": match.source_row_id,
            "machine_current_label": match.machine_current_label,
            "machine_reason": match.machine_reason,
            "machine_blockers": ";".join(match.machine_blockers),
            "candidate_apex_rt": match.row.get("apex_rt", ""),
            "family_reference_rt": match.row.get("family_center_rt", ""),
            "seed_delta_sec": match.row.get("rt_delta_sec", ""),
            "intensity_status": _intensity_status(oracle),
            "dda_opportunity_status": _dda_status(oracle),
            "metric_availability_status": "partial",
        }
    )
    validate_row_tokens(row)
    return row


def _base_row(oracle: ManualOracleRow) -> dict[str, str]:
    tags = set(oracle.manual_reason_tags)
    return {
        column: ""
        for column in EVIDENCE_VECTOR_COLUMNS
    } | {
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "oracle_row_id": oracle.oracle_row_id,
        "feature_family_id": oracle.feature_family_id,
        "sample_id": oracle.sample_id,
        "rt_context_status": _rt_status(tags),
        "shape_status": _shape_status(tags),
        "apex_clarity_status": "not_assessed",
        "single_peak_region_status": "single_plausible_peak"
        if "single_plausible_peak" in tags
        else "not_assessed",
        "peak_completeness_status": "complete"
        if "shape_complete" in tags
        else "not_assessed",
        "boundary_reference_status": "not_assessed",
        "pattern_similarity_status": _pattern_status(tags),
        "pattern_conflict_status": "rt_pattern_conflict"
        if {"rt_too_far", "pattern_mismatch"} <= tags
        else "none",
        "delta_mass_context": "related_family_context_only"
        if "delta_mass_related" in tags
        else "none",
        "intensity_status": _intensity_status(oracle),
        "dda_opportunity_status": _dda_status(oracle),
        "fragmentation_observation_status": "not_observed"
        if "dda_stochastic_missing" in tags
        else "not_assessed",
        "scan_availability_status": "not_assessed",
    }


def _rt_status(tags: set[str]) -> str:
    if "rt_too_far" in tags:
        return "conflicting"
    if "rt_drift_possible" in tags:
        return "drift_possible"
    if "rt_close" in tags:
        return "supportive"
    return "not_assessed"


def _shape_status(tags: set[str]) -> str:
    if "shape_bad" in tags:
        return "noisy_unjudgeable"
    if "low_intensity" in tags and "shape_complete" in tags:
        return "low_intensity_but_coherent"
    if "shape_complete" in tags:
        return "complete"
    if "shape_normal" in tags:
        return "acceptable"
    return "not_assessed"


def _pattern_status(tags: set[str]) -> str:
    if "pattern_mismatch" in tags:
        return "mismatch"
    if "pattern_partial" in tags:
        return "partial_similar"
    if "pattern_similar" in tags:
        return "similar"
    return "not_assessed"


def _intensity_status(oracle: ManualOracleRow) -> str:
    tags = set(oracle.manual_reason_tags)
    if "low_intensity" in tags:
        return "low_but_visible"
    if oracle.manual_label == "human_unjudgeable":
        return "too_low_to_assess"
    if oracle.manual_label in {"pass", "suspect"}:
        return "sufficient"
    return "not_assessed"


def _dda_status(oracle: ManualOracleRow) -> str:
    tags = set(oracle.manual_reason_tags)
    if oracle.is_sentinel:
        return "not_applicable"
    if "dda_stochastic_missing" in tags:
        return "low_intensity_stochastic_not_observed"
    return "observed" if oracle.manual_label in {"pass", "suspect"} else "not_assessed"
