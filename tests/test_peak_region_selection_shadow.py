import csv
from dataclasses import dataclass, field
from pathlib import Path

from xic_extractor.extraction.peak_region_selection_shadow import (
    build_peak_region_selection_blast_radius_rows,
    build_peak_region_selection_shadow_rows,
    build_peak_region_selection_shadow_summary_rows,
    write_peak_region_selection_shadow_for_file_results,
)
from xic_extractor.output.peak_region_selection_shadow import (
    PEAK_REGION_SELECTION_SHADOW_HEADERS,
    write_peak_region_selection_shadow_tsv,
)


def test_shadow_rows_group_boundary_rows_by_target_and_include_review_context() -> None:
    rows = build_peak_region_selection_shadow_rows(
        [
            _boundary_row(
                sample_name="SampleA",
                target_label="d3-N6-medA",
                target_mz="269.21080",
                candidate_id="current",
                boundary_id="current|candidate",
                selected_candidate="TRUE",
                boundary_sources="candidate_interval",
                area="100.00",
                score="55",
            ),
            _boundary_row(
                sample_name="SampleA",
                target_label="d3-N6-medA",
                target_mz="269.21080",
                candidate_id="current",
                boundary_id="current|wide",
                selected_candidate="TRUE",
                boundary_sources="baseline_return",
                area="180.00",
                score="56",
                left="9.80000",
                right="10.60000",
            ),
        ]
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["sample_name"] == "SampleA"
    assert row["target_label"] == "d3-N6-medA"
    assert row["target_mz"] == "269.21080"
    assert row["current_area_raw_counts_seconds"] == "100.00"
    assert row["shadow_area_raw_counts_seconds"] == "180.00"
    assert row["shadow_status"] == "evaluated"
    assert row["shadow_verdict"] == "wider_boundary_preferred"
    assert row["decision_status"] == "evaluated"
    assert row["decision_class"] == "wider_boundary_preferred"
    assert row["product_action"] == "behavior_change_required"
    assert row["selected_candidate_id"] == "current"
    assert row["selected_boundary_id"] == "current|candidate"
    assert row["alternate_boundary_ids"] == "current|wide"
    assert row["baseline_method"] == "asls"
    assert row["merge_suggestion_source"] == ""
    assert row["area_ratio"] == "1.80000"
    assert row["current_scan_count"] == "5"
    assert row["shadow_scan_count"] == "5"
    assert row["review_reason"]


def test_shadow_summary_keeps_human_review_columns() -> None:
    rows = build_peak_region_selection_shadow_rows(
        [
            _boundary_row(boundary_id="current|candidate", selected_candidate="TRUE"),
            _boundary_row(
                boundary_id="current|wide",
                selected_candidate="TRUE",
                boundary_sources="baseline_return",
                area="200.00",
                score="65",
                left="9.80000",
                right="10.60000",
            ),
        ]
    )

    summary = build_peak_region_selection_shadow_summary_rows(rows)

    assert list(summary[0]) == [
        "sample_name",
        "target_label",
        "target_mz",
        "role",
        "resolver_mode",
        "current_rt_apex_min",
        "shadow_rt_apex_min",
        "current_area_raw_counts_seconds",
        "shadow_area_raw_counts_seconds",
        "shadow_status",
        "shadow_verdict",
        "decision_status",
        "decision_class",
        "product_action",
        "selected_candidate_id",
        "selected_boundary_id",
        "alternate_boundary_ids",
        "evidence_sources",
        "support_reasons",
        "conflict_reasons",
        "audit_reason",
        "promotion_reason",
        "baseline_method",
        "merge_suggestion_source",
        "score_delta",
        "area_ratio",
        "current_scan_count",
        "shadow_scan_count",
        "selected_interval_count",
        "selected_interval_gap_max_min",
        "selected_interval_total_score",
        "best_single_boundary_score",
        "local_mixture_diagnostic",
        "local_mixture_reason",
        "review_reason",
    ]


def test_shadow_row_exposes_adjacent_wis_merge_source() -> None:
    rows = build_peak_region_selection_shadow_rows(
        [
            _boundary_row(
                candidate_id="left",
                boundary_id="left|candidate",
                selected_candidate="TRUE",
                area="100.00",
                score="70",
                right="10.20000",
                nonoverlap_selected="TRUE",
            ),
            _boundary_row(
                candidate_id="right",
                boundary_id="right|candidate",
                selected_candidate="FALSE",
                area="10.00",
                score="55",
                left="10.22000",
                apex="10.25000",
                right="10.35000",
                nonoverlap_selected="TRUE",
            ),
        ]
    )

    assert rows[0]["shadow_verdict"] == "merge_suggested"
    assert rows[0]["merge_suggestion_source"] == (
        "adjacent_wis_local_minimum_merge"
    )


def test_malformed_boundary_row_emits_visible_skipped_shadow_row() -> None:
    rows = build_peak_region_selection_shadow_rows(
        [
            _boundary_row(
                boundary_id="current|candidate",
                selected_candidate="TRUE",
                area="not-a-number",
            ),
        ]
    )

    assert rows[0]["shadow_status"] == "skipped_invalid_trace"
    assert rows[0]["shadow_verdict"] == "insufficient_evidence"
    assert rows[0]["decision_status"] == "skipped_invalid_trace"
    assert rows[0]["decision_class"] == "insufficient_evidence"
    assert rows[0]["product_action"] == "review_only"
    assert rows[0]["review_reason"]


def test_blast_radius_summary_counts_rows_that_would_change() -> None:
    rows = [
        {
            "target_label": "ISTD-A",
            "role": "ISTD",
            "shadow_verdict": "wider_boundary_preferred",
            "area_ratio": "1.80000",
        },
        {
            "target_label": "Target-B",
            "role": "Analyte",
            "shadow_verdict": "current_supported",
            "area_ratio": "1.00000",
        },
    ]

    summary = build_peak_region_selection_blast_radius_rows(rows)

    assert summary == [
        {
            "total_rows": "2",
            "rows_that_would_change": "1",
            "istd_rows_that_would_change": "1",
            "affected_target_labels": "ISTD-A",
            "area_ratio_min": "1.80000",
            "area_ratio_median": "1.80000",
            "area_ratio_max": "1.80000",
        }
    ]


def test_blast_radius_summary_ignores_blank_area_ratios() -> None:
    rows = [
        {
            "target_label": "Target-A",
            "role": "Analyte",
            "shadow_status": "evaluated",
            "shadow_verdict": "split_supported",
            "area_ratio": "",
        },
        {
            "target_label": "Target-B",
            "role": "Analyte",
            "shadow_status": "evaluated",
            "shadow_verdict": "merge_suggested",
            "area_ratio": "1.20000",
        },
    ]

    summary = build_peak_region_selection_blast_radius_rows(rows)

    assert summary == [
        {
            "total_rows": "2",
            "rows_that_would_change": "2",
            "istd_rows_that_would_change": "0",
            "affected_target_labels": "Target-A;Target-B",
            "area_ratio_min": "1.20000",
            "area_ratio_median": "1.20000",
            "area_ratio_max": "1.20000",
        }
    ]


def test_shadow_writer_serializes_rows_safely(tmp_path: Path) -> None:
    path = tmp_path / "peak_region_selection_shadow.tsv"
    row = {header: "" for header in PEAK_REGION_SELECTION_SHADOW_HEADERS}
    row["sample_name"] = "SampleA"
    row["target_label"] = "line1\nline2\twith tab"
    row["shadow_status"] = "evaluated"
    row["shadow_verdict"] = "current_supported"

    write_peak_region_selection_shadow_tsv(path, [row])

    text = path.read_text(encoding="utf-8-sig")
    assert "line1 line2 with tab" in text
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert rows[0]["target_label"] == "line1 line2 with tab"


def test_shadow_writer_is_noop_when_disabled(tmp_path: Path) -> None:
    path = tmp_path / "peak_region_selection_shadow.tsv"

    write_peak_region_selection_shadow_tsv(
        path,
        [{"sample_name": "SampleA"}],
        enabled=False,
    )

    assert not path.exists()


def test_file_result_writer_emits_shadow_summary_and_blast_radius(
    tmp_path: Path,
) -> None:
    full_path = tmp_path / "peak_region_selection_shadow.tsv"
    file_result = _FileResult(
        peak_candidate_boundary_rows=[
            _boundary_row(boundary_id="current|candidate", selected_candidate="TRUE"),
            _boundary_row(
                boundary_id="current|wide",
                selected_candidate="TRUE",
                boundary_sources="baseline_return",
                area="180.00",
                score="56",
            ),
        ]
    )

    write_peak_region_selection_shadow_for_file_results(full_path, [file_result])

    assert full_path.exists()
    assert full_path.with_name("peak_region_selection_shadow_summary.tsv").exists()
    assert full_path.with_name("peak_region_selection_shadow_blast_radius.tsv").exists()


@dataclass
class _FileResult:
    peak_candidate_boundary_rows: list[dict[str, str]] = field(default_factory=list)


def _boundary_row(
    *,
    sample_name: str = "SampleA",
    target_label: str = "TargetA",
    target_mz: str = "258.10850",
    candidate_id: str = "current",
    boundary_id: str = "current|candidate",
    selected_candidate: str = "TRUE",
    boundary_sources: str = "candidate_interval",
    area: str = "100.00",
    score: str = "55",
    scan_count: str = "5",
    left: str = "9.90000",
    apex: str = "10.00000",
    right: str = "10.20000",
    nonoverlap_selected: str = "FALSE",
) -> dict[str, str]:
    return {
        "sample_name": sample_name,
        "group": "Tumor",
        "target_label": target_label,
        "target_mz": target_mz,
        "role": "ISTD" if target_label.startswith("ISTD") else "Analyte",
        "istd_pair": "ISTD-A",
        "analysis_mode": "targeted",
        "resolver_mode": "local_minimum",
        "candidate_id": candidate_id,
        "proposal_sources": "local_minimum",
        "selected_candidate": selected_candidate,
        "boundary_id": boundary_id,
        "boundary_sources": boundary_sources,
        "rt_left_min": left,
        "rt_apex_min": apex,
        "rt_right_min": right,
        "rt_width_min": "0.30000",
        "area_raw_counts_seconds": area,
        "boundary_audit_score": score,
        "boundary_nonoverlap_selected": nonoverlap_selected,
        "boundary_support_labels": "scan_support_ok",
        "boundary_concern_labels": "",
        "scan_count": scan_count,
        "is_candidate_interval": "TRUE"
        if boundary_sources == "candidate_interval"
        else "FALSE",
    }
