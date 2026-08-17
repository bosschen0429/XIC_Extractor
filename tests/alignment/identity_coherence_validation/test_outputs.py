from __future__ import annotations

from pathlib import Path

from xic_extractor.alignment.identity_coherence_validation import outputs
from xic_extractor.alignment.identity_coherence_validation.models import (
    AcceptanceReport,
    AcceptanceRow,
    ValidationResult,
    ValidationRow,
)
from xic_extractor.alignment.identity_coherence_validation.outputs import (
    write_validation_outputs,
)


def _validation_result_with_rows(rows: tuple[ValidationRow, ...]) -> ValidationResult:
    return ValidationResult(rows=rows)


def test_write_validation_outputs_labels_controls_not_assessed(
    tmp_path: Path,
) -> None:
    result = ValidationResult(
        rows=(
            ValidationRow(
                check_name="requests_tsv_exact",
                status="pass",
                serial_value="1",
                process_value="1",
                details="ok",
            ),
        )
    )

    write_validation_outputs(
        output_root=tmp_path,
        result=result,
        controls_manifest=None,
    )

    report = (
        tmp_path / "identity_coherence_8raw_validation_report.md"
    ).read_text(encoding="utf-8")
    assert "Controls: not_assessed" in report
    assert "diagnostic-only" in report
    assert "does not validate final matrix filtering" in report
    assert "not final retained feature inclusion" in report
    summary = (
        tmp_path / "identity_coherence_8raw_validation_summary.tsv"
    ).read_text(encoding="utf-8")
    assert "controls_manifest_assessment\tnot_assessed" in summary


def test_validation_summary_tsv_writer_preserves_raw_contract(tmp_path: Path) -> None:
    path = tmp_path / "summary.tsv"

    outputs._write_summary_tsv(
        path,
        ValidationResult(
            rows=(
                ValidationRow(
                    check_name="=check",
                    status="pass",
                    serial_value="1",
                    process_value="1",
                    details="@detail",
                ),
            )
        ),
    )

    assert path.read_text(encoding="utf-8").splitlines() == [
        "check_name\tstatus\tserial_value\tprocess_value\tdetails",
        "=check\tpass\t1\t1\t@detail",
    ]


def test_acceptance_tsv_writer_preserves_lf_and_parent_creation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "acceptance.tsv"

    outputs._write_acceptance_tsv(
        path,
        AcceptanceReport(
            rows=(AcceptanceRow("v04_acceptance", "pass", "ok", "@detail"),),
        ),
    )

    assert path.read_text(encoding="utf-8") == (
        "criterion\tstatus\tevidence\tdetails\n"
        "v04_acceptance\tpass\tok\t@detail\n"
    )


def test_write_validation_outputs_writes_acceptance_artifacts(
    tmp_path: Path,
) -> None:
    result = _validation_result_with_rows(
        (
            ValidationRow("requests_tsv_exact", "pass", "3", "3", "ok"),
            ValidationRow("decisions_tsv_exact", "pass", "3", "3", "ok"),
            ValidationRow("cell_evidence_tsv_exact", "pass", "8", "8", "ok"),
            ValidationRow("controls_tsv_parity_only", "pass", "2", "2", "ok"),
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
            ValidationRow("summary_md_presence", "pass", "present", "present", "ok"),
        )
    )

    acceptance = write_validation_outputs(
        output_root=tmp_path,
        result=result,
        controls_manifest=tmp_path / "controls.reviewed.tsv",
    )

    acceptance_tsv = tmp_path / "identity_coherence_v04_acceptance.tsv"
    acceptance_md = tmp_path / "identity_coherence_v04_acceptance.md"
    assert acceptance_tsv.exists()
    assert acceptance_md.exists()
    assert "v04_acceptance\tpass" in acceptance_tsv.read_text(encoding="utf-8")
    assert acceptance.accepted
    markdown = acceptance_md.read_text(encoding="utf-8")
    assert "# Identity Coherence V0.4 Acceptance" in markdown
    assert "| `v04_acceptance` | `pass` |" in markdown
    assert "does not clear full-cohort execution" in markdown


def test_write_validation_outputs_keeps_parity_report_pass_when_controls_fail(
    tmp_path: Path,
) -> None:
    result = _validation_result_with_rows(
        (
            ValidationRow("requests_tsv_exact", "pass", "3", "3", "ok"),
            ValidationRow("decisions_tsv_exact", "pass", "3", "3", "ok"),
            ValidationRow("cell_evidence_tsv_exact", "pass", "8", "8", "ok"),
            ValidationRow("controls_tsv_parity_only", "pass", "2", "2", "ok"),
            ValidationRow(
                "controls_manifest_assessment",
                "not_assessed",
                "provided",
                "provided",
                "manifest provided",
            ),
            ValidationRow(
                "positive_control_pass_fraction",
                "fail",
                "0.500",
                "0.500",
                "one positive control failed",
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
            ValidationRow("summary_md_presence", "pass", "present", "present", "ok"),
        )
    )

    acceptance = write_validation_outputs(
        output_root=tmp_path,
        result=result,
        controls_manifest=tmp_path / "controls.reviewed.tsv",
    )

    report = (tmp_path / "identity_coherence_8raw_validation_report.md").read_text(
        encoding="utf-8",
    )
    assert "Parity result: PASS" in report
    assert not acceptance.accepted
