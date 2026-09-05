import csv
from pathlib import Path
from types import SimpleNamespace

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.extraction.output_dispatch import write_outputs
from xic_extractor.extractor import FileResult, RunOutput
from xic_extractor.output.target_pair_rt_auto_reselection import (
    TARGET_PAIR_RT_AUTO_RESELECTION_HEADERS,
    build_target_pair_rt_auto_reselection_rows,
    summarize_target_pair_rt_auto_reselection_rows,
    write_target_pair_rt_auto_reselection_tsv,
)
from xic_extractor.peak_detection.model_selection import PeakModelSelectionResult
from xic_extractor.target_pair_rt_calibration import (
    TARGET_PAIR_RT_CALIBRATION_SCHEMA_VERSION,
    TargetPairRTCalibrationRow,
    write_target_pair_rt_calibration_tsv,
)


def test_shadow_rows_can_propose_8oxodg_without_product_mutation() -> None:
    targets = [_target("8-oxodG", istd_pair="15N5-8-oxodG"), _istd()]
    file_result = _file_result(
        sample_name="TumorBC2263_DNA",
        target_label="8-oxodG",
        paired_label="15N5-8-oxodG",
        model=_model_result(
            legacy_selected_candidate_id="legacy-15min",
            selected_candidate_id="successor-16min",
            selection_status="expected_diff",
        ),
        peak_candidate_rows=[
            _candidate_row("8-oxodG", "legacy-15min", "15.20000"),
            _candidate_row("8-oxodG", "successor-16min", "16.60000"),
        ],
    )

    rows = build_target_pair_rt_auto_reselection_rows(
        [file_result],
        targets=targets,
        calibration_rows=[_calibration(target_label="8-oxodG")],
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["sample_name"] == "TumorBC2263_DNA"
    assert row["target_label"] == "8-oxodG"
    assert row["selection_action"] == "shadow_auto_reselect_proposed"
    assert row["product_switch_allowed"] == "FALSE"
    assert row["previous_candidate_id"] == "legacy-15min"
    assert row["selected_candidate_id"] == "successor-16min"
    assert row["selected_candidate_rt"] == "16.60000"
    assert row["paired_istd_rt"] == "16.55000"
    assert row["pair_rt_delta_observed"] == "0.05000"
    assert row["gate_decision"] == "externalize"
    assert "product_switch_not_activated" in row["block_reason"]

    summary = summarize_target_pair_rt_auto_reselection_rows(rows)
    assert summary["shadow_auto_reselect_proposed_count"] == "1"
    assert summary["changed_row_denominator"] == "1"
    assert summary["product_switch_allowed_true_count"] == "0"
    assert summary["auto_reselected_count"] == "0"


def test_shadow_rows_include_leave_one_out_paired_area_ratio_evidence() -> None:
    targets = [_target("8-oxodG", istd_pair="15N5-8-oxodG"), _istd()]
    files = [
        _file_result(
            sample_name="BenignfatBC1055_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="legacy-middle",
                selected_candidate_id="successor-right",
                selection_status="expected_diff",
            ),
            target_area=1_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[
                _candidate_row(
                    "8-oxodG",
                    "legacy-middle",
                    "16.43000",
                    area="1_000.0",
                ),
                _candidate_row(
                    "8-oxodG",
                    "successor-right",
                    "17.18000",
                    area="10_000.0",
                    morphology_area="50_000.0",
                ),
            ],
        ),
        _file_result(
            sample_name="RefA_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-a",
                selected_candidate_id="ref-a",
                selection_status="parity",
            ),
            target_area=40_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
        _file_result(
            sample_name="RefB_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-b",
                selected_candidate_id="ref-b",
                selection_status="parity",
            ),
            target_area=50_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
        _file_result(
            sample_name="RefC_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-c",
                selected_candidate_id="ref-c",
                selection_status="parity",
            ),
            target_area=60_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
    ]

    rows = build_target_pair_rt_auto_reselection_rows(
        files,
        targets=targets,
        calibration_rows=[_calibration(target_label="8-oxodG")],
    )

    row = next(row for row in rows if row["sample_name"] == "BenignfatBC1055_DNA")
    assert row["paired_area_ratio_observed"] == "0.50000"
    assert row["paired_area_ratio_reference_n"] == "3"
    assert row["paired_area_ratio_reference_min"] == "0.40000"
    assert row["paired_area_ratio_reference_median"] == "0.50000"
    assert row["paired_area_ratio_reference_max"] == "0.60000"
    assert row["paired_area_ratio_status"] == "within_robust_range"
    assert row["paired_area_ratio_basis"] == (
        "leave_one_sample_out_median_plus_minus_3_scaled_mad_area_over_istd_area"
    )
    assert row["paired_area_ratio_robust_status"] == "within_robust_range"
    assert row["paired_area_ratio_robust_reference_median"] == "0.50000"
    assert row["paired_area_ratio_robust_reference_mad"] == "0.10000"
    assert row["paired_area_ratio_robust_basis"] == (
        "leave_one_sample_out_median_plus_minus_3_scaled_mad_area_over_istd_area"
    )
    assert row["false_positive_review_status"] == "row_approval_candidate"
    assert row["false_positive_review_reasons"] == (
        "dda_missing_ms2_not_observed;row_specific_expected_diff_required"
    )

    summary = summarize_target_pair_rt_auto_reselection_rows(rows)
    assert summary["paired_area_ratio_within_active_count"] == "4"
    assert summary["paired_area_ratio_outside_active_count"] == "0"
    assert summary["paired_area_ratio_inconclusive_count"] == "0"
    assert summary["false_positive_review_required_count"] == "0"
    assert summary["row_approval_candidate_count"] == "1"


def test_shadow_rows_expose_minmax_vs_robust_area_ratio_conflict() -> None:
    targets = [_target("8-oxodG", istd_pair="15N5-8-oxodG"), _istd()]
    files = [
        _file_result(
            sample_name="Sample_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="sample",
                selected_candidate_id="sample",
                selection_status="parity",
            ),
            target_area=600_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
        _file_result(
            sample_name="RefA_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-a",
                selected_candidate_id="ref-a",
                selection_status="parity",
            ),
            target_area=40_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
        _file_result(
            sample_name="RefB_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-b",
                selected_candidate_id="ref-b",
                selection_status="parity",
            ),
            target_area=50_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
        _file_result(
            sample_name="RefC_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-c",
                selected_candidate_id="ref-c",
                selection_status="parity",
            ),
            target_area=1_000_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
    ]

    rows = build_target_pair_rt_auto_reselection_rows(
        files,
        targets=targets,
        calibration_rows=[_calibration(target_label="8-oxodG")],
    )

    row = next(row for row in rows if row["sample_name"] == "Sample_DNA")
    assert row["paired_area_ratio_status"] == "outside_robust_range"
    assert row["paired_area_ratio_robust_status"] == "outside_robust_range"


def test_shadow_rows_prefer_chrom_morphology_candidate_with_pair_area_support() -> None:
    targets = [_target("8-oxodG", istd_pair="15N5-8-oxodG"), _istd()]
    files = [
        _file_result(
            sample_name="BenignfatBC1055_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="legacy-small",
                selected_candidate_id="model-local-minimum",
                selection_status="expected_diff",
            ),
            target_area=2_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[
                _candidate_row(
                    "8-oxodG",
                    "legacy-small",
                    "16.30000",
                    area="2_000.0",
                    proposal_sources="local_minimum",
                ),
                _candidate_row(
                    "8-oxodG",
                    "model-local-minimum",
                    "16.35000",
                    area="2_100.0",
                    proposal_sources="local_minimum",
                ),
                _candidate_row(
                    "8-oxodG",
                    "chrom-gaussian15-main",
                    "17.10000",
                    area="50_000.0",
                    proposal_sources="chrom_peak_segment",
                    support_labels="local_sn_strong;shape_clean;trace_clean",
                    concern_labels="rt_prior_borderline",
                ),
            ],
        ),
        _file_result(
            sample_name="RefA_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-a",
                selected_candidate_id="ref-a",
                selection_status="parity",
            ),
            target_area=40_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
        _file_result(
            sample_name="RefB_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-b",
                selected_candidate_id="ref-b",
                selection_status="parity",
            ),
            target_area=50_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
        _file_result(
            sample_name="RefC_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-c",
                selected_candidate_id="ref-c",
                selection_status="parity",
            ),
            target_area=60_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
    ]

    rows = build_target_pair_rt_auto_reselection_rows(
        files,
        targets=targets,
        calibration_rows=[_calibration(target_label="8-oxodG")],
    )

    row = next(row for row in rows if row["sample_name"] == "BenignfatBC1055_DNA")
    assert row["selected_candidate_id"] == "chrom-gaussian15-main"
    assert row["selected_candidate_rt"] == "17.10000"
    assert row["paired_area_ratio_observed"] == "0.50000"
    assert row["paired_area_ratio_status"] == "within_robust_range"
    assert "chrom_morphology_area_ratio" in row["selection_basis"]


def test_chrom_morphology_pair_area_rule_does_not_expand_parity_rows() -> None:
    targets = [_target("8-oxodG", istd_pair="15N5-8-oxodG"), _istd()]
    files = [
        _file_result(
            sample_name="BenignfatBC1055_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="model-local-minimum",
                selected_candidate_id="model-local-minimum",
                selection_status="parity",
            ),
            target_area=2_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[
                _candidate_row(
                    "8-oxodG",
                    "model-local-minimum",
                    "16.35000",
                    area="2_100.0",
                    proposal_sources="local_minimum",
                ),
                _candidate_row(
                    "8-oxodG",
                    "chrom-gaussian15-main",
                    "17.10000",
                    area="50_000.0",
                    proposal_sources="chrom_peak_segment",
                    support_labels="local_sn_strong;shape_clean;trace_clean",
                    concern_labels="rt_prior_borderline",
                ),
            ],
        ),
        _file_result(
            sample_name="RefA_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-a",
                selected_candidate_id="ref-a",
                selection_status="parity",
            ),
            target_area=40_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
        _file_result(
            sample_name="RefB_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-b",
                selected_candidate_id="ref-b",
                selection_status="parity",
            ),
            target_area=50_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
        _file_result(
            sample_name="RefC_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-c",
                selected_candidate_id="ref-c",
                selection_status="parity",
            ),
            target_area=60_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
    ]

    rows = build_target_pair_rt_auto_reselection_rows(
        files,
        targets=targets,
        calibration_rows=[_calibration(target_label="8-oxodG")],
    )

    row = next(row for row in rows if row["sample_name"] == "BenignfatBC1055_DNA")
    assert row["selected_candidate_id"] == "model-local-minimum"
    assert "chrom_morphology_area_ratio" not in row["selection_basis"]
    assert row["selection_action"] == "auto_reselect_blocked"


def test_shadow_rows_mark_paired_area_ratio_outside_run_reference() -> None:
    targets = [_target("8-oxodG", istd_pair="15N5-8-oxodG"), _istd()]
    files = [
        _file_result(
            sample_name="BenignfatBC1055_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="legacy-middle",
                selected_candidate_id="successor-right",
                selection_status="expected_diff",
            ),
            target_area=1_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[
                _candidate_row(
                    "8-oxodG",
                    "successor-right",
                    "17.18000",
                    area="2_000.0",
                ),
            ],
        ),
        _file_result(
            sample_name="RefA_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-a",
                selected_candidate_id="ref-a",
                selection_status="parity",
            ),
            target_area=40_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
        _file_result(
            sample_name="RefB_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-b",
                selected_candidate_id="ref-b",
                selection_status="parity",
            ),
            target_area=50_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
        _file_result(
            sample_name="RefC_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-c",
                selected_candidate_id="ref-c",
                selection_status="parity",
            ),
            target_area=60_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
    ]

    rows = build_target_pair_rt_auto_reselection_rows(
        files,
        targets=targets,
        calibration_rows=[_calibration(target_label="8-oxodG")],
    )

    row = next(row for row in rows if row["sample_name"] == "BenignfatBC1055_DNA")
    assert row["paired_area_ratio_observed"] == "0.02000"
    assert row["paired_area_ratio_status"] == "outside_robust_range"
    assert row["false_positive_review_status"] == (
        "false_positive_review_required"
    )
    assert "paired_area_ratio:outside_robust_range" in (
        row["false_positive_review_reasons"]
    )

    summary = summarize_target_pair_rt_auto_reselection_rows(rows)
    assert summary["false_positive_review_required_count"] == "1"
    assert "paired_area_ratio:outside_robust_range" in (
        summary["false_positive_strata"]
    )


def test_shadow_rows_treat_ms2_nl_contradiction_as_review_not_hard_veto() -> None:
    targets = [_target("8-oxodG", istd_pair="15N5-8-oxodG"), _istd()]
    files = [
        _file_result(
            sample_name="BenignfatBC1055_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="legacy-middle",
                selected_candidate_id="successor-right",
                selection_status="expected_diff",
            ),
            target_area=1_000.0,
            paired_area=100_000.0,
            candidate_ms2_evidence=SimpleNamespace(
                ms2_present=True,
                trigger_scan_count=1,
                strict_nl_scan_count=0,
            ),
            peak_candidate_rows=[
                _candidate_row(
                    "8-oxodG",
                    "successor-right",
                    "17.18000",
                    area="50_000.0",
                ),
            ],
        ),
        _file_result(
            sample_name="RefA_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-a",
                selected_candidate_id="ref-a",
                selection_status="parity",
            ),
            target_area=40_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
        _file_result(
            sample_name="RefB_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-b",
                selected_candidate_id="ref-b",
                selection_status="parity",
            ),
            target_area=50_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
        _file_result(
            sample_name="RefC_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-c",
                selected_candidate_id="ref-c",
                selection_status="parity",
            ),
            target_area=60_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
    ]

    rows = build_target_pair_rt_auto_reselection_rows(
        files,
        targets=targets,
        calibration_rows=[_calibration(target_label="8-oxodG")],
    )

    row = next(row for row in rows if row["sample_name"] == "BenignfatBC1055_DNA")
    assert row["paired_area_ratio_status"] == "within_robust_range"
    assert row["missing_ms2_explanation"] == "contradicted"
    assert row["false_positive_review_status"] == "row_approval_candidate"
    assert row["false_positive_review_reasons"] == (
        "ms2_nl_contradicted;row_specific_expected_diff_required"
    )


def test_shadow_rows_fail_closed_when_successor_candidate_row_is_missing() -> None:
    targets = [_target("8-oxodG", istd_pair="15N5-8-oxodG"), _istd()]
    files = [
        _file_result(
            sample_name="BenignfatBC1055_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="legacy-middle",
                selected_candidate_id="successor-right",
                selection_status="expected_diff",
            ),
            target_area=1_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[
                _candidate_row(
                    "8-oxodG",
                    "legacy-middle",
                    "16.43000",
                    area="1_000.0",
                ),
            ],
        ),
        _file_result(
            sample_name="RefA_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-a",
                selected_candidate_id="ref-a",
                selection_status="parity",
            ),
            target_area=40_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
        _file_result(
            sample_name="RefB_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-b",
                selected_candidate_id="ref-b",
                selection_status="parity",
            ),
            target_area=50_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
        _file_result(
            sample_name="RefC_DNA",
            target_label="8-oxodG",
            paired_label="15N5-8-oxodG",
            model=_model_result(
                legacy_selected_candidate_id="ref-c",
                selected_candidate_id="ref-c",
                selection_status="parity",
            ),
            target_area=60_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
    ]

    rows = build_target_pair_rt_auto_reselection_rows(
        files,
        targets=targets,
        calibration_rows=[_calibration(target_label="8-oxodG")],
    )

    row = next(row for row in rows if row["sample_name"] == "BenignfatBC1055_DNA")
    assert row["previous_candidate_rt"] == "16.43000"
    assert row["selected_candidate_rt"] == ""
    assert row["paired_area_ratio_observed"] == ""
    assert row["paired_area_ratio_status"] == "missing_candidate_area"
    assert row["false_positive_review_status"] == (
        "false_positive_review_required"
    )
    assert row["false_positive_review_reasons"] == (
        "selected_candidate_lookup_missing;"
        "paired_area_ratio:missing_candidate_area;"
        "dda_missing_ms2_not_observed;"
        "row_specific_expected_diff_required"
    )


def test_shadow_rows_block_rna_containing_target_in_pure_dna_sample() -> None:
    targets = [
        _target(
            "8-oxo-Guo",
            istd_pair="[13C,15N2]-8-oxo-Guo",
            sample_applicability="rna_containing",
        ),
        _target("[13C,15N2]-8-oxo-Guo", is_istd=True),
    ]
    files = [
        _file_result(
            sample_name="TumorBC2306_DNA",
            target_label="8-oxo-Guo",
            paired_label="[13C,15N2]-8-oxo-Guo",
            model=_model_result(
                legacy_selected_candidate_id="legacy-small",
                selected_candidate_id="successor-rescue",
                selection_status="expected_diff",
            ),
            target_counted_detection=True,
            peak_candidate_rows=[
                _candidate_row("8-oxo-Guo", "legacy-small", "13.10000"),
                _candidate_row(
                    "8-oxo-Guo",
                    "successor-rescue",
                    "13.50000",
                    area="50_000.0",
                    proposal_sources="chrom_peak_segment",
                    support_labels="local_sn_strong;shape_clean;trace_clean",
                ),
            ],
        )
    ]

    rows = build_target_pair_rt_auto_reselection_rows(
        files,
        targets=targets,
        calibration_rows=[
            _calibration(
                target_label="8-oxo-Guo",
                paired_istd_label="[13C,15N2]-8-oxo-Guo",
            )
        ],
    )

    row = rows[0]
    assert row["false_positive_review_status"] == "false_positive_review_required"
    assert (
        "target_sample_applicability:rna_containing"
        in row["false_positive_review_reasons"]
    )
    assert "target_sample_applicability:rna_containing" in row["block_reason"]


def test_shadow_rows_block_paired_rt_delta_conflict_when_ms2_is_not_confirmatory(
) -> None:
    targets = [_target("N6-HE-dA", istd_pair="d4-N6-2HE-dA"), _istd("d4-N6-2HE-dA")]
    files = [
        _file_result(
            sample_name="BenignfatBC0980_DNA",
            target_label="N6-HE-dA",
            paired_label="d4-N6-2HE-dA",
            model=_model_result(
                legacy_selected_candidate_id="legacy-left",
                selected_candidate_id="wrong-left",
                selection_status="expected_diff",
            ),
            target_area=50_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[
                _candidate_row("N6-HE-dA", "legacy-left", "22.50000", area="1_000.0"),
                _candidate_row("N6-HE-dA", "wrong-left", "22.73634", area="50_000.0"),
            ],
        ),
        _file_result(
            sample_name="RefA_DNA",
            target_label="N6-HE-dA",
            paired_label="d4-N6-2HE-dA",
            model=_model_result(
                legacy_selected_candidate_id="ref-a",
                selected_candidate_id="ref-a",
                selection_status="parity",
            ),
            target_area=40_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
        _file_result(
            sample_name="RefB_DNA",
            target_label="N6-HE-dA",
            paired_label="d4-N6-2HE-dA",
            model=_model_result(
                legacy_selected_candidate_id="ref-b",
                selected_candidate_id="ref-b",
                selection_status="parity",
            ),
            target_area=50_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
        _file_result(
            sample_name="RefC_DNA",
            target_label="N6-HE-dA",
            paired_label="d4-N6-2HE-dA",
            model=_model_result(
                legacy_selected_candidate_id="ref-c",
                selected_candidate_id="ref-c",
                selection_status="parity",
            ),
            target_area=60_000.0,
            paired_area=100_000.0,
            peak_candidate_rows=[],
        ),
    ]

    rows = build_target_pair_rt_auto_reselection_rows(
        files,
        targets=targets,
        calibration_rows=[
            _calibration(target_label="N6-HE-dA", paired_istd_label="d4-N6-2HE-dA")
        ],
    )

    row = next(row for row in rows if row["sample_name"] == "BenignfatBC0980_DNA")
    assert row["paired_area_ratio_status"] == "within_robust_range"
    assert abs(float(row["pair_rt_delta_error"])) > 0.25
    assert row["false_positive_review_status"] == "false_positive_review_required"
    assert "paired_rt_delta:outside_expected" in row["false_positive_review_reasons"]


def test_shadow_rows_do_not_block_rna_containing_strict_nl_target_by_sample_gate(
) -> None:
    targets = [
        _target(
            "8-oxo-Guo",
            istd_pair="[13C,15N2]-8-oxo-Guo",
            sample_applicability="rna_containing",
        ),
        _target("[13C,15N2]-8-oxo-Guo", is_istd=True),
    ]
    files = [
        _file_result(
            sample_name="TumorBC2304_DNAandRNA",
            target_label="8-oxo-Guo",
            paired_label="[13C,15N2]-8-oxo-Guo",
            model=_model_result(
                legacy_selected_candidate_id="legacy-small",
                selected_candidate_id="strict-nl-main",
                selection_status="expected_diff",
            ),
            candidate_ms2_evidence=SimpleNamespace(
                ms2_present=True,
                nl_match=True,
                nl_status="OK",
                trigger_scan_count=3,
                strict_nl_scan_count=3,
            ),
            peak_candidate_rows=[
                _candidate_row("8-oxo-Guo", "legacy-small", "12.76153"),
                _candidate_row(
                    "8-oxo-Guo",
                    "strict-nl-main",
                    "13.07787",
                    area="5_589_246.76",
                    support_labels="strict_nl_ok;local_sn_strong;trace_clean",
                ),
            ],
        )
    ]

    rows = build_target_pair_rt_auto_reselection_rows(
        files,
        targets=targets,
        calibration_rows=[
            _calibration(
                target_label="8-oxo-Guo",
                paired_istd_label="[13C,15N2]-8-oxo-Guo",
            )
        ],
    )

    row = rows[0]
    assert "target_sample_applicability:rna_containing" not in row["block_reason"]
    assert (
        "target_sample_applicability:rna_containing"
        not in row["false_positive_review_reasons"]
    )


def test_shadow_rows_do_not_block_rna_sample_by_sample_gate() -> None:
    targets = [
        _target(
            "8-oxo-Guo",
            istd_pair="[13C,15N2]-8-oxo-Guo",
            sample_applicability="rna_containing",
        ),
        _target("[13C,15N2]-8-oxo-Guo", is_istd=True),
    ]
    files = [
        _file_result(
            sample_name="TumorBC2304_RNA",
            target_label="8-oxo-Guo",
            paired_label="[13C,15N2]-8-oxo-Guo",
            model=_model_result(
                legacy_selected_candidate_id="legacy-small",
                selected_candidate_id="strict-nl-main",
                selection_status="expected_diff",
            ),
            candidate_ms2_evidence=SimpleNamespace(
                ms2_present=True,
                nl_match=True,
                nl_status="OK",
                trigger_scan_count=3,
                strict_nl_scan_count=3,
            ),
            peak_candidate_rows=[
                _candidate_row("8-oxo-Guo", "legacy-small", "12.76153"),
                _candidate_row(
                    "8-oxo-Guo",
                    "strict-nl-main",
                    "13.07787",
                    area="5_589_246.76",
                    support_labels="strict_nl_ok;local_sn_strong;trace_clean",
                ),
            ],
        )
    ]

    rows = build_target_pair_rt_auto_reselection_rows(
        files,
        targets=targets,
        calibration_rows=[
            _calibration(
                target_label="8-oxo-Guo",
                paired_istd_label="[13C,15N2]-8-oxo-Guo",
            )
        ],
    )

    row = rows[0]
    assert "target_sample_applicability:rna_containing" not in row["block_reason"]
    assert (
        "target_sample_applicability:rna_containing"
        not in row["false_positive_review_reasons"]
    )


def test_product_allowed_pair_rt_row_serializes_auto_reselected() -> None:
    targets = [_target("8-oxodG", istd_pair="15N5-8-oxodG"), _istd()]
    file_result = _file_result(
        sample_name="TumorBC2263_DNA",
        target_label="8-oxodG",
        paired_label="15N5-8-oxodG",
        model=_model_result(
            legacy_selected_candidate_id="legacy-15min",
            selected_candidate_id="successor-16min",
            selection_status="expected_diff",
            product_switch_allowed=True,
        ),
        peak_candidate_rows=[
            _candidate_row("8-oxodG", "legacy-15min", "15.20000"),
            _candidate_row("8-oxodG", "successor-16min", "16.60000"),
        ],
    )

    rows = build_target_pair_rt_auto_reselection_rows(
        [file_result],
        targets=targets,
        calibration_rows=[
            _calibration(
                target_label="8-oxodG",
                delta_source="biological_high_confidence",
                calibration_level="biological_transfer",
                product_transfer_status="validated",
            )
        ],
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["selection_action"] == "auto_reselected"
    assert row["product_switch_allowed"] == "TRUE"
    assert row["target_counted_detection"] == "TRUE"
    assert row["gate_decision"] == "promote"
    assert row["block_reason"] == ""

    summary = summarize_target_pair_rt_auto_reselection_rows(rows)
    assert summary["shadow_auto_reselect_proposed_count"] == "0"
    assert summary["product_switch_allowed_true_count"] == "1"
    assert summary["auto_reselected_count"] == "1"


def test_product_allowed_pair_rt_row_blocks_when_projection_is_not_counted() -> None:
    targets = [_target("8-oxodG", istd_pair="15N5-8-oxodG"), _istd()]
    file_result = _file_result(
        sample_name="TumorBC2263_DNA",
        target_label="8-oxodG",
        paired_label="15N5-8-oxodG",
        model=_model_result(
            legacy_selected_candidate_id="legacy-15min",
            selected_candidate_id="successor-16min",
            selection_status="expected_diff",
            product_switch_allowed=True,
        ),
        target_counted_detection=False,
        peak_candidate_rows=[
            _candidate_row("8-oxodG", "legacy-15min", "15.20000"),
            _candidate_row("8-oxodG", "successor-16min", "16.60000"),
        ],
    )

    rows = build_target_pair_rt_auto_reselection_rows(
        [file_result],
        targets=targets,
        calibration_rows=[
            _calibration(
                target_label="8-oxodG",
                delta_source="biological_high_confidence",
                calibration_level="biological_transfer",
                product_transfer_status="validated",
            )
        ],
    )

    row = rows[0]
    assert row["selection_action"] == "auto_reselect_blocked"
    assert row["product_switch_allowed"] == "FALSE"
    assert row["target_counted_detection"] == "FALSE"
    assert row["gate_decision"] == "no_go"
    assert "target_projection_not_counted" in row["block_reason"]
    assert row["false_positive_review_status"] == "false_positive_review_required"


def test_auto_reselected_row_preserves_previous_rt_when_legacy_lookup_misses() -> None:
    targets = [_target("8-oxodG", istd_pair="15N5-8-oxodG"), _istd()]
    legacy_id = (
        "BenignfatBC1055_DNA|8-oxodG|region_first_safe_merge|"
        "local_minimum;chrom_peak_segment|16.38663|16.13814|16.67782"
    )
    successor_id = (
        "BenignfatBC1055_DNA|8-oxodG|region_first_safe_merge|"
        "chrom_peak_segment|17.13547|16.80263|17.55202"
    )
    file_result = _file_result(
        sample_name="BenignfatBC1055_DNA",
        target_label="8-oxodG",
        paired_label="15N5-8-oxodG",
        model=_model_result(
            legacy_selected_candidate_id=legacy_id,
            selected_candidate_id=successor_id,
            selection_status="expected_diff",
            product_switch_allowed=True,
        ),
        peak_candidate_rows=[
            _candidate_row("8-oxodG", successor_id, "17.13547"),
        ],
    )

    rows = build_target_pair_rt_auto_reselection_rows(
        [file_result],
        targets=targets,
        calibration_rows=[
            _calibration(
                target_label="8-oxodG",
                delta_source="biological_high_confidence",
                calibration_level="biological_transfer",
                product_transfer_status="validated",
            )
        ],
    )

    row = rows[0]
    assert row["selection_action"] == "auto_reselected"
    assert row["previous_candidate_id"] == legacy_id
    assert row["selected_candidate_id"] == successor_id
    assert row["previous_candidate_rt"] == "16.38663"
    assert row["selected_candidate_rt"] == "17.13547"


def test_product_allowed_clean_standard_pair_rt_row_stays_blocked() -> None:
    targets = [_target("8-oxodG", istd_pair="15N5-8-oxodG"), _istd()]
    file_result = _file_result(
        sample_name="TumorBC2263_DNA",
        target_label="8-oxodG",
        paired_label="15N5-8-oxodG",
        model=_model_result(
            legacy_selected_candidate_id="legacy-15min",
            selected_candidate_id="successor-16min",
            selection_status="expected_diff",
            product_switch_allowed=True,
        ),
        peak_candidate_rows=[
            _candidate_row("8-oxodG", "legacy-15min", "15.20000"),
            _candidate_row("8-oxodG", "successor-16min", "16.60000"),
        ],
    )

    rows = build_target_pair_rt_auto_reselection_rows(
        [file_result],
        targets=targets,
        calibration_rows=[_calibration(target_label="8-oxodG")],
    )

    row = rows[0]
    assert row["selection_action"] == "auto_reselect_blocked"
    assert row["product_switch_allowed"] == "FALSE"
    assert row["gate_decision"] == "no_go"
    assert "product_transfer_status:not_assessed" in row["block_reason"]
    assert "calibration_level:clean_standard_only" in row["block_reason"]


def test_product_allowed_config_fallback_pair_rt_row_stays_blocked() -> None:
    targets = [_target("8-oxodG", istd_pair="15N5-8-oxodG"), _istd()]
    file_result = _file_result(
        sample_name="TumorBC2263_DNA",
        target_label="8-oxodG",
        paired_label="15N5-8-oxodG",
        model=_model_result(
            legacy_selected_candidate_id="legacy-15min",
            selected_candidate_id="successor-16min",
            selection_status="expected_diff",
            product_switch_allowed=True,
        ),
        peak_candidate_rows=[
            _candidate_row("8-oxodG", "legacy-15min", "15.20000"),
            _candidate_row("8-oxodG", "successor-16min", "16.60000"),
        ],
    )

    rows = build_target_pair_rt_auto_reselection_rows(
        [file_result],
        targets=targets,
        calibration_rows=[
            _calibration(
                target_label="8-oxodG",
                delta_source="config_fallback",
                calibration_level="biological_transfer",
                product_transfer_status="validated",
            )
        ],
    )

    row = rows[0]
    assert row["selection_action"] == "auto_reselect_blocked"
    assert row["product_switch_allowed"] == "FALSE"
    assert row["gate_decision"] == "no_go"
    assert "delta_source:config_fallback" in row["block_reason"]


def test_product_allowed_pair_rt_row_blocks_without_same_sample_istd() -> None:
    targets = [_target("8-oxodG", istd_pair="15N5-8-oxodG"), _istd()]
    file_result = _file_result(
        sample_name="TumorBC2263_DNA",
        target_label="8-oxodG",
        paired_label="15N5-8-oxodG",
        model=_model_result(
            legacy_selected_candidate_id="legacy-15min",
            selected_candidate_id="successor-16min",
            selection_status="expected_diff",
            product_switch_allowed=True,
        ),
        peak_candidate_rows=[
            _candidate_row("8-oxodG", "legacy-15min", "15.20000"),
            _candidate_row("8-oxodG", "successor-16min", "16.60000"),
        ],
        include_paired_result=False,
    )

    rows = build_target_pair_rt_auto_reselection_rows(
        [file_result],
        targets=targets,
        calibration_rows=[
            _calibration(
                target_label="8-oxodG",
                delta_source="biological_high_confidence",
                calibration_level="biological_transfer",
                product_transfer_status="validated",
            )
        ],
    )

    row = rows[0]
    assert row["selection_action"] == "auto_reselect_blocked"
    assert row["product_switch_allowed"] == "FALSE"
    assert row["role_policy"] == "paired_analyte_missing_credible_istd"
    assert "paired_istd_not_credible_in_sample" in row["block_reason"]


def test_shadow_rows_block_hash_mismatch_and_do_not_promote() -> None:
    target = _target("d3-5-medC", is_istd=True)
    file_result = _file_result(
        sample_name="TumorBC2289_DNA",
        target_label="d3-5-medC",
        paired_label="",
        model=_model_result(
            legacy_selected_candidate_id="legacy",
            selected_candidate_id="legacy",
            selection_status="parity",
        ),
        peak_candidate_rows=[_candidate_row("d3-5-medC", "legacy", "12.05000")],
    )
    calibration = _calibration(
        target_label="d3-5-medC",
        paired_istd_label="d3-5-medC",
        activation_block_reason="target_config_hash_mismatch",
    )

    rows = build_target_pair_rt_auto_reselection_rows(
        [file_result],
        targets=[target],
        calibration_rows=[calibration],
    )

    assert rows[0]["selection_action"] == "auto_reselect_blocked"
    assert rows[0]["product_switch_allowed"] == "FALSE"
    assert rows[0]["block_reason"] == "target_config_hash_mismatch"


def test_shadow_writer_serializes_schema_and_forces_product_switch_false(
    tmp_path: Path,
) -> None:
    path = tmp_path / "target_pair_rt_auto_reselection.tsv"
    row = {
        header: "value" for header in TARGET_PAIR_RT_AUTO_RESELECTION_HEADERS
    }
    row["selection_action"] = "shadow_auto_reselect_proposed"
    row["product_switch_allowed"] = "TRUE"

    write_target_pair_rt_auto_reselection_tsv(path, [row])

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert reader.fieldnames == list(TARGET_PAIR_RT_AUTO_RESELECTION_HEADERS)
        rows = list(reader)
    assert rows[0]["product_switch_allowed"] == "FALSE"


def test_writer_preserves_product_switch_true_only_for_auto_reselected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "target_pair_rt_auto_reselection.tsv"
    row = {
        header: "value" for header in TARGET_PAIR_RT_AUTO_RESELECTION_HEADERS
    }
    row["selection_action"] = "auto_reselected"
    row["product_switch_allowed"] = "TRUE"

    write_target_pair_rt_auto_reselection_tsv(path, [row])

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)
    assert rows[0]["product_switch_allowed"] == "TRUE"


def test_output_dispatch_emits_shadow_tsv_only_with_peer_diagnostics(
    tmp_path: Path,
) -> None:
    calibration_path = tmp_path / "target_pair_rt_calibration.tsv"
    write_target_pair_rt_calibration_tsv(
        calibration_path,
        [_calibration(target_config_hash="targethash")],
    )
    config = _config(
        tmp_path,
        emit_peak_candidates=True,
        calibration_path=calibration_path,
        target_config_hash="targethash",
    )
    targets = [_target("8-oxodG", istd_pair="15N5-8-oxodG"), _istd()]
    output = RunOutput(
        file_results=[
            _file_result(
                sample_name="TumorBC2263_DNA",
                target_label="8-oxodG",
                paired_label="15N5-8-oxodG",
                model=_model_result(
                    legacy_selected_candidate_id="legacy",
                    selected_candidate_id="successor",
                    selection_status="expected_diff",
                ),
                peak_candidate_rows=[
                    _candidate_row("8-oxodG", "legacy", "15.20000"),
                    _candidate_row("8-oxodG", "successor", "16.60000"),
                ],
            )
        ],
        diagnostics=[],
    )

    write_outputs(config, targets, output)

    assert config.output_csv.with_name("peak_candidates.tsv").exists()
    assert config.output_csv.with_name("target_pair_rt_auto_reselection.tsv").exists()
    assert config.output_csv.with_name(
        "target_pair_rt_auto_reselection_summary.tsv"
    ).exists()


def test_output_dispatch_skips_shadow_tsv_when_candidate_diagnostics_disabled(
    tmp_path: Path,
) -> None:
    calibration_path = tmp_path / "target_pair_rt_calibration.tsv"
    write_target_pair_rt_calibration_tsv(calibration_path, [_calibration()])
    config = _config(
        tmp_path,
        emit_peak_candidates=False,
        calibration_path=calibration_path,
    )
    output = RunOutput(file_results=[], diagnostics=[])

    write_outputs(config, [_target("8-oxodG")], output)

    assert not config.output_csv.with_name(
        "target_pair_rt_auto_reselection.tsv"
    ).exists()
    assert output.diagnostics[0].issue == "TARGET_PAIR_RT_AUTO_RESELECTION_SKIPPED"


def _config(
    tmp_path: Path,
    *,
    emit_peak_candidates: bool,
    calibration_path: Path,
    target_config_hash: str = "",
) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "output" / "xic_results.csv",
        diagnostics_csv=tmp_path / "output" / "xic_diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        emit_peak_candidates=emit_peak_candidates,
        target_pair_rt_calibration_path=calibration_path,
        target_config_hash=target_config_hash,
    )


def _target(
    label: str,
    *,
    istd_pair: str = "",
    is_istd: bool = False,
    sample_applicability: str = "all",
) -> Target:
    return Target(
        label=label,
        mz=284.0989,
        rt_min=15.0,
        rt_max=18.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=is_istd,
        istd_pair=istd_pair,
        paired_rt_relation="learned_delta_only" if istd_pair else "none",
        sample_applicability=sample_applicability,
    )


def _istd(label: str = "15N5-8-oxodG") -> Target:
    return _target(label, is_istd=True)


def _file_result(
    *,
    sample_name: str,
    target_label: str,
    paired_label: str,
    model: PeakModelSelectionResult,
    peak_candidate_rows: list[dict[str, str]],
    include_paired_result: bool = True,
    target_area: float = 10_000.0,
    paired_area: float = 100_000.0,
    candidate_ms2_evidence: object | None = None,
    target_counted_detection: bool = True,
) -> FileResult:
    results = {
        target_label: SimpleNamespace(
            model_selection_result=model,
            reported_rt=15.2,
            reported_peak_area=target_area,
            candidate_ms2_evidence=candidate_ms2_evidence,
            targeted_product_projection=SimpleNamespace(
                counted_detection=target_counted_detection
            ),
            nl_token="" if candidate_ms2_evidence is not None else "NO_MS2",
        ),
    }
    if include_paired_result:
        results[paired_label] = SimpleNamespace(
            reported_rt=16.55,
            reported_peak_area=paired_area,
        )
    return FileResult(
        sample_name=sample_name,
        results=results,
        peak_candidate_rows=peak_candidate_rows,
    )


def _candidate_row(
    target_label: str,
    candidate_id: str,
    rt_apex_min: str,
    *,
    area: str = "",
    morphology_area: str = "",
    proposal_sources: str = "",
    support_labels: str = "",
    concern_labels: str = "",
    quality_flags: str = "",
) -> dict[str, str]:
    return {
        "sample_name": "TumorBC2263_DNA",
        "target_label": target_label,
        "candidate_id": candidate_id,
        "rt_apex_min": rt_apex_min,
        "area_raw_counts_seconds": area,
        "area_ms1_morphology": morphology_area,
        "proposal_sources": proposal_sources,
        "support_labels": support_labels,
        "concern_labels": concern_labels,
        "quality_flags": quality_flags,
    }


def _model_result(
    *,
    legacy_selected_candidate_id: str,
    selected_candidate_id: str,
    selection_status: str,
    product_switch_allowed: bool = False,
) -> PeakModelSelectionResult:
    return PeakModelSelectionResult(
        selected_candidate_id=selected_candidate_id,
        legacy_selected_candidate_id=legacy_selected_candidate_id,
        stable_row_id="model_selection|legacy=legacy|successor=successor",
        trace_group_id="TumorBC2263_DNA|8-oxodG|region_first_safe_merge",
        decision_class="review",
        selection_status=selection_status,
        selection_reasons=("paired_rt_support",),
        legacy_reasons=("selected_by_legacy_scoring",),
        diff_reasons=(),
        public_projection={},
        evidence_sources=("ms1_trace", "role_aware_rt"),
        compatibility_oracle="legacy_peak_scoring_current_oracle",
        policy_source="selected_hypothesis_model_selection_v1",
        product_switch_allowed=product_switch_allowed,
        evidence_comparison_policy="limited_evidence_shadow",
    )


def _calibration(
    *,
    target_label: str = "8-oxodG",
    paired_istd_label: str = "15N5-8-oxodG",
    target_config_hash: str = "targethash",
    delta_source: str = "mixstds_clean_standard",
    calibration_level: str = "clean_standard_only",
    product_transfer_status: str = "not_assessed",
    activation_block_reason: str = "",
) -> TargetPairRTCalibrationRow:
    return TargetPairRTCalibrationRow(
        schema_version=TARGET_PAIR_RT_CALIBRATION_SCHEMA_VERSION,
        target_config_hash=target_config_hash,
        source_artifact="mixstds.tsv",
        source_hash="sourcehash",
        source_hash_status="present",
        target_label=target_label,
        paired_istd_label=paired_istd_label,
        pair_rt_delta_min=0.05,
        delta_source=delta_source,
        point_count=6,
        rt_delta_median_min=0.05,
        rt_delta_mad_min=0.01,
        rt_delta_direction="target_later",
        isotope_label_type="heavy_non_deuterium",
        paired_rt_relation="learned_delta_only",
        calibration_status="usable",
        calibration_level=calibration_level,
        product_transfer_status=product_transfer_status,
        activation_block_reason=activation_block_reason,
    )
