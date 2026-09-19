from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from xic_extractor.instrument_qc.calibration_matrix_preview import (
    build_response_preview_rows,
    build_rt_preview_rows,
    correction_status_counts,
    preview_summary,
    read_optional_injection_order,
)
from xic_extractor.instrument_qc.calibration_product_evidence import (
    collect_level0_evidence,
    file_hash,
)
from xic_extractor.instrument_qc.calibration_product_models import (
    ARTIFACT_SCHEMA_VERSION,
    ArtifactInventoryItem,
    CalibrationBundleManifest,
)
from xic_extractor.instrument_qc.calibration_product_writers import (
    write_calibration_evidence_summary_json,
    write_calibration_evidence_tsv,
    write_calibration_manifest_json,
    write_matrix_response_preview_tsv,
    write_matrix_rt_preview_tsv,
    write_preview_summary_json,
    write_rt_drift_model_tsv,
    write_rt_leave_one_anchor_out_tsv,
)
from xic_extractor.instrument_qc.calibration_rt_model import build_rt_model_bundle


@dataclass(frozen=True)
class CalibrationBundleResult:
    manifest_json: Path
    evidence_tsv: Path
    evidence_summary_json: Path
    rt_preview_tsv: Path | None = None
    rt_preview_summary_json: Path | None = None
    rt_model_tsv: Path | None = None
    rt_model_summary_json: Path | None = None
    rt_leave_one_anchor_out_tsv: Path | None = None
    response_preview_tsv: Path | None = None
    response_preview_summary_json: Path | None = None


def build_level0_calibration_bundle(
    *,
    instrument_qc_dir: Path,
    output_dir: Path,
    generation_command: str,
) -> CalibrationBundleResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_id = output_dir.name or "calibration_bundle"
    collected = collect_level0_evidence(
        instrument_qc_dir=instrument_qc_dir,
        bundle_id=bundle_id,
    )
    evidence_tsv = output_dir / "instrument_qc_calibration_evidence.tsv"
    evidence_summary_json = (
        output_dir / "instrument_qc_calibration_evidence_summary.json"
    )
    manifest_json = output_dir / "instrument_qc_calibration_manifest.json"

    write_calibration_evidence_tsv(evidence_tsv, collected.rows)
    write_calibration_evidence_summary_json(evidence_summary_json, collected.summary)

    manifest = CalibrationBundleManifest(
        schema_version=ARTIFACT_SCHEMA_VERSION,
        bundle_id=bundle_id,
        run_id=bundle_id,
        product_maturity_level="level_0",
        overall_verdict="diagnostic_only",
        artifact_inventory=_base_artifact_inventory(
            manifest_json=manifest_json,
            evidence_tsv=evidence_tsv,
            evidence_summary_json=evidence_summary_json,
        ),
        source_artifacts=collected.source_artifacts,
        source_contracts={
            "instrument_qc_sdolek_trend.tsv": "trend",
            "instrument_qc_mixstds_trend.tsv": "trend_optional",
            "instrument_qc_hcd_audit.tsv": "hcd_audit_optional",
        },
        generation_command=generation_command,
        created_at_utc=datetime.now(UTC).replace(microsecond=0).isoformat(),
        created_by="instrument_qc_matrix_calibration_preview.py",
        status_counts={
            "source_type": collected.summary.counts_by_source_type,
            "coverage_status": collected.summary.counts_by_coverage_status,
            "product_support_status": (
                collected.summary.counts_by_product_support_status
            ),
            "calibration_eligible": collected.summary.counts_by_calibration_eligible,
        },
        first_human_file="",
        first_machine_file=evidence_tsv.name,
    )
    write_calibration_manifest_json(manifest_json, manifest)
    return CalibrationBundleResult(
        manifest_json=manifest_json,
        evidence_tsv=evidence_tsv,
        evidence_summary_json=evidence_summary_json,
    )


def build_level1_rt_calibration_preview(
    *,
    instrument_qc_dir: Path,
    matrix_input: Path,
    matrix_input_role: str,
    output_dir: Path,
    generation_command: str,
) -> CalibrationBundleResult:
    return build_level1_calibration_preview(
        instrument_qc_dir=instrument_qc_dir,
        matrix_input=matrix_input,
        matrix_input_role=matrix_input_role,
        preview_kind="rt",
        output_dir=output_dir,
        generation_command=generation_command,
    )


def build_level1_calibration_preview(
    *,
    instrument_qc_dir: Path,
    matrix_input: Path,
    matrix_input_role: str,
    preview_kind: str,
    output_dir: Path,
    generation_command: str,
) -> CalibrationBundleResult:
    if matrix_input_role != "untargeted_cell_table":
        raise ValueError(f"unsupported matrix input role: {matrix_input_role}")
    if preview_kind not in {"rt", "response", "both"}:
        raise ValueError(f"unsupported preview kind: {preview_kind}")
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_id = output_dir.name or "calibration_bundle"
    matrix_hash_before = file_hash(matrix_input)
    collected = collect_level0_evidence(
        instrument_qc_dir=instrument_qc_dir,
        bundle_id=bundle_id,
    )

    evidence_tsv = output_dir / "instrument_qc_calibration_evidence.tsv"
    evidence_summary_json = (
        output_dir / "instrument_qc_calibration_evidence_summary.json"
    )
    manifest_json = output_dir / "instrument_qc_calibration_manifest.json"

    write_calibration_evidence_tsv(evidence_tsv, collected.rows)
    write_calibration_evidence_summary_json(evidence_summary_json, collected.summary)

    rt_preview_tsv: Path | None = None
    rt_preview_summary_json: Path | None = None
    rt_model_tsv: Path | None = None
    rt_model_summary_json: Path | None = None
    rt_leave_one_anchor_out_tsv: Path | None = None
    response_preview_tsv: Path | None = None
    response_preview_summary_json: Path | None = None
    rt_preview_counts: dict[str, int] = {}
    response_preview_counts: dict[str, int] = {}
    inventory_extra: list[ArtifactInventoryItem] = []
    first_machine_file = evidence_tsv.name

    if preview_kind in {"rt", "both"}:
        rt_model = build_rt_model_bundle(
            bundle_id=bundle_id,
            evidence_rows=collected.rows,
        )
        rt_model_tsv = output_dir / "instrument_qc_rt_drift_model.tsv"
        rt_model_summary_json = (
            output_dir / "instrument_qc_rt_drift_model_summary.json"
        )
        rt_leave_one_anchor_out_tsv = (
            output_dir / "instrument_qc_rt_leave_one_anchor_out.tsv"
        )
        write_rt_drift_model_tsv(rt_model_tsv, rt_model.model_rows)
        write_rt_leave_one_anchor_out_tsv(
            rt_leave_one_anchor_out_tsv,
            rt_model.leave_one_anchor_out_rows,
        )
        write_preview_summary_json(rt_model_summary_json, rt_model.summary)

        rt_preview_tsv = output_dir / "matrix_rt_calibration_preview.tsv"
        rt_preview_summary_json = (
            output_dir / "matrix_rt_calibration_preview_summary.json"
        )
        rt_rows = build_rt_preview_rows(
            bundle_id=bundle_id,
            matrix_input=matrix_input,
            matrix_hash=matrix_hash_before,
            rt_model=rt_model,
            injection_order=read_optional_injection_order(instrument_qc_dir),
        )
        rt_preview_counts = correction_status_counts(rt_rows)
        rt_summary = preview_summary(
            bundle_id=bundle_id,
            matrix_source=matrix_input.name,
            matrix_source_hash=matrix_hash_before,
            total_rows=len(rt_rows),
            correction_status_counts=rt_preview_counts,
        )
        write_matrix_rt_preview_tsv(rt_preview_tsv, rt_rows)
        write_preview_summary_json(rt_preview_summary_json, rt_summary)
        inventory_extra.extend(
            (
                ArtifactInventoryItem(
                    artifact_id="rt_model",
                    path=Path(rt_model_tsv.name),
                    role="rt_alignment_support_model",
                    required=True,
                    schema_version=ARTIFACT_SCHEMA_VERSION,
                    status="present",
                ),
                ArtifactInventoryItem(
                    artifact_id="rt_model_summary",
                    path=Path(rt_model_summary_json.name),
                    role="summary",
                    required=True,
                    schema_version=ARTIFACT_SCHEMA_VERSION,
                    status="present",
                ),
                ArtifactInventoryItem(
                    artifact_id="rt_leave_one_anchor_out",
                    path=Path(rt_leave_one_anchor_out_tsv.name),
                    role="rt_model_validation",
                    required=True,
                    schema_version=ARTIFACT_SCHEMA_VERSION,
                    status="present",
                ),
                ArtifactInventoryItem(
                    artifact_id="rt_preview",
                    path=Path(rt_preview_tsv.name),
                    role="matrix_preview",
                    required=True,
                    schema_version=ARTIFACT_SCHEMA_VERSION,
                    status="present",
                ),
                ArtifactInventoryItem(
                    artifact_id="rt_preview_summary",
                    path=Path(rt_preview_summary_json.name),
                    role="summary",
                    required=True,
                    schema_version=ARTIFACT_SCHEMA_VERSION,
                    status="present",
                ),
            )
        )
        first_machine_file = rt_preview_tsv.name

    if preview_kind in {"response", "both"}:
        response_preview_tsv = output_dir / "matrix_response_calibration_preview.tsv"
        response_preview_summary_json = (
            output_dir / "matrix_response_calibration_preview_summary.json"
        )
        response_rows = build_response_preview_rows(
            bundle_id=bundle_id,
            matrix_input=matrix_input,
            matrix_hash=matrix_hash_before,
        )
        response_preview_counts = correction_status_counts(response_rows)
        response_summary = preview_summary(
            bundle_id=bundle_id,
            matrix_source=matrix_input.name,
            matrix_source_hash=matrix_hash_before,
            total_rows=len(response_rows),
            correction_status_counts=response_preview_counts,
        )
        write_matrix_response_preview_tsv(response_preview_tsv, response_rows)
        write_preview_summary_json(response_preview_summary_json, response_summary)
        inventory_extra.extend(
            (
                ArtifactInventoryItem(
                    artifact_id="response_preview",
                    path=Path(response_preview_tsv.name),
                    role="matrix_preview",
                    required=True,
                    schema_version=ARTIFACT_SCHEMA_VERSION,
                    status="present",
                ),
                ArtifactInventoryItem(
                    artifact_id="response_preview_summary",
                    path=Path(response_preview_summary_json.name),
                    role="summary",
                    required=True,
                    schema_version=ARTIFACT_SCHEMA_VERSION,
                    status="present",
                ),
            )
        )
        first_machine_file = (
            rt_preview_tsv.name
            if rt_preview_tsv is not None
            else response_preview_tsv.name
        )

    matrix_hash_after = file_hash(matrix_input)
    if matrix_hash_after != matrix_hash_before:
        raise ValueError(
            f"matrix input changed during preview generation: {matrix_input}"
        )

    source_artifacts = {
        **collected.source_artifacts,
        matrix_input.name: matrix_hash_before,
    }
    manifest = CalibrationBundleManifest(
        schema_version=ARTIFACT_SCHEMA_VERSION,
        bundle_id=bundle_id,
        run_id=bundle_id,
        product_maturity_level="level_1",
        overall_verdict="preview_ready",
        artifact_inventory=(
            *_base_artifact_inventory(
                manifest_json=manifest_json,
                evidence_tsv=evidence_tsv,
                evidence_summary_json=evidence_summary_json,
            ),
            *inventory_extra,
        ),
        source_artifacts=source_artifacts,
        source_contracts={
            "instrument_qc_sdolek_trend.tsv": "trend",
            "instrument_qc_mixstds_trend.tsv": "trend_optional",
            "instrument_qc_hcd_audit.tsv": "hcd_audit_optional",
            matrix_input.name: matrix_input_role,
        },
        generation_command=generation_command,
        created_at_utc=datetime.now(UTC).replace(microsecond=0).isoformat(),
        created_by="instrument_qc_matrix_calibration_preview.py",
        status_counts={
            "source_type": collected.summary.counts_by_source_type,
            "coverage_status": collected.summary.counts_by_coverage_status,
            "product_support_status": (
                collected.summary.counts_by_product_support_status
            ),
            "calibration_eligible": collected.summary.counts_by_calibration_eligible,
            **({"rt_preview_status": rt_preview_counts} if rt_preview_counts else {}),
            **(
                {"response_preview_status": response_preview_counts}
                if response_preview_counts
                else {}
            ),
        },
        first_human_file="",
        first_machine_file=first_machine_file,
    )
    write_calibration_manifest_json(manifest_json, manifest)
    return CalibrationBundleResult(
        manifest_json=manifest_json,
        evidence_tsv=evidence_tsv,
        evidence_summary_json=evidence_summary_json,
        rt_preview_tsv=rt_preview_tsv,
        rt_preview_summary_json=rt_preview_summary_json,
        response_preview_tsv=response_preview_tsv,
        response_preview_summary_json=response_preview_summary_json,
        rt_model_tsv=rt_model_tsv,
        rt_model_summary_json=rt_model_summary_json,
        rt_leave_one_anchor_out_tsv=rt_leave_one_anchor_out_tsv,
    )


def _base_artifact_inventory(
    *,
    manifest_json: Path,
    evidence_tsv: Path,
    evidence_summary_json: Path,
) -> tuple[ArtifactInventoryItem, ...]:
    return (
        ArtifactInventoryItem(
            artifact_id="manifest",
            path=Path(manifest_json.name),
            role="entrypoint",
            required=True,
            schema_version=ARTIFACT_SCHEMA_VERSION,
            status="present",
        ),
        ArtifactInventoryItem(
            artifact_id="evidence",
            path=Path(evidence_tsv.name),
            role="row_contract",
            required=True,
            schema_version=ARTIFACT_SCHEMA_VERSION,
            status="present",
        ),
        ArtifactInventoryItem(
            artifact_id="evidence_summary",
            path=Path(evidence_summary_json.name),
            role="summary",
            required=True,
            schema_version=ARTIFACT_SCHEMA_VERSION,
            status="present",
        ),
    )
