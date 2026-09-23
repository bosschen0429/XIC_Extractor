from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from tools.diagnostics import backfill_peakhypothesis_promotion as promotion_cli
from xic_extractor.diagnostics.backfill_peakhypothesis_promotion import (
    ALLOWLIST_SCHEMA_VERSION,
    AREA_UNCERTAINTY_COLUMNS,
    PROMOTION_COLUMNS,
    SCHEMA_VERSION,
    build_promotion_index,
    file_sha256,
    write_promotion_outputs,
)

SHADOW_SHA = "shadow-file-sha"
ROW_SHA = "shadow-row-sha"
PRODUCT_CHAIN = (
    "MS1:product_authorized:supportive:trace_constellation:anchor;"
    "same_peak_reason:family_ms1_overlay_anchor_peak_own_max_shape_supported"
)


def test_standard_promotes_and_nonstandard_stays_review_only() -> None:
    index = build_promotion_index(
        (
            _shadow_row("PH001", "FAM001", "SEED001", "S_STANDARD"),
            _shadow_row("PH002", "FAM002", "SEED002", "S_NONSTANDARD"),
        ),
        (
            _allowlist_row("PH001", "FAM001", "SEED001", "S_STANDARD"),
            _allowlist_row(
                "PH002",
                "FAM002",
                "SEED002",
                "S_NONSTANDARD",
                area_policy="nonstandard_assessable_area",
                area_uncertainty_reason="manual_boundary_transfer",
                area_uncertainty_fraction="0.12",
                area_uncertainty_fraction_status="estimated",
                matrix_quantitative_use="use_with_uncertainty",
            ),
        ),
        SHADOW_SHA,
    )

    rows = {row.sample_stem: row for row in index.rows}
    standard = rows["S_STANDARD"]
    nonstandard = rows["S_NONSTANDARD"]
    assert standard.promotion_decision == "promote_matrix_write"
    assert standard.promotion_blockers == ()
    assert standard.promotion_reasons == (
        "allowlisted_peakhypothesis_same_peak_backfill",
    )
    assert nonstandard.promotion_decision == "blocked"
    assert nonstandard.promotion_blockers == ("nonstandard_area_review_only",)
    assert nonstandard.promotion_reasons == ()
    assert standard.area_uncertainty_state == "standard_assessable"
    assert nonstandard.area_uncertainty_state == "nonstandard_assessable"
    assert nonstandard.area_uncertainty_reason == "manual_boundary_transfer"


def test_unassessable_area_blocks_otherwise_good_row() -> None:
    index = build_promotion_index(
        (_shadow_row("PH001"),),
        (
            _allowlist_row(
                "PH001",
                area_policy="unassessable_area",
                matrix_quantitative_use="review_only",
            ),
        ),
        SHADOW_SHA,
    )

    row = index.rows[0]
    assert row.promotion_decision == "blocked"
    assert row.promotion_blockers == ("area_unassessable",)
    assert row.area_uncertainty_state == "unassessable"


def test_identity_supported_review_row_promotes_after_standard_area_allowlist() -> None:
    index = build_promotion_index(
        (
            _shadow_row(
                "PH001",
                shadow_decision="context",
                shadow_reasons="identity_supported_review",
                projected_matrix_written="FALSE",
                projected_matrix_value="321.5",
                product_authority_chain="",
            ),
        ),
        (
            _allowlist_row(
                "PH001",
                expected_product_authority_chain="",
            ),
        ),
        SHADOW_SHA,
    )

    row = index.rows[0]
    assert row.promotion_decision == "promote_matrix_write"
    assert row.promotion_blockers == ()
    assert row.promotion_reasons == (
        "allowlisted_peakhypothesis_same_peak_backfill",
    )
    assert row.projected_matrix_written == "FALSE"
    assert row.projected_matrix_value == "321.5"


def test_identity_supported_nonstandard_row_stays_review_only() -> None:
    index = build_promotion_index(
        (
            _shadow_row(
                "PH001",
                shadow_decision="context",
                shadow_reasons="identity_supported_review",
                projected_matrix_written="FALSE",
                projected_matrix_value="321.5",
                product_authority_chain="",
            ),
        ),
        (
            _allowlist_row(
                "PH001",
                expected_product_authority_chain="",
                area_policy="nonstandard_assessable_area",
                area_uncertainty_reason="manual shape review",
                area_uncertainty_fraction_status="estimated",
                matrix_quantitative_use="use_with_uncertainty",
            ),
        ),
        SHADOW_SHA,
    )

    row = index.rows[0]
    assert row.promotion_decision == "blocked"
    assert row.promotion_blockers == ("nonstandard_area_review_only",)


def test_product_authority_chain_drift_blocks_otherwise_good_row() -> None:
    index = build_promotion_index(
        (_shadow_row("PH001"),),
        (_allowlist_row("PH001", expected_product_authority_chain="different-chain"),),
        SHADOW_SHA,
    )

    row = index.rows[0]
    assert row.promotion_decision == "blocked"
    assert row.promotion_blockers == ("product_authority_chain_drift",)


def test_missing_shadow_row_blocks_without_shadow_detail_noise() -> None:
    index = build_promotion_index((), (_allowlist_row("PH001"),), SHADOW_SHA)

    row = index.rows[0]
    assert row.promotion_decision == "blocked"
    assert row.promotion_blockers == ("missing_shadow_projection_row",)


def test_writer_emits_cells_uncertainty_and_diagnostic_only_summary(
    tmp_path: Path,
) -> None:
    index = build_promotion_index(
        (
            _shadow_row("PH001", "FAM001", "SEED001", "S_STANDARD"),
            _shadow_row("PH002", "FAM002", "SEED002", "S_NONSTANDARD"),
        ),
        (
            _allowlist_row("PH001", "FAM001", "SEED001", "S_STANDARD"),
            _allowlist_row(
                "PH002",
                "FAM002",
                "SEED002",
                "S_NONSTANDARD",
                area_policy="nonstandard_assessable_area",
                area_uncertainty_reason="manual boundary uncertainty",
                area_uncertainty_fraction_status="estimated",
                matrix_quantitative_use="use_with_uncertainty",
            ),
        ),
        SHADOW_SHA,
        source_run_id="run-123",
    )

    outputs = write_promotion_outputs(tmp_path, index)

    assert outputs.cells_tsv == tmp_path / "backfill_peakhypothesis_promotion_cells.tsv"
    assert outputs.area_uncertainty_tsv == (
        tmp_path / "backfill_peakhypothesis_area_uncertainty.tsv"
    )
    assert outputs.summary_json == (
        tmp_path / "backfill_peakhypothesis_promotion_summary.json"
    )
    cells = _read_tsv(outputs.cells_tsv)
    uncertainty = _read_tsv(outputs.area_uncertainty_tsv)
    summary = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert tuple(cells[0]) == PROMOTION_COLUMNS
    assert tuple(uncertainty[0]) == AREA_UNCERTAINTY_COLUMNS
    assert len(cells) == 2
    assert len(uncertainty) == 1
    assert uncertainty[0]["sample_stem"] == "S_STANDARD"
    assert summary["schema_version"] == SCHEMA_VERSION
    assert summary["readiness_label"] == "diagnostic_only"
    assert summary["source_run_id"] == "run-123"
    assert summary["decision_counts"] == {
        "blocked": 1,
        "promote_matrix_write": 1,
    }
    assert summary["area_uncertainty_counts"] == {
        "nonstandard_assessable": 1,
        "standard_assessable": 1,
    }
    assert summary["matrix_contract_changed"] is False
    assert summary["product_behavior_changed"] is False


@pytest.mark.parametrize(
    ("shadow_overrides", "expected_blocker"),
    (
        ({"shadow_reasons": ""}, "missing_product_authorized_same_peak_reason"),
        ({"current_raw_status": "missing"}, "current_raw_status_not_rescued"),
        (
            {"product_authority_chain": "MS1:product_authorized:supportive:other"},
            "malformed_product_authority_chain",
        ),
        ({"current_matrix_written": "TRUE"}, "current_matrix_already_written"),
        ({"current_matrix_written": ""}, "current_matrix_already_written"),
    ),
)
def test_incomplete_evidence_chain_cases_fail_closed(
    shadow_overrides: dict[str, str],
    expected_blocker: str,
) -> None:
    shadow = _shadow_row("PH001", **shadow_overrides)
    allowlist = _allowlist_row(
        "PH001",
        expected_product_authority_chain=shadow["product_authority_chain"],
    )

    index = build_promotion_index((shadow,), (allowlist,), SHADOW_SHA)

    row = index.rows[0]
    assert row.promotion_decision == "blocked"
    assert row.promotion_blockers == (expected_blocker,)


def test_nonstandard_without_uncertainty_fraction_or_status_fails_closed() -> None:
    index = build_promotion_index(
        (_shadow_row("PH001"),),
        (
            _allowlist_row(
                "PH001",
                area_policy="nonstandard_assessable_area",
                area_uncertainty_reason="manual boundary uncertainty",
                area_uncertainty_fraction="",
                area_uncertainty_fraction_status="",
                matrix_quantitative_use="use_with_uncertainty",
            ),
        ),
        SHADOW_SHA,
    )

    row = index.rows[0]
    assert row.promotion_decision == "blocked"
    assert row.promotion_blockers == (
        "missing_area_uncertainty_fraction_status",
        "nonstandard_area_review_only",
    )


@pytest.mark.parametrize(
    ("allowlist_overrides", "expected_blocker"),
    (
        ({"reviewer": ""}, "missing_reviewer"),
        ({"reviewed_at": ""}, "missing_reviewed_at"),
        ({"authority_reason": ""}, "missing_authority_reason"),
        ({"integration_bounds_source": ""}, "missing_integration_bounds_source"),
        ({"peak_start_rt": ""}, "missing_peak_start_rt"),
        ({"peak_end_rt": ""}, "missing_peak_end_rt"),
    ),
)
def test_reviewed_allowlist_provenance_and_bounds_are_required(
    allowlist_overrides: dict[str, str],
    expected_blocker: str,
) -> None:
    index = build_promotion_index(
        (_shadow_row("PH001"),),
        (_allowlist_row("PH001", **allowlist_overrides),),
        SHADOW_SHA,
    )

    row = index.rows[0]
    assert row.promotion_decision == "blocked"
    assert expected_blocker in row.promotion_blockers


@pytest.mark.parametrize(
    ("allowlist_overrides", "expected_blocker"),
    (
        ({"area_uncertainty_reason": ""}, "missing_area_uncertainty_reason"),
        ({"area_uncertainty_fraction": "nan"}, "invalid_area_uncertainty_fraction"),
        ({"area_uncertainty_fraction": "1.5"}, "invalid_area_uncertainty_fraction"),
        (
            {"area_uncertainty_fraction_status": "unknown"},
            "unsupported_area_uncertainty_fraction_status",
        ),
    ),
)
def test_nonstandard_uncertainty_metadata_must_be_usable(
    allowlist_overrides: dict[str, str],
    expected_blocker: str,
) -> None:
    allowlist = _allowlist_row(
        "PH001",
        area_policy="nonstandard_assessable_area",
        area_uncertainty_reason="manual boundary uncertainty",
        area_uncertainty_fraction="0.25",
        area_uncertainty_fraction_status="estimated",
        matrix_quantitative_use="use_with_uncertainty",
    )
    allowlist.update(allowlist_overrides)
    index = build_promotion_index(
        (_shadow_row("PH001"),),
        (allowlist,),
        SHADOW_SHA,
    )

    row = index.rows[0]
    assert row.promotion_decision == "blocked"
    assert expected_blocker in row.promotion_blockers
    assert "nonstandard_area_review_only" in row.promotion_blockers


def test_blocked_rows_do_not_write_area_uncertainty_sidecar_rows(
    tmp_path: Path,
) -> None:
    index = build_promotion_index(
        (_shadow_row("PH001"),),
        (
            _allowlist_row(
                "PH001",
                area_policy="unassessable_area",
                matrix_quantitative_use="review_only",
            ),
        ),
        SHADOW_SHA,
    )

    outputs = write_promotion_outputs(tmp_path, index)

    uncertainty = _read_tsv(outputs.area_uncertainty_tsv)
    assert uncertainty == []


def test_cli_writes_outputs_from_tsv_fixtures(tmp_path: Path) -> None:
    shadow_tsv = tmp_path / "shadow.tsv"
    allowlist_tsv = tmp_path / "allowlist.tsv"
    output_dir = tmp_path / "out"
    _write_tsv(shadow_tsv, [_shadow_row("PH001")])
    shadow_sha256 = file_sha256(shadow_tsv)
    _write_tsv(
        allowlist_tsv,
        [_allowlist_row("PH001", expected_shadow_projection_sha256=shadow_sha256)],
    )

    exit_code = promotion_cli.main(
        (
            "--shadow-projection-cells-tsv",
            str(shadow_tsv),
            "--authority-allowlist-tsv",
            str(allowlist_tsv),
            "--shadow-projection-sha256",
            shadow_sha256,
            "--output-dir",
            str(output_dir),
            "--source-run-id",
            "fixture-run",
            "--readiness-label",
            "shadow_ready",
        ),
    )

    assert exit_code == 0
    cells = _read_tsv(output_dir / "backfill_peakhypothesis_promotion_cells.tsv")
    summary = json.loads(
        (output_dir / "backfill_peakhypothesis_promotion_summary.json").read_text(
            encoding="utf-8",
        ),
    )
    assert cells[0]["promotion_decision"] == "promote_matrix_write"
    assert summary["readiness_label"] == "shadow_ready"
    assert summary["source_run_id"] == "fixture-run"
    assert summary["shadow_projection_sha256"] == shadow_sha256


def test_cli_returns_2_when_declared_shadow_sha_does_not_match_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shadow_tsv = tmp_path / "shadow.tsv"
    allowlist_tsv = tmp_path / "allowlist.tsv"
    output_dir = tmp_path / "out"
    _write_tsv(shadow_tsv, [_shadow_row("PH001")])
    shadow_sha256 = file_sha256(shadow_tsv)
    _write_tsv(
        allowlist_tsv,
        [_allowlist_row("PH001", expected_shadow_projection_sha256=shadow_sha256)],
    )

    exit_code = promotion_cli.main(
        (
            "--shadow-projection-cells-tsv",
            str(shadow_tsv),
            "--authority-allowlist-tsv",
            str(allowlist_tsv),
            "--shadow-projection-sha256",
            "not-the-shadow-file-sha",
            "--output-dir",
            str(output_dir),
        ),
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert (
        "shadow projection SHA256 argument does not match "
        "shadow projection TSV content"
    ) in captured.err


def test_cli_returns_2_when_allowlist_schema_is_incomplete(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shadow_tsv = tmp_path / "shadow.tsv"
    allowlist_tsv = tmp_path / "allowlist.tsv"
    output_dir = tmp_path / "out"
    _write_tsv(shadow_tsv, [_shadow_row("PH001")])
    shadow_sha256 = file_sha256(shadow_tsv)
    allowlist = _allowlist_row("PH001")
    allowlist["expected_shadow_projection_sha256"] = shadow_sha256
    del allowlist["area_uncertainty_fraction"]
    _write_tsv(allowlist_tsv, [allowlist])

    exit_code = promotion_cli.main(
        (
            "--shadow-projection-cells-tsv",
            str(shadow_tsv),
            "--authority-allowlist-tsv",
            str(allowlist_tsv),
            "--shadow-projection-sha256",
            shadow_sha256,
            "--output-dir",
            str(output_dir),
        ),
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "missing required columns: area_uncertainty_fraction" in captured.err


def test_duplicate_allowlist_and_shadow_keys_raise_value_error() -> None:
    with pytest.raises(ValueError, match="duplicate allowlist key"):
        build_promotion_index(
            (_shadow_row("PH001"),),
            (_allowlist_row("PH001"), _allowlist_row("PH001")),
            SHADOW_SHA,
        )

    with pytest.raises(ValueError, match="duplicate shadow projection key"):
        build_promotion_index(
            (_shadow_row("PH001"), _shadow_row("PH001")),
            (_allowlist_row("PH001"),),
            SHADOW_SHA,
        )


def _shadow_row(
    peak_hypothesis_id: str,
    feature_family_id: str = "FAM001",
    seed_group_id: str = "SEED001",
    sample_stem: str = "SAMPLE001",
    **overrides: str,
) -> dict[str, str]:
    row = {
        "peak_hypothesis_id": peak_hypothesis_id,
        "activation_unit_scope": "peak_hypothesis",
        "feature_family_id": feature_family_id,
        "seed_group_id": seed_group_id,
        "sample_stem": sample_stem,
        "current_raw_status": "backfilled",
        "current_production_status": "review_backfill",
        "current_matrix_written": "FALSE",
        "shadow_decision": "accept",
        "shadow_reasons": "product_authorized_same_peak_backfill",
        "projected_matrix_written": "TRUE",
        "projected_matrix_value": "125.5",
        "product_authority_chain": PRODUCT_CHAIN,
        "hard_blockers": "",
        "missing_evidence": "",
        "shadow_projection_row_sha256": ROW_SHA,
    }
    row.update(overrides)
    return row


def _allowlist_row(
    peak_hypothesis_id: str,
    feature_family_id: str = "FAM001",
    seed_group_id: str = "SEED001",
    sample_stem: str = "SAMPLE001",
    **overrides: str,
) -> dict[str, str]:
    row = {
        "schema_version": ALLOWLIST_SCHEMA_VERSION,
        "peak_hypothesis_id": peak_hypothesis_id,
        "activation_unit_scope": "peak_hypothesis",
        "feature_family_id": feature_family_id,
        "seed_group_id": seed_group_id,
        "sample_stem": sample_stem,
        "authority_status": "product_authorized",
        "authority_source": "manual_review",
        "authority_reason": "same_peak_identity_and_area_policy_reviewed",
        "expected_shadow_projection_sha256": SHADOW_SHA,
        "expected_shadow_projection_row_sha256": ROW_SHA,
        "expected_product_authority_chain": PRODUCT_CHAIN,
        "area_policy": "standard_assessable_area",
        "area_uncertainty_reason": "",
        "area_uncertainty_fraction": "",
        "area_uncertainty_fraction_status": "",
        "matrix_quantitative_use": "standard_quantitative_use",
        "integration_bounds_source": "shadow_projection",
        "peak_start_rt": "1.10",
        "peak_end_rt": "1.25",
        "reviewer": "reviewer",
        "reviewed_at": "2026-06-08T00:00:00Z",
    }
    row.update(overrides)
    return row


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = tuple(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
