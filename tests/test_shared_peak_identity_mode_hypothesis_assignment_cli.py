from __future__ import annotations

import csv
from pathlib import Path

from tools.diagnostics.build_mode_hypothesis_assignment import main


def test_mode_hypothesis_assignment_cli_writes_peak_selection(tmp_path: Path) -> None:
    rt_mode = tmp_path / "shared_peak_identity_rt_mode_evidence.tsv"
    candidate_ms2 = tmp_path / "shared_peak_identity_candidate_ms2_pattern.tsv"
    ms1 = tmp_path / "shared_peak_identity_ms1_pattern.tsv"
    qc = tmp_path / "shared_peak_identity_qc_ms1_reference.tsv"
    rt_drift = tmp_path / "shared_peak_identity_matrix_rt_drift.tsv"
    output = tmp_path / "shared_peak_identity_peak_hypothesis_selection.tsv"

    _write_tsv(
        rt_mode,
        [
            _rt_mode_row("FAM_TYPED", "S_TAG", mode_id="irt_blue_core"),
            _rt_mode_row("FAM_TYPED", "S_DDA", mode_id="irt_blue_core"),
        ],
    )
    _write_tsv(
        candidate_ms2,
        [
            _candidate_ms2_row("FAM_TYPED", "S_TAG", tag_observed=True),
            _candidate_ms2_row("FAM_TYPED", "S_DDA", dda_missing=True),
        ],
    )
    _write_tsv(ms1, [_ms1_row("FAM_TYPED", "S_TAG"), _ms1_row("FAM_TYPED", "S_DDA")])
    _write_tsv(qc, [_qc_row("FAM_TYPED", "S_TAG"), _qc_row("FAM_TYPED", "S_DDA")])
    _write_tsv(
        rt_drift,
        [_rt_drift_row("FAM_TYPED", "S_TAG"), _rt_drift_row("FAM_TYPED", "S_DDA")],
    )

    assert (
        main(
            [
                "--rt-mode-evidence-tsv",
                str(rt_mode),
                "--candidate-ms2-pattern-evidence-tsv",
                str(candidate_ms2),
                "--ms1-pattern-coherence-evidence-tsv",
                str(ms1),
                "--qc-ms1-pattern-reference-evidence-tsv",
                str(qc),
                "--matrix-rt-drift-policy-tsv",
                str(rt_drift),
                "--output-tsv",
                str(output),
            ]
        )
        == 0
    )

    rows = _read_tsv(output)
    assert len(rows) == 2
    assert {row["peak_hypothesis_id"] for row in rows} == {
        "FAM_TYPED::irt_blue_core"
    }
    assert all(
        row["peak_hypothesis_status"] == "product_candidate_core" for row in rows
    )


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = tuple(dict.fromkeys(field for row in rows for field in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _rt_mode_row(
    family_id: str,
    sample_stem: str,
    *,
    mode_id: str,
) -> dict[str, str]:
    return {
        "rt_mode_evidence_schema_version": "shared_peak_identity_rt_mode_evidence",
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "rt_mode_status": "mode_supported",
        "rt_mode_evidence_level": "irt_selected_apex_modes",
        "selected_mode_id": mode_id,
        "selected_mode_role": "tag_bearing_core",
        "selected_mode_tag_status": "tag_supported",
        "family_mode_class": "tag_backed_core_with_outlier_modes",
        "family_mode_count": "2",
        "tag_bearing_mode_count": "1",
        "selected_mode_cell_count": "2",
        "selected_mode_sample_type_counts": "Tumor:1",
        "selected_mode_status_counts": "rescued:1",
        "raw_selected_rt": "7.93",
        "normalized_selected_rt": "7.81",
        "selected_mode_raw_rt_range_min": "0.1",
        "selected_mode_normalized_rt_range_min": "0.08",
        "family_raw_rt_range_min": "2.1",
        "family_normalized_rt_range_min": "1.9",
        "reason": "unit_test_rt_mode",
        "diagnostic_only": "TRUE",
    }


def _candidate_ms2_row(
    family_id: str,
    sample_stem: str,
    *,
    tag_observed: bool = False,
    dda_missing: bool = False,
) -> dict[str, str]:
    if tag_observed:
        return {
            "feature_family_id": family_id,
            "sample_stem": sample_stem,
            "candidate_ms2_pattern_status": "supportive",
            "candidate_ms2_evidence_level": "sample_candidate_aligned",
            "raw_ms2_strict_nl_scan_count": "1",
            "matched_neutral_loss_count": "1",
            "source_matched_tag_count": "1",
            "diagnostic_only": "TRUE",
        }
    if dda_missing:
        return {
            "feature_family_id": family_id,
            "sample_stem": sample_stem,
            "candidate_ms2_pattern_status": "not_observed",
            "candidate_ms2_evidence_level": "sample_boundary_no_observed_pattern",
            "raw_ms2_trigger_scan_count": "3",
            "raw_ms2_strict_nl_scan_count": "0",
            "raw_ms2_trace_strength": "strong",
            "diagnostic_only": "TRUE",
        }
    raise AssertionError("test fixture needs tag or DDA evidence")


def _ms1_row(family_id: str, sample_stem: str) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "ms1_pattern_status": "supportive",
        "ms1_pattern_evidence_level": "trace_constellation",
        "apex_coherence_sec": "1",
        "boundary_overlap_score": "0.9",
        "shape_correlation_score": "0.8",
        "relative_pattern_stability_score": "0.8",
        "local_interference_score": "0.1",
        "constellation_peak_count": "3",
        "reference_peak_count": "3",
        "drift_compatible_status": "compatible",
        "cell_height": "30000",
        "reason": "unit_test_ms1",
        "diagnostic_only": "TRUE",
    }


def _qc_row(family_id: str, sample_stem: str) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "qc_reference_status": "supportive",
        "qc_reference_evidence_level": "qc_consensus_with_local_qc_overlay",
        "target_injection_order": "10",
        "nearest_qc_sample_stem": "QC5",
        "nearest_qc_injection_order": "11",
        "nearest_qc_injection_order_delta": "1",
        "target_apex_rt": "7.93",
        "nearest_qc_apex_rt": "7.91",
        "target_minus_qc_apex_delta_sec": "1.2",
        "target_qc_apex_abs_delta_sec": "1.2",
        "target_qc_shape_similarity": "0.8",
        "target_local_window_to_global_max_ratio": "0.5",
        "nearest_qc_local_window_to_global_max_ratio": "0.6",
        "reason": "unit_test_qc",
        "diagnostic_only": "TRUE",
    }


def _rt_drift_row(family_id: str, sample_stem: str) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "matrix_rt_drift_status": "drift_supported",
        "drift_evidence_level": "sample_istd_aligned",
        "raw_rt_delta_sec": "120",
        "drift_corrected_delta_sec": "2",
        "matrix_shift_sec": "118",
        "drift_reference_count": "5",
        "drift_reference_source": "istd_rt_trend",
        "drift_compatible_status": "compatible",
        "reason": "unit_test_rt_drift",
        "diagnostic_only": "TRUE",
    }
