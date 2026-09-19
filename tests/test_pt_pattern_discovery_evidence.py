from __future__ import annotations

import json

import pytest

from tests.pt_pattern_trace_fixtures import (
    full_context_trace_summary_row,
    write_trace_summary_lineage_bundle,
)
from xic_extractor.discovery import pt_pattern_trace_evidence as trace_evidence
from xic_extractor.discovery.delta_mass_patterns import PtPatternCandidate
from xic_extractor.discovery.pt_pattern_artifact_contract import (
    PT_PATTERN_ANCHOR_LOCAL_TRACE_SUMMARY_SCHEMA_VERSION,
    PT_PATTERN_CANDIDATE_ASSIGNMENT_COLUMNS,
    PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE,
    PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
    PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSION,
    pt_pattern_candidate_assignment_fingerprint,
    validate_pt_pattern_evidence_row_contract,
)
from xic_extractor.discovery.pt_pattern_discovery_evidence import (
    build_pt_pattern_evidence_rows,
    load_pt_discovery_trace_bundle,
)
from xic_extractor.tabular_io import (
    file_sha256,
    read_delimited_rows,
    write_delimited_rows,
)

TRACE_EXTRACTION_SHA256 = "e" * 64
TRACE_MANIFEST_SHA256 = "f" * 64


def _candidate(**overrides: object) -> PtPatternCandidate:
    values: dict[str, object] = {
        "schema_version": "pt_pattern_candidates",
        "sample_stem": "Sample_1",
        "method_label": "pt_pattern",
        "pattern_id": "pt",
        "candidate_id": "Sample_1@pt@base-1",
        "candidate_state": "pt_pattern_candidate",
        "review_priority": "HIGH",
        "evidence_tier": "A",
        "evidence_score": 90,
        "base_feature_id": "base-1",
        "base_mz": 500.0,
        "base_rt_apex_min": 10.00,
        "base_height": 1000.0,
        "base_area": 500.0,
        "position_count": 4,
        "required_position_count": 4,
        "rt_span_sec": 5.0,
        "pattern_rt_status": "within_30s",
        "local_signal_support_status": "supported",
        "cross_sample_support_status": "not_evaluated",
        "ratio_basis": "both",
        "cid_nl_base_status": "not_required",
        "cid_nl_base_candidate_id": "",
        "cid_nl_base_join_mode": "not_required",
        "reason": "full Pt pattern within tolerance",
        "pt_plus_1_feature_id": "plus1-1",
        "pt_plus_1_mz": 501.0021,
        "pt_plus_1_expected_mz": 501.0021,
        "pt_plus_1_mz_error_ppm": 0.0,
        "pt_plus_1_delta_error_da": 0.0,
        "pt_plus_1_rt_delta_sec": 1.0,
        "pt_plus_1_height_ratio_to_base": 0.8,
        "pt_plus_1_area_ratio_to_base": 0.8,
        "pt_plus_2_feature_id": "plus2-1",
        "pt_plus_2_mz": 502.0023,
        "pt_plus_2_expected_mz": 502.0023,
        "pt_plus_2_mz_error_ppm": 0.0,
        "pt_plus_2_delta_error_da": 0.0,
        "pt_plus_2_rt_delta_sec": 2.0,
        "pt_plus_2_height_ratio_to_base": 0.2,
        "pt_plus_2_area_ratio_to_base": 0.2,
        "pt_plus_4_feature_id": "plus4-1",
        "pt_plus_4_mz": 504.0052,
        "pt_plus_4_expected_mz": 504.0052,
        "pt_plus_4_mz_error_ppm": 0.0,
        "pt_plus_4_delta_error_da": 0.0,
        "pt_plus_4_rt_delta_sec": 3.0,
        "pt_plus_4_height_ratio_to_base": 0.05,
        "pt_plus_4_area_ratio_to_base": 0.05,
    }
    values.update(overrides)
    if "base_mz" in overrides:
        base_mz = float(values["base_mz"])
        for label, delta in (
            ("pt_plus_1", 1.002111),
            ("pt_plus_2", 2.002271),
            ("pt_plus_4", 4.005213),
        ):
            values.setdefault(f"{label}_mz", base_mz + delta)
            values.setdefault(f"{label}_expected_mz", base_mz + delta)
            if f"{label}_mz" not in overrides:
                values[f"{label}_mz"] = base_mz + delta
            if f"{label}_expected_mz" not in overrides:
                values[f"{label}_expected_mz"] = base_mz + delta
    return PtPatternCandidate(**values)  # type: ignore[arg-type]


def _trace_summary_row(**overrides: object) -> dict[str, object]:
    candidate = _candidate(
        **{
            field: value
            for field, value in overrides.items()
            if field in PT_PATTERN_CANDIDATE_ASSIGNMENT_COLUMNS
        }
    )
    candidate_payload = {
        field: getattr(candidate, field)
        for field in PT_PATTERN_CANDIDATE_ASSIGNMENT_COLUMNS
    }
    row: dict[str, object] = {
        "schema_version": PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSION,
        "sample_stem": candidate.sample_stem,
        "candidate_id": candidate.candidate_id,
        "candidate_assignment_sha256": (
            pt_pattern_candidate_assignment_fingerprint(candidate_payload)
        ),
        "trace_artifact_role": PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
        "trace_extraction_sha256": TRACE_EXTRACTION_SHA256,
        "base_mz": candidate.base_mz,
        "base_rt_apex_min": candidate.base_rt_apex_min,
        "scan_pattern_seed_status": "full_pattern_seeded",
        "combined_pt_pattern_status": "pt_pattern_detected_diagnostic",
        "non_base_positions_supported": 3,
        "max_abs_apex_delta_sec": 0.4,
        "min_median_rt_step_sec": "",
        "min_pearson_r_to_base": 0.95,
        "min_active_overlap_fraction": 0.70,
        "min_shifted_pearson_r_to_base": "",
        "max_abs_shape_shift_sec_to_base": "",
        "trace_ambiguity_status": "clear",
        "trace_ambiguity_flags": "none",
        "shifted_only_position_labels": "none",
    }
    row.update(overrides)
    return row


def test_discovery_evidence_keeps_missing_trace_as_not_evaluated() -> None:
    rows = build_pt_pattern_evidence_rows([_candidate()])

    assert len(rows) == 1
    row = rows[0]
    assert row["scan_pattern_seed_status"] == "full_pattern_seeded"
    assert row["trace_evidence_status"] == "not_evaluated"
    assert row["trace_rule_call"] == "not_evaluated"
    assert row["trace_detection_status"] == "not_evaluated"
    assert row["trace_rule_reason"] == "trace_summary_not_provided"
    assert row["trace_schema_version"] == ""
    assert row["trace_artifact_role"] == ""
    assert row["trace_extraction_sha256"] == ""
    assert row["trace_manifest_sha256"] == ""
    assert row["base_mz"] == 500.0
    assert row["base_rt_apex_min"] == 10.0
    assert row["matrix_write_authority"] is False
    assert row["counted_detection_authority"] is False
    assert row["export_activation_authority"] is False


def test_discovery_evidence_applies_shared_rule_when_trace_summary_matches() -> None:
    rows = build_pt_pattern_evidence_rows(
        [_candidate()],
        trace_summary_rows=[_trace_summary_row()],
        trace_manifest_sha256=TRACE_MANIFEST_SHA256,
    )

    row = rows[0]
    assert row["trace_evidence_status"] == "evaluated"
    assert row["trace_rule_call"] == "accept"
    assert row["trace_detection_status"] == "detected"
    assert row["trace_rule_reason"] == "full_pattern_close_apex_shape_overlap"
    assert row["combined_pt_pattern_status"] == "pt_pattern_detected_diagnostic"
    assert row["min_pearson_r_to_base"] == 0.95


def test_discovery_evidence_records_effective_scan_aware_apex_threshold() -> None:
    rows = build_pt_pattern_evidence_rows(
        [_candidate()],
        trace_summary_rows=[
            _trace_summary_row(
                max_abs_apex_delta_sec=1.30413,
                min_median_rt_step_sec=19.7353 / 15.0,
                min_effective_smoothing_window_sec=19.7353,
                trace_smoothing_window_points=15,
                min_pearson_r_to_base=0.982299,
                min_active_overlap_fraction=0.952381,
            )
        ],
        trace_manifest_sha256=TRACE_MANIFEST_SHA256,
    )

    row = rows[0]
    assert row["trace_rule_call"] == "accept"
    assert row["configured_max_abs_apex_delta_threshold_sec"] == 0.6
    assert row["max_abs_apex_delta_scan_step_multiplier"] == 1.25
    assert row["effective_max_abs_apex_delta_threshold_sec"] == (
        1.6446083333333332
    )


def test_discovery_evidence_persists_component_derived_scan_interval() -> None:
    rows = build_pt_pattern_evidence_rows(
        [_candidate()],
        trace_summary_rows=[
            _trace_summary_row(
                max_abs_apex_delta_sec=2.0,
                min_median_rt_step_sec="",
                median_rt_step_sec=2.0,
            )
        ],
        trace_manifest_sha256=TRACE_MANIFEST_SHA256,
    )

    row = rows[0]
    assert row["trace_rule_call"] == "accept"
    assert row["min_median_rt_step_sec"] == 2.0
    assert row["effective_max_abs_apex_delta_threshold_sec"] == 2.5
    validate_pt_pattern_evidence_row_contract(row)


def test_discovery_evidence_keeps_shifted_shape_review_only() -> None:
    rows = build_pt_pattern_evidence_rows(
        [_candidate()],
        trace_summary_rows=[
            _trace_summary_row(
                max_abs_apex_delta_sec=25.0,
                min_pearson_r_to_base=0.75,
                min_shifted_pearson_r_to_base=0.85,
                max_abs_shape_shift_sec_to_base=44.9,
            )
        ],
        trace_manifest_sha256=TRACE_MANIFEST_SHA256,
    )

    row = rows[0]
    assert row["trace_rule_call"] == "reject"
    assert row["trace_detection_status"] == "non_detected"
    assert row["trace_rule_reason"] == "shifted_shape_review_only"
    assert row["min_shifted_pearson_r_to_base_threshold"] == 0.85
    assert row["max_abs_shape_shift_sec_to_base_threshold"] == 45.0


def test_discovery_evidence_rejects_legacy_trace_summary_without_assignment_binding(
    tmp_path,
) -> None:
    legacy_trace_summary = tmp_path / "legacy_trace_summary.csv"
    legacy_trace_summary.write_text(
        "\n".join(
            [
                (
                    "schema_version,sample_stem,candidate_id,"
                    "scan_pattern_seed_status,base_mz,base_rt_apex_min,"
                    "rt_half_window_sec,ppm,mz_source,trace_decision_basis,"
                    "trace_smoothing_method,trace_smoothing_window_points,"
                    "min_effective_smoothing_window_sec,"
                    "positions_requested,positions_with_points,"
                    "positions_with_signal,non_base_positions_supported,"
                    "max_abs_apex_delta_sec,min_pearson_r_to_base,"
                    "min_active_overlap_fraction,raw_max_abs_apex_delta_sec,"
                    "raw_min_pearson_r_to_base,raw_min_active_overlap_fraction,"
                    "trace_support_status,combined_pt_pattern_status,reason"
                ),
                (
                    "pt_pattern_trace_diagnosis,Sample_1,Sample_1@pt@base-1,"
                    "full_pattern_seeded,500,10,30,20,observed,"
                    "gaussian15_smoothed_trace,gaussian15,15,19.5,4,4,4,3,0.4,0.95,"
                    "0.70,0.4,0.95,0.70,supported,"
                    "pt_pattern_detected_diagnostic,legacy close-apex row"
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing required columns"):
        load_pt_discovery_trace_bundle(legacy_trace_summary)


def test_discovery_evidence_rejects_anchor_local_trace_rows() -> None:
    with pytest.raises(ValueError, match="full-context trace schema"):
        build_pt_pattern_evidence_rows(
            [_candidate()],
            trace_summary_rows=[
                _trace_summary_row(
                    schema_version=(
                        PT_PATTERN_ANCHOR_LOCAL_TRACE_SUMMARY_SCHEMA_VERSION
                    ),
                    trace_artifact_role=PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE,
                )
            ],
            trace_manifest_sha256=TRACE_MANIFEST_SHA256,
        )


@pytest.mark.parametrize(
    ("artifact", "field", "value"),
    [
        ("summary", "min_pearson_r_to_base", "0.10"),
        ("summary", "combined_pt_pattern_status", "pt_pattern_candidate_review"),
        ("metrics", "point_count", "100"),
        ("metrics", "trace_support_status", "review"),
    ],
)
def test_discovery_trace_loader_rejects_refreshed_hash_projection_tamper(
    tmp_path,
    artifact: str,
    field: str,
    value: str,
) -> None:
    summary_csv = tmp_path / "pt_pattern_trace_summary.csv"
    paths = write_trace_summary_lineage_bundle(
        trace_summary_csv=summary_csv,
        summary_rows=[
            full_context_trace_summary_row(
                candidate_id="candidate-a",
                candidate_assignment_sha256="a" * 64,
                sample_stem="Sample_1",
            )
        ],
    )
    manifest_path = paths["trace_manifest_json"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if artifact == "summary":
        rows = read_delimited_rows(
            summary_csv,
            required_columns=trace_evidence.SUMMARY_COLUMNS,
            exact_columns=trace_evidence.SUMMARY_COLUMNS,
            delimiter=",",
        )
        rows[0][field] = value
        write_delimited_rows(summary_csv, rows, trace_evidence.SUMMARY_COLUMNS)
        manifest["output_sha256"]["summary_csv"] = file_sha256(
            summary_csv
        ).lower()
    else:
        metrics_csv = paths["trace_metrics_csv"]
        rows = read_delimited_rows(
            metrics_csv,
            required_columns=trace_evidence.METRIC_COLUMNS,
            exact_columns=trace_evidence.METRIC_COLUMNS,
            delimiter=",",
        )
        rows[1][field] = value
        write_delimited_rows(metrics_csv, rows, trace_evidence.METRIC_COLUMNS)
        manifest["output_sha256"]["metrics_csv"] = file_sha256(
            metrics_csv
        ).lower()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="deterministic projection"):
        load_pt_discovery_trace_bundle(summary_csv)


def test_discovery_evidence_records_reject_without_dropping_seed_candidate() -> None:
    rows = build_pt_pattern_evidence_rows(
        [_candidate()],
        trace_summary_rows=[_trace_summary_row(min_pearson_r_to_base=0.84)],
        trace_manifest_sha256=TRACE_MANIFEST_SHA256,
    )

    row = rows[0]
    assert row["candidate_state"] == "pt_pattern_candidate"
    assert row["trace_rule_call"] == "reject"
    assert row["trace_detection_status"] == "non_detected"
    assert row["trace_rule_reason"] == "pearson_below_threshold"


def test_discovery_evidence_marks_missing_candidate_trace_row() -> None:
    rows = build_pt_pattern_evidence_rows(
        [_candidate()],
        trace_summary_rows=[
            _trace_summary_row(candidate_id="Sample_1@pt@other-base")
        ],
        trace_manifest_sha256=TRACE_MANIFEST_SHA256,
    )

    row = rows[0]
    assert row["trace_rule_call"] == "not_evaluated"
    assert row["trace_rule_reason"] == "trace_summary_missing_for_candidate"


def test_discovery_evidence_rejects_duplicate_trace_summary_candidate_ids() -> None:
    try:
        build_pt_pattern_evidence_rows(
            [_candidate()],
            trace_summary_rows=[_trace_summary_row(), _trace_summary_row()],
            trace_manifest_sha256=TRACE_MANIFEST_SHA256,
        )
    except ValueError as error:
        assert "duplicate trace summary candidate_id" in str(error)
    else:  # pragma: no cover - defensive assertion style keeps py3.10 simple
        raise AssertionError("expected duplicate candidate_id to raise ValueError")


def test_discovery_evidence_joins_whitespace_equivalent_candidate_ids() -> None:
    rows = build_pt_pattern_evidence_rows(
        [_candidate(candidate_id=" candidate-a ")],
        trace_summary_rows=[_trace_summary_row(candidate_id="candidate-a")],
        trace_manifest_sha256=TRACE_MANIFEST_SHA256,
    )

    assert rows[0]["trace_evidence_status"] == "evaluated"
    assert rows[0]["trace_rule_call"] == "accept"


def test_discovery_evidence_rejects_blank_candidate_id() -> None:
    with pytest.raises(ValueError, match="candidate.*missing candidate_id"):
        build_pt_pattern_evidence_rows([_candidate(candidate_id=" ")])


def test_discovery_evidence_rejects_normalized_duplicate_candidate_ids() -> None:
    with pytest.raises(ValueError, match="duplicate candidate candidate_id"):
        build_pt_pattern_evidence_rows(
            [
                _candidate(candidate_id="candidate-a"),
                _candidate(candidate_id=" candidate-a "),
            ]
        )


@pytest.mark.parametrize(
    ("field", "conflicting_value"),
    [
        ("sample_stem", "Sample_2"),
        ("base_mz", 501.0),
        ("base_rt_apex_min", 10.5),
    ],
)
def test_discovery_evidence_rejects_trace_summary_identity_mismatch(
    field: str,
    conflicting_value: object,
) -> None:
    with pytest.raises(ValueError, match=rf"{field}.*does not match"):
        build_pt_pattern_evidence_rows(
            [_candidate()],
            trace_summary_rows=[_trace_summary_row(**{field: conflicting_value})],
            trace_manifest_sha256=TRACE_MANIFEST_SHA256,
        )


def test_discovery_evidence_rejects_trace_from_old_partner_assignment() -> None:
    with pytest.raises(ValueError, match="candidate_assignment_sha256"):
        build_pt_pattern_evidence_rows(
            [_candidate(pt_plus_1_feature_id="plus1-reassigned")],
            trace_summary_rows=[_trace_summary_row()],
            trace_manifest_sha256=TRACE_MANIFEST_SHA256,
        )


def test_discovery_evidence_join_is_independent_of_trace_summary_order() -> None:
    candidate_a = _candidate(candidate_id="candidate-a")
    candidate_b = _candidate(
        candidate_id="candidate-b",
        base_mz=600.0,
        base_rt_apex_min=11.0,
    )
    summary_a = _trace_summary_row(candidate_id="candidate-a")
    summary_b = _trace_summary_row(
        candidate_id="candidate-b",
        base_mz=600.0,
        base_rt_apex_min=11.0,
        min_pearson_r_to_base=0.84,
    )

    rows = build_pt_pattern_evidence_rows(
        [candidate_a, candidate_b],
        trace_summary_rows=[summary_b, summary_a],
        trace_manifest_sha256=TRACE_MANIFEST_SHA256,
    )

    assert [(row["candidate_id"], row["trace_rule_call"]) for row in rows] == [
        ("candidate-a", "accept"),
        ("candidate-b", "reject"),
    ]
