"""Contract tests for Pt pattern CSV/JSON I/O (Task 1 RED phase).

Covers ``load_detected_features_csv()``, ``write_pt_pattern_candidates_csv()``,
``write_pt_pattern_review_csv()``, and ``write_pt_pattern_sidecar_json()`` from
``xic_extractor.discovery.delta_mass_csv``. Header order and field names are
copied verbatim from the Public Contract Details section of the implementation
plan (Detected Features CSV v1, Pt Candidate CSV v1, Pt Review CSV v1, Pt
Sidecar JSON v1). These tests are expected to fail on import until Task 3
implements the real module.
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
    write_pt_pattern_review_csv,
    write_pt_pattern_sidecar_json,
)
from xic_extractor.discovery.delta_mass_patterns import PT_PATTERN, PtPatternCandidate
from xic_extractor.discovery.detected_features import DetectedFeature

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
        "candidate_state": "pt_pattern_detected",
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
    return PtPatternCandidate(**values)  # type: ignore[arg-type]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


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


def test_load_detected_features_csv_preserves_source_feature_row_id_for_joins(
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
        cid_nl_candidates_csv=None,
        candidates=[_pt_candidate()],
    )

    write_pt_pattern_sidecar_json(output_path, result=result)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "pt_pattern_sidecar.v1"
    assert payload["method_label"] == "pt_pattern"
    assert payload["pattern"] == {
        "pattern_id": "pt",
        "charge": 1,
        "polarity": "positive",
        "positions": [
            {"label": "base", "delta_da": 0.0},
            {"label": "pt_plus_1", "delta_da": 1.0021},
            {"label": "pt_plus_2", "delta_da": 2.0023},
            {"label": "pt_plus_4", "delta_da": 4.0052},
        ],
        "mz_tolerance_ppm": 20.0,
        "rt_tolerance_sec": 30.0,
    }
    assert payload["inputs"] == {
        "detected_features_csv": "detected_features.csv",
        "cid_nl_candidates_csv": None,
    }
    assert payload["summary"] == {
        "candidate_count": 1,
        "pt_only_candidate_count": 1,
        "cid_nl_supported_candidate_count": 0,
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
        method_label="pt_pattern_cid_nl_dr",
        pattern=PT_PATTERN,
        detected_features_csv="detected_features.csv",
        cid_nl_candidates_csv="discovery_candidates.csv",
        candidates=[composite_candidate],
    )

    write_pt_pattern_sidecar_json(output_path, result=result)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["method_label"] == "pt_pattern_cid_nl_dr"
    assert payload["inputs"]["cid_nl_candidates_csv"] == "discovery_candidates.csv"
    assert payload["inputs"]["cid_nl_candidates_csv"] is not None
    assert payload["summary"] == {
        "candidate_count": 1,
        "pt_only_candidate_count": 0,
        "cid_nl_supported_candidate_count": 1,
    }


def test_pt_sidecar_json_writes_empty_candidates_summary_for_no_matches(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "pt_pattern_sidecar.json"
    result = DeltaMassDiscoveryResult(
        method_label="pt_pattern",
        pattern=PT_PATTERN,
        detected_features_csv="detected_features.csv",
        cid_nl_candidates_csv=None,
        candidates=[],
    )

    write_pt_pattern_sidecar_json(output_path, result=result)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["summary"] == {
        "candidate_count": 0,
        "pt_only_candidate_count": 0,
        "cid_nl_supported_candidate_count": 0,
    }
    assert payload["candidates"] == []
