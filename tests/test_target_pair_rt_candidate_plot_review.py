from __future__ import annotations

import csv
from pathlib import Path

import pytest

from tools.diagnostics.target_pair_rt_candidate_plot_review import (
    PLOT_INDEX_HEADERS,
    _read_istd_product_intervals,
    _write_tsv,
    parse_candidate_interval,
    select_target_pair_plot_requests,
)


def test_parse_candidate_interval_reads_apex_and_bounds_from_suffix() -> None:
    interval = parse_candidate_interval(
        "Sample|8-oxodG|region_first_safe_merge|local_minimum;chrom_peak_segment|"
        "16.38663|16.13814|16.67782"
    )

    assert interval.apex_rt == pytest.approx(16.38663)
    assert interval.rt_start == pytest.approx(16.13814)
    assert interval.rt_end == pytest.approx(16.67782)


def test_select_requests_keeps_accepted_reference_before_review_candidates() -> None:
    rows = [
        {
            "sample_name": "accepted",
            "target_label": "8-oxodG",
            "selected_candidate_id": "accepted|8-oxodG|r|s|17.1|16.8|17.5",
            "false_positive_review_status": "product_switch_accepted",
            "pair_rt_delta_error": "0.65",
        },
        {
            "sample_name": "candidate",
            "target_label": "8-oxodG",
            "selected_candidate_id": "candidate|8-oxodG|r|s|17.0|16.8|17.2",
            "false_positive_review_status": "row_approval_candidate",
            "missing_ms2_explanation": "contradicted",
            "pair_rt_delta_error": "0.70",
        },
        {
            "sample_name": "false-positive-review",
            "target_label": "8-oxodG",
            "selected_candidate_id": "review|8-oxodG|r|s|17.2|16.9|17.4",
            "false_positive_review_status": "false_positive_review_required",
            "missing_ms2_explanation": "contradicted",
            "pair_rt_delta_error": "0.80",
        },
    ]

    requests = select_target_pair_plot_requests(
        rows,
        max_8oxodg_contradicted=8,
        per_target=2,
        max_outside_area_ratio=4,
        max_total=24,
    )

    assert [request.row["sample_name"] for request in requests] == [
        "accepted",
        "false-positive-review",
        "candidate",
    ]
    assert requests[0].plot_group == "product_switch_accepted_reference"


def test_select_requests_can_plot_every_input_row() -> None:
    rows = [
        {
            "sample_name": "backfilled",
            "target_label": "8-oxodG",
            "previous_candidate_id": "rescued|8-oxodG|r|s|16.4|16.2|16.7",
            "selected_candidate_id": "rescued|8-oxodG|r|s|16.4|16.2|16.7",
            "false_positive_review_status": "not_applicable",
        },
        {
            "sample_name": "missing-id",
            "target_label": "8-oxodG",
            "selected_candidate_id": "",
            "false_positive_review_status": "not_applicable",
        },
    ]

    requests = select_target_pair_plot_requests(
        rows,
        max_8oxodg_contradicted=8,
        per_target=2,
        max_outside_area_ratio=4,
        max_total=24,
        plot_input_rows=True,
    )

    assert [request.row["sample_name"] for request in requests] == ["backfilled"]


def test_select_requests_excludes_sample_inapplicable_rows_by_default() -> None:
    rows = [
        {
            "sample_name": "pure-dna",
            "target_label": "8-oxo-Guo",
            "previous_candidate_id": "pure-dna|8-oxo-Guo|r|old|14.2|14.0|14.3",
            "selected_candidate_id": "pure-dna|8-oxo-Guo|r|new|13.9|13.6|14.2",
            "false_positive_review_status": "false_positive_review_required",
            "false_positive_review_reasons": (
                "target_sample_applicability:rna_containing;"
                "paired_area_ratio:inconclusive"
            ),
        },
        {
            "sample_name": "rna-containing",
            "target_label": "8-oxo-Guo",
            "previous_candidate_id": "rna-containing|8-oxo-Guo|r|old|12.8|12.6|13.0",
            "selected_candidate_id": "rna-containing|8-oxo-Guo|r|new|13.1|12.9|13.3",
            "false_positive_review_status": "false_positive_review_required",
            "false_positive_review_reasons": "paired_area_ratio:inconclusive",
        },
    ]

    requests = select_target_pair_plot_requests(
        rows,
        max_8oxodg_contradicted=8,
        per_target=2,
        max_outside_area_ratio=4,
        max_total=24,
        plot_input_rows=True,
    )

    assert [request.row["sample_name"] for request in requests] == [
        "rna-containing"
    ]


def test_select_requests_reuses_per_target_groups_in_label_order() -> None:
    rows = [
        _candidate_row(sample="b-low", target="B", delta="0.1"),
        _candidate_row(sample="a-high", target="A", delta="0.9"),
        _candidate_row(sample="b-high", target="B", delta="0.8"),
        _candidate_row(sample="a-low", target="A", delta="0.2"),
    ]

    requests = select_target_pair_plot_requests(
        rows,
        max_8oxodg_contradicted=0,
        per_target=1,
        max_outside_area_ratio=0,
        max_total=10,
    )

    assert [
        (request.plot_group, request.row["sample_name"])
        for request in requests
    ] == [
        ("per_target_high_delta_A", "a-high"),
        ("per_target_high_delta_B", "b-high"),
    ]


def test_select_requests_can_include_sample_inapplicable_rows_for_audit() -> None:
    rows = [
        {
            "sample_name": "pure-dna",
            "target_label": "8-oxo-Guo",
            "previous_candidate_id": "pure-dna|8-oxo-Guo|r|old|14.2|14.0|14.3",
            "selected_candidate_id": "pure-dna|8-oxo-Guo|r|new|13.9|13.6|14.2",
            "false_positive_review_status": "false_positive_review_required",
            "false_positive_review_reasons": (
                "target_sample_applicability:rna_containing;"
                "paired_area_ratio:inconclusive"
            ),
        }
    ]

    requests = select_target_pair_plot_requests(
        rows,
        max_8oxodg_contradicted=8,
        per_target=2,
        max_outside_area_ratio=4,
        max_total=24,
        plot_input_rows=True,
        include_sample_inapplicable=True,
    )

    assert [request.row["sample_name"] for request in requests] == ["pure-dna"]


def test_read_istd_product_intervals_uses_istd_long_rows(tmp_path) -> None:
    long_csv = tmp_path / "xic_results_long.csv"
    long_csv.write_text(
        "SampleName,Target,Role,RT,PeakStart,PeakEnd,Product State,"
        "Counted Detection\n"
        "SampleA,Analyte,Analyte,ND,ND,ND,ambiguous,FALSE\n"
        "SampleA,ISTD,ISTD,16.4283,16.10,16.80,detected_clean,TRUE\n",
        encoding="utf-8",
    )

    intervals = _read_istd_product_intervals(long_csv)

    assert set(intervals) == {("SampleA", "ISTD")}
    interval = intervals[("SampleA", "ISTD")]
    assert interval.apex_rt == pytest.approx(16.4283)
    assert interval.rt_start == pytest.approx(16.10)
    assert interval.rt_end == pytest.approx(16.80)
    assert interval.product_state == "detected_clean"
    assert interval.counted_detection == "TRUE"


def test_plot_index_writer_preserves_tsv_contract(tmp_path: Path) -> None:
    index_tsv = tmp_path / "target_pair_rt_candidate_plot_index.tsv"

    _write_tsv(
        index_tsv,
        PLOT_INDEX_HEADERS,
        [{"plot_rank": "1", "plot_group": "false_positive_review_required"}],
    )

    assert index_tsv.read_bytes().splitlines(keepends=True)[0].endswith(b"\r\n")
    assert index_tsv.read_text(encoding="utf-8").splitlines()[0].split(
        "\t"
    ) == list(PLOT_INDEX_HEADERS)
    row = _read_tsv(index_tsv)[0]
    assert row["plot_rank"] == "1"
    assert row["plot_group"] == "false_positive_review_required"
    assert row["sample_name"] == ""
    with pytest.raises(ValueError, match="dict contains fields not in fieldnames"):
        _write_tsv(
            tmp_path / "invalid.tsv",
            ("plot_rank",),
            [{"plot_rank": "1", "unexpected": "value"}],
        )


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _candidate_row(*, sample: str, target: str, delta: str) -> dict[str, str]:
    return {
        "sample_name": sample,
        "target_label": target,
        "selected_candidate_id": f"{sample}|{target}|r|s|17.0|16.8|17.2",
        "false_positive_review_status": "row_approval_candidate",
        "pair_rt_delta_error": delta,
    }
