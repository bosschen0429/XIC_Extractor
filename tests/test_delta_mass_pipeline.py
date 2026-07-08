"""Contract tests for the Pt pattern discovery pipeline.

Covers ``run_pt_pattern_discovery()`` from
``xic_extractor.discovery.delta_mass_pipeline``: end-to-end from a Detected
Features CSV (and optional CID-NL(dR) candidates CSV for the composite
strategy) to ``pt_pattern_candidates.csv``, ``pt_pattern_review.csv``,
``pt_pattern_evidence.csv``, and ``pt_pattern_sidecar.json``.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from xic_extractor.discovery.delta_mass_patterns import (
    match_delta_mass_pattern as real_match_delta_mass_pattern,
)
from xic_extractor.discovery.delta_mass_pipeline import (
    DeltaMassDiscoveryOutputs,
    run_pt_pattern_discovery,
)
from xic_extractor.discovery.pt_pattern_trace_evidence import SUMMARY_COLUMNS

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


def _write_external_feature_table(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = (
        "external_tool",
        "external_run_id",
        "external_tool_version",
        "external_adapter_version",
        "sample_id",
        "feature_id",
        "source_feature_row_id",
        "mz",
        "rt",
        "rt_start_min",
        "rt_end_min",
        "area_or_intensity",
        "area_or_intensity_semantics",
        "area",
        "trace_quality",
        "shape_score",
        "mz_tolerance_unit",
        "rt_tolerance_unit",
        "duplicate_feature_policy",
        "missing_value_policy",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for row in rows:
            source_row = {
                "external_tool": "MZmine",
                "external_run_id": "run1",
                "external_tool_version": "4.9.14",
                "external_adapter_version": "contract_v1",
                "mz_tolerance_unit": "ppm",
                "rt_tolerance_unit": "minutes",
                "duplicate_feature_policy": "source_unique",
                "missing_value_policy": "blank_is_missing",
                "area_or_intensity_semantics": "height",
                **row,
            }
            writer.writerow(
                {column: source_row.get(column, "") for column in fieldnames}
            )


def _write_mzmine_wide_feature_table(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = (
        "row ID",
        "row m/z",
        "row retention time",
        "Sample_A.mzML Peak area",
        "Sample_B.mzML Peak area",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in fieldnames})


def _write_trace_summary_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in SUMMARY_COLUMNS})


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
            "mz": base_mz + 1.002111,
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
            "mz": base_mz + 2.002271,
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
            "mz": base_mz + 4.005213,
            "rt_apex_min": 10.003,
            "height": 50.0,
            "area": 25.0,
            "trace_quality": "GOOD",
            "shape_score": 0.9,
            "source": "targeted",
        },
    ]


def test_run_pt_pattern_discovery_writes_pt_only_outputs(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv, _full_pattern_rows())
    output_dir = tmp_path / "out"

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=output_dir,
        preset_name="pt_pattern",
        cid_nl_candidates_csv=None,
    )

    assert isinstance(outputs, DeltaMassDiscoveryOutputs)
    assert outputs.candidates_csv == output_dir / "pt_pattern_candidates.csv"
    assert outputs.review_csv == output_dir / "pt_pattern_review.csv"
    assert outputs.evidence_csv == output_dir / "pt_pattern_evidence.csv"
    assert outputs.sidecar_json == output_dir / "pt_pattern_sidecar.json"
    assert outputs.candidates_csv.exists()
    assert outputs.review_csv.exists()
    assert outputs.evidence_csv.exists()
    assert outputs.sidecar_json.exists()

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["method_label"] == "pt_pattern"
    assert rows[0]["candidate_state"] == "pt_pattern_candidate"

    with outputs.evidence_csv.open(newline="", encoding="utf-8") as handle:
        evidence_rows = list(csv.DictReader(handle))
    assert len(evidence_rows) == 1
    assert evidence_rows[0]["scan_pattern_seed_status"] == "full_pattern_seeded"
    assert evidence_rows[0]["trace_rule_call"] == "not_evaluated"
    assert evidence_rows[0]["trace_detection_status"] == "not_evaluated"
    assert evidence_rows[0]["matrix_write_authority"] == "FALSE"


def test_run_pt_pattern_discovery_accepts_external_feature_table_source(
    tmp_path: Path,
) -> None:
    external_table = tmp_path / "external_features.tsv"
    external_rows = [
        {
            "sample_id": row["sample_stem"],
            "feature_id": row["feature_id"],
            "source_feature_row_id": row.get("source_feature_row_id", ""),
            "mz": row["mz"],
            "rt": row["rt_apex_min"],
            "rt_start_min": row.get("rt_start_min", ""),
            "rt_end_min": row.get("rt_end_min", ""),
            "area_or_intensity": row["height"],
            "area": row.get("area", ""),
            "trace_quality": row["trace_quality"],
            "shape_score": row.get("shape_score", ""),
        }
        for row in _full_pattern_rows()
    ]
    _write_external_feature_table(external_table, external_rows)
    output_dir = tmp_path / "out"

    outputs = run_pt_pattern_discovery(
        external_feature_table_tsv=external_table,
        output_dir=output_dir,
        preset_name="pt_pattern",
        cid_nl_candidates_csv=None,
    )

    prepared_csv = output_dir / "detected_features.csv"
    assert prepared_csv.exists()
    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1

    payload = json.loads(outputs.sidecar_json.read_text(encoding="utf-8"))
    assert payload["inputs"]["detected_features_csv"] == str(prepared_csv)
    assert payload["inputs"]["external_feature_table_tsv"] == str(external_table)
    assert payload["inputs"]["mzmine_feature_table_csv"] is None


def test_run_pt_pattern_discovery_accepts_mzmine_wide_feature_table_source(
    tmp_path: Path,
) -> None:
    mzmine_table = tmp_path / "mzmine_features.csv"
    base_mz = 500.0
    _write_mzmine_wide_feature_table(
        mzmine_table,
        [
            {
                "row ID": "1",
                "row m/z": base_mz,
                "row retention time": 10.000,
                "Sample_A.mzML Peak area": 1000.0,
                "Sample_B.mzML Peak area": 900.0,
            },
            {
                "row ID": "2",
                "row m/z": base_mz + 1.002111,
                "row retention time": 10.001,
                "Sample_A.mzML Peak area": 800.0,
                "Sample_B.mzML Peak area": 700.0,
            },
            {
                "row ID": "3",
                "row m/z": base_mz + 2.002271,
                "row retention time": 10.002,
                "Sample_A.mzML Peak area": 200.0,
                "Sample_B.mzML Peak area": 180.0,
            },
            {
                "row ID": "4",
                "row m/z": base_mz + 4.005213,
                "row retention time": 10.003,
                "Sample_A.mzML Peak area": 50.0,
                "Sample_B.mzML Peak area": 0,
            },
        ],
    )
    output_dir = tmp_path / "out"

    outputs = run_pt_pattern_discovery(
        mzmine_feature_table_csv=mzmine_table,
        output_dir=output_dir,
        preset_name="pt_pattern",
        cid_nl_candidates_csv=None,
    )

    prepared_csv = output_dir / "detected_features.csv"
    assert prepared_csv.exists()
    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["sample_stem"] == "Sample_A"
    assert rows[0]["base_area"] == "1000"
    assert rows[0]["local_signal_support_status"] == "feature_source_detected"
    assert rows[0]["evidence_tier"] == "B"
    assert rows[0]["evidence_score"] == "70"
    assert "feature source support only" in rows[0]["reason"]
    with outputs.review_csv.open(newline="", encoding="utf-8") as handle:
        review_rows = list(csv.DictReader(handle))
    assert "feature-source support only" in review_rows[0]["review_note"]

    payload = json.loads(outputs.sidecar_json.read_text(encoding="utf-8"))
    assert payload["inputs"]["detected_features_csv"] == str(prepared_csv)
    assert payload["inputs"]["external_feature_table_tsv"] is None
    assert payload["inputs"]["mzmine_feature_table_csv"] == str(mzmine_table)


def test_composite_mzmine_feature_source_uses_mz_rt_fallback(
    tmp_path: Path,
) -> None:
    mzmine_table = tmp_path / "mzmine_features.csv"
    base_mz = 500.0
    mzmine_table.write_text(
        "row ID,row m/z,row retention time,Sample_1.raw Peak area,\n"
        f"1,{base_mz},10.000,1000.0,\n"
        f"2,{base_mz + 1.002111},10.001,800.0,\n"
        f"3,{base_mz + 2.002271},10.002,200.0,\n"
        f"4,{base_mz + 4.005213},10.003,50.0,\n",
        encoding="utf-8",
    )
    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    cid_nl_candidates_csv.write_text(
        "ms1_feature_row_id,neutral_loss_tag,discovery_candidate_state,"
        "precursor_mz,ms1_apex_rt,evidence_score\n"
        "Sample_1|DNA_dR|500.000000|10.000000,DNA_dR,"
        "ms1_feature_nl_supported,500.0,10.000,90\n",
        encoding="utf-8",
    )

    outputs = run_pt_pattern_discovery(
        mzmine_feature_table_csv=mzmine_table,
        output_dir=tmp_path / "out",
        preset_name="pt_pattern_cid_nl_dr",
        cid_nl_candidates_csv=cid_nl_candidates_csv,
    )

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["sample_stem"] == "Sample_1"
    assert rows[0]["candidate_state"] == "pt_pattern_cid_nl_supported"
    assert rows[0]["cid_nl_base_join_mode"] == "mz_rt_fallback"


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
            preset_name="pt_pattern_cid_nl_dr",
            cid_nl_candidates_csv=None,
        )


def test_run_pt_pattern_discovery_rejects_non_pt_pattern_preset(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv, _full_pattern_rows())
    output_dir = tmp_path / "out"

    with pytest.raises(ValueError, match="pt_pattern"):
        run_pt_pattern_discovery(
            detected_features_csv=detected_features_csv,
            output_dir=output_dir,
            preset_name="dna_dr",
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
        "ms1_feature_row_id,neutral_loss_tag,discovery_candidate_state\n"
        "Sample_1|DNA_dR|258.108512|7.841234,DNA_dR,ms1_feature_nl_supported\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "out"

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=output_dir,
        preset_name="pt_pattern_cid_nl_dr",
        cid_nl_candidates_csv=cid_nl_candidates_csv,
    )

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows_out = list(csv.DictReader(handle))
    assert len(rows_out) == 1
    assert rows_out[0]["candidate_state"] == "pt_pattern_cid_nl_supported"
    assert rows_out[0]["cid_nl_base_status"] == "matched"
    assert rows_out[0]["cid_nl_base_join_mode"] == "same_feature_id"

    with outputs.evidence_csv.open(newline="", encoding="utf-8") as handle:
        evidence_rows = list(csv.DictReader(handle))
    assert evidence_rows[0]["candidate_state"] == "pt_pattern_cid_nl_supported"
    assert evidence_rows[0]["trace_rule_call"] == "not_evaluated"


def test_composite_uses_cid_nl_base_seeds_before_matching(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    rows = _full_pattern_rows()
    rows[0]["source_feature_row_id"] = "Sample_1|DNA_dR|258.108512|7.841234"
    # Decoy full pattern: Pt-only would match it, but composite seed-first must
    # not even offer this base to the matcher because CID-NL does not seed it.
    decoy_rows = _full_pattern_rows()
    for row in decoy_rows:
        row["mz"] = float(row["mz"]) + 200.0
        row["feature_id"] = f"decoy-{row['feature_id']}"
    rows.extend(decoy_rows)
    _write_detected_features_csv(detected_features_csv, rows)

    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    cid_nl_candidates_csv.write_text(
        "ms1_feature_row_id,neutral_loss_tag,discovery_candidate_state\n"
        "Sample_1|DNA_dR|258.108512|7.841234,DNA_dR,ms1_feature_nl_supported\n",
        encoding="utf-8",
    )
    seen_base_feature_ids: set[str] | None = None

    def _capturing_match_delta_mass_pattern(
        features: object,
        *,
        settings: object,
        base_feature_ids: set[str] | None = None,
    ) -> object:
        nonlocal seen_base_feature_ids
        seen_base_feature_ids = set(base_feature_ids or ())
        return real_match_delta_mass_pattern(
            features,  # type: ignore[arg-type]
            settings=settings,  # type: ignore[arg-type]
            base_feature_ids=base_feature_ids,
        )

    monkeypatch.setattr(
        "xic_extractor.discovery.delta_mass_pipeline.match_delta_mass_pattern",
        _capturing_match_delta_mass_pattern,
    )

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=tmp_path / "out",
        preset_name="pt_pattern_cid_nl_dr",
        cid_nl_candidates_csv=cid_nl_candidates_csv,
    )

    assert seen_base_feature_ids == {"base-1"}
    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows_out = list(csv.DictReader(handle))
    assert len(rows_out) == 1
    assert rows_out[0]["base_feature_id"] == "base-1"


def test_run_pt_pattern_discovery_sidecar_records_input_paths_as_provided_strings(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv, _full_pattern_rows())
    output_dir = tmp_path / "out"

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=output_dir,
        preset_name="pt_pattern",
        cid_nl_candidates_csv=None,
    )

    payload = json.loads(outputs.sidecar_json.read_text(encoding="utf-8"))
    assert payload["inputs"]["detected_features_csv"] == str(detected_features_csv)
    assert payload["inputs"]["external_feature_table_tsv"] is None
    assert payload["inputs"]["mzmine_feature_table_csv"] is None
    assert payload["inputs"]["cid_nl_candidates_csv"] is None
    assert payload["inputs"]["trace_summary_csv"] is None


def test_run_pt_pattern_discovery_applies_optional_trace_summary_evidence(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv, _full_pattern_rows())
    trace_summary_csv = tmp_path / "trace_summary.csv"
    _write_trace_summary_csv(
        trace_summary_csv,
        [
            {
                "schema_version": "pt_pattern_trace_diagnosis.v2",
                "sample_stem": "Sample_1",
                "candidate_id": (
                    "Sample_1|pt|base:base-1|mz:500.000000|rt:10.000000"
                ),
                "scan_pattern_seed_status": "full_pattern_seeded",
                "base_mz": 500.0,
                "base_rt_apex_min": 10.0,
                "non_base_positions_supported": 3,
                "max_abs_apex_delta_sec": 0.4,
                "min_pearson_r_to_base": 0.95,
                "min_active_overlap_fraction": 0.70,
                "min_shifted_pearson_r_to_base": 0.96,
                "max_abs_shape_shift_sec_to_base": 2.0,
                "trace_ambiguity_status": "review_required",
                "trace_ambiguity_flags": "shifted_only_support_present",
                "shifted_only_position_labels": "pt_plus_4",
                "trace_support_status": "supported",
                "combined_pt_pattern_status": "pt_pattern_detected_diagnostic",
                "reason": "supported",
            }
        ],
    )

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=tmp_path / "out",
        preset_name="pt_pattern",
        cid_nl_candidates_csv=None,
        trace_summary_csv=trace_summary_csv,
    )

    with outputs.evidence_csv.open(newline="", encoding="utf-8") as handle:
        evidence_rows = list(csv.DictReader(handle))
    assert len(evidence_rows) == 1
    assert evidence_rows[0]["trace_evidence_status"] == "evaluated"
    assert evidence_rows[0]["trace_rule_call"] == "accept"
    assert evidence_rows[0]["trace_detection_status"] == "detected"
    assert evidence_rows[0]["trace_rule_reason"] == (
        "full_pattern_close_apex_shape_overlap"
    )
    assert evidence_rows[0]["trace_ambiguity_status"] == "review_required"
    assert evidence_rows[0]["trace_ambiguity_flags"] == (
        "shifted_only_support_present"
    )
    assert evidence_rows[0]["shifted_only_position_labels"] == "pt_plus_4"

    payload = json.loads(outputs.sidecar_json.read_text(encoding="utf-8"))
    assert payload["inputs"]["trace_summary_csv"] == str(trace_summary_csv)
    assert payload["summary"]["trace_rule_call_counts"] == {"accept": 1}


def test_run_pt_pattern_discovery_ignores_review_only_orphan_nl_rows(
    tmp_path: Path,
) -> None:
    """Regression test: real ``discovery_candidates.csv`` files from the
    existing ``scripts.run_discovery`` CLI contain a mix of join-eligible
    rows (``ms1_feature_nl_supported``/``ms1_feature_nl_rescued``, with a
    populated ``ms1_feature_row_id``) and ``review_only_orphan_nl`` rows
    (blank ``ms1_feature_row_id`` by design, since they are not backed by
    any MS1 feature). The composite pipeline must not raise on the orphan
    rows and must simply exclude them from join consideration.
    """

    detected_features_csv = tmp_path / "detected_features.csv"
    rows = _full_pattern_rows()
    rows[0]["source_feature_row_id"] = "Sample_1|DNA_dR|258.108512|7.841234"
    _write_detected_features_csv(detected_features_csv, rows)

    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    cid_nl_candidates_csv.write_text(
        "ms1_feature_row_id,neutral_loss_tag,discovery_candidate_state\n"
        "Sample_1|DNA_dR|258.108512|7.841234,DNA_dR,ms1_feature_nl_supported\n"
        ",DNA_dR,review_only_orphan_nl\n"
        ",DNA_dR,review_only_orphan_nl\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "out"

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=output_dir,
        preset_name="pt_pattern_cid_nl_dr",
        cid_nl_candidates_csv=cid_nl_candidates_csv,
    )

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows_out = list(csv.DictReader(handle))
    assert len(rows_out) == 1
    assert rows_out[0]["candidate_state"] == "pt_pattern_cid_nl_supported"
    assert rows_out[0]["cid_nl_base_status"] == "matched"
    assert rows_out[0]["cid_nl_base_join_mode"] == "same_feature_id"


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
        preset_name="pt_pattern",
        cid_nl_candidates_csv=None,
    )

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert len(rows) == 1  # header only, no candidate rows

    payload = json.loads(outputs.sidecar_json.read_text(encoding="utf-8"))
    assert payload["summary"]["candidate_count"] == 0
