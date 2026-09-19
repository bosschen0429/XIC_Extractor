from pathlib import Path

from xic_extractor.alignment.identity_coherence_validation.acceptance import (
    evaluate_acceptance,
    sidecar_parity_failed_count,
)
from xic_extractor.alignment.identity_coherence_validation.models import (
    ValidationResult,
    ValidationRow,
)


def _result_with_rows(rows: tuple[ValidationRow, ...]) -> ValidationResult:
    return ValidationResult(rows=rows)


def _passing_sidecar_rows() -> tuple[ValidationRow, ...]:
    return (
        ValidationRow("requests_tsv_exact", "pass", "3", "3", "ok"),
        ValidationRow("decisions_tsv_exact", "pass", "3", "3", "ok"),
        ValidationRow("cell_evidence_tsv_exact", "pass", "8", "8", "ok"),
        ValidationRow("controls_tsv_parity_only", "pass", "2", "2", "ok"),
        ValidationRow("summary_md_presence", "pass", "true", "true", "ok"),
    )


def _passing_reviewed_control_rows() -> tuple[ValidationRow, ...]:
    return (
        ValidationRow(
            "controls_manifest_assessment",
            "not_assessed",
            "provided",
            "provided",
            "manifest provided",
        ),
        ValidationRow(
            "positive_control_pass_fraction",
            "pass",
            "1.000",
            "1.000",
            "all positives passed",
        ),
        ValidationRow(
            "decoy_coherent_seed_count",
            "pass",
            "0",
            "0",
            "no decoy promotion",
        ),
        ValidationRow(
            "decoy_correctly_rejected_count",
            "pass",
            "3/3",
            "3/3",
            "all decoys rejected",
        ),
    )


def test_sidecar_parity_failed_count_requires_all_frozen_sidecars() -> None:
    result = _result_with_rows(
        (
            ValidationRow("requests_tsv_exact", "pass", "3", "3", "ok"),
            ValidationRow("decisions_tsv_exact", "fail", "a", "b", "diff"),
        )
    )

    assert sidecar_parity_failed_count(result) == 4


def test_acceptance_passes_when_parity_and_reviewed_controls_pass() -> None:
    result = _result_with_rows(
        (
            *_passing_sidecar_rows(),
            ValidationRow(
                "controls_manifest_assessment",
                "not_assessed",
                "provided",
                "provided",
                "manifest passed through",
            ),
            ValidationRow(
                "positive_control_pass_fraction",
                "pass",
                "1.000",
                "1.000",
                "all positive controls passed",
            ),
            ValidationRow("decoy_coherent_seed_count", "pass", "0", "0", "ok"),
            ValidationRow(
                "decoy_correctly_rejected_count",
                "pass",
                "3/3",
                "3/3",
                "all rejected",
            ),
        )
    )

    report = evaluate_acceptance(
        result,
        controls_manifest=Path("controls.reviewed.tsv"),
    )

    rows = {row.criterion: row for row in report.rows}
    assert report.accepted is True
    assert rows["v04_acceptance"].status == "pass"


def test_acceptance_fails_without_reviewed_controls() -> None:
    result = _result_with_rows(
        (
            *_passing_sidecar_rows(),
            ValidationRow(
                "controls_manifest_assessment",
                "not_assessed",
                "not_provided",
                "not_provided",
                "no manifest",
            ),
            ValidationRow(
                "positive_control_pass_fraction",
                "not_assessed",
                "not_assessed",
                "not_assessed",
                "no manifest",
            ),
            ValidationRow(
                "decoy_coherent_seed_count",
                "not_assessed",
                "not_assessed",
                "not_assessed",
                "no manifest",
            ),
            ValidationRow(
                "decoy_correctly_rejected_count",
                "not_assessed",
                "not_assessed",
                "not_assessed",
                "no manifest",
            ),
        )
    )

    report = evaluate_acceptance(
        result,
        controls_manifest=Path("identity_coherence_controls_manifest.reviewed.tsv"),
    )

    rows = {row.criterion: row for row in report.rows}
    assert rows["serial_process_sidecar_parity"].status == "pass"
    assert rows["reviewed_controls_manifest"].status == "fail"
    assert rows["v04_acceptance"].status == "fail"
    assert not report.accepted


def test_acceptance_fails_for_non_reviewed_manifest_name() -> None:
    result = _result_with_rows(
        (
            *_passing_sidecar_rows(),
            *_passing_reviewed_control_rows(),
        )
    )

    report = evaluate_acceptance(result, controls_manifest=Path("controls.tsv"))

    rows = {row.criterion: row for row in report.rows}
    assert rows["reviewed_controls_manifest"].status == "fail"
    assert rows["v04_acceptance"].status == "fail"
    assert not report.accepted


def test_acceptance_fails_when_decoy_promotes() -> None:
    result = _result_with_rows(
        (
            *_passing_sidecar_rows(),
            ValidationRow(
                "controls_manifest_assessment",
                "not_assessed",
                "provided",
                "provided",
                "manifest passed through",
            ),
            ValidationRow(
                "positive_control_pass_fraction",
                "pass",
                "1.000",
                "1.000",
                "all positive controls passed",
            ),
            ValidationRow("decoy_coherent_seed_count", "fail", "1", "1", "bad"),
            ValidationRow(
                "decoy_correctly_rejected_count",
                "pass",
                "2/3",
                "2/3",
                "one promoted",
            ),
        )
    )

    report = evaluate_acceptance(
        result,
        controls_manifest=Path("controls.reviewed.tsv"),
    )

    rows = {row.criterion: row for row in report.rows}
    assert report.accepted is False
    assert rows["identity_decoy_specificity"].status == "fail"
    assert rows["v04_acceptance"].evidence == "identity_decoy_specificity"


def test_acceptance_fails_when_decoy_rejected_count_fails() -> None:
    result = _result_with_rows(
        (
            *_passing_sidecar_rows(),
            ValidationRow(
                "controls_manifest_assessment",
                "not_assessed",
                "provided",
                "provided",
                "manifest provided",
            ),
            ValidationRow(
                "positive_control_pass_fraction",
                "pass",
                "1.000",
                "1.000",
                "all positives passed",
            ),
            ValidationRow(
                "decoy_coherent_seed_count",
                "pass",
                "0",
                "0",
                "no coherent decoy",
            ),
            ValidationRow(
                "decoy_correctly_rejected_count",
                "fail",
                "2/3",
                "2/3",
                "one decoy not correctly rejected",
            ),
        )
    )

    report = evaluate_acceptance(
        result,
        controls_manifest=Path("identity_coherence_controls_manifest.reviewed.tsv"),
    )

    rows = {row.criterion: row for row in report.rows}
    assert rows["identity_decoy_specificity"].status == "fail"
    assert rows["v04_acceptance"].status == "fail"
    assert not report.accepted
