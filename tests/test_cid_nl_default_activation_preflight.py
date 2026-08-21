import csv
import json
from pathlib import Path

from scripts.check_cid_nl_default_activation_preflight import (
    evaluate_cid_nl_default_activation_preflight,
    main,
)


def test_preflight_blocks_when_current_authority_ids_do_not_replay(
    tmp_path: Path,
) -> None:
    paths = _write_preflight_fixture(tmp_path, manifest_peak_id="FAM_OLD")

    payload = evaluate_cid_nl_default_activation_preflight(
        **paths,
        expected_focus_nonblank_count=1,
    )

    assert payload["overall_status"] == "blocked"
    assert payload["target_alignment_evidence_status"] == "pass"
    assert payload["readiness_label"] == "production_candidate"
    assert payload["replay"]["accepted_authority_cell_count"] == 1
    assert payload["replay"]["accepted_missing_identity_count"] == 1
    assert "peak_hypothesis_id missing from matrix identity" in payload["replay"][
        "first_blocking_reason"
    ]
    assert payload["target_pairs"]["focus_300_184"]["peak_hypothesis_id"] == (
        "FAM300"
    )
    assert payload["target_pairs"]["preserve_301_185"]["review"][
        "neutral_loss_tag"
    ] == "DNA_dR"


def test_preflight_passes_when_replay_and_target_provenance_match(
    tmp_path: Path,
) -> None:
    paths = _write_preflight_fixture(tmp_path, manifest_peak_id="FAM_BACKFILL")

    payload = evaluate_cid_nl_default_activation_preflight(
        **paths,
        expected_focus_nonblank_count=1,
    )

    assert payload["overall_status"] == "pass"
    assert payload["replay"]["accepted_missing_identity_count"] == 0
    assert payload["target_pairs"]["focus_300_184"]["provenance"][
        "focus_sample_row"
    ]["source_candidate_id"] == "TumorBC2312_DNA#19561@mz300.160635_p184.113235"
    assert payload["target_pairs"]["preserve_301_185"]["provenance"][
        "focus_sample_row"
    ]["source_candidate_id"] == "TumorBC2312_DNA#19561@mz301.164978_p185.115845"


def test_preflight_rejects_product_row_without_matching_source_provenance(
    tmp_path: Path,
) -> None:
    paths = _write_preflight_fixture(
        tmp_path,
        manifest_peak_id="FAM_BACKFILL",
        preserve_source_candidate_id=(
            "TumorBC2312_DNA#19561@mz301.164978_p184.113235"
        ),
    )

    payload = evaluate_cid_nl_default_activation_preflight(
        **paths,
        expected_focus_nonblank_count=1,
    )

    assert payload["overall_status"] == "fail"
    assert any(
        "preserve_301_185: source_candidate_id product does not match target"
        in problem
        for problem in payload["problems"]
    )


def test_preflight_rejects_wrong_source_candidate_identity(
    tmp_path: Path,
) -> None:
    paths = _write_preflight_fixture(
        tmp_path,
        manifest_peak_id="FAM_BACKFILL",
        preserve_source_candidate_id=(
            "WrongSample#19561@mz301.164978_p185.115845"
        ),
    )

    payload = evaluate_cid_nl_default_activation_preflight(
        **paths,
        expected_focus_nonblank_count=1,
    )

    assert payload["overall_status"] == "fail"
    assert any(
        "preserve_301_185: source_candidate_id does not match expected source"
        in problem
        for problem in payload["problems"]
    )


def test_preflight_rejects_provenance_family_identity_mismatch(
    tmp_path: Path,
) -> None:
    paths = _write_preflight_fixture(
        tmp_path,
        manifest_peak_id="FAM_BACKFILL",
        preserve_feature_family_id="FAM_WRONG",
    )

    payload = evaluate_cid_nl_default_activation_preflight(
        **paths,
        expected_focus_nonblank_count=1,
    )

    assert payload["overall_status"] == "fail"
    assert any(
        "preserve_301_185: provenance feature_family_id does not match target"
        in problem
        for problem in payload["problems"]
    )


def test_preflight_rejects_duplicate_focus_sample_provenance_rows(
    tmp_path: Path,
) -> None:
    paths = _write_preflight_fixture(
        tmp_path,
        manifest_peak_id="FAM_BACKFILL",
        duplicate_preserve_provenance=True,
    )

    payload = evaluate_cid_nl_default_activation_preflight(
        **paths,
        expected_focus_nonblank_count=1,
    )

    assert payload["overall_status"] == "fail"
    assert any(
        "preserve_301_185: expected exactly one provenance row for TumorBC2312_DNA"
        in problem
        for problem in payload["problems"]
    )


def test_preflight_cli_writes_blocked_summary_without_requiring_pass(
    tmp_path: Path,
) -> None:
    paths = _write_preflight_fixture(tmp_path, manifest_peak_id="FAM_OLD")
    summary_json = tmp_path / "summary.json"

    status = main(
        [
            "--input-quant-matrix-tsv",
            str(paths["input_quant_matrix_tsv"]),
            "--input-matrix-identity-tsv",
            str(paths["input_matrix_identity_tsv"]),
            "--alignment-review-tsv",
            str(paths["alignment_review_tsv"]),
            "--backfill-cell-evidence-tsv",
            str(paths["backfill_cell_evidence_tsv"]),
            "--production-acceptance-manifest-tsv",
            str(paths["production_acceptance_manifest_tsv"]),
            "--expected-diff-tsv",
            str(paths["expected_diff_tsv"]),
            "--summary-json",
            str(summary_json),
            "--expected-focus-nonblank-count",
            "1",
        ]
    )

    assert status == 0
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    assert payload["overall_status"] == "blocked"
    assert payload["product_surface_changed"] is False


def _write_preflight_fixture(
    tmp_path: Path,
    *,
    manifest_peak_id: str,
    preserve_source_candidate_id: str = (
        "TumorBC2312_DNA#19561@mz301.164978_p185.115845"
    ),
    preserve_feature_family_id: str = "FAM301",
    duplicate_preserve_provenance: bool = False,
) -> dict[str, Path]:
    matrix = tmp_path / "alignment_matrix.tsv"
    identity = tmp_path / "alignment_matrix_identity.tsv"
    review = tmp_path / "alignment_review.tsv"
    evidence = tmp_path / "alignment_backfill_cell_evidence.tsv"
    manifest = tmp_path / "production_acceptance_manifest.tsv"
    expected_diff = tmp_path / "expected_diff.tsv"

    _write_tsv(
        matrix,
        ("Mz", "RT", "SampleA", "TumorBC2312_DNA"),
        [
            {
                "Mz": "250.0",
                "RT": "10.0",
                "SampleA": "",
                "TumorBC2312_DNA": "",
            },
            {
                "Mz": "300.1606",
                "RT": "23.3493",
                "SampleA": "",
                "TumorBC2312_DNA": "100",
            },
            {
                "Mz": "301.165",
                "RT": "23.3413",
                "SampleA": "",
                "TumorBC2312_DNA": "50",
            },
        ],
    )
    _write_tsv(
        identity,
        (
            "matrix_row_index",
            "Mz",
            "RT",
            "peak_hypothesis_id",
            "row_identity_basis",
            "projection_status",
            "source_feature_family_ids",
            "source_feature_family_count",
            "accepted_cell_count",
            "accepted_sample_count",
            "evidence_status",
            "parent_peak_hypothesis_id",
            "child_peak_hypothesis_ids",
        ),
        [
            _identity_row(1, "250.0", "10.0", "FAM_BACKFILL", "0"),
            _identity_row(2, "300.1606", "23.3493", "FAM300", "1"),
            _identity_row(3, "301.165", "23.3413", "FAM301", "1"),
        ],
    )
    _write_tsv(
        review,
        (
            "group_hypothesis_id",
            "neutral_loss_tag",
            "family_center_mz",
            "family_center_rt",
            "family_product_mz",
            "identity_confidence",
            "accepted_cell_count",
            "include_in_primary_matrix",
            "row_flags",
            "consolidation_state",
        ),
        [
            _review_row(
                "FAM300",
                "300.1606",
                "184.113",
                "high",
                "",
            ),
            _review_row(
                "FAM301",
                "301.165",
                "185.116",
                "review",
                "backfill_cell_evidence_required",
            ),
        ],
    )
    evidence_rows = [
        _evidence_row(
            "FAM300",
            "100",
            "300.1606",
            "TumorBC2312_DNA#19561@mz300.160635_p184.113235",
        ),
        _evidence_row(
            "FAM301",
            "50",
            "301.165",
            preserve_source_candidate_id,
            feature_family_id=preserve_feature_family_id,
            row_flags="backfill_cell_evidence_required",
        ),
    ]
    if duplicate_preserve_provenance:
        evidence_rows.append(
            _evidence_row(
                "FAM301",
                "50",
                "301.165",
                preserve_source_candidate_id,
                row_flags="backfill_cell_evidence_required",
            )
        )
    _write_tsv(
        evidence,
        (
            "feature_family_id",
            "group_hypothesis_id",
            "public_family_id",
            "sample_stem",
            "status",
            "production_cell_status",
            "write_matrix_value",
            "include_in_primary_matrix",
            "identity_decision",
            "row_flags",
            "primary_matrix_area",
            "primary_matrix_area_source",
            "source_candidate_id",
            "neutral_loss_tag",
            "family_center_mz",
            "family_center_rt",
            "reason",
        ),
        evidence_rows,
    )
    _write_tsv(
        manifest,
        (
            "peak_hypothesis_id",
            "sample_stem",
            "acceptance_decision",
            "write_authority",
            "matrix_write_allowed",
            "shadow_only",
            "quant_value",
            "acceptance_basis",
            "truth_status",
            "quant_value_source",
            "matrix_area_source",
            "source_artifact_relpath",
            "source_artifact_sha256",
            "source_row_sha256",
            "manifest_sha256",
        ),
        [
            {
                "peak_hypothesis_id": manifest_peak_id,
                "sample_stem": "SampleA",
                "acceptance_decision": "accept_basic_backfill",
                "write_authority": "TRUE",
                "matrix_write_allowed": "TRUE",
                "shadow_only": "FALSE",
                "quant_value": "123",
                "acceptance_basis": "synthetic",
                "truth_status": "not_truth_claimed",
                "quant_value_source": "synthetic",
                "matrix_area_source": "synthetic",
                "source_artifact_relpath": "synthetic.tsv",
                "source_artifact_sha256": "abc",
                "source_row_sha256": "def",
                "manifest_sha256": "ghi",
            }
        ],
    )
    _write_tsv(
        expected_diff,
        (
            "schema_version",
            "peak_hypothesis_id",
            "sample_stem",
            "baseline_value",
            "activated_value",
            "expected_matrix_effect",
            "expected_reason",
        ),
        [
            {
                "schema_version": "quant_matrix_version_expected_diff_v1",
                "peak_hypothesis_id": manifest_peak_id,
                "sample_stem": "SampleA",
                "baseline_value": "",
                "activated_value": "123",
                "expected_matrix_effect": "write_accepted_backfill",
                "expected_reason": "synthetic",
            }
        ],
    )
    return {
        "input_quant_matrix_tsv": matrix,
        "input_matrix_identity_tsv": identity,
        "alignment_review_tsv": review,
        "backfill_cell_evidence_tsv": evidence,
        "production_acceptance_manifest_tsv": manifest,
        "expected_diff_tsv": expected_diff,
    }


def _identity_row(
    index: int,
    mz: str,
    rt: str,
    peak_id: str,
    accepted_count: str,
) -> dict[str, str]:
    return {
        "matrix_row_index": str(index),
        "Mz": mz,
        "RT": rt,
        "peak_hypothesis_id": peak_id,
        "row_identity_basis": "no_split_peak_hypothesis",
        "projection_status": "not_projection",
        "source_feature_family_ids": peak_id,
        "source_feature_family_count": "1",
        "accepted_cell_count": accepted_count,
        "accepted_sample_count": accepted_count,
        "evidence_status": "product_matrix_identity_complete",
        "parent_peak_hypothesis_id": "",
        "child_peak_hypothesis_ids": "",
    }


def _review_row(
    peak_id: str,
    center_mz: str,
    product_mz: str,
    confidence: str,
    row_flags: str,
) -> dict[str, str]:
    return {
        "group_hypothesis_id": peak_id,
        "neutral_loss_tag": "DNA_dR",
        "family_center_mz": center_mz,
        "family_center_rt": "23.35",
        "family_product_mz": product_mz,
        "identity_confidence": confidence,
        "accepted_cell_count": "1",
        "include_in_primary_matrix": "TRUE",
        "row_flags": row_flags,
        "consolidation_state": "primary_winner",
    }


def _evidence_row(
    peak_id: str,
    primary_matrix_area: str,
    center_mz: str,
    source_candidate_id: str,
    *,
    feature_family_id: str | None = None,
    row_flags: str = "",
) -> dict[str, str]:
    family_id = feature_family_id or peak_id
    return {
        "feature_family_id": family_id,
        "group_hypothesis_id": peak_id,
        "public_family_id": family_id,
        "sample_stem": "TumorBC2312_DNA",
        "status": "detected",
        "production_cell_status": "detected",
        "write_matrix_value": "TRUE",
        "include_in_primary_matrix": "TRUE",
        "identity_decision": "production_family",
        "row_flags": row_flags,
        "primary_matrix_area": primary_matrix_area,
        "primary_matrix_area_source": "synthetic",
        "source_candidate_id": source_candidate_id,
        "neutral_loss_tag": "DNA_dR",
        "family_center_mz": center_mz,
        "family_center_rt": "23.35",
        "reason": "sample-local MS1 owner with original MS2 evidence",
    }


def _write_tsv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(
            {field: row.get(field, "") for field in fieldnames} for row in rows
        )
