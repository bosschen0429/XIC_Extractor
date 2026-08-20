from __future__ import annotations

from pathlib import Path

from xic_extractor.alignment.identity_coherence_validation.models import (
    SIDECAR_PARITY_CHECKS,
    AcceptanceReport,
    AcceptanceRow,
    ValidationResult,
    ValidationRow,
)


def sidecar_parity_failed_count(result: ValidationResult) -> int:
    return len(_sidecar_parity_failures(result))


def _sidecar_parity_failures(result: ValidationResult) -> tuple[str, ...]:
    rows_by_name = {row.check_name: row for row in result.rows}
    failures: list[str] = []
    for name in SIDECAR_PARITY_CHECKS:
        row = rows_by_name.get(name)
        if row is None or row.status != "pass":
            failures.append(name)
    return tuple(failures)


def evaluate_v04_acceptance(
    result: ValidationResult,
    *,
    controls_manifest: Path | None = None,
) -> AcceptanceReport:
    rows_by_name = {row.check_name: row for row in result.rows}
    parity = _acceptance_sidecar_parity(rows_by_name)
    controls = _acceptance_reviewed_controls(rows_by_name, controls_manifest)
    positives = _acceptance_positive_controls(rows_by_name)
    decoys = _acceptance_identity_decoys(rows_by_name)
    required = (parity, controls, positives, decoys)
    if all(row.status == "pass" for row in required):
        final = AcceptanceRow(
            criterion="v04_acceptance",
            status="pass",
            evidence="all_required_criteria_passed",
            details=(
                "V0.4 diagnostic mechanics and reviewed controls passed for "
                "diagnostic-subset method review; this does not clear "
                "full-cohort execution."
            ),
        )
    else:
        failing = ",".join(row.criterion for row in required if row.status != "pass")
        final = AcceptanceRow(
            criterion="v04_acceptance",
            status="fail",
            evidence=failing,
            details=(
                "V0.4 acceptance is blocked until all required criteria pass; "
                "do not treat serial/process parity alone as method validation."
            ),
        )
    return AcceptanceReport(rows=(*required, final))


def _acceptance_sidecar_parity(
    rows_by_name: dict[str, ValidationRow],
) -> AcceptanceRow:
    required = SIDECAR_PARITY_CHECKS
    missing = [name for name in required if name not in rows_by_name]
    failing = [
        name
        for name in required
        if name in rows_by_name and rows_by_name[name].status != "pass"
    ]
    if not missing and not failing:
        return AcceptanceRow(
            criterion="serial_process_sidecar_parity",
            status="pass",
            evidence=",".join(required),
            details="serial and process frozen sidecars match exactly",
        )
    evidence = ",".join((*missing, *failing))
    return AcceptanceRow(
        criterion="serial_process_sidecar_parity",
        status="fail",
        evidence=evidence,
        details="serial/process sidecar parity must pass before method review",
    )


def _acceptance_reviewed_controls(
    rows_by_name: dict[str, ValidationRow],
    controls_manifest: Path | None,
) -> AcceptanceRow:
    row = rows_by_name.get("controls_manifest_assessment")
    reviewed_path = (
        controls_manifest is not None
        and controls_manifest.name.lower().endswith(".reviewed.tsv")
    )
    if (
        row is not None
        and row.serial_value == row.process_value == "provided"
        and reviewed_path
    ):
        return AcceptanceRow(
            criterion="reviewed_controls_manifest",
            status="pass",
            evidence="provided",
            details="a .reviewed.tsv controls manifest was supplied to the validator",
        )
    return AcceptanceRow(
        criterion="reviewed_controls_manifest",
        status="fail",
        evidence="not_provided_or_not_reviewed",
        details=(
            "a .reviewed.tsv controls manifest is required; proposed manifests "
            "and ad-hoc TSV paths cannot satisfy this criterion"
        ),
    )


def _acceptance_positive_controls(
    rows_by_name: dict[str, ValidationRow],
) -> AcceptanceRow:
    row = rows_by_name.get("positive_control_pass_fraction")
    if row is not None and row.status == "pass":
        return AcceptanceRow(
            criterion="positive_control_sensitivity",
            status="pass",
            evidence=f"{row.serial_value}/{row.process_value}",
            details=row.details,
        )
    return AcceptanceRow(
        criterion="positive_control_sensitivity",
        status="fail",
        evidence=_row_evidence(row),
        details="positive controls must be present and pass before V0.4 acceptance",
    )


def _acceptance_identity_decoys(
    rows_by_name: dict[str, ValidationRow],
) -> AcceptanceRow:
    coherent = rows_by_name.get("decoy_coherent_seed_count")
    rejected = rows_by_name.get("decoy_correctly_rejected_count")
    if (
        coherent is not None
        and rejected is not None
        and coherent.status == "pass"
        and coherent.serial_value == coherent.process_value == "0"
        and rejected.status == "pass"
    ):
        return AcceptanceRow(
            criterion="identity_decoy_specificity",
            status="pass",
            evidence=f"promoted=0 rejected={rejected.serial_value}",
            details="identity decoys were rejected without coherent-seed promotion",
        )
    return AcceptanceRow(
        criterion="identity_decoy_specificity",
        status="fail",
        evidence=(
            f"promoted={_row_evidence(coherent)} "
            f"rejected={_row_evidence(rejected)}"
        ),
        details="identity decoys must not reach coherent seed or would-primary",
    )


def _row_evidence(row: ValidationRow | None) -> str:
    if row is None:
        return "missing"
    return f"{row.status}:{row.serial_value}/{row.process_value}"
