"""Contract tests for Pt pattern CSV/JSON I/O.

Covers ``load_detected_features_csv()``, ``write_pt_pattern_candidates_csv()``,
``write_pt_pattern_review_csv()``, ``write_pt_pattern_evidence_csv()``, and
``write_pt_pattern_sidecar_json()`` from ``xic_extractor.discovery.delta_mass_csv``.
Header order and field names are copied verbatim from the Public Contract
Details section of the implementation plan (Detected Features CSV v1, Pt
Candidate CSV v1, Pt Review CSV v1, Pt Discovery Evidence CSV v1, Pt Sidecar
JSON v2).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from xic_extractor.discovery.delta_mass_csv import (
    DeltaMassDiscoveryResult,
    load_detected_features_csv,
    write_pt_pattern_candidates_csv,
    write_pt_pattern_evidence_csv,
    write_pt_pattern_review_csv,
    write_pt_pattern_sidecar_json,
)
from xic_extractor.discovery.delta_mass_patterns import PT_PATTERN, PtPatternCandidate
from xic_extractor.discovery.detected_features import (
    DetectedFeature,
    comparable_cid_nl_identity,
    write_detected_features_csv,
)
from xic_extractor.discovery.pt_pattern_trace_evidence import (
    load_candidate_rows,
    validate_candidate_sample_binding,
)

DETECTED_FEATURES_COLUMNS = (
    "sample_stem",
    "feature_id",
    "source_feature_row_id",
    "mz",
    "rt_apex_min",
    "rt_start_min",
    "rt_end_min",
    "height",
    "area",
    "trace_quality",
    "shape_score",
    "source",
)

PT_CANDIDATE_COLUMNS = (
    "schema_version",
    "sample_stem",
    "method_label",
    "pattern_id",
    "candidate_id",
    "candidate_state",
    "review_priority",
    "evidence_tier",
    "evidence_score",
    "base_feature_id",
    "base_mz",
    "base_rt_apex_min",
    "base_height",
    "base_area",
    "position_count",
    "required_position_count",
    "rt_span_sec",
    "pattern_rt_status",
    "local_signal_support_status",
    "cross_sample_support_status",
    "ratio_basis",
    "cid_nl_base_status",
    "cid_nl_base_candidate_id",
    "cid_nl_base_join_mode",
    "reason",
    "pt_plus_1_feature_id",
    "pt_plus_1_mz",
    "pt_plus_1_expected_mz",
    "pt_plus_1_mz_error_ppm",
    "pt_plus_1_delta_error_da",
    "pt_plus_1_rt_delta_sec",
    "pt_plus_1_height_ratio_to_base",
    "pt_plus_1_area_ratio_to_base",
    "pt_plus_2_feature_id",
    "pt_plus_2_mz",
    "pt_plus_2_expected_mz",
    "pt_plus_2_mz_error_ppm",
    "pt_plus_2_delta_error_da",
    "pt_plus_2_rt_delta_sec",
    "pt_plus_2_height_ratio_to_base",
    "pt_plus_2_area_ratio_to_base",
    "pt_plus_4_feature_id",
    "pt_plus_4_mz",
    "pt_plus_4_expected_mz",
    "pt_plus_4_mz_error_ppm",
    "pt_plus_4_delta_error_da",
    "pt_plus_4_rt_delta_sec",
    "pt_plus_4_height_ratio_to_base",
    "pt_plus_4_area_ratio_to_base",
)

PT_REVIEW_COLUMNS = (
    "review_priority",
    "evidence_tier",
    "evidence_score",
    "sample_stem",
    "candidate_id",
    "method_label",
    "base_mz",
    "base_rt_apex_min",
    "rt_span_sec",
    "position_count",
    "cid_nl_base_status",
    "cid_nl_base_join_mode",
    "reason",
    "review_note",
)

PT_EVIDENCE_COLUMNS = (
    "schema_version",
    "sample_stem",
    "method_label",
    "pattern_id",
    "candidate_id",
    "candidate_state",
    "scan_pattern_seed_status",
    "trace_evidence_status",
    "trace_rule_call",
    "trace_detection_status",
    "trace_rule_reason",
    "evidence_rule_id",
    "evidence_rule_owner",
    "required_combined_status",
    "min_non_base_positions_supported",
    "max_abs_apex_delta_threshold_sec",
    "min_pearson_r_to_base_threshold",
    "min_active_overlap_fraction_threshold",
    "min_shifted_pearson_r_to_base_threshold",
    "max_abs_shape_shift_sec_to_base_threshold",
    "combined_pt_pattern_status",
    "non_base_positions_supported",
    "max_abs_apex_delta_sec",
    "min_pearson_r_to_base",
    "min_active_overlap_fraction",
    "min_shifted_pearson_r_to_base",
    "max_abs_shape_shift_sec_to_base",
    "trace_ambiguity_status",
    "trace_ambiguity_flags",
    "shifted_only_position_labels",
    "matrix_write_authority",
    "counted_detection_authority",
    "export_activation_authority",
)


def _detected_features_csv_text() -> str:
    header = ",".join(DETECTED_FEATURES_COLUMNS)
    row = (
        "Sample_1,base-1,,500.0,10.00,9.95,10.05,1000.0,500.0,GOOD,0.9,targeted"
    )
    return f"{header}\r\n{row}\r\n"


def _pt_candidate(**overrides: object) -> PtPatternCandidate:
    values: dict[str, object] = {
        "schema_version": "pt_pattern_candidates.v1",
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
        "pt_plus_1_mz": 501.002111,
        "pt_plus_1_expected_mz": 501.002111,
        "pt_plus_1_mz_error_ppm": 0.0,
        "pt_plus_1_delta_error_da": 0.0,
        "pt_plus_1_rt_delta_sec": 1.0,
        "pt_plus_1_height_ratio_to_base": 0.8,
        "pt_plus_1_area_ratio_to_base": 0.8,
        "pt_plus_2_feature_id": "plus2-1",
        "pt_plus_2_mz": 502.002271,
        "pt_plus_2_expected_mz": 502.002271,
        "pt_plus_2_mz_error_ppm": 0.0,
        "pt_plus_2_delta_error_da": 0.0,
        "pt_plus_2_rt_delta_sec": 2.0,
        "pt_plus_2_height_ratio_to_base": 0.2,
        "pt_plus_2_area_ratio_to_base": 0.2,
        "pt_plus_4_feature_id": "plus4-1",
        "pt_plus_4_mz": 504.005213,
        "pt_plus_4_expected_mz": 504.005213,
        "pt_plus_4_mz_error_ppm": 0.0,
        "pt_plus_4_delta_error_da": 0.0,
        "pt_plus_4_rt_delta_sec": 3.0,
        "pt_plus_4_height_ratio_to_base": 0.05,
        "pt_plus_4_area_ratio_to_base": 0.05,
    }
    values.update(overrides)
    return PtPatternCandidate(**values)  # type: ignore[arg-type]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _pt_evidence_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": "pt_pattern_discovery_evidence.v1",
        "sample_stem": "Sample_1",
        "method_label": "pt_pattern",
        "pattern_id": "pt",
        "candidate_id": "Sample_1@pt@base-1",
        "candidate_state": "pt_pattern_candidate",
        "scan_pattern_seed_status": "full_pattern_seeded",
        "trace_evidence_status": "evaluated",
        "trace_rule_call": "accept",
        "trace_detection_status": "detected",
        "trace_rule_reason": "full_pattern_close_apex_shape_overlap",
        "evidence_rule_id": (
            "pt_v2_fullpattern_apex_le_0p6s_or_1p25scan_pearson_ge_0p9_overlap_ge_0p5"
            "_shiftedreviewonly_shiftedpearson_ge_0p85_shapeshift_le_45s"
        ),
        "evidence_rule_owner": "xic_extractor.discovery.pt_pattern_evidence_rule",
        "required_combined_status": "pt_pattern_detected_diagnostic",
        "min_non_base_positions_supported": 3,
        "max_abs_apex_delta_threshold_sec": 0.6,
        "min_pearson_r_to_base_threshold": 0.90,
        "min_active_overlap_fraction_threshold": 0.50,
        "min_shifted_pearson_r_to_base_threshold": 0.85,
        "max_abs_shape_shift_sec_to_base_threshold": 45.0,
        "combined_pt_pattern_status": "pt_pattern_detected_diagnostic",
        "non_base_positions_supported": 3,
        "max_abs_apex_delta_sec": 0.4,
        "min_pearson_r_to_base": 0.95,
        "min_active_overlap_fraction": 0.70,
        "min_shifted_pearson_r_to_base": 0.96,
        "max_abs_shape_shift_sec_to_base": 2.0,
        "trace_ambiguity_status": "clear",
        "trace_ambiguity_flags": "none",
        "shifted_only_position_labels": "none",
        "matrix_write_authority": False,
        "counted_detection_authority": False,
        "export_activation_authority": False,
    }
    row.update(overrides)
    return row


def test_load_detected_features_csv_parses_required_and_blank_optional_fields(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "detected_features.csv"
    csv_path.write_text(_detected_features_csv_text(), encoding="utf-8")

    features = load_detected_features_csv(csv_path)

    assert len(features) == 1
    feature = features[0]
    assert isinstance(feature, DetectedFeature)
    assert feature.sample_stem == "Sample_1"
    assert feature.feature_id == "base-1"
    assert feature.source_feature_row_id is None
    assert feature.mz == 500.0
    assert feature.rt_apex_min == 10.00
    assert feature.height == 1000.0
    assert feature.area == 500.0
    assert feature.trace_quality == "GOOD"
    assert feature.shape_score == 0.9
    assert feature.source == "targeted"


@pytest.mark.parametrize(
    "contents",
    (
        "",
        "sample_stem,feature_id\r\n",
    ),
)
def test_load_detected_features_csv_rejects_missing_required_headers(
    tmp_path: Path,
    contents: str,
) -> None:
    csv_path = tmp_path / "detected_features.csv"
    csv_path.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match="missing required columns"):
        load_detected_features_csv(csv_path)


def test_load_detected_features_csv_accepts_header_only_contract(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "detected_features.csv"
    csv_path.write_text(
        ",".join(DETECTED_FEATURES_COLUMNS) + "\r\n",
        encoding="utf-8",
    )

    assert load_detected_features_csv(csv_path) == ()


def test_detected_feature_identity_is_sample_local_and_duplicate_fails_closed(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "detected_features.csv"
    header = ",".join(DETECTED_FEATURES_COLUMNS)
    row_a = "Sample_A,F1,,500.0,10.0,,,1000,,GOOD,,targeted"
    row_b = "Sample_B,F1,,600.0,11.0,,,900,,GOOD,,targeted"
    csv_path.write_text(
        f"{header}\r\n{row_a}\r\n{row_b}\r\n",
        encoding="utf-8",
    )

    features = load_detected_features_csv(csv_path)

    assert [(feature.sample_stem, feature.feature_id) for feature in features] == [
        ("Sample_A", "F1"),
        ("Sample_B", "F1"),
    ]

    csv_path.write_text(
        f"{header}\r\n{row_a}\r\n{row_a}\r\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate detected feature identity"):
        load_detected_features_csv(csv_path)


def test_machine_csv_float_fields_round_trip_without_six_digit_rounding(
    tmp_path: Path,
) -> None:
    precise_mz = 1537.5390625
    feature = DetectedFeature(
        sample_stem="Sample_1",
        feature_id="F1",
        source_feature_row_id=None,
        mz=precise_mz,
        rt_apex_min=13.1234567890123,
        rt_start_min=None,
        rt_end_min=None,
        height=1000.0,
        area=None,
        trace_quality="GOOD",
        shape_score=None,
        source="targeted",
    )
    detected_path = write_detected_features_csv(
        tmp_path / "detected_features.csv",
        [feature],
    )
    candidate_path = write_pt_pattern_candidates_csv(
        tmp_path / "pt_pattern_candidates.csv",
        [_pt_candidate(base_mz=precise_mz)],
    )

    detected_row = _read_csv(detected_path)[0]
    candidate_row = _read_csv(candidate_path)[0]
    assert detected_row["mz"] == repr(precise_mz)
    assert candidate_row["base_mz"] == repr(precise_mz)
    assert float(detected_row["mz"]) == precise_mz
    assert float(candidate_row["base_mz"]) == precise_mz


def test_load_detected_features_csv_requires_non_blank_required_fields(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "detected_features.csv"
    header = ",".join(DETECTED_FEATURES_COLUMNS)
    bad_row = ",base-1,,500.0,10.00,,,1000.0,,GOOD,,targeted"
    csv_path.write_text(f"{header}\r\n{bad_row}\r\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_detected_features_csv(csv_path)


def test_load_detected_features_csv_requires_positive_height(tmp_path: Path) -> None:
    csv_path = tmp_path / "detected_features.csv"
    header = ",".join(DETECTED_FEATURES_COLUMNS)
    bad_row = "Sample_1,base-1,,500.0,10.00,,,0.0,,GOOD,,targeted"
    csv_path.write_text(f"{header}\r\n{bad_row}\r\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_detected_features_csv(csv_path)


def test_load_detected_features_csv_preserves_source_feature_row_id_provenance(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "detected_features.csv"
    header = ",".join(DETECTED_FEATURES_COLUMNS)
    row = (
        "Sample_1,base-1,Sample_1|DNA_dR|258.108512|7.841234,500.0,10.00,,,"
        "1000.0,,GOOD,,cid_nl"
    )
    csv_path.write_text(f"{header}\r\n{row}\r\n", encoding="utf-8")

    features = load_detected_features_csv(csv_path)

    assert features[0].source_feature_row_id == (
        "Sample_1|DNA_dR|258.108512|7.841234"
    )
    assert comparable_cid_nl_identity(features[0]) is None


def test_pt_candidate_csv_header_order_matches_public_contract(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "pt_pattern_candidates.csv"

    write_pt_pattern_candidates_csv(output_path, [_pt_candidate()])

    with output_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        assert next(reader) == list(PT_CANDIDATE_COLUMNS)


def test_pt_candidate_csv_writes_both_height_and_area_ratios(tmp_path: Path) -> None:
    output_path = tmp_path / "pt_pattern_candidates.csv"

    write_pt_pattern_candidates_csv(output_path, [_pt_candidate()])

    row = _read_csv(output_path)[0]
    assert row["pt_plus_1_height_ratio_to_base"] == "0.8"
    assert row["pt_plus_1_area_ratio_to_base"] == "0.8"
    assert row["pt_plus_2_height_ratio_to_base"] == "0.2"
    assert row["pt_plus_2_area_ratio_to_base"] == "0.2"


def test_pt_candidate_csv_writes_blank_area_ratio_without_failing(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "pt_pattern_candidates.csv"
    candidate = _pt_candidate(
        base_area=None,
        pt_plus_1_area_ratio_to_base=None,
        pt_plus_2_area_ratio_to_base=None,
        pt_plus_4_area_ratio_to_base=None,
    )

    write_pt_pattern_candidates_csv(output_path, [candidate])

    row = _read_csv(output_path)[0]
    assert row["base_area"] == ""
    assert row["pt_plus_1_height_ratio_to_base"] == "0.8"
    assert row["pt_plus_1_area_ratio_to_base"] == ""


def test_pt_review_csv_header_order_matches_public_contract(tmp_path: Path) -> None:
    output_path = tmp_path / "pt_pattern_review.csv"

    write_pt_pattern_review_csv(output_path, [_pt_candidate()])

    with output_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        assert next(reader) == list(PT_REVIEW_COLUMNS)


def test_pt_evidence_csv_header_order_matches_public_contract(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "pt_pattern_evidence.csv"

    write_pt_pattern_evidence_csv(output_path, [_pt_evidence_row()])

    with output_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        assert next(reader) == list(PT_EVIDENCE_COLUMNS)


def test_pt_evidence_csv_writes_trace_rule_and_authority_fields(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "pt_pattern_evidence.csv"

    write_pt_pattern_evidence_csv(output_path, [_pt_evidence_row()])

    row = _read_csv(output_path)[0]
    assert row["trace_rule_call"] == "accept"
    assert row["trace_detection_status"] == "detected"
    assert row["matrix_write_authority"] == "FALSE"
    assert row["counted_detection_authority"] == "FALSE"
    assert row["export_activation_authority"] == "FALSE"


def test_pt_review_csv_uses_same_candidate_ordering_as_candidates_csv(
    tmp_path: Path,
) -> None:
    candidates = [
        _pt_candidate(candidate_id="low", review_priority="LOW", evidence_score=10),
        _pt_candidate(candidate_id="high", review_priority="HIGH", evidence_score=90),
    ]

    write_pt_pattern_candidates_csv(
        tmp_path / "pt_pattern_candidates.csv", candidates
    )
    write_pt_pattern_review_csv(tmp_path / "pt_pattern_review.csv", candidates)

    candidate_order = [
        row["candidate_id"]
        for row in _read_csv(tmp_path / "pt_pattern_candidates.csv")
    ]
    review_order = [
        row["candidate_id"] for row in _read_csv(tmp_path / "pt_pattern_review.csv")
    ]
    assert candidate_order == review_order


def test_pt_sidecar_json_shape_and_schema_version_for_pt_pattern_only(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "pt_pattern_sidecar.json"
    result = DeltaMassDiscoveryResult(
        method_label="pt_pattern",
        pattern=PT_PATTERN,
        detected_features_csv="detected_features.csv",
        external_feature_table_tsv=None,
        mzmine_feature_table_csv=None,
        cid_nl_candidates_csv=None,
        candidates=[_pt_candidate()],
        evidence_rows=[_pt_evidence_row()],
    )

    write_pt_pattern_sidecar_json(output_path, result=result)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "pt_pattern_sidecar.v3"
    assert payload["method_label"] == "pt_pattern"
    assert payload["pattern"] == {
        "pattern_id": "pt",
        "charge": 1,
        "polarity": "positive",
        "positions": [
            {"label": "base", "delta_da": 0.0},
            {"label": "pt_plus_1", "delta_da": 1.002111},
            {"label": "pt_plus_2", "delta_da": 2.002271},
            {"label": "pt_plus_4", "delta_da": 4.005213},
        ],
        "mz_tolerance_ppm": 20.0,
        "rt_tolerance_sec": 30.0,
    }
    assert payload["inputs"] == {
        "detected_features_csv": "detected_features.csv",
        "external_feature_table_tsv": None,
        "mzmine_feature_table_csv": None,
        "cid_nl_candidates_csv": None,
        "trace_summary_csv": None,
    }
    assert payload["summary"] == {
        "candidate_count": 1,
        "pt_only_candidate_count": 1,
        "cid_nl_supported_candidate_count": 0,
        "evidence_row_count": 1,
        "trace_rule_call_counts": {"accept": 1},
        "trace_evidence_status_counts": {"evaluated": 1},
        "trace_detection_status_counts": {"detected": 1},
        "trace_rule_reason_counts": {"full_pattern_close_apex_shape_overlap": 1},
    }
    assert len(payload["candidates"]) == 1


def test_pt_sidecar_json_records_cid_nl_candidates_csv_for_composite_method(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "pt_pattern_sidecar.json"
    composite_candidate = _pt_candidate(
        method_label="pt_pattern+cid_nl_dr",
        candidate_state="pt_pattern_cid_nl_supported",
        cid_nl_base_status="matched",
        cid_nl_base_candidate_id="TumorBC2312_DNA#101@mz258.1085_p142.0611",
        cid_nl_base_join_mode="same_feature_id",
    )

    result = DeltaMassDiscoveryResult(
        method_label="pt_pattern+cid_nl_dr",
        pattern=PT_PATTERN,
        detected_features_csv="detected_features.csv",
        external_feature_table_tsv="external_features.tsv",
        mzmine_feature_table_csv=None,
        cid_nl_candidates_csv="discovery_candidates.csv",
        candidates=[composite_candidate],
        trace_summary_csv="trace_summary.csv",
        evidence_rows=[
            _pt_evidence_row(
                method_label="pt_pattern+cid_nl_dr",
                candidate_state="pt_pattern_cid_nl_supported",
                trace_rule_call="reject",
                trace_detection_status="non_detected",
                trace_rule_reason="pearson_below_threshold",
            )
        ],
    )

    write_pt_pattern_sidecar_json(output_path, result=result)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["method_label"] == "pt_pattern+cid_nl_dr"
    assert payload["inputs"]["cid_nl_candidates_csv"] == "discovery_candidates.csv"
    assert payload["inputs"]["external_feature_table_tsv"] == "external_features.tsv"
    assert payload["inputs"]["mzmine_feature_table_csv"] is None
    assert payload["inputs"]["cid_nl_candidates_csv"] is not None
    assert payload["inputs"]["trace_summary_csv"] == "trace_summary.csv"
    assert payload["summary"] == {
        "candidate_count": 1,
        "pt_only_candidate_count": 0,
        "cid_nl_supported_candidate_count": 1,
        "evidence_row_count": 1,
        "trace_rule_call_counts": {"reject": 1},
        "trace_evidence_status_counts": {"evaluated": 1},
        "trace_detection_status_counts": {"non_detected": 1},
        "trace_rule_reason_counts": {"pearson_below_threshold": 1},
    }


def test_pt_sidecar_json_writes_empty_candidates_summary_for_no_matches(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "pt_pattern_sidecar.json"
    result = DeltaMassDiscoveryResult(
        method_label="pt_pattern",
        pattern=PT_PATTERN,
        detected_features_csv="detected_features.csv",
        external_feature_table_tsv=None,
        mzmine_feature_table_csv=None,
        cid_nl_candidates_csv=None,
        candidates=[],
    )

    write_pt_pattern_sidecar_json(output_path, result=result)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["summary"] == {
        "candidate_count": 0,
        "pt_only_candidate_count": 0,
        "cid_nl_supported_candidate_count": 0,
        "evidence_row_count": 0,
        "trace_rule_call_counts": {},
        "trace_evidence_status_counts": {},
        "trace_detection_status_counts": {},
        "trace_rule_reason_counts": {},
    }
    assert payload["candidates"] == []


def test_load_detected_features_csv_rejects_non_finite_required_field(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "detected_features.csv"
    header = ",".join(DETECTED_FEATURES_COLUMNS)
    bad_row = "Sample_1,base-1,,nan,10.00,,,1000.0,,GOOD,,targeted"
    csv_path.write_text(f"{header}\r\n{bad_row}\r\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_detected_features_csv(csv_path)


def test_load_detected_features_csv_rejects_non_finite_optional_field(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "detected_features.csv"
    header = ",".join(DETECTED_FEATURES_COLUMNS)
    bad_row = "Sample_1,base-1,,500.0,10.00,,,1000.0,inf,GOOD,,targeted"
    csv_path.write_text(f"{header}\r\n{bad_row}\r\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_detected_features_csv(csv_path)


def test_pt_candidate_csv_writes_header_only_when_no_candidates(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "pt_pattern_candidates.csv"

    write_pt_pattern_candidates_csv(output_path, [])

    with output_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        rows = list(reader)
    assert rows == [list(PT_CANDIDATE_COLUMNS)]


def test_pt_review_csv_writes_header_only_when_no_candidates(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "pt_pattern_review.csv"

    write_pt_pattern_review_csv(output_path, [])

    with output_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        rows = list(reader)
    assert rows == [list(PT_REVIEW_COLUMNS)]


def test_pt_evidence_csv_writes_header_only_when_no_rows(tmp_path: Path) -> None:
    output_path = tmp_path / "pt_pattern_evidence.csv"

    write_pt_pattern_evidence_csv(output_path, [])

    with output_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        rows = list(reader)
    assert rows == [list(PT_EVIDENCE_COLUMNS)]


def test_pt_candidate_csv_creates_parent_directories(tmp_path: Path) -> None:
    output_path = tmp_path / "nested" / "dir" / "pt_pattern_candidates.csv"

    write_pt_pattern_candidates_csv(output_path, [_pt_candidate()])

    assert output_path.exists()


def test_pt_candidate_csv_fails_loudly_on_unexpected_extra_field(
    tmp_path: Path,
) -> None:
    """Extra row fields must fail the shared writer API (extrasaction='raise'),
    not only a test-local renderer, per the Task 3 plan requirement."""
    from xic_extractor.discovery import delta_mass_csv

    output_path = tmp_path / "pt_pattern_candidates.csv"
    candidate = _pt_candidate()

    original_candidate_row = delta_mass_csv._candidate_row

    def _row_with_extra_field(candidate: PtPatternCandidate) -> dict[str, object]:
        row = original_candidate_row(candidate)
        row["unexpected_extra_field"] = "boom"
        return row

    monkeypatched = delta_mass_csv._candidate_row
    try:
        delta_mass_csv._candidate_row = _row_with_extra_field  # type: ignore[assignment]
        with pytest.raises(ValueError):
            write_pt_pattern_candidates_csv(output_path, [candidate])
    finally:
        delta_mass_csv._candidate_row = monkeypatched  # type: ignore[assignment]


def test_pt_review_csv_fails_loudly_on_unexpected_extra_field(
    tmp_path: Path,
) -> None:
    from xic_extractor.discovery import delta_mass_csv

    output_path = tmp_path / "pt_pattern_review.csv"
    candidate = _pt_candidate()

    original_review_row = delta_mass_csv._review_row

    def _row_with_extra_field(candidate: PtPatternCandidate) -> dict[str, object]:
        row = original_review_row(candidate)
        row["unexpected_extra_field"] = "boom"
        return row

    monkeypatched = delta_mass_csv._review_row
    try:
        delta_mass_csv._review_row = _row_with_extra_field  # type: ignore[assignment]
        with pytest.raises(ValueError):
            write_pt_pattern_review_csv(output_path, [candidate])
    finally:
        delta_mass_csv._review_row = monkeypatched  # type: ignore[assignment]


def test_pt_evidence_csv_fails_loudly_on_unexpected_extra_field(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "pt_pattern_evidence.csv"
    row = _pt_evidence_row(unexpected_extra_field="boom")

    with pytest.raises(ValueError):
        write_pt_pattern_evidence_csv(output_path, [row])


def test_pt_candidate_csv_preserves_machine_ids_and_escapes_prose(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "pt_pattern_candidates.csv"
    candidate = _pt_candidate(
        sample_stem="=cmd|'/c calc'!A1",
        candidate_id="=cmd|'/c calc'!A1|pt|base:+base-1",
        base_feature_id="+base-1",
        cid_nl_base_candidate_id="@cid-nl-1",
        reason="-suspicious reason",
        pt_plus_1_feature_id="@plus1-1",
        pt_plus_2_feature_id="+plus2-1",
        pt_plus_4_feature_id="=plus4-1",
    )

    write_pt_pattern_candidates_csv(output_path, [candidate])

    row = _read_csv(output_path)[0]
    assert row["sample_stem"] == "=cmd|'/c calc'!A1"
    assert row["candidate_id"] == "=cmd|'/c calc'!A1|pt|base:+base-1"
    assert row["base_feature_id"] == "+base-1"
    assert row["cid_nl_base_candidate_id"] == "@cid-nl-1"
    assert row["reason"] == "'-suspicious reason"
    assert row["pt_plus_1_feature_id"] == "@plus1-1"
    assert row["pt_plus_2_feature_id"] == "+plus2-1"
    assert row["pt_plus_4_feature_id"] == "=plus4-1"

    loaded = load_candidate_rows(output_path, limit=1)
    validate_candidate_sample_binding(
        loaded,
        expected_sample_stem="=cmd|'/c calc'!A1",
    )
    assert loaded[0]["candidate_id"] == candidate.candidate_id


def test_pt_review_csv_escapes_formula_like_text_consistently(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "pt_pattern_review.csv"
    candidate = _pt_candidate(
        sample_stem="=cmd|'/c calc'!A1",
        candidate_id="+candidate-1",
        reason="+suspicious reason",
    )

    write_pt_pattern_review_csv(output_path, [candidate])

    row = _read_csv(output_path)[0]
    assert row["sample_stem"] == "'=cmd|'/c calc'!A1"
    assert row["candidate_id"] == "'+candidate-1"
    assert row["reason"] == "'+suspicious reason"


def test_pt_evidence_csv_escapes_formula_like_text_consistently(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "pt_pattern_evidence.csv"

    write_pt_pattern_evidence_csv(
        output_path,
        [
            _pt_evidence_row(
                sample_stem="=cmd|'/c calc'!A1",
                candidate_id="+candidate-1",
                trace_rule_reason="-suspicious reason",
            )
        ],
    )

    row = _read_csv(output_path)[0]
    assert row["sample_stem"] == "=cmd|'/c calc'!A1"
    assert row["candidate_id"] == "+candidate-1"
    assert row["trace_rule_reason"] == "'-suspicious reason"


def test_pt_candidate_csv_preserves_literal_leading_apostrophe_identity(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "pt_pattern_candidates.csv"
    candidate = _pt_candidate(
        sample_stem="'=literal-sample",
        candidate_id="'=literal-candidate",
    )

    write_pt_pattern_candidates_csv(output_path, [candidate])

    row = _read_csv(output_path)[0]
    assert row["sample_stem"] == "'=literal-sample"
    assert row["candidate_id"] == "'=literal-candidate"
