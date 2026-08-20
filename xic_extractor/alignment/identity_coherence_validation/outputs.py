from __future__ import annotations

from pathlib import Path

from xic_extractor.alignment.identity_coherence_validation.acceptance import (
    evaluate_v04_acceptance,
    sidecar_parity_failed_count,
)
from xic_extractor.alignment.identity_coherence_validation.controls_summary import (
    controls_manifest_row,
)
from xic_extractor.alignment.identity_coherence_validation.models import (
    ACCEPTANCE_SUMMARY_COLUMNS,
    VALIDATION_SUMMARY_COLUMNS,
    AcceptanceReport,
    AcceptanceRow,
    RunMetadata,
    ValidationResult,
    ValidationRow,
)
from xic_extractor.tabular_io import write_tsv


def write_validation_outputs(
    *,
    output_root: Path,
    result: ValidationResult,
    controls_manifest: Path | None,
    run_metadata: tuple[RunMetadata, ...] = (),
) -> AcceptanceReport:
    result = _with_controls_manifest_row(result, controls_manifest)
    output_root.mkdir(parents=True, exist_ok=True)
    metadata = run_metadata if run_metadata else result.run_metadata
    _write_summary_tsv(
        output_root / "identity_coherence_validation_summary.tsv",
        result,
    )
    _write_report_md(
        output_root / "identity_coherence_validation_report.md",
        result=result,
        controls_manifest=controls_manifest,
        run_metadata=metadata,
    )
    acceptance = evaluate_v04_acceptance(
        result,
        controls_manifest=controls_manifest,
    )
    _write_acceptance_tsv(
        output_root / "identity_coherence_v04_acceptance.tsv",
        acceptance,
    )
    _write_acceptance_md(
        output_root / "identity_coherence_v04_acceptance.md",
        report=acceptance,
        controls_manifest=controls_manifest,
    )
    return acceptance


def _with_controls_manifest_row(
    result: ValidationResult,
    controls_manifest: Path | None,
) -> ValidationResult:
    if any(row.check_name == "controls_manifest_assessment" for row in result.rows):
        return result
    return ValidationResult(
        rows=(*result.rows, controls_manifest_row(controls_manifest)),
        run_metadata=result.run_metadata,
    )


def _write_summary_tsv(path: Path, result: ValidationResult) -> None:
    write_tsv(
        path,
        [_validation_summary_row(row) for row in result.rows],
        VALIDATION_SUMMARY_COLUMNS,
        formatter=_format_tsv_value,
    )


def _write_acceptance_tsv(path: Path, report: AcceptanceReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(
        path,
        [_acceptance_summary_row(row) for row in report.rows],
        ACCEPTANCE_SUMMARY_COLUMNS,
        formatter=_format_tsv_value,
        lineterminator="\n",
    )


def _validation_summary_row(row: ValidationRow) -> dict[str, str]:
    return {
        "check_name": row.check_name,
        "status": row.status,
        "serial_value": row.serial_value,
        "process_value": row.process_value,
        "details": row.details,
    }


def _acceptance_summary_row(row: AcceptanceRow) -> dict[str, str]:
    return {
        "criterion": row.criterion,
        "status": row.status,
        "evidence": row.evidence,
        "details": row.details,
    }


def _format_tsv_value(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _write_acceptance_md(
    path: Path,
    *,
    report: AcceptanceReport,
    controls_manifest: Path | None,
) -> None:
    controls_text = (
        str(controls_manifest) if controls_manifest is not None else "not_provided"
    )
    verdict = "PASS" if report.accepted else "FAIL"
    lines = [
        "# Identity Coherence V0.4 Acceptance",
        "",
        (
            "This report closes the V0.4 diagnostic acceptance loop. It is "
            "diagnostic-only and does not validate final matrix filtering, "
            "background filtering, normalization, statistics, or full-cohort "
            "execution."
        ),
        "",
        f"**Verdict:** `{verdict}`",
        "",
        f"**Reviewed controls manifest:** `{controls_text}`",
        "",
        "| Criterion | Status | Evidence | Details |",
        "| --- | --- | --- | --- |",
    ]
    for row in report.rows:
        lines.append(
            f"| `{row.criterion}` | `{row.status}` | "
            f"{_format_markdown_cell(row.evidence)} | "
            f"{_format_markdown_cell(row.details)} |"
        )
    lines.extend(
        [
            "",
            "## Handoff Notes",
            "",
            (
                "- A PASS means the V0.4 diagnostic is ready for human method "
                "review on the diagnostic subset."
            ),
            (
                "- A PASS does not authorize full-cohort execution; that still "
                "requires a reviewed count/fraction policy and request-budget "
                "ceiling."
            ),
            (
                "- A FAIL with passing serial/process parity usually means "
                "reviewed controls are missing or controls failed; do not "
                "reinterpret that as a retrieval or Backfill failure."
            ),
            (
                "- Contaminants that are chromatographically coherent can "
                "still appear as would-primary diagnostic rows; downstream "
                "filtering owns final-matrix exclusion."
            ),
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _format_markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _write_report_md(
    path: Path,
    *,
    result: ValidationResult,
    controls_manifest: Path | None,
    run_metadata: tuple[RunMetadata, ...] = (),
) -> None:
    parity_result = "PASS" if sidecar_parity_failed_count(result) == 0 else "FAIL"
    controls_status = (
        "provided_not_assessed" if controls_manifest is not None else "not_assessed"
    )
    lines = [
        "# Identity Coherence Validation",
        "",
        f"Parity result: {parity_result}",
        "",
        "This report is diagnostic-only. It validates identity coherence sidecar "
        "parity and does not validate final matrix filtering. It is not method "
        "validation and does not establish positive-control sensitivity or decoy "
        "specificity unless those checks are explicitly reported.",
        "",
        "`would_primary_provisional_identity_family_support` is a diagnostic "
        "sidecar label, not final retained feature inclusion.",
        "",
        f"Controls: {controls_status}",
        "",
        "## Run Metadata",
        "",
        "| Mode | Return Code | Output Dir | Command |",
        "| --- | ---: | --- | --- |",
    ]
    if not run_metadata:
        lines.append("| `not_assessed` | 0 |  | metadata not supplied |")
    for item in run_metadata:
        lines.append(
            "| "
            f"`{item.mode}` | {item.returncode} | `{item.output_dir}` | "
            f"`{item.command_line}` |"
        )
    lines.extend(
        [
            "",
            "## Parity Checks",
            "",
            "| Check | Status | Serial | Process | Details |",
            "| --- | --- | ---: | ---: | --- |",
        ]
    )
    for row in result.rows:
        lines.append(
            "| "
            f"`{row.check_name}` | `{row.status}` | {row.serial_value} | "
            f"{row.process_value} | {row.details} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
