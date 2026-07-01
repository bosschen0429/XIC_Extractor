"""Contract tests for the Pt pattern discovery pipeline (Task 1 RED phase).

Covers ``run_pt_pattern_discovery()`` from
``xic_extractor.discovery.delta_mass_pipeline``: end-to-end from a Detected
Features CSV (and optional CID-NL(dR) candidates CSV for the composite
strategy) to ``pt_pattern_candidates.csv``, ``pt_pattern_review.csv``, and
``pt_pattern_sidecar.json``. Expected to fail on import until Task 5
implements the real module.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from xic_extractor.discovery.delta_mass_pipeline import (
    PtPatternRunOutputs,
    run_pt_pattern_discovery,
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


def _write_detected_features_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DETECTED_FEATURES_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {column: row.get(column, "") for column in DETECTED_FEATURES_COLUMNS}
            )


def _full_pattern_rows(sample_stem: str = "Sample_1") -> list[dict[str, object]]:
    base_mz = 500.0
    return [
        {
            "sample_stem": sample_stem,
            "feature_id": "base-1",
            "mz": base_mz,
            "rt_apex_min": 10.000,
            "height": 1000.0,
            "area": 500.0,
            "trace_quality": "GOOD",
            "shape_score": 0.9,
            "source": "targeted",
        },
        {
            "sample_stem": sample_stem,
            "feature_id": "plus1-1",
            "mz": base_mz + 1.0021,
            "rt_apex_min": 10.001,
            "height": 800.0,
            "area": 400.0,
            "trace_quality": "GOOD",
            "shape_score": 0.9,
            "source": "targeted",
        },
        {
            "sample_stem": sample_stem,
            "feature_id": "plus2-1",
            "mz": base_mz + 2.0023,
            "rt_apex_min": 10.002,
            "height": 200.0,
            "area": 100.0,
            "trace_quality": "GOOD",
            "shape_score": 0.9,
            "source": "targeted",
        },
        {
            "sample_stem": sample_stem,
            "feature_id": "plus4-1",
            "mz": base_mz + 4.0052,
            "rt_apex_min": 10.003,
            "height": 50.0,
            "area": 25.0,
            "trace_quality": "GOOD",
            "shape_score": 0.9,
            "source": "targeted",
        },
    ]


def test_run_pt_pattern_discovery_writes_three_pt_only_outputs(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv, _full_pattern_rows())
    output_dir = tmp_path / "out"

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=output_dir,
        method_label="pt_pattern",
        cid_nl_candidates_csv=None,
    )

    assert isinstance(outputs, PtPatternRunOutputs)
    assert outputs.candidates_csv == output_dir / "pt_pattern_candidates.csv"
    assert outputs.review_csv == output_dir / "pt_pattern_review.csv"
    assert outputs.sidecar_json == output_dir / "pt_pattern_sidecar.json"
    assert outputs.candidates_csv.exists()
    assert outputs.review_csv.exists()
    assert outputs.sidecar_json.exists()

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["method_label"] == "pt_pattern"
    assert rows[0]["candidate_state"] == "pt_pattern_detected"


def test_run_pt_pattern_discovery_requires_cid_nl_csv_for_composite_method(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv, _full_pattern_rows())
    output_dir = tmp_path / "out"

    with pytest.raises(ValueError, match="cid_nl_candidates_csv"):
        run_pt_pattern_discovery(
            detected_features_csv=detected_features_csv,
            output_dir=output_dir,
            method_label="pt_pattern_cid_nl_dr",
            cid_nl_candidates_csv=None,
        )


def test_run_pt_pattern_discovery_composite_marks_matched_base_as_supported(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    rows = _full_pattern_rows()
    rows[0]["source_feature_row_id"] = "Sample_1|DNA_dR|258.108512|7.841234"
    _write_detected_features_csv(detected_features_csv, rows)

    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    cid_nl_candidates_csv.write_text(
        "ms1_feature_row_id\nSample_1|DNA_dR|258.108512|7.841234\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "out"

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=output_dir,
        method_label="pt_pattern_cid_nl_dr",
        cid_nl_candidates_csv=cid_nl_candidates_csv,
    )

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows_out = list(csv.DictReader(handle))
    assert len(rows_out) == 1
    assert rows_out[0]["candidate_state"] == "pt_pattern_cid_nl_supported"
    assert rows_out[0]["cid_nl_base_status"] == "matched"
    assert rows_out[0]["cid_nl_base_join_mode"] == "same_feature_id"


def test_run_pt_pattern_discovery_sidecar_records_input_paths_as_provided_strings(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv, _full_pattern_rows())
    output_dir = tmp_path / "out"

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=output_dir,
        method_label="pt_pattern",
        cid_nl_candidates_csv=None,
    )

    payload = json.loads(outputs.sidecar_json.read_text(encoding="utf-8"))
    assert payload["inputs"]["detected_features_csv"] == str(detected_features_csv)
    assert payload["inputs"]["cid_nl_candidates_csv"] is None


def test_run_pt_pattern_discovery_writes_header_only_outputs_when_no_pattern_found(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    partial_rows = _full_pattern_rows()[:2]  # base + plus1 only, missing plus2/plus4
    _write_detected_features_csv(detected_features_csv, partial_rows)
    output_dir = tmp_path / "out"

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=output_dir,
        method_label="pt_pattern",
        cid_nl_candidates_csv=None,
    )

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert len(rows) == 1  # header only, no candidate rows

    payload = json.loads(outputs.sidecar_json.read_text(encoding="utf-8"))
    assert payload["summary"]["candidate_count"] == 0
