from __future__ import annotations

import csv
import json
from pathlib import Path

from xic_extractor.diagnostics.shared_peak_identity_explanation import (
    machine_evidence_support,
    rt_mode_evidence,
)


def test_rt_mode_evidence_marks_tag_backed_core_and_outlier_modes(
    tmp_path: Path,
) -> None:
    assignments = tmp_path / "mode_assignments.tsv"
    candidate_ms2 = tmp_path / "candidate_ms2.tsv"
    _write_mode_assignments(
        assignments,
        [
            _assignment("S1", "mode_core", "7.91", "7.95", status="detected"),
            _assignment("S2", "mode_core", "7.96", "7.97"),
            _assignment("S3", "mode_core", "8.02", "8.01"),
            _assignment("S4", "mode_outlier", "6.02", "6.01"),
        ],
    )
    _write_candidate_ms2(candidate_ms2, sample_stem="S1")

    rows = rt_mode_evidence.build_rt_mode_evidence_rows(
        mode_assignment_tsv=assignments,
        oracle_keys=(("FAM001", "S1"), ("FAM001", "S4")),
        feature_family_id="FAM001",
        candidate_ms2_pattern_evidence_tsv=candidate_ms2,
    )

    by_sample = {row["sample_stem"]: row for row in rows}
    assert by_sample["S1"]["rt_mode_status"] == "mode_supported"
    assert by_sample["S1"]["selected_mode_role"] == "tag_bearing_core"
    assert by_sample["S1"]["selected_mode_tag_status"] == "tag_supported"
    assert by_sample["S1"]["family_mode_class"] == (
        "tag_backed_core_with_outlier_modes"
    )

    assert by_sample["S4"]["rt_mode_status"] == "mode_conflict"
    assert by_sample["S4"]["selected_mode_role"] == "non_tag_outlier"
    assert by_sample["S4"]["selected_mode_tag_status"] == "no_tag_observed"
    assert by_sample["S4"]["reason"] == "selected_mode_not_tag_bearing_core"


def test_rt_mode_evidence_marks_no_tag_multimodal_family_as_consolidation_no_go(
    tmp_path: Path,
) -> None:
    assignments = tmp_path / "mode_assignments.tsv"
    _write_mode_assignments(
        assignments,
        [
            _assignment("S1", "mode_yellow", "5.10", "5.20"),
            _assignment("S2", "mode_green", "6.40", "6.55"),
            _assignment("S3", "mode_blue", "7.90", "7.96"),
        ],
    )

    rows = rt_mode_evidence.build_rt_mode_evidence_rows(
        mode_assignment_tsv=assignments,
        oracle_keys=(("FAM011810", "S1"),),
        feature_family_id="FAM011810",
    )

    row = rows[0]
    assert row["rt_mode_status"] == "consolidation_no_go"
    assert row["family_mode_class"] == "consolidation_no_go"
    assert row["selected_mode_role"] == "mixed_mode"
    assert row["reason"] == "multimodal_family_without_tag_bearing_core"


def test_rt_mode_evidence_keeps_two_mode_without_tag_as_split_review(
    tmp_path: Path,
) -> None:
    assignments = tmp_path / "mode_assignments.tsv"
    _write_mode_assignments(
        assignments,
        [
            _assignment("S1", "mode_early", "5.10", "5.20"),
            _assignment("S2", "mode_late", "7.90", "7.96"),
        ],
    )

    rows = rt_mode_evidence.build_rt_mode_evidence_rows(
        mode_assignment_tsv=assignments,
        oracle_keys=(("FAM_TWO_MODE", "S1"),),
        feature_family_id="FAM_TWO_MODE",
    )

    row = rows[0]
    assert row["rt_mode_status"] == "mode_split_required"
    assert row["family_mode_class"] == "irt_refined_mode_split"
    assert row["selected_mode_role"] == "split_mode"
    assert row["reason"] == "multimodal_family_requires_split_before_product_label"


def test_rt_mode_evidence_accepts_multi_family_assignment_artifact(
    tmp_path: Path,
) -> None:
    assignments = tmp_path / "mode_assignments.tsv"
    _write_mode_assignments(
        assignments,
        [
            _assignment("S1", "fam1_mode", "7.91", "7.95", family_id="FAM001"),
            _assignment("S2", "fam1_mode", "7.96", "7.97", family_id="FAM001"),
            _assignment("S1", "fam2_mode", "14.10", "14.12", family_id="FAM002"),
            _assignment("S2", "fam2_mode", "14.15", "14.16", family_id="FAM002"),
        ],
    )

    rows = rt_mode_evidence.build_rt_mode_evidence_rows(
        mode_assignment_tsv=assignments,
        oracle_keys=(("FAM001", "S1"), ("FAM002", "S1")),
    )

    by_family = {row["feature_family_id"]: row for row in rows}
    assert by_family["FAM001"]["selected_mode_id"] == "fam1_mode"
    assert by_family["FAM001"]["rt_mode_status"] == "mode_supported"
    assert by_family["FAM002"]["selected_mode_id"] == "fam2_mode"
    assert by_family["FAM002"]["rt_mode_status"] == "mode_supported"


def test_rt_mode_evidence_keeps_summary_overrides_scoped_by_family(
    tmp_path: Path,
) -> None:
    assignments = tmp_path / "mode_assignments.tsv"
    summary = tmp_path / "mode_summary.tsv"
    _write_mode_assignments(
        assignments,
        [
            _assignment("S1", "fam1_mode", "7.91", "7.95", family_id="FAM001"),
            _assignment("S1", "fam2_mode", "14.10", "14.12", family_id="FAM002"),
        ],
    )
    _write_tsv(
        summary,
        ("feature_family_id", "mode_id", "family_mode_class"),
        [
            {
                "feature_family_id": "FAM002",
                "mode_id": "fam2_mode",
                "family_mode_class": "consolidation_no_go",
            }
        ],
    )

    rows = rt_mode_evidence.build_rt_mode_evidence_rows(
        mode_assignment_tsv=assignments,
        mode_summary_tsv=summary,
        oracle_keys=(("FAM001", "S1"), ("FAM002", "S1")),
    )

    by_family = {row["feature_family_id"]: row for row in rows}
    assert by_family["FAM001"]["family_mode_class"] == "rt_mode_pure"
    assert by_family["FAM001"]["rt_mode_status"] == "mode_supported"
    assert by_family["FAM002"]["family_mode_class"] == "consolidation_no_go"
    assert by_family["FAM002"]["rt_mode_status"] == "consolidation_no_go"


def test_rt_mode_evidence_ignores_raw_unknown_modes_in_family_count(
    tmp_path: Path,
) -> None:
    assignments = tmp_path / "mode_assignments.tsv"
    _write_mode_assignments(
        assignments,
        [
            _assignment("S1", "mode_core", "7.91", "7.95"),
            _assignment("S2", "raw_unknown", "5.10", "5.20"),
            _assignment("S3", "outlier_unassigned", "6.10", "6.20"),
        ],
    )

    rows = rt_mode_evidence.build_rt_mode_evidence_rows(
        mode_assignment_tsv=assignments,
        oracle_keys=(("FAM001", "S1"),),
        feature_family_id="FAM001",
    )

    row = rows[0]
    assert row["rt_mode_status"] == "mode_supported"
    assert row["family_mode_class"] == "rt_mode_pure"
    assert row["family_mode_count"] == "1"


def test_rt_mode_evidence_can_build_from_overlay_trace_data_json(
    tmp_path: Path,
) -> None:
    overlay = tmp_path / "overlay_trace_data.json"
    candidate_ms2 = tmp_path / "candidate_ms2.tsv"
    _write_overlay_trace_data(
        overlay,
        family_id="FAM002",
        traces=[
            _overlay_trace("S1", 7.95, status="detected"),
            _overlay_trace("S2", 7.98),
            _overlay_trace("S3", 6.10),
            _overlay_trace("S4", 6.15),
        ],
    )
    _write_candidate_ms2(candidate_ms2, family_id="FAM002", sample_stem="S1")

    rows = rt_mode_evidence.build_rt_mode_evidence_rows_from_overlay_trace_data(
        overlay_trace_data_jsons=(overlay,),
        oracle_keys=(("FAM002", "S1"), ("FAM002", "S3"), ("FAM999", "S1")),
        candidate_ms2_pattern_evidence_tsv=candidate_ms2,
    )

    by_key = {(row["feature_family_id"], row["sample_stem"]): row for row in rows}
    assert by_key[("FAM002", "S1")]["rt_mode_status"] == "mode_supported"
    assert by_key[("FAM002", "S1")]["rt_mode_evidence_level"] == (
        "raw_selected_apex_modes"
    )
    assert by_key[("FAM002", "S1")]["selected_mode_role"] == "tag_bearing_core"

    assert by_key[("FAM002", "S3")]["rt_mode_status"] == "raw_mode_review_only"
    assert by_key[("FAM002", "S3")]["selected_mode_role"] == "raw_non_tag_outlier"
    assert by_key[("FAM002", "S3")]["reason"] == (
        "raw_overlay_non_core_mode_requires_irt_confirmation"
    )

    assert by_key[("FAM999", "S1")]["rt_mode_status"] == "not_available"


def test_rt_mode_evidence_prefers_assignment_rows_over_overlay_rows(
    tmp_path: Path,
) -> None:
    assignments = tmp_path / "mode_assignments.tsv"
    overlay = tmp_path / "overlay_trace_data.json"
    _write_mode_assignments(
        assignments,
        [_assignment("S1", "irt_blue_core", "7.95", "7.90")],
    )
    _write_overlay_trace_data(
        overlay,
        family_id="FAM001",
        traces=[_overlay_trace("S1", 6.10), _overlay_trace("S2", 6.15)],
    )

    assignment_rows = rt_mode_evidence.build_rt_mode_evidence_rows(
        mode_assignment_tsv=assignments,
        oracle_keys=(("FAM001", "S1"),),
        feature_family_id="FAM001",
    )
    overlay_rows = rt_mode_evidence.build_rt_mode_evidence_rows_from_overlay_trace_data(
        overlay_trace_data_jsons=(overlay,),
        oracle_keys=(("FAM001", "S1"),),
    )

    rows = rt_mode_evidence.merge_rt_mode_evidence_rows(
        assignment_rows,
        overlay_rows,
    )

    assert rows[0]["selected_mode_id"] == "irt_blue_core"
    assert rows[0]["rt_mode_evidence_level"] == "irt_selected_apex_modes"


def test_rt_mode_evidence_writer_matches_machine_support_loader(
    tmp_path: Path,
) -> None:
    assignments = tmp_path / "mode_assignments.tsv"
    output = tmp_path / "rt_mode_evidence.tsv"
    _write_mode_assignments(
        assignments,
        [_assignment("S1", "mode_1", "1.00", "1.01")],
    )
    rows = rt_mode_evidence.build_rt_mode_evidence_rows(
        mode_assignment_tsv=assignments,
        oracle_keys=(("FAM001", "S1"),),
        feature_family_id="FAM001",
    )

    rt_mode_evidence.write_rt_mode_evidence_rows(output, rows)

    loaded = machine_evidence_support.load_rt_mode_evidence(output)
    assert loaded[("FAM001", "S1")]["rt_mode_status"] == "mode_supported"
    assert loaded[("FAM001", "S1")]["diagnostic_only"] == "TRUE"


def _assignment(
    sample_stem: str,
    mode_id: str,
    raw_rt: str,
    normalized_rt: str,
    *,
    status: str = "backfilled",
    family_id: str = "",
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "sample_type": "Tumor",
        "status": status,
        "irt_cluster": mode_id,
        "cell_apex_rt": raw_rt,
        "norm_apex_rt": normalized_rt,
    }


def _write_mode_assignments(path: Path, rows: list[dict[str, str]]) -> None:
    _write_tsv(
        path,
        (
            "sample_stem",
            "feature_family_id",
            "sample_type",
            "status",
            "irt_cluster",
            "cell_apex_rt",
            "norm_apex_rt",
        ),
        rows,
    )


def _write_candidate_ms2(
    path: Path,
    *,
    sample_stem: str,
    family_id: str = "FAM001",
) -> None:
    _write_tsv(
        path,
        (
            "feature_family_id",
            "sample_stem",
            "candidate_ms2_pattern_status",
            "candidate_ms2_evidence_level",
            "raw_ms2_strict_nl_scan_count",
        ),
        [
            {
                "feature_family_id": family_id,
                "sample_stem": sample_stem,
                "candidate_ms2_pattern_status": "supportive",
                "candidate_ms2_evidence_level": "sample_candidate_aligned",
                "raw_ms2_strict_nl_scan_count": "1",
            }
        ],
    )


def _overlay_trace(
    sample_stem: str,
    apex_rt: float,
    *,
    status: str = "backfilled",
) -> dict[str, object]:
    return {
        "sample_stem": sample_stem,
        "status": status,
        "cell_apex_rt": apex_rt,
        "cell_height": 1000.0,
    }


def _write_overlay_trace_data(
    path: Path,
    *,
    family_id: str,
    traces: list[dict[str, object]],
) -> None:
    path.write_text(
        json.dumps({"family_id": family_id, "traces": traces}),
        encoding="utf-8",
    )


def _write_tsv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
