from pathlib import Path

import pytest

from xic_extractor.peak_detection.model_selection_approval_registry import (
    EXPECTED_DIFF_APPROVAL_REGISTRY_HEADERS,
    load_expected_diff_approval_registry,
)


def test_load_expected_diff_approval_registry_reads_approved_tsv(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "model_selection_expected_diff_approvals.tsv"
    registry_path.write_text(
        "\t".join(EXPECTED_DIFF_APPROVAL_REGISTRY_HEADERS)
        + "\n"
        + "\t".join(
            (
                "SampleA|Analyte|legacy|successor",
                "SampleA",
                "Analyte",
                "legacy",
                "successor",
                "expected_diff",
                "approved",
                "targeted_benchmark",
                "selected_rt;boundary",
                "area_value_changed",
                "rt;shape",
                "benchmark supports successor peak",
                "domain_reviewer",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    approvals = load_expected_diff_approval_registry(registry_path)

    approval = approvals["SampleA|Analyte|legacy|successor"]
    assert approval.sample_name == "SampleA"
    assert approval.target_label == "Analyte"
    assert approval.legacy_selected_candidate_id == "legacy"
    assert approval.successor_selected_candidate_id == "successor"
    assert approval.public_outputs_touched == (
        "selected_rt",
        "boundary",
    )
    assert approval.matrix_value_impact == "area_value_changed"
    assert approval.evidence_sources == ("rt", "shape")


def test_load_expected_diff_approval_registry_reports_missing_file(
    tmp_path: Path,
) -> None:
    missing_path = tmp_path / "missing_approvals.tsv"

    with pytest.raises(ValueError, match="approval registry file not found"):
        load_expected_diff_approval_registry(missing_path)


def test_load_expected_diff_approval_registry_rejects_duplicate_stable_row_id(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "approvals.tsv"
    row = "\t".join(
        (
            "duplicate-row",
            "SampleA",
            "Analyte",
            "legacy",
            "successor",
            "expected_diff",
            "approved",
            "targeted_benchmark",
            "selected_rt",
            "none",
            "rt",
            "reviewed",
            "domain_reviewer",
        )
    )
    registry_path.write_text(
        "\t".join(EXPECTED_DIFF_APPROVAL_REGISTRY_HEADERS)
        + "\n"
        + row
        + "\n"
        + row
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate stable_row_id"):
        load_expected_diff_approval_registry(registry_path)


def test_load_expected_diff_approval_registry_rejects_matrix_synthetic_fixture(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "approvals.tsv"
    registry_path.write_text(
        "\t".join(EXPECTED_DIFF_APPROVAL_REGISTRY_HEADERS)
        + "\n"
        + "\t".join(
            (
                "SampleA|Analyte|legacy|successor",
                "SampleA",
                "Analyte",
                "legacy",
                "successor",
                "expected_diff",
                "approved",
                "synthetic_fixture",
                "final_matrix",
                "area_value_changed",
                "rt",
                "synthetic only",
                "domain_reviewer",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="matrix-affecting"):
        load_expected_diff_approval_registry(registry_path)
