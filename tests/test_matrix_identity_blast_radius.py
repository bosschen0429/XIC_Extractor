from __future__ import annotations

import csv
import json
from pathlib import Path

from tools.diagnostics import analyze_matrix_identity_blast_radius as blast
from xic_extractor.alignment.promotion_policy import (
    ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
)


def test_blast_radius_reports_complete_identity_changes_and_benchmark_join(
    tmp_path: Path,
) -> None:
    alignment_dir = _write_alignment_run(
        tmp_path / "alignment",
        review_rows=[
            _review_row("FAM001", "TRUE", "single_sample_local_owner"),
            _review_row("FAM002", "TRUE", "owner_complete_link;owner_count=2"),
        ],
        cell_rows=[
            _cell_row("FAM001", "sample-a", "detected", 100.0),
            _cell_row("FAM001", "sample-b", "rescued", 90.0),
            _cell_row("FAM002", "sample-a", "detected", 100.0),
            _cell_row("FAM002", "sample-b", "detected", 90.0),
        ],
    )
    benchmark_dir = _write_benchmark_dir(tmp_path / "benchmark", "FAM002")

    code = blast.main(
        [
            "--alignment-run",
            str(alignment_dir),
            "--benchmark-dir",
            str(benchmark_dir),
            "--output-dir",
            str(tmp_path / "blast"),
            "--require-targeted-benchmark",
        ],
    )

    assert code == 0
    rows = _read_tsv(tmp_path / "blast" / "matrix_identity_blast_radius.tsv")
    by_id = {row["feature_family_id"]: row for row in rows}
    assert by_id["FAM001"]["evidence_status"] == "complete"
    assert by_id["FAM001"]["would_change_to_audit"] == "TRUE"
    assert by_id["FAM001"]["identity_decision"] == "provisional_discovery"
    assert by_id["FAM002"]["targeted_target_name"] == "d3-5-medC"
    assert by_id["FAM002"]["active_dna_istd_candidate"] == "TRUE"
    payload = json.loads(
        (tmp_path / "blast" / "matrix_identity_blast_radius.json").read_text(
            encoding="utf-8",
        ),
    )
    assert payload["evidence_status"] == "complete"
    assert payload["would_change_to_audit_count"] == 1


def test_blast_radius_projects_single_detected_product_authority_action(
    tmp_path: Path,
) -> None:
    alignment_dir = _write_alignment_run(
        tmp_path / "alignment",
        review_rows=[
            _review_row("FAM_ONE", "FALSE", "owner_complete_link;owner_count=1"),
        ],
        cell_rows=[
            _cell_row("FAM_ONE", "sample-a", "detected", 100.0),
            _cell_row(
                "FAM_ONE",
                "sample-b",
                "rescued",
                90.0,
                trace_quality="clean",
                scan_support_score=0.8,
                typed_backfill_support=True,
            ),
            _cell_row(
                "FAM_ONE",
                "sample-c",
                "rescued",
                80.0,
                trace_quality="clean",
                scan_support_score=0.8,
                typed_backfill_support=True,
            ),
        ],
    )

    code = blast.main(
        [
            "--alignment-run",
            str(alignment_dir),
            "--output-dir",
            str(tmp_path / "blast"),
        ],
    )

    assert code == 0
    rows = _read_tsv(tmp_path / "blast" / "matrix_identity_blast_radius.tsv")
    by_id = {row["feature_family_id"]: row for row in rows}

    assert by_id["FAM_ONE"]["identity_decision"] == "production_family"
    assert by_id["FAM_ONE"]["matrix_role"] == "primary"
    assert by_id["FAM_ONE"]["recommended_action"] == "use"
    assert by_id["FAM_ONE"]["evidence_tier"] == "1"
    assert by_id["FAM_ONE"]["blockers"] == ""
    assert "ms1_backfill_supported" in by_id["FAM_ONE"]["support_reasons"]


def test_blast_radius_preserves_review_only_projection(
    tmp_path: Path,
) -> None:
    alignment_dir = _write_alignment_run(
        tmp_path / "alignment",
        review_rows=[
            _review_row(
                "FAM_REVIEW",
                "FALSE",
                "owner_complete_link;owner_count=1",
                identity_reason="review_only",
            ),
        ],
        cell_rows=[
            _cell_row("FAM_REVIEW", "sample-a", "detected", 100.0),
            _cell_row(
                "FAM_REVIEW",
                "sample-b",
                "rescued",
                90.0,
                trace_quality="clean",
                scan_support_score=0.8,
            ),
            _cell_row(
                "FAM_REVIEW",
                "sample-c",
                "rescued",
                80.0,
                trace_quality="clean",
                scan_support_score=0.8,
            ),
        ],
    )

    code = blast.main(
        [
            "--alignment-run",
            str(alignment_dir),
            "--output-dir",
            str(tmp_path / "blast"),
        ],
    )

    assert code == 0
    rows = _read_tsv(tmp_path / "blast" / "matrix_identity_blast_radius.tsv")
    by_id = {row["feature_family_id"]: row for row in rows}

    assert by_id["FAM_REVIEW"]["identity_reason"] == "review_only"
    assert by_id["FAM_REVIEW"]["matrix_role"] == "audit"
    assert by_id["FAM_REVIEW"]["recommended_action"] == "review"


def test_blast_radius_accepts_downstream_mz_rt_matrix(
    tmp_path: Path,
) -> None:
    alignment_dir = _write_alignment_run(
        tmp_path / "alignment",
        review_rows=[
            _review_row("FAM001", "TRUE", "owner_complete_link;owner_count=2"),
        ],
        cell_rows=[
            _cell_row("FAM001", "sample-a", "detected", 100.0),
            _cell_row("FAM001", "sample-b", "detected", 90.0),
        ],
    )
    _write_tsv(
        alignment_dir / "alignment_matrix.tsv",
        [{"Mz": "500.123", "RT": "8.49", "sample-a": "100", "sample-b": "90"}],
    )

    code = blast.main(
        [
            "--alignment-run",
            str(alignment_dir),
            "--output-dir",
            str(tmp_path / "blast"),
        ],
    )

    assert code == 0
    rows = _read_tsv(tmp_path / "blast" / "matrix_identity_blast_radius.tsv")
    assert rows[0]["evidence_status"] == "complete"
    assert rows[0]["matrix_role"] == "primary"


def test_blast_radius_missing_peak_columns_outputs_incomplete_summary(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    alignment_dir.mkdir()
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        [_review_row("FAM001", "TRUE", "single_sample_local_owner")],
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        [
            {
                "feature_family_id": "FAM001",
                "sample_stem": "sample-a",
                "status": "detected",
            },
        ],
    )
    _write_tsv(
        alignment_dir / "alignment_matrix.tsv",
        [{"feature_family_id": "FAM001", "sample-a": "100"}],
    )
    benchmark_dir = _write_benchmark_dir(tmp_path / "benchmark", "FAM001")

    code = blast.main(
        [
            "--alignment-run",
            str(alignment_dir),
            "--benchmark-dir",
            str(benchmark_dir),
            "--output-dir",
            str(tmp_path / "blast"),
            "--require-targeted-benchmark",
            "--allow-incomplete-summary",
        ],
    )

    assert code == 0
    rows = _read_tsv(tmp_path / "blast" / "matrix_identity_blast_radius.tsv")
    assert rows[0]["evidence_status"] == "evidence_incomplete"
    assert "alignment_cells.tsv:area" in rows[0]["missing_required_columns"]
    assert "alignment_cells.tsv:peak_start_rt" in rows[0]["missing_required_columns"]


def test_blast_radius_requires_targeted_benchmark_when_enabled(
    tmp_path: Path,
) -> None:
    alignment_dir = _write_alignment_run(
        tmp_path / "alignment",
        review_rows=[
            _review_row("FAM001", "TRUE", "owner_complete_link;owner_count=2"),
        ],
        cell_rows=[
            _cell_row("FAM001", "sample-a", "detected", 100.0),
            _cell_row("FAM001", "sample-b", "detected", 90.0),
        ],
    )

    code = blast.main(
        [
            "--alignment-run",
            str(alignment_dir),
            "--benchmark-dir",
            str(tmp_path / "missing-benchmark"),
            "--output-dir",
            str(tmp_path / "blast"),
            "--require-targeted-benchmark",
        ],
    )

    assert code == 2


def test_blast_radius_joins_current_targeted_istd_benchmark_aliases(
    tmp_path: Path,
) -> None:
    alignment_dir = _write_alignment_run(
        tmp_path / "alignment",
        review_rows=[
            _review_row("FAM001", "TRUE", "owner_complete_link;owner_count=2"),
            _review_row("FAM002", "TRUE", "owner_complete_link;owner_count=2"),
        ],
        cell_rows=[
            _cell_row("FAM001", "sample-a", "detected", 100.0),
            _cell_row("FAM001", "sample-b", "detected", 90.0),
            _cell_row("FAM002", "sample-a", "detected", 50.0),
            _cell_row("FAM002", "sample-b", "detected", 45.0),
        ],
    )
    benchmark_dir = tmp_path / "benchmark"
    benchmark_dir.mkdir()
    _write_tsv(
        benchmark_dir / "targeted_istd_benchmark_matches.tsv",
        [
            {"target_label": "d3-5-medC", "feature_family_id": "FAM001"},
            {"target_label": "d3-5-medC", "feature_family_id": "FAM002"},
        ],
    )
    _write_tsv(
        benchmark_dir / "targeted_istd_benchmark_summary.tsv",
        [
            {
                "target_label": "d3-5-medC",
                "role": "ISTD",
                "status": "PASS",
                "active_tag": "TRUE",
                "selected_feature_id": "FAM001",
            },
        ],
    )
    (benchmark_dir / "targeted_istd_benchmark.json").write_text(
        json.dumps({"overall_status": "PASS"}),
        encoding="utf-8",
    )

    code = blast.main(
        [
            "--alignment-run",
            str(alignment_dir),
            "--benchmark-dir",
            str(benchmark_dir),
            "--output-dir",
            str(tmp_path / "blast"),
            "--require-targeted-benchmark",
        ],
    )

    assert code == 0
    rows = _read_tsv(tmp_path / "blast" / "matrix_identity_blast_radius.tsv")
    by_family = {row["feature_family_id"]: row for row in rows}
    assert by_family["FAM001"]["targeted_target_name"] == "d3-5-medC"
    assert by_family["FAM001"]["targeted_role"] == "ISTD"
    assert by_family["FAM001"]["targeted_benchmark_class"] == "PASS"
    assert by_family["FAM001"]["active_dna_istd_candidate"] == "TRUE"
    assert by_family["FAM002"]["targeted_target_name"] == "d3-5-medC"
    assert by_family["FAM002"]["targeted_benchmark_class"] == "PASS"
    assert by_family["FAM002"]["active_dna_istd_candidate"] == ""


def test_blast_radius_input_bundle_preserves_existing_order_contracts() -> None:
    review_rows = [
        _review_row("FAM_B", "TRUE", "owner_complete_link;owner_count=2"),
        _review_row("FAM_A", "FALSE", "single_sample_local_owner"),
    ]
    cell_rows = [
        _cell_row("FAM_B", "sample-b", "detected", 100.0),
        _cell_row("FAM_A", "sample-c", "rescued", 90.0),
        _cell_row("FAM_B", "sample-a", "detected", 80.0),
    ]

    inputs = blast._blast_radius_inputs_from_tsv(review_rows, cell_rows)

    assert [cluster.feature_family_id for cluster in inputs.matrix.clusters] == [
        "FAM_B",
        "FAM_A",
    ]
    assert inputs.matrix.sample_order == ("sample-a", "sample-b", "sample-c")
    assert [
        row["sample_stem"] for row in inputs.cell_rows_by_family["FAM_B"]
    ] == [
        "sample-b",
        "sample-a",
    ]
    assert inputs.current_by_family["FAM_A"]["include_in_primary_matrix"] == "FALSE"


def test_blast_radius_writer_preserves_value_formatting(tmp_path: Path) -> None:
    path = tmp_path / "matrix_identity_blast_radius.tsv"

    blast._write_tsv(
        path,
        ("flag", "count", "ratio", "empty"),
        [
            {
                "flag": True,
                "count": 1,
                "ratio": 1.25,
                "empty": None,
                "ignored": "value",
            },
        ],
    )

    assert _read_tsv(path) == [
        {
            "flag": "TRUE",
            "count": "1",
            "ratio": "1.25",
            "empty": "",
        },
    ]


def _write_alignment_run(
    path: Path,
    *,
    review_rows: list[dict[str, object]],
    cell_rows: list[dict[str, object]],
) -> Path:
    path.mkdir(parents=True)
    _write_tsv(path / "alignment_review.tsv", review_rows)
    _write_tsv(path / "alignment_cells.tsv", cell_rows)
    _write_tsv(path / "alignment_matrix.tsv", [{"feature_family_id": "FAM001"}])
    return path


def _review_row(
    family_id: str,
    include: str,
    evidence: str,
    *,
    identity_reason: str = "",
    row_flags: str = "",
) -> dict[str, object]:
    return {
        "feature_family_id": family_id,
        "include_in_primary_matrix": include,
        "family_evidence": evidence,
        "identity_reason": identity_reason,
        "row_flags": row_flags,
        "neutral_loss_tag": "DNA_dR",
        "family_center_mz": 500.0,
        "family_center_rt": 8.5,
        "has_anchor": "TRUE",
    }


def _cell_row(
    family_id: str,
    sample: str,
    status: str,
    area: float,
    *,
    trace_quality: str = "",
    scan_support_score: float | None = None,
    reason: str | None = None,
    typed_backfill_support: bool = False,
) -> dict[str, object]:
    row: dict[str, object] = {
        "feature_family_id": family_id,
        "sample_stem": sample,
        "status": status,
        "area": area,
        "primary_matrix_area": area,
        "primary_matrix_area_source": "gaussian15_positive_asls_residual",
        "primary_matrix_area_reason": "",
        "apex_rt": 8.5,
        "height": 100.0,
        "peak_start_rt": 8.4,
        "peak_end_rt": 8.6,
        "rt_delta_sec": 0.0,
        "trace_quality": trace_quality,
        "scan_support_score": scan_support_score,
        "reason": reason if reason is not None else status,
    }
    if typed_backfill_support:
        row.update(
            {
                "backfill_ms1_pattern_status": "supportive",
                "backfill_ms1_pattern_evidence_level": "trace_constellation",
                "backfill_matrix_rt_drift_status": "rt_close",
                "backfill_drift_evidence_level": "sample_istd_aligned",
                "backfill_drift_compatible_status": "compatible",
                "backfill_candidate_ms2_pattern_status": "supportive",
                "backfill_candidate_ms2_evidence_level": "sample_candidate_aligned",
                "backfill_evidence_reason": ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
                **_product_authority_fields(),
            },
        )
    return row


def _product_authority_fields() -> dict[str, object]:
    return {
        "backfill_ms1_product_authority_status": "product_authorized",
        "backfill_ms1_product_authority_scope": "feature_family_sample",
        "backfill_ms1_product_authority_source": "unit_test_reviewed_allowlist",
        "backfill_ms1_product_authority_reason": "unit_test_authorized",
        "backfill_ms1_product_authority_evidence_sha256": "unit-test-ms1-sha256",
        "backfill_candidate_ms2_product_authority_status": "product_authorized",
        "backfill_candidate_ms2_product_authority_scope": "feature_family_sample",
        "backfill_candidate_ms2_product_authority_source": (
            "unit_test_reviewed_allowlist"
        ),
        "backfill_candidate_ms2_product_authority_reason": "unit_test_authorized",
        "backfill_candidate_ms2_product_authority_evidence_sha256": (
            "unit-test-ms2-sha256"
        ),
    }


def _write_benchmark_dir(path: Path, family_id: str) -> Path:
    path.mkdir(parents=True)
    benchmark_rows = [
        {
            "target_name": "d3-5-medC",
            "role": "ISTD",
            "benchmark_class": "PASS",
            "feature_family_id": family_id,
            "active_dna_istd_candidate": "TRUE",
        },
    ]
    _write_tsv(path / "targeted_istd_benchmark_matches.tsv", benchmark_rows)
    _write_tsv(path / "targeted_istd_benchmark_summary.tsv", benchmark_rows)
    (path / "targeted_istd_benchmark.json").write_text(
        json.dumps({"overall_status": "PASS"}),
        encoding="utf-8",
    )
    return path


def _write_tsv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
