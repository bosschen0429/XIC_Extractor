import csv
import json
from pathlib import Path

import pytest

from tools.diagnostics import alignment_decision_report as report


def test_alignment_decision_report_renders_four_sections_and_known_exception(
    tmp_path: Path,
) -> None:
    alignment_dir = _alignment_dir(tmp_path)
    _write_json(
        tmp_path / "targeted_istd_benchmark.json",
        _benchmark_payload(status="FAIL", failure_modes=("AREA_MISMATCH",)),
    )
    _write_json(tmp_path / "economics.json", _economics_payload())
    _write_json(tmp_path / "timing.json", _timing_payload())
    output_html = tmp_path / "alignment_decision_report.html"

    code = report.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--targeted-istd-benchmark-json",
            str(tmp_path / "targeted_istd_benchmark.json"),
            "--owner-backfill-economics-json",
            str(tmp_path / "economics.json"),
            "--timing-json",
            str(tmp_path / "timing.json"),
            "--known-istd-exception",
            "d3-N6-medA:AREA_MISMATCH",
            "--output-html",
            str(output_html),
        ]
    )

    assert code == 0
    html = output_html.read_text(encoding="utf-8")
    assert "Run Verdict" in html
    assert "ISTD Benchmark" in html
    assert "Matrix Cleanliness" in html
    assert "Backfill Economics" in html
    assert "Run Verdict: WARN" in html
    assert "d3-N6-medA" in html
    assert "KNOWN" in html
    assert "alignment.write_outputs" in html
    assert 'class="visual-panel"' in html
    assert 'class="stacked-bar"' in html
    assert 'class="bar-list"' in html
    assert 'class="istd-board"' in html
    assert '<details class="data-table">' in html


def test_alignment_decision_report_renders_optional_rt_warping_evidence(
    tmp_path: Path,
) -> None:
    alignment_dir = _alignment_dir(tmp_path, clean=True)
    _write_json(
        tmp_path / "targeted_istd_benchmark.json",
        _benchmark_payload(status="PASS", failure_modes=()),
    )
    _write_json(tmp_path / "economics.json", _economics_payload())
    _write_json(tmp_path / "timing.json", _timing_payload())
    _write_json(tmp_path / "rt_normalization.json", _rt_normalization_payload())
    output_html = tmp_path / "report.html"

    assert report.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--targeted-istd-benchmark-json",
            str(tmp_path / "targeted_istd_benchmark.json"),
            "--owner-backfill-economics-json",
            str(tmp_path / "economics.json"),
            "--timing-json",
            str(tmp_path / "timing.json"),
            "--rt-normalization-json",
            str(tmp_path / "rt_normalization.json"),
            "--output-html",
            str(output_html),
        ]
    ) == 0

    html = output_html.read_text(encoding="utf-8")
    assert "RT Warping Evidence" in html
    assert "Leave-one-anchor-out p95 error" in html
    assert "RT Band Outcome" in html
    assert "anchor-a" in html
    assert "20-30 / improved" in html


def test_alignment_decision_report_fails_on_unhandled_istd_failure(
    tmp_path: Path,
) -> None:
    alignment_dir = _alignment_dir(tmp_path, clean=True)
    benchmark = tmp_path / "targeted_istd_benchmark.json"
    _write_json(benchmark, _benchmark_payload(status="FAIL", failure_modes=("DRIFT",)))

    payload = report.build_report(
        alignment_dir=alignment_dir,
        targeted_istd_benchmark_json=benchmark,
        owner_backfill_economics_json=tmp_path / "economics.json",
        timing_json=tmp_path / "timing.json",
    )

    assert payload["verdict"] == "FAIL"
    assert payload["istd"]["unhandled_failures"] == [
        {
            "target_label": "d3-N6-medA",
            "status": "FAIL",
            "failure_modes": ("DRIFT",),
        }
    ]


def test_alignment_decision_report_treats_known_reliability_warning_as_known(
    tmp_path: Path,
) -> None:
    alignment_dir = _alignment_dir(tmp_path, clean=True)
    benchmark = tmp_path / "targeted_istd_benchmark.json"
    _write_json(
        benchmark,
        _benchmark_payload(
            status="WARN",
            failure_modes=(),
            warning_modes=(
                "TARGETED_REVIEW_POSITIVE_EVIDENCE",
                "TARGETED_REVIEW_POSITIVE_REASON:product_outside_diagnostic_window",
            ),
        ),
    )

    payload = report.build_report(
        alignment_dir=alignment_dir,
        targeted_istd_benchmark_json=benchmark,
        owner_backfill_economics_json=tmp_path / "economics.json",
        timing_json=tmp_path / "timing.json",
        known_istd_exceptions=(
            "d3-N6-medA:TARGETED_REVIEW_POSITIVE_EVIDENCE",
            "d3-N6-medA:TARGETED_REVIEW_POSITIVE_REASON:product_outside_diagnostic_window",
        ),
    )

    assert payload["verdict"] == "WARN"
    assert payload["istd"]["known_count"] == 1
    assert payload["istd"]["fail_count"] == 0
    assert payload["istd"]["rows"][1]["known"] is True
    assert payload["istd"]["rows"][1]["failure_modes"] == (
        "TARGETED_REVIEW_POSITIVE_EVIDENCE;"
        "TARGETED_REVIEW_POSITIVE_REASON:product_outside_diagnostic_window"
    )


def test_alignment_decision_report_treats_unhandled_warning_as_warn_not_fail(
    tmp_path: Path,
) -> None:
    alignment_dir = _alignment_dir(tmp_path, clean=True)
    benchmark = tmp_path / "targeted_istd_benchmark.json"
    _write_json(
        benchmark,
        _benchmark_payload(
            status="WARN",
            failure_modes=(),
            warning_modes=(
                "TARGETED_REVIEW_POSITIVE_EVIDENCE",
                "TARGETED_REVIEW_POSITIVE_REASON:product_outside_diagnostic_window",
            ),
        ),
    )

    payload = report.build_report(
        alignment_dir=alignment_dir,
        targeted_istd_benchmark_json=benchmark,
        owner_backfill_economics_json=tmp_path / "economics.json",
        timing_json=tmp_path / "timing.json",
    )

    assert payload["verdict"] == "WARN"
    assert payload["istd"]["warning_count"] == 1
    assert payload["istd"]["fail_count"] == 0
    assert payload["istd"]["unhandled_failures"] == []
    assert payload["istd"]["rows"][1]["known"] is False


def test_alignment_decision_report_warns_when_optional_inputs_are_missing(
    tmp_path: Path,
) -> None:
    alignment_dir = _alignment_dir(tmp_path, clean=True)
    output_html = tmp_path / "report.html"

    assert report.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--output-html",
            str(output_html),
        ]
    ) == 0

    html = output_html.read_text(encoding="utf-8")
    assert "Run Verdict: WARN" in html
    assert "Not provided: ISTD benchmark JSON was not provided." in html
    assert "Not provided: Owner-backfill economics JSON was not provided." in html


def test_alignment_decision_report_cleanliness_warning_is_warn_not_fail(
    tmp_path: Path,
) -> None:
    alignment_dir = _alignment_dir(tmp_path)
    benchmark = tmp_path / "targeted_istd_benchmark.json"
    economics = tmp_path / "economics.json"
    timing = tmp_path / "timing.json"
    _write_json(benchmark, _benchmark_payload(status="PASS", failure_modes=()))
    _write_json(economics, _economics_payload())
    _write_json(timing, _timing_payload())

    payload = report.build_report(
        alignment_dir=alignment_dir,
        targeted_istd_benchmark_json=benchmark,
        owner_backfill_economics_json=economics,
        timing_json=timing,
    )

    assert payload["verdict"] == "WARN"
    assert payload["cleanliness"]["flag_counts"]["duplicate_claim_pressure"] == 1
    assert payload["cleanliness"]["flag_counts"]["high_backfill_dependency"] == 1
    assert payload["cleanliness"]["flag_counts"]["weak_seed_tolerated"] == 1


def test_alignment_decision_report_missing_review_columns_fails_clearly(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (
            {
                "feature_family_id": "FAM001",
                "identity_decision": "production_family",
            },
        ),
    )
    _write_matrix(alignment_dir / "alignment_matrix.tsv")

    with pytest.raises(ValueError, match="missing required columns: .*present_rate"):
        report.build_report(alignment_dir=alignment_dir)


def test_alignment_decision_report_missing_matrix_columns_fails_clearly(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    _write_review(alignment_dir / "alignment_review.tsv", clean=True)
    _write_tsv(
        alignment_dir / "alignment_matrix.tsv",
        ({"feature_family_id": "FAM001"},),
    )

    with pytest.raises(
        ValueError,
        match="missing required columns: .*neutral_loss_tag",
    ):
        report.build_report(alignment_dir=alignment_dir)


def test_alignment_decision_report_escapes_user_and_file_derived_strings(
    tmp_path: Path,
) -> None:
    alignment_dir = _alignment_dir(
        tmp_path,
        clean=True,
        feature_id="<script>x</script>",
    )
    benchmark = tmp_path / "targeted_istd_benchmark.json"
    _write_json(
        benchmark,
        {
            "summaries": [
                {
                    "target_label": "<b>ISTD</b>",
                    "active_tag": "TRUE",
                    "status": "PASS",
                    "failure_modes": "",
                    "selected_feature_id": "<script>x</script>",
                    "primary_match_count": 1,
                }
            ]
        },
    )
    output_html = tmp_path / "escaped.html"

    assert report.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--targeted-istd-benchmark-json",
            str(benchmark),
            "--output-html",
            str(output_html),
        ]
    ) == 0

    html = output_html.read_text(encoding="utf-8")
    assert "<b>ISTD</b>" not in html
    assert "<script>x</script>" not in html
    assert "&lt;b&gt;ISTD&lt;/b&gt;" in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html


def _alignment_dir(
    tmp_path: Path,
    *,
    clean: bool = False,
    feature_id: str = "FAM001",
) -> Path:
    alignment_dir = tmp_path / "alignment"
    _write_review(
        alignment_dir / "alignment_review.tsv",
        clean=clean,
        feature_id=feature_id,
    )
    _write_matrix(alignment_dir / "alignment_matrix.tsv", feature_id=feature_id)
    _write_json(tmp_path / "economics.json", _economics_payload())
    _write_json(tmp_path / "timing.json", _timing_payload())
    return alignment_dir


def _write_review(path: Path, *, clean: bool, feature_id: str = "FAM001") -> None:
    warning_flags = (
        ""
        if clean
        else "duplicate_claim_pressure;high_backfill_dependency;weak_seed_tolerated"
    )
    rows = (
        _review_row(
            feature_id,
            identity="production_family",
            primary=True,
            present_rate="1",
            detected="2",
            backfill="0",
            flags=warning_flags,
        ),
        _review_row(
            "FAM002",
            identity="provisional_discovery",
            primary=False,
            present_rate="0",
            detected="1",
            backfill="0",
            flags="",
        ),
        _review_row(
            "FAM003",
            identity="audit_family",
            primary=False,
            present_rate="0",
            detected="0",
            backfill="0",
            flags="rescue_heavy",
        ),
    )
    _write_tsv(path, rows)


def _write_matrix(path: Path, *, feature_id: str = "FAM001") -> None:
    _write_tsv(
        path,
        (
            {
                "feature_family_id": feature_id,
                "neutral_loss_tag": "dR",
                "family_center_mz": "300.0",
                "family_center_rt": "8.0",
                "Sample_A": "1000",
                "Sample_B": "1100",
            },
        ),
    )


def _review_row(
    feature_id: str,
    *,
    identity: str,
    primary: bool,
    present_rate: str,
    detected: str,
    backfill: str,
    flags: str,
) -> dict[str, str]:
    return {
        "feature_family_id": feature_id,
        "neutral_loss_tag": "dR",
        "family_center_mz": "300.0",
        "family_center_rt": "8.0",
        "detected_count": detected,
        "accepted_backfill_count": backfill,
        "present_rate": present_rate,
        "identity_decision": identity,
        "include_in_primary_matrix": "TRUE" if primary else "FALSE",
        "row_flags": flags,
        "warning": "",
    }


def _benchmark_payload(
    *,
    status: str,
    failure_modes: tuple[str, ...],
    warning_modes: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "overall_status": status,
        "summaries": [
            {
                "target_label": "d3-5-medC",
                "active_tag": "TRUE",
                "status": "PASS",
                "failure_modes": "",
                "selected_feature_id": "FAM001",
                "primary_match_count": 1,
                "family_mean_rt_delta_min": 0.02,
                "sample_rt_p95_abs_delta_min": 0.05,
                "log_area_spearman": 0.97,
                "log_area_pearson": 0.93,
                "targeted_positive_count": 2,
                "untargeted_positive_count": 2,
                "coverage_minimum": 1,
            },
            {
                "target_label": "d3-N6-medA",
                "active_tag": "TRUE",
                "status": status,
                "failure_modes": ";".join(failure_modes),
                "targeted_reliability_warning_modes": ";".join(warning_modes),
                "selected_feature_id": "FAM004",
                "primary_match_count": 1,
                "family_mean_rt_delta_min": 0.01,
                "sample_rt_p95_abs_delta_min": 0.04,
                "log_area_spearman": 0.2,
                "log_area_pearson": 0.1,
                "targeted_positive_count": 2,
                "untargeted_positive_count": 2,
                "coverage_minimum": 1,
            },
        ],
    }


def _economics_payload() -> dict[str, object]:
    return {
        "totals": {
            "request_target_count": 4,
            "request_extract_count_estimate": 6,
            "production_request_target_count": 2,
            "non_primary_request_target_count": 2,
            "rescued_target_count": 1,
            "absent_target_count": 2,
            "duplicate_assigned_target_count": 1,
        },
        "summary": [
            {
                "identity_decision": "production_family",
                "neutral_loss_tag": "dR",
                "include_in_primary_matrix": "True",
                "eligible_group_family_count": 1,
                "request_target_count": 2,
                "request_extract_count_estimate": 2,
                "rescued_target_count": 1,
                "absent_target_count": 0,
                "duplicate_assigned_target_count": 1,
            }
        ],
        "features": [
            {
                "feature_family_id": "FAM001",
                "neutral_loss_tag": "dR",
                "identity_decision": "production_family",
                "include_in_primary_matrix": True,
                "request_target_count": 2,
                "request_extract_count_estimate": 2,
                "rescued_target_count": 1,
                "absent_target_count": 0,
                "duplicate_assigned_target_count": 1,
                "row_flags": "duplicate_claim_pressure",
            }
        ],
    }


def _timing_payload() -> dict[str, object]:
    return {
        "pipeline": "alignment",
        "run_id": "run-1",
        "records": [
            {
                "stage": "alignment.owner_backfill",
                "elapsed_sec": 10.0,
                "metrics": {},
            },
            {
                "stage": "alignment.write_outputs",
                "elapsed_sec": 2.5,
                "metrics": {},
            },
        ],
    }


def _rt_normalization_payload() -> dict[str, object]:
    return {
        "overall_status": "PASS",
        "reference_source": "injection-local-median",
        "model_type": "auto",
        "anchor_label_count": 3,
        "sample_count": 3,
        "modelled_sample_count": 3,
        "unmodelled_sample_count": 0,
        "excluded_anchor_count": 1,
        "families_improved_count": 4,
        "families_worsened_count": 2,
        "median_rt_range_improvement_min": 0.04,
        "rt_band_summary": {
            "20-30": {"improved": 3, "worsened": 1, "stable": 0},
            ">=30": {"improved": 1, "worsened": 1, "stable": 0},
        },
        "leave_one_anchor_out": [
            {
                "target_label": "anchor-a",
                "evaluated_count": 3,
                "median_abs_error_min": 0.01,
                "p95_abs_error_min": 0.04,
                "max_abs_error_min": 0.05,
                "status": "PASS",
            },
            {
                "target_label": "anchor-b",
                "evaluated_count": 3,
                "median_abs_error_min": 0.02,
                "p95_abs_error_min": 0.12,
                "max_abs_error_min": 0.15,
                "status": "PASS",
            },
        ],
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_tsv(path: Path, rows: tuple[dict[str, str], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
