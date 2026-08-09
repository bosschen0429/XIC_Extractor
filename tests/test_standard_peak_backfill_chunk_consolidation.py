from __future__ import annotations

import csv
import json
from pathlib import Path

from tools.diagnostics import standard_peak_backfill_chunk_consolidation as cli
from xic_extractor.alignment.tsv_writer import ALIGNMENT_REVIEW_COLUMNS
from xic_extractor.diagnostics.shadow_production_projection import (
    SHADOW_PRODUCTION_PROJECTION_COLUMNS,
)


def test_chunk_consolidation_applies_one_matrix_from_two_chunks(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    out = tmp_path / "out"

    assert (
        cli.main(
            [
                "--chunk-dir",
                str(fixture["chunk_a"]),
                "--chunk-dir",
                str(fixture["chunk_b"]),
                "--review-queue-tsv",
                str(fixture["queue"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(out),
                "--source-run-id",
                "unit-chunk-consolidation",
                "--emit-formal-product-output",
            ],
        )
        == 0
    )

    summary = json.loads(
        (
            out / "standard_peak_backfill_chunk_consolidation_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["status"] == "pass"
    assert summary["coverage_status"] == "complete"
    assert summary["covered_queue_row_count"] == "2"
    assert summary["matrix_cells_written"] == "2"
    assert summary["merged_shadow_projection_row_count"] == "2"
    assert summary["formal_product_output_dir"] == str(
        out / "formal_product_output",
    )
    assert summary["formal_product_manifest_json"] == str(
        out
        / "formal_product_output"
        / "standard_peak_formal_product_manifest.json",
    )
    assert summary["published_alignment_output_dir"] == str(tmp_path)
    assert summary["published_alignment_manifest_json"] == str(
        tmp_path / "standard_peak_default_matrix_manifest.json",
    )

    matrix_rows = _read_tsv(
        out
        / "standard_peak_productization"
        / "activated_matrix"
        / "alignment_matrix.tsv",
    )
    identity_rows = _read_tsv(
        out
        / "standard_peak_productization"
        / "activated_matrix"
        / "alignment_matrix_identity.tsv",
    )
    row_by_hypothesis = {
        row["peak_hypothesis_id"]: int(row["matrix_row_index"]) - 1
        for row in identity_rows
    }
    assert matrix_rows[row_by_hypothesis["FAM_A"]]["S2"] == "1.11000e+02"
    assert matrix_rows[row_by_hypothesis["FAM_B"]]["S2"] == "2.22000e+02"
    formal_matrix_rows = _read_tsv(
        out / "formal_product_output" / "alignment_matrix.tsv",
    )
    assert formal_matrix_rows == matrix_rows
    assert _read_tsv(fixture["matrix"]) == matrix_rows
    assert (
        tmp_path / "alignment_matrix.pre_standard_peak_backfill.tsv"
    ).exists()
    assert (
        tmp_path / "alignment_matrix_identity.pre_standard_peak_backfill.tsv"
    ).exists()
    manifest = json.loads(
        (
            out
            / "formal_product_output"
            / "standard_peak_formal_product_manifest.json"
        ).read_text(encoding="utf-8"),
    )
    assert manifest["status"] == "pass"
    assert manifest["schema_version"] == "standard_peak_formal_product_output_v1"
    assert (
        manifest["product_output_role"]
        == "standard_peak_backfill_formal_product_output"
    )
    assert manifest["activation_output_mode"] == "matrix-only"
    assert manifest["activation_decision_scope"] == "machine_gate_standard_peak_rows"
    assert manifest["must_not_regress_basis"] == (
        "machine_shift_aware_standard_peak_gate"
    )
    assert manifest["standard_peak_gate_status"] == "pass"
    assert manifest["selected_activation_row_count"] == "2"
    assert manifest["matrix_cells_written"] == "2"
    assert manifest["skipped_non_standard_reason_count"] == "0"
    assert manifest["source_shadow_projection_sha256"]
    assert manifest["product_matrix_tsv"] == str(
        out / "formal_product_output" / "alignment_matrix.tsv",
    )
    assert "alignment_matrix.tsv" in manifest["artifact_sha256"]
    default_manifest = json.loads(
        (tmp_path / "standard_peak_default_matrix_manifest.json").read_text(
            encoding="utf-8",
        ),
    )
    assert default_manifest["status"] == "pass"
    assert default_manifest["default_matrix_status"] == (
        "standard_peak_backfill_applied"
    )
    assert default_manifest["matrix_cells_written"] == "2"
    assert default_manifest["published_alignment_matrix_tsv"] == str(
        fixture["matrix"],
    )
    assert default_manifest["alignment_matrix_backup_sha256"]
    assert default_manifest["alignment_matrix_identity_backup_sha256"]


def test_chunk_consolidation_allows_formal_output_noop_when_no_activation_rows(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    out = tmp_path / "out"
    for chunk_key in ("chunk_a", "chunk_b"):
        shadow = (
            fixture[chunk_key]
            / "shadow_projection"
            / "shadow_production_projection_cells.tsv"
        )
        rows = _read_tsv(shadow)
        for row in rows:
            row.update(
                {
                    "shadow_decision": "context",
                    "shadow_reasons": "evidence_gate_requires_review",
                    "projected_matrix_written": "FALSE",
                    "projected_matrix_value": "",
                    "product_authority_chain": "",
                },
            )
        _write_tsv(shadow, rows, SHADOW_PRODUCTION_PROJECTION_COLUMNS)

    assert (
        cli.main(
            [
                "--chunk-dir",
                str(fixture["chunk_a"]),
                "--chunk-dir",
                str(fixture["chunk_b"]),
                "--review-queue-tsv",
                str(fixture["queue"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(out),
                "--source-run-id",
                "unit-chunk-consolidation-noop",
                "--emit-formal-product-output",
            ],
        )
        == 0
    )

    summary = json.loads(
        (
            out / "standard_peak_backfill_chunk_consolidation_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["status"] == "pass"
    assert summary["coverage_status"] == "complete"
    assert summary["covered_queue_row_count"] == "2"
    assert summary["matrix_cells_written"] == ""
    assert summary["formal_product_output_dir"] == ""
    assert summary["formal_product_manifest_json"] == ""
    assert summary["published_alignment_manifest_json"] == ""
    assert not (out / "formal_product_output").exists()
    product_summary = json.loads(
        Path(summary["productization_summary_json"]).read_text(encoding="utf-8"),
    )
    assert product_summary["selected_activation_row_count"] == "0"
    assert _read_tsv(fixture["matrix"]) == [
        {"Mz": "300.1", "RT": "10.1", "S1": "10", "S2": ""},
        {"Mz": "301.1", "RT": "10.2", "S1": "20", "S2": ""},
    ]


def test_chunk_consolidation_dedupes_full_matrix_shadow_projection_rows(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    shadow_a = fixture["chunk_a"] / "shadow_projection" / (
        "shadow_production_projection_cells.tsv"
    )
    full_matrix_context_row = _shadow_row("FAM_B", "S2", "222")
    full_matrix_context_row.update(
        {
            "shadow_decision": "context",
            "shadow_reasons": "evidence_gate_requires_review",
            "projected_matrix_written": "FALSE",
            "projected_matrix_value": "",
            "product_authority_chain": "",
            "shadow_projection_row_sha256": "d" * 64,
            "evidence_gate_status": "evidence_missing",
            "missing_evidence": "missing_overlay_evidence",
        },
    )
    _write_tsv(
        shadow_a,
        _read_tsv(shadow_a) + [full_matrix_context_row],
        SHADOW_PRODUCTION_PROJECTION_COLUMNS,
    )
    out = tmp_path / "out"

    assert (
        cli.main(
            [
                "--chunk-dir",
                str(fixture["chunk_a"]),
                "--chunk-dir",
                str(fixture["chunk_b"]),
                "--review-queue-tsv",
                str(fixture["queue"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(out),
                "--emit-formal-product-output",
            ],
        )
        == 0
    )

    summary = json.loads(
        (
            out / "standard_peak_backfill_chunk_consolidation_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["merged_shadow_projection_row_count"] == "2"
    assert summary["matrix_cells_written"] == "2"
    projection_rows = _read_tsv(out / "consolidated_shadow_projection_cells.tsv")
    fam_b_rows = [
        row
        for row in projection_rows
        if row["peak_hypothesis_id"] == "FAM_B" and row["sample_stem"] == "S2"
    ]
    assert len(fam_b_rows) == 1
    assert fam_b_rows[0]["shadow_decision"] == "accept"
    assert fam_b_rows[0]["projected_matrix_written"] == "TRUE"
    assert fam_b_rows[0]["projected_matrix_value"] == "222"


def test_chunk_consolidation_refuses_conflicting_accepted_projection_rows(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    shadow_a = fixture["chunk_a"] / "shadow_projection" / (
        "shadow_production_projection_cells.tsv"
    )
    conflicting_accept = _shadow_row("FAM_B", "S2", "999")
    conflicting_accept["shadow_projection_row_sha256"] = "e" * 64
    _write_tsv(
        shadow_a,
        _read_tsv(shadow_a) + [conflicting_accept],
        SHADOW_PRODUCTION_PROJECTION_COLUMNS,
    )

    assert (
        cli.main(
            [
                "--chunk-dir",
                str(fixture["chunk_a"]),
                "--chunk-dir",
                str(fixture["chunk_b"]),
                "--review-queue-tsv",
                str(fixture["queue"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(tmp_path / "out"),
            ],
        )
        == 2
    )


def test_chunk_consolidation_fails_when_queue_rank_is_missing(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)

    assert (
        cli.main(
            [
                "--chunk-dir",
                str(fixture["chunk_a"]),
                "--review-queue-tsv",
                str(fixture["queue"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(tmp_path / "out"),
            ],
        )
        == 1
    )

    summary = json.loads(
        (
            tmp_path
            / "out"
            / "standard_peak_backfill_chunk_consolidation_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["status"] == "fail"
    assert summary["coverage_status"] == "incomplete"
    assert summary["missing_queue_rank_count"] == "1"
    assert "queue_coverage_missing:2" in summary["status_reasons"]


def test_chunk_consolidation_can_keep_formal_output_as_sidecar_only(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    out = tmp_path / "out"

    assert (
        cli.main(
            [
                "--chunk-dir",
                str(fixture["chunk_a"]),
                "--chunk-dir",
                str(fixture["chunk_b"]),
                "--review-queue-tsv",
                str(fixture["queue"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(out),
                "--emit-formal-product-output",
                "--no-publish-to-source-alignment-output",
            ],
        )
        == 0
    )

    summary = json.loads(
        (
            out / "standard_peak_backfill_chunk_consolidation_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["status"] == "pass"
    assert summary["formal_product_manifest_json"]
    assert summary["published_alignment_manifest_json"] == ""
    source_rows = _read_tsv(fixture["matrix"])
    assert source_rows[0]["S2"] == ""
    assert source_rows[1]["S2"] == ""
    assert not (tmp_path / "standard_peak_default_matrix_manifest.json").exists()


def test_chunk_consolidation_can_rerun_from_backup_and_publish_to_final_matrix(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    out = tmp_path / "out"
    base_args = [
        "--chunk-dir",
        str(fixture["chunk_a"]),
        "--chunk-dir",
        str(fixture["chunk_b"]),
        "--review-queue-tsv",
        str(fixture["queue"]),
        "--alignment-review-tsv",
        str(fixture["review"]),
        "--output-dir",
        str(out),
        "--emit-formal-product-output",
    ]

    assert (
        cli.main(
            [
                *base_args,
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
            ],
        )
        == 0
    )
    backup_matrix = tmp_path / "alignment_matrix.pre_standard_peak_backfill.tsv"
    backup_identity = (
        tmp_path / "alignment_matrix_identity.pre_standard_peak_backfill.tsv"
    )
    assert backup_matrix.exists()
    assert backup_identity.exists()

    assert (
        cli.main(
            [
                *base_args,
                "--alignment-matrix-tsv",
                str(backup_matrix),
                "--alignment-matrix-identity-tsv",
                str(backup_identity),
                "--publish-alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--publish-alignment-matrix-identity-tsv",
                str(fixture["identity"]),
            ],
        )
        == 0
    )

    summary = json.loads(
        (
            out / "standard_peak_backfill_chunk_consolidation_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["status"] == "pass"
    assert summary["matrix_cells_written"] == "2"
    assert summary["published_alignment_manifest_json"] == str(
        tmp_path / "standard_peak_default_matrix_manifest.json",
    )
    assert _read_tsv(fixture["matrix"]) == _read_tsv(
        out / "formal_product_output" / "alignment_matrix.tsv",
    )


def test_chunk_consolidation_does_not_emit_formal_output_when_coverage_fails(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    out = tmp_path / "out"

    assert (
        cli.main(
            [
                "--chunk-dir",
                str(fixture["chunk_a"]),
                "--review-queue-tsv",
                str(fixture["queue"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(out),
                "--emit-formal-product-output",
            ],
        )
        == 1
    )

    summary = json.loads(
        (
            out / "standard_peak_backfill_chunk_consolidation_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["status"] == "fail"
    assert summary["coverage_status"] == "incomplete"
    assert summary["formal_product_output_dir"] == ""
    assert summary["formal_product_manifest_json"] == ""
    assert not (out / "formal_product_output").exists()


def test_chunk_consolidation_clears_stale_formal_output_when_rerun_fails(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    out = tmp_path / "out"
    pass_args = [
        "--chunk-dir",
        str(fixture["chunk_a"]),
        "--chunk-dir",
        str(fixture["chunk_b"]),
        "--review-queue-tsv",
        str(fixture["queue"]),
        "--alignment-matrix-tsv",
        str(fixture["matrix"]),
        "--alignment-matrix-identity-tsv",
        str(fixture["identity"]),
        "--alignment-review-tsv",
        str(fixture["review"]),
        "--output-dir",
        str(out),
        "--emit-formal-product-output",
    ]
    assert cli.main(pass_args) == 0
    assert (
        out
        / "formal_product_output"
        / "standard_peak_formal_product_manifest.json"
    ).exists()
    assert (out / "formal_product_output" / "alignment_matrix.tsv").exists()

    fail_args = [
        "--chunk-dir",
        str(fixture["chunk_a"]),
        "--review-queue-tsv",
        str(fixture["queue"]),
        "--alignment-matrix-tsv",
        str(fixture["matrix"]),
        "--alignment-matrix-identity-tsv",
        str(fixture["identity"]),
        "--alignment-review-tsv",
        str(fixture["review"]),
        "--output-dir",
        str(out),
        "--emit-formal-product-output",
    ]
    assert cli.main(fail_args) == 1

    summary = json.loads(
        (
            out / "standard_peak_backfill_chunk_consolidation_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["status"] == "fail"
    assert summary["formal_product_manifest_json"] == ""
    assert not (
        out
        / "formal_product_output"
        / "standard_peak_formal_product_manifest.json"
    ).exists()
    assert not (out / "formal_product_output" / "alignment_matrix.tsv").exists()


def test_chunk_consolidation_requires_queue_for_formal_output(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    out = tmp_path / "out"

    assert (
        cli.main(
            [
                "--chunk-dir",
                str(fixture["chunk_a"]),
                "--chunk-dir",
                str(fixture["chunk_b"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(out),
                "--emit-formal-product-output",
            ],
        )
        == 1
    )

    summary = json.loads(
        (
            out / "standard_peak_backfill_chunk_consolidation_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["status"] == "fail"
    assert summary["coverage_status"] == "not_checked"
    assert "formal_product_output_requires_review_queue" in summary["status_reasons"]
    assert summary["formal_product_manifest_json"] == ""
    assert not (out / "formal_product_output").exists()


def test_chunk_consolidation_requires_publish_targets_as_pair(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)

    assert (
        cli.main(
            [
                "--chunk-dir",
                str(fixture["chunk_a"]),
                "--chunk-dir",
                str(fixture["chunk_b"]),
                "--review-queue-tsv",
                str(fixture["queue"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(tmp_path / "out"),
                "--emit-formal-product-output",
                "--publish-alignment-matrix-tsv",
                str(tmp_path / "final_alignment_matrix.tsv"),
            ],
        )
        == 2
    )


def test_chunk_consolidation_does_not_emit_formal_output_for_duplicate_rank(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    overlay_b = (
        fixture["chunk_b"]
        / "family_ms1_overlay_batch"
        / "family_ms1_overlay_batch_summary.tsv"
    )
    _write_tsv(
        overlay_b,
        [{"rank": "1", "feature_family_id": "FAM_B"}],
        ("rank", "feature_family_id"),
    )
    out = tmp_path / "out"

    assert (
        cli.main(
            [
                "--chunk-dir",
                str(fixture["chunk_a"]),
                "--chunk-dir",
                str(fixture["chunk_b"]),
                "--review-queue-tsv",
                str(fixture["queue"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(out),
                "--emit-formal-product-output",
            ],
        )
        == 1
    )

    summary = json.loads(
        (
            out / "standard_peak_backfill_chunk_consolidation_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["status"] == "fail"
    assert summary["duplicate_queue_rank_count"] == "1"
    assert "queue_coverage_duplicate_ranks:1" in summary["status_reasons"]
    assert summary["formal_product_manifest_json"] == ""
    assert not (out / "formal_product_output").exists()


def test_chunk_consolidation_formal_output_keeps_unauthorized_rows_blank(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    shadow_b = fixture["chunk_b"] / "shadow_projection" / (
        "shadow_production_projection_cells.tsv"
    )
    blocked_row = _shadow_row("FAM_B", "S2", "222")
    blocked_row.update(
        {
            "shadow_decision": "block",
            "shadow_reasons": "nonstandard_peak_shape_blocked",
            "projected_matrix_written": "FALSE",
            "product_authority_chain": "",
            "hard_blockers": "nonstandard_peak_shape",
        },
    )
    _write_tsv(shadow_b, [blocked_row], SHADOW_PRODUCTION_PROJECTION_COLUMNS)
    out = tmp_path / "out"

    assert (
        cli.main(
            [
                "--chunk-dir",
                str(fixture["chunk_a"]),
                "--chunk-dir",
                str(fixture["chunk_b"]),
                "--review-queue-tsv",
                str(fixture["queue"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(out),
                "--emit-formal-product-output",
            ],
        )
        == 0
    )

    summary = json.loads(
        (
            out / "standard_peak_backfill_chunk_consolidation_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["status"] == "pass"
    assert summary["coverage_status"] == "complete"
    assert summary["matrix_cells_written"] == "1"
    formal_rows = _read_tsv(out / "formal_product_output" / "alignment_matrix.tsv")
    identity_rows = _read_tsv(
        out / "formal_product_output" / "alignment_matrix_identity.tsv",
    )
    row_by_hypothesis = {
        row["peak_hypothesis_id"]: int(row["matrix_row_index"]) - 1
        for row in identity_rows
    }
    assert formal_rows[row_by_hypothesis["FAM_A"]]["S2"] == "1.11000e+02"
    assert formal_rows[row_by_hypothesis["FAM_B"]]["S2"] == ""
    delta_rows = _read_tsv(
        out / "formal_product_output" / "activation_value_delta.tsv",
    )
    assert len(delta_rows) == 1
    assert delta_rows[0]["peak_hypothesis_id"] == "FAM_A"


def test_chunk_consolidation_uses_actual_overlay_ranks_for_coverage(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    summary_path = (
        fixture["chunk_b"] / "standard_peak_backfill_machine_pipeline_summary.json"
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["start_rank"] = 99
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    assert (
        cli.main(
            [
                "--chunk-dir",
                str(fixture["chunk_a"]),
                "--chunk-dir",
                str(fixture["chunk_b"]),
                "--review-queue-tsv",
                str(fixture["queue"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(tmp_path / "out"),
            ],
        )
        == 0
    )

    summary = json.loads(
        (
            tmp_path
            / "out"
            / "standard_peak_backfill_chunk_consolidation_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["coverage_status"] == "complete"
    assert summary["missing_queue_rank_count"] == "0"
    assert summary["duplicate_queue_rank_count"] == "0"


def test_chunk_consolidation_refuses_formal_output_over_source_alignment_dir(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)

    assert (
        cli.main(
            [
                "--chunk-dir",
                str(fixture["chunk_a"]),
                "--chunk-dir",
                str(fixture["chunk_b"]),
                "--review-queue-tsv",
                str(fixture["queue"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(tmp_path / "out"),
                "--emit-formal-product-output",
                "--formal-product-output-dir",
                str(fixture["matrix"].parent),
            ],
        )
        == 2
    )


def _write_fixture(tmp_path: Path) -> dict[str, Path]:
    matrix = tmp_path / "alignment_matrix.tsv"
    identity = tmp_path / "alignment_matrix_identity.tsv"
    review = tmp_path / "alignment_review.tsv"
    queue = tmp_path / "review_queue.tsv"
    chunk_a = tmp_path / "chunk_a"
    chunk_b = tmp_path / "chunk_b"

    _write_tsv(
        matrix,
        [
            {"Mz": "300.1", "RT": "10.1", "S1": "10", "S2": ""},
            {"Mz": "301.1", "RT": "10.2", "S1": "20", "S2": ""},
        ],
        ("Mz", "RT", "S1", "S2"),
    )
    _write_tsv(
        identity,
        [
            _identity_row("1", "300.1", "10.1", "FAM_A"),
            _identity_row("2", "301.1", "10.2", "FAM_B"),
        ],
        (
            "matrix_row_index",
            "Mz",
            "RT",
            "peak_hypothesis_id",
            "row_identity_basis",
            "source_feature_family_ids",
        ),
    )
    _write_tsv(
        review,
        [
            _review_row("FAM_A", "300.1", "10.1"),
            _review_row("FAM_B", "301.1", "10.2"),
        ],
        ALIGNMENT_REVIEW_COLUMNS,
    )
    _write_tsv(
        queue,
        [
            {"feature_family_id": "FAM_A"},
            {"feature_family_id": "FAM_B"},
        ],
        ("feature_family_id",),
    )
    _write_chunk(chunk_a, rank=1, family="FAM_A", sample="S2", value="111")
    _write_chunk(chunk_b, rank=2, family="FAM_B", sample="S2", value="222")
    return {
        "matrix": matrix,
        "identity": identity,
        "review": review,
        "queue": queue,
        "chunk_a": chunk_a,
        "chunk_b": chunk_b,
    }


def _write_chunk(
    chunk_dir: Path,
    *,
    rank: int,
    family: str,
    sample: str,
    value: str,
) -> None:
    shadow = chunk_dir / "shadow_projection" / "shadow_production_projection_cells.tsv"
    overlay = (
        chunk_dir / "family_ms1_overlay_batch" / "family_ms1_overlay_batch_summary.tsv"
    )
    gate = (
        chunk_dir
        / "shift_aware_standard_peak_gate"
        / "shift_aware_standard_peak_gate_calibration.tsv"
    )
    _write_tsv(
        shadow,
        [_shadow_row(family, sample, value)],
        SHADOW_PRODUCTION_PROJECTION_COLUMNS,
    )
    _write_tsv(
        overlay,
        [{"rank": str(rank), "feature_family_id": family}],
        ("rank", "feature_family_id"),
    )
    _write_tsv(
        gate,
        [
            {
                "feature_family_id": family,
                "standard_peak_gate_call": "standard_peak_gate_supported",
                "standard_peak_gate_reasons": (
                    "shift_aware_same_pattern_supported;"
                    "family_overlay_gaussian_smoothed_standard_peak_supported"
                ),
                "standard_peak_gate_blockers": "",
            },
        ],
        (
            "feature_family_id",
            "standard_peak_gate_call",
            "standard_peak_gate_reasons",
            "standard_peak_gate_blockers",
        ),
    )
    summary = {
        "status": "pass",
        "start_rank": rank,
        "overlay_selected_row_count": 1,
        "shift_aware_selected_row_count": 1,
        "shadow_projection_cells_tsv": str(shadow),
        "overlay_batch_summary_tsv": str(overlay),
        "shift_aware_standard_peak_gate_tsv": str(gate),
    }
    (chunk_dir / "standard_peak_backfill_machine_pipeline_summary.json").write_text(
        json.dumps(summary),
        encoding="utf-8",
    )


def _identity_row(index: str, mz: str, rt: str, family: str) -> dict[str, str]:
    return {
        "matrix_row_index": index,
        "Mz": mz,
        "RT": rt,
        "peak_hypothesis_id": family,
        "row_identity_basis": "no_split_peak_hypothesis",
        "source_feature_family_ids": family,
    }


def _review_row(family: str, mz: str, rt: str) -> dict[str, str]:
    row = _blank_row(ALIGNMENT_REVIEW_COLUMNS)
    row.update(
        {
            "feature_family_id": family,
            "group_hypothesis_id": f"{family}::group",
            "public_family_id": family,
            "family_center_mz": mz,
            "family_center_rt": rt,
            "detected_count": "1",
            "accepted_cell_count": "1",
            "accepted_rescue_count": "1",
            "identity_decision": "provisional_discovery",
            "identity_confidence": "review_only",
            "primary_evidence": "owner_backfill_context",
            "identity_reason": "owner_backfill_context",
            "include_in_primary_matrix": "FALSE",
            "reason": "fixture",
        },
    )
    return row


def _shadow_row(family: str, sample: str, value: str) -> dict[str, str]:
    row = _blank_row(SHADOW_PRODUCTION_PROJECTION_COLUMNS)
    row.update(
        {
            "schema_version": "shadow_production_projection_v1",
            "peak_hypothesis_id": family,
            "activation_unit_scope": "peak_hypothesis",
            "feature_family_id": family,
            "sample_stem": sample,
            "current_raw_status": "rescued",
            "current_production_status": "review_rescue",
            "current_matrix_written": "FALSE",
            "shadow_decision": "accept",
            "shadow_reasons": (
                "same_peak_reason:shift_aware_standard_peak_gate_supported"
            ),
            "projected_matrix_written": "TRUE",
            "projected_matrix_value": value,
            "product_authority_chain": (
                "MS1:product_authorized:supportive:trace_constellation:"
                "feature_family_sample:machine_standard_peak_gate_authorized | "
                "same_peak_reason:shift_aware_standard_peak_gate_supported"
            ),
            "shadow_projection_row_sha256": "c" * 64,
            "evidence_gate_status": "visual_support",
            "support_components": "seed_request_provenance",
            "hard_blockers": "",
            "overlay_verdict": "ms1_shape_supports_family_backfill",
        },
    )
    return row


def _blank_row(columns: tuple[str, ...]) -> dict[str, str]:
    return {column: "" for column in columns}


def _write_tsv(
    path: Path,
    rows: list[dict[str, str]],
    fieldnames: tuple[str, ...],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
