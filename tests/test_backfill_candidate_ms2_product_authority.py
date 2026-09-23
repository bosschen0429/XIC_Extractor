from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from tools.diagnostics.authorize_backfill_candidate_ms2_pattern_evidence import main
from xic_extractor.alignment.backfill_candidate_ms2_product_authority import (
    ALLOWLIST_COLUMNS,
    SCHEMA_VERSION,
    authorize_candidate_ms2_pattern_rows,
    output_columns,
    source_row_sha256,
)
from xic_extractor.alignment.backfill_evidence_projection import (
    PRODUCT_AUTHORITY_SCOPE_FIELD,
    PRODUCT_AUTHORITY_SOURCE_FIELD,
    PRODUCT_AUTHORITY_STATUS_FIELD,
    PRODUCT_AUTHORIZED_SCOPE,
    PRODUCT_AUTHORIZED_STATUS,
    project_backfill_evidence_to_cells,
)
from xic_extractor.alignment.promotion_policy import (
    ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
    evidence_from_tsv_rows,
)


def test_authorizes_allowlisted_candidate_ms2_pattern_row() -> None:
    source = _candidate_ms2_row()
    result = authorize_candidate_ms2_pattern_rows(
        candidate_ms2_pattern_rows=[source],
        allowlist_rows=[_allowlist_row(source)],
    )

    assert result.summary["authorized_row_count"] == 1
    row = result.authorized_rows[0]
    assert row["diagnostic_only"] == "FALSE"
    assert row[PRODUCT_AUTHORITY_STATUS_FIELD] == PRODUCT_AUTHORIZED_STATUS
    assert row[PRODUCT_AUTHORITY_SCOPE_FIELD] == PRODUCT_AUTHORIZED_SCOPE
    assert row[PRODUCT_AUTHORITY_SOURCE_FIELD] == "manual_candidate_ms2_review"
    assert row["product_authority_observed_candidate_ms2_similarity_score"] == "1"
    assert (
        row["product_authority_candidate_ms2_source_row_sha256"]
        == source_row_sha256(source)
    )
    assert result.audit_rows[0]["decision"] == "authorized"


def test_rejects_not_observed_candidate_ms2_pattern_row() -> None:
    source = {
        **_candidate_ms2_row(),
        "candidate_ms2_pattern_status": "not_observed",
        "candidate_ms2_evidence_level": "sample_boundary_no_observed_pattern",
        "candidate_ms2_similarity_score": "",
    }
    result = authorize_candidate_ms2_pattern_rows(
        candidate_ms2_pattern_rows=[source],
        allowlist_rows=[
            _allowlist_row(
                source,
                expected_status="not_observed",
                expected_level="sample_boundary_no_observed_pattern",
            )
        ],
    )

    assert result.summary["authorized_row_count"] == 0
    assert result.audit_rows[0]["decision"] == "rejected"
    assert (
        result.audit_rows[0]["decision_reason"]
        == "source_candidate_ms2_pattern_not_supportive"
    )


def test_rejects_candidate_ms2_source_row_hash_mismatch() -> None:
    source = _candidate_ms2_row()
    result = authorize_candidate_ms2_pattern_rows(
        candidate_ms2_pattern_rows=[source],
        allowlist_rows=[
            {
                **_allowlist_row(source),
                "expected_candidate_ms2_source_row_sha256": "0" * 64,
            }
        ],
    )

    assert result.summary["authorized_row_count"] == 0
    assert (
        result.audit_rows[0]["decision_reason"]
        == "allowlist_candidate_ms2_source_row_sha256_mismatch"
    )


def test_rejects_candidate_ms2_allowlist_status_level_or_alignment_drift() -> None:
    source = _candidate_ms2_row()
    result = authorize_candidate_ms2_pattern_rows(
        candidate_ms2_pattern_rows=[source],
        allowlist_rows=[
            {
                **_allowlist_row(source),
                "expected_ms2_alignment_source": "discovery_source_candidate",
            }
        ],
    )

    assert result.summary["authorized_row_count"] == 0
    assert (
        result.audit_rows[0]["decision_reason"]
        == "allowlist_ms2_alignment_source_mismatch"
    )


def test_rejects_candidate_ms2_source_without_full_producer_columns() -> None:
    source = {
        "candidate_ms2_pattern_schema_version": (
            "shared_peak_identity_candidate_ms2_pattern"
        ),
        "feature_family_id": "FAM_MS2",
        "sample_stem": "S2",
        "candidate_ms2_pattern_status": "supportive",
        "candidate_ms2_evidence_level": "sample_boundary_aligned",
        "candidate_ms2_similarity_score": "1",
        "ms2_alignment_source": "raw_boundary_scan",
        "diagnostic_only": "TRUE",
    }
    result = authorize_candidate_ms2_pattern_rows(
        candidate_ms2_pattern_rows=[source],
        allowlist_rows=[_allowlist_row(source)],
    )

    assert result.summary["authorized_row_count"] == 0
    assert (
        result.audit_rows[0]["decision_reason"]
        == "source_candidate_ms2_full_producer_columns_missing"
    )


def test_rejects_candidate_ms2_source_with_wrong_schema_version() -> None:
    source = {
        **_candidate_ms2_row(),
        "candidate_ms2_pattern_schema_version": "legacy_candidate_ms2_pattern",
    }
    result = authorize_candidate_ms2_pattern_rows(
        candidate_ms2_pattern_rows=[source],
        allowlist_rows=[_allowlist_row(source)],
    )

    assert result.summary["authorized_row_count"] == 0
    assert (
        result.audit_rows[0]["decision_reason"]
        == "source_candidate_ms2_schema_version_mismatch"
    )


def test_rejects_boundary_candidate_ms2_without_strict_nl_evidence() -> None:
    source = {
        **_candidate_ms2_row(),
        "raw_ms2_strict_nl_scan_count": "0",
    }
    result = authorize_candidate_ms2_pattern_rows(
        candidate_ms2_pattern_rows=[source],
        allowlist_rows=[_allowlist_row(source)],
    )

    assert result.summary["authorized_row_count"] == 0
    assert (
        result.audit_rows[0]["decision_reason"]
        == "source_candidate_ms2_boundary_strict_nl_missing"
    )


def test_rejects_candidate_ms2_similarity_below_threshold() -> None:
    source = {
        **_candidate_ms2_row(
            status="partial_support",
            score="0.5",
        ),
        "reason": "raw_boundary_ms2_pattern_warn_band_matches_family_context",
    }
    result = authorize_candidate_ms2_pattern_rows(
        candidate_ms2_pattern_rows=[source],
        allowlist_rows=[
            {
                **_allowlist_row(source, expected_status="partial_support"),
                "min_candidate_ms2_similarity_score": "0.75",
            }
        ],
    )

    assert result.summary["authorized_row_count"] == 0
    assert (
        result.audit_rows[0]["decision_reason"]
        == "source_candidate_ms2_similarity_score_below_threshold"
    )


def test_duplicate_candidate_ms2_source_keys_raise() -> None:
    with pytest.raises(
        ValueError,
        match="duplicate backfill Candidate MS2 product authority source key",
    ):
        authorize_candidate_ms2_pattern_rows(
            candidate_ms2_pattern_rows=[_candidate_ms2_row(), _candidate_ms2_row()],
            allowlist_rows=[_allowlist_row(_candidate_ms2_row())],
        )


def test_duplicate_candidate_ms2_allowlist_keys_raise() -> None:
    source = _candidate_ms2_row()

    with pytest.raises(
        ValueError,
        match="duplicate backfill Candidate MS2 product authority allowlist key",
    ):
        authorize_candidate_ms2_pattern_rows(
            candidate_ms2_pattern_rows=[source],
            allowlist_rows=[_allowlist_row(source), _allowlist_row(source)],
        )


def test_authorized_candidate_ms2_projects_into_promotion_ms2_context() -> None:
    source = _candidate_ms2_row()
    authorized = authorize_candidate_ms2_pattern_rows(
        candidate_ms2_pattern_rows=[source],
        allowlist_rows=[_allowlist_row(source)],
    ).authorized_rows[0]

    rows = project_backfill_evidence_to_cells(
        cell_rows=[_cell_row()],
        candidate_ms2_pattern_rows=[authorized],
        ms1_pattern_coherence_rows=[_authorized_ms1_row()],
        matrix_rt_drift_policy_rows=[_authorized_rt_drift_row()],
    )

    evidence = evidence_from_tsv_rows(
        {
            "neutral_loss_tag": "DNA_dR",
            "primary_evidence": "owner_complete_link",
            "detected_count": "2",
            "accepted_backfill_count": "1",
        },
        rows,
        seed_quality=None,
        sample_count=3,
    )
    assert rows[0]["backfill_candidate_ms2_pattern_status"] == "supportive"
    assert rows[0]["backfill_candidate_ms2_evidence_level"] == (
        "sample_boundary_aligned"
    )
    assert evidence.cells[0].ms2_context_supported
    assert evidence.cells[0].supported_for_backfill


def test_cli_writes_candidate_ms2_product_authority_outputs(tmp_path: Path) -> None:
    source = _candidate_ms2_row()
    source_tsv = tmp_path / "shared_peak_identity_candidate_ms2_pattern_evidence.tsv"
    allowlist_tsv = tmp_path / "allowlist.tsv"
    output_dir = tmp_path / "authorized"
    source_columns = output_columns(tuple(source))

    _write_tsv(source_tsv, [source], source_columns)
    _write_tsv(allowlist_tsv, [_allowlist_row(source)], ALLOWLIST_COLUMNS)

    assert main(
        [
            "--candidate-ms2-pattern-evidence-tsv",
            str(source_tsv),
            "--authority-allowlist-tsv",
            str(allowlist_tsv),
            "--output-dir",
            str(output_dir),
        ]
    ) == 0

    authorized_tsv = (
        output_dir / "shared_peak_identity_candidate_ms2_pattern_product_authorized.tsv"
    )
    audit_tsv = output_dir / "backfill_candidate_ms2_product_authority_audit.tsv"
    summary_json = output_dir / "backfill_candidate_ms2_product_authority_summary.json"
    assert authorized_tsv.exists()
    assert audit_tsv.exists()
    summary = json.loads(summary_json.read_text(encoding="utf-8"))
    assert summary["authorized_row_count"] == 1
    rows = _read_tsv(authorized_tsv)
    assert rows[0]["diagnostic_only"] == "FALSE"
    assert rows[0][PRODUCT_AUTHORITY_STATUS_FIELD] == PRODUCT_AUTHORIZED_STATUS


def _candidate_ms2_row(
    *,
    family_id: str = "FAM_MS2",
    sample_stem: str = "S2",
    status: str = "supportive",
    score: str = "1",
) -> dict[str, str]:
    return {
        "candidate_ms2_pattern_schema_version": (
            "shared_peak_identity_candidate_ms2_pattern"
        ),
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "candidate_ms2_pattern_status": status,
        "candidate_ms2_evidence_level": "sample_boundary_aligned",
        "source_candidate_id": "",
        "source_candidate_status": "missing",
        "source_discovery_feature_family_id": "",
        "source_ms2_support": "",
        "source_evidence_tier": "",
        "source_evidence_score": "",
        "source_matched_tag_count": "",
        "source_matched_tag_names": "",
        "source_best_ms2_scan_id": "",
        "source_seed_scan_ids": "",
        "source_product_mz": "",
        "source_observed_neutral_loss_da": "",
        "source_neutral_loss_mass_error_ppm": "",
        "family_product_mz": "228.0655",
        "family_observed_neutral_loss_da": "116.0474",
        "product_mz_delta_ppm": "",
        "observed_loss_delta_ppm": "",
        "candidate_ms2_similarity_score": score,
        "matched_product_count": "2",
        "matched_neutral_loss_count": "2",
        "apex_ms2_delta_sec": "4.2",
        "ms2_alignment_source": "raw_boundary_scan",
        "raw_ms2_trigger_scan_count": "4",
        "raw_ms2_strict_nl_scan_count": "2",
        "raw_ms2_best_loss_ppm": "2.1",
        "raw_ms2_best_scan_rt": "10.03",
        "raw_ms2_best_product_base_ratio": "0.75",
        "raw_ms2_diagnostic_product_absence_reason": "",
        "raw_ms2_nearest_product_loss_ppm": "2.1",
        "raw_ms2_nearest_product_base_ratio": "0.75",
        "raw_ms2_nearest_product_mz": "228.0655",
        "raw_ms2_trace_product_point_count": "2",
        "raw_ms2_trace_product_apex_rt": "10.03",
        "raw_ms2_trace_product_apex_delta_sec": "4.2",
        "raw_ms2_trace_strength": "moderate",
        "raw_file_path": "C:\\Xcalibur\\data\\validation\\S2.raw",
        "raw_reader_runtime": "pythonnet",
        "nl_ppm_warn": "10",
        "nl_ppm_max": "20",
        "ms2_precursor_tol_da": "0.7",
        "nl_min_intensity_ratio": "0.01",
        "reason": "raw_boundary_ms2_pattern_matches_family_context",
        "diagnostic_only": "TRUE",
    }


def _allowlist_row(
    source: dict[str, str],
    *,
    expected_status: str = "supportive",
    expected_level: str = "sample_boundary_aligned",
) -> dict[str, str]:
    return {
        "schema_version": SCHEMA_VERSION,
        "feature_family_id": source["feature_family_id"],
        "sample_stem": source["sample_stem"],
        "authority_status": PRODUCT_AUTHORIZED_STATUS,
        "authority_source": "manual_candidate_ms2_review",
        "authority_reason": "reviewed_candidate_ms2_boundary_pattern",
        "expected_candidate_ms2_pattern_status": expected_status,
        "expected_candidate_ms2_evidence_level": expected_level,
        "expected_ms2_alignment_source": source["ms2_alignment_source"],
        "expected_candidate_ms2_source_row_sha256": source_row_sha256(source),
        "min_candidate_ms2_similarity_score": "0.5",
    }


def _cell_row() -> dict[str, str]:
    return {
        "feature_family_id": "FAM_MS2",
        "sample_stem": "S2",
        "status": "backfilled",
        "primary_matrix_area": "123",
        "apex_rt": "10.0",
        "height": "40000",
        "peak_start_rt": "9.8",
        "peak_end_rt": "10.2",
        "rt_delta_sec": "120",
        "scan_support_score": "0.8",
    }


def _authorized_ms1_row() -> dict[str, str]:
    return {
        "feature_family_id": "FAM_MS2",
        "sample_stem": "S2",
        "ms1_pattern_status": "supportive",
        "ms1_pattern_evidence_level": "trace_constellation",
        "reason": ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
        "diagnostic_only": "FALSE",
        **_product_authority(),
    }


def _authorized_rt_drift_row() -> dict[str, str]:
    return {
        "feature_family_id": "FAM_MS2",
        "sample_stem": "S2",
        "matrix_rt_drift_status": "drift_supported",
        "drift_evidence_level": "matrix_reference_aligned",
        "drift_corrected_delta_sec": "20",
        "drift_compatible_status": "compatible",
        "reason": "matrix_rt_drift_explains_alignment",
        "diagnostic_only": "FALSE",
        **_product_authority(),
    }


def _product_authority() -> dict[str, str]:
    return {
        PRODUCT_AUTHORITY_STATUS_FIELD: PRODUCT_AUTHORIZED_STATUS,
        PRODUCT_AUTHORITY_SCOPE_FIELD: PRODUCT_AUTHORIZED_SCOPE,
        PRODUCT_AUTHORITY_SOURCE_FIELD: "unit_test_reviewed_allowlist",
    }


def _write_tsv(
    path: Path,
    rows: list[dict[str, str]],
    columns: tuple[str, ...],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=columns,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
